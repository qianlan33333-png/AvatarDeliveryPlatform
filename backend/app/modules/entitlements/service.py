from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.models import (
    CourseEntitlement,
    EntitlementEvent,
    ProductCourseMapping,
    User,
)
from backend.app.security import (
    decrypt_phone,
    encrypt_phone,
    lookup_phone_hash,
    normalize_phone,
)

EntitlementAction = Literal["grant", "renew", "revoke"]
_ENTITLEMENT_LOCK_NAMESPACE = "avatar-delivery:entitlement:v1:"


class EntitlementApplicationError(ValueError):
    pass


@dataclass(frozen=True)
class EntitlementCommand:
    event_id: str
    order_id: str
    product_code: str
    phone: str
    action: EntitlementAction
    effective_at: datetime
    expires_at: datetime | None


def canonical_command_hash(command: EntitlementCommand) -> str:
    payload = {
        "action": command.action,
        "effective_at": command.effective_at.isoformat(),
        "event_id": command.event_id,
        "expires_at": command.expires_at.isoformat() if command.expires_at else None,
        "order_id": command.order_id,
        "phone": normalize_phone(command.phone),
        "product_code": command.product_code,
    }
    normalized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(normalized.encode()).hexdigest()


def build_webhook_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def webhook_signature_matches(
    secret: str,
    timestamp: str,
    nonce: str,
    body: bytes,
    provided_signature: str,
) -> bool:
    normalized = provided_signature.removeprefix("sha256=").strip().lower()
    expected = build_webhook_signature(secret, timestamp, nonce, body)
    return bool(normalized and hmac.compare_digest(expected, normalized))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def entitlement_phone_lock_key(phone_hash: str) -> int:
    """Return a stable signed bigint suitable for PostgreSQL advisory locks."""

    digest = hashlib.sha256(f"{_ENTITLEMENT_LOCK_NAMESPACE}{phone_hash}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def lock_entitlements_for_phone_hash(db: Session, phone_hash: str) -> None:
    """Serialize all entitlement mutations for one normalized phone identity.

    Transaction-scoped advisory locks are intentionally a PostgreSQL-only
    production guard. SQLite remains a no-op for the local test suite.
    """

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": entitlement_phone_lock_key(phone_hash)},
        )


def entitlement_is_active(entitlement: CourseEntitlement, now: datetime | None = None) -> bool:
    current = now or datetime.now(UTC)
    if entitlement.status != "active" or _utc(entitlement.effective_at) > current:
        return False
    return entitlement.expires_at is None or _utc(entitlement.expires_at) > current


def user_has_course_access(
    db: Session,
    user_id: str,
    course_id: str,
    now: datetime | None = None,
) -> bool:
    entitlement = db.scalar(
        select(CourseEntitlement).where(
            CourseEntitlement.user_id == user_id,
            CourseEntitlement.course_id == course_id,
        )
    )
    return bool(entitlement and entitlement_is_active(entitlement, now))


def _merge_expiry(current: datetime | None, requested: datetime | None) -> datetime | None:
    if current is None or requested is None:
        return None
    return max(_utc(current), _utc(requested))


def entitlement_event_is_stale(
    entitlement: CourseEntitlement,
    *,
    action: EntitlementAction,
    effective_at: datetime,
) -> bool:
    if entitlement.last_event_effective_at is None:
        return False
    incoming = _utc(effective_at)
    current = _utc(entitlement.last_event_effective_at)
    if incoming < current:
        return True
    return incoming == current and entitlement.last_event_action == "revoke" and action != "revoke"


def apply_entitlement_command(
    db: Session,
    command: EntitlementCommand,
    *,
    payload_hash: str,
    created_by: str = "webhook",
    replayed_from_event_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[EntitlementEvent, int]:
    configured = settings or get_settings()
    phone = normalize_phone(command.phone)
    phone_hash = lookup_phone_hash(phone, configured)
    lock_entitlements_for_phone_hash(db, phone_hash)
    phone_ciphertext = encrypt_phone(phone, configured)
    mappings = list(
        db.scalars(
            select(ProductCourseMapping).where(
                ProductCourseMapping.product_code == command.product_code,
                ProductCourseMapping.is_active.is_(True),
            )
        )
    )
    event = EntitlementEvent(
        event_id=command.event_id,
        action=command.action,
        order_id=command.order_id,
        product_code=command.product_code,
        phone_hash=phone_hash,
        phone_ciphertext=phone_ciphertext,
        payload_hash=payload_hash,
        result="received",
        effective_at=command.effective_at,
        expires_at=command.expires_at,
        replayed_from_event_id=replayed_from_event_id,
        created_by=created_by,
    )
    db.add(event)
    if not mappings:
        event.result = "failed_mapping"
        event.error_message = "商品码没有绑定任何有效课程"
        db.commit()
        raise EntitlementApplicationError(event.error_message)

    owner = db.scalar(select(User).where(User.phone_hash == phone_hash))
    changed = 0
    for mapping in mappings:
        entitlement = db.scalar(
            select(CourseEntitlement).where(
                CourseEntitlement.phone_hash == phone_hash,
                CourseEntitlement.course_id == mapping.course_id,
            )
        )
        if not entitlement:
            entitlement = CourseEntitlement(
                user_id=owner.id if owner else None,
                phone_hash=phone_hash,
                course_id=mapping.course_id,
                effective_at=command.effective_at,
                expires_at=command.expires_at,
                source_order_id=command.order_id,
            )
            db.add(entitlement)
        elif owner and entitlement.user_id is None:
            entitlement.user_id = owner.id

        if entitlement_event_is_stale(
            entitlement,
            action=command.action,
            effective_at=command.effective_at,
        ):
            continue

        if command.action == "revoke":
            entitlement.status = "revoked"
        else:
            entitlement.status = "active"
            entitlement.effective_at = min(
                _utc(entitlement.effective_at),
                _utc(command.effective_at),
            )
            entitlement.expires_at = _merge_expiry(
                entitlement.expires_at,
                command.expires_at,
            )
        entitlement.source_order_id = command.order_id
        entitlement.last_event_effective_at = command.effective_at
        entitlement.last_event_action = command.action
        entitlement.last_event_id = command.event_id
        changed += 1

    event.result = "applied" if changed else "ignored_stale"
    db.commit()
    return event, changed


def replay_entitlement_event(
    db: Session,
    original: EntitlementEvent,
    settings: Settings | None = None,
) -> EntitlementEvent:
    configured = settings or get_settings()
    if original.result == "applied":
        raise EntitlementApplicationError("已成功事件无需重放")
    if original.effective_at is None:
        raise EntitlementApplicationError("原事件缺少生效时间，无法重放")
    phone = decrypt_phone(original.phone_ciphertext, configured)
    command = EntitlementCommand(
        event_id=f"{original.event_id}:replay:{uuid.uuid4().hex[:12]}",
        order_id=original.order_id,
        product_code=original.product_code,
        phone=phone,
        action=original.action,  # type: ignore[arg-type]
        effective_at=original.effective_at,
        expires_at=original.expires_at,
    )
    replayed, _ = apply_entitlement_command(
        db,
        command,
        payload_hash=canonical_command_hash(command),
        created_by="admin_replay",
        replayed_from_event_id=original.event_id,
        settings=configured,
    )
    return replayed


def set_manual_course_entitlement(
    db: Session,
    *,
    user: User,
    course_id: str,
    action: Literal["grant", "revoke"],
    expires_at: datetime | None,
) -> EntitlementEvent:
    if not user.phone_hash or not user.phone_ciphertext:
        raise EntitlementApplicationError("用户尚未绑定手机号")
    lock_entitlements_for_phone_hash(db, user.phone_hash)
    now = datetime.now(UTC)
    event_id = f"manual:{uuid.uuid4().hex}"
    entitlement = db.scalar(
        select(CourseEntitlement).where(
            CourseEntitlement.phone_hash == user.phone_hash,
            CourseEntitlement.course_id == course_id,
        )
    )
    if not entitlement:
        entitlement = CourseEntitlement(
            user_id=user.id,
            phone_hash=user.phone_hash,
            course_id=course_id,
            effective_at=now,
            expires_at=expires_at,
            source_order_id="manual",
        )
        db.add(entitlement)
    entitlement.user_id = user.id
    entitlement.status = "active" if action == "grant" else "revoked"
    if action == "grant":
        entitlement.effective_at = now
        entitlement.expires_at = expires_at
    entitlement.last_event_effective_at = now
    entitlement.last_event_action = action
    entitlement.last_event_id = event_id

    event_payload = {
        "action": action,
        "course_id": course_id,
        "event_id": event_id,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "user_id": user.id,
    }
    payload_hash = hashlib.sha256(
        json.dumps(event_payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    event = EntitlementEvent(
        event_id=event_id,
        action=action,
        order_id="manual",
        product_code=f"manual:{course_id}",
        phone_hash=user.phone_hash,
        phone_ciphertext=user.phone_ciphertext,
        payload_hash=payload_hash,
        result="applied",
        effective_at=now,
        expires_at=expires_at,
        created_by="admin",
    )
    db.add(event)
    db.commit()
    return event
