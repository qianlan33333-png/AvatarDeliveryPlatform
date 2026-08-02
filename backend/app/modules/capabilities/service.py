from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.models import (
    EntitlementEvent,
    ProductCourseMapping,
    User,
)
from backend.app.modules.capabilities.models import (
    CapabilityEntitlement,
    ProductCapabilityMapping,
)
from backend.app.modules.entitlements.service import (
    EntitlementApplicationError,
    EntitlementCommand,
    apply_entitlement_command,
    canonical_command_hash,
    lock_entitlements_for_phone_hash,
)
from backend.app.security import decrypt_phone, encrypt_phone, lookup_phone_hash, normalize_phone

CapabilityCode = Literal["chat_qa", "copywriting"]
CapabilityAction = Literal["grant", "renew", "revoke"]

CAPABILITY_CODES: tuple[CapabilityCode, ...] = ("chat_qa", "copywriting")
CAPABILITY_LABELS: dict[CapabilityCode, str] = {
    "chat_qa": "聊一聊会员",
    "copywriting": "话术会员",
}


class CapabilityEntitlementError(ValueError):
    pass


@dataclass(frozen=True)
class BenefitApplicationResult:
    event: EntitlementEvent
    course_entitlements_changed: int
    capability_entitlements_changed: int


def validate_capability_code(value: str) -> CapabilityCode:
    normalized = value.strip()
    if normalized not in CAPABILITY_CODES:
        raise CapabilityEntitlementError("未知的 AI 能力")
    return cast(CapabilityCode, normalized)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _merge_expiry(current: datetime | None, requested: datetime | None) -> datetime | None:
    if current is None or requested is None:
        return None
    return max(_utc(current), _utc(requested))


def capability_entitlement_status(
    entitlement: CapabilityEntitlement | None,
    now: datetime | None = None,
) -> Literal["active", "upcoming", "expired", "revoked", "not_granted"]:
    if entitlement is None:
        return "not_granted"
    if entitlement.status == "revoked":
        return "revoked"
    current = _utc(now or datetime.now(UTC))
    if _utc(entitlement.effective_at) > current:
        return "upcoming"
    if entitlement.expires_at is not None and _utc(entitlement.expires_at) <= current:
        return "expired"
    return "active"


def capability_is_active(
    db: Session,
    user_id: str,
    code: str,
    now: datetime | None = None,
) -> bool:
    capability_code = validate_capability_code(code)
    entitlement = db.scalar(
        select(CapabilityEntitlement).where(
            CapabilityEntitlement.user_id == user_id,
            CapabilityEntitlement.capability_code == capability_code,
        )
    )
    return capability_entitlement_status(entitlement, now) == "active"


def list_active_capabilities(
    db: Session,
    user_id: str,
    now: datetime | None = None,
) -> list[str]:
    entitlements = {
        item.capability_code: item
        for item in db.scalars(
            select(CapabilityEntitlement).where(CapabilityEntitlement.user_id == user_id)
        )
    }
    return [
        code
        for code in CAPABILITY_CODES
        if capability_entitlement_status(entitlements.get(code), now) == "active"
    ]


def list_capability_states(
    db: Session,
    user_id: str,
    now: datetime | None = None,
) -> list[dict[str, str | None]]:
    entitlements = {
        item.capability_code: item
        for item in db.scalars(
            select(CapabilityEntitlement).where(CapabilityEntitlement.user_id == user_id)
        )
    }
    result: list[dict[str, str | None]] = []
    for code in CAPABILITY_CODES:
        entitlement = entitlements.get(code)
        result.append(
            {
                "code": code,
                "status": capability_entitlement_status(entitlement, now),
                "effective_at": (
                    _utc(entitlement.effective_at).isoformat() if entitlement else None
                ),
                "expires_at": (
                    _utc(entitlement.expires_at).isoformat()
                    if entitlement and entitlement.expires_at
                    else None
                ),
            }
        )
    return result


def claim_capability_entitlements_by_phone_hash(
    db: Session,
    *,
    user_id: str,
    phone_hash: str,
    commit: bool = True,
) -> int:
    lock_entitlements_for_phone_hash(db, phone_hash)
    pending = list(
        db.scalars(
            select(CapabilityEntitlement).where(
                CapabilityEntitlement.phone_hash == phone_hash,
                CapabilityEntitlement.user_id.is_(None),
            )
        )
    )
    for entitlement in pending:
        entitlement.user_id = user_id
    if commit:
        db.commit()
    return len(pending)


def claim_capability_entitlements_for_user(
    db: Session,
    user: User,
    *,
    commit: bool = True,
) -> int:
    if not user.phone_hash:
        return 0
    return claim_capability_entitlements_by_phone_hash(
        db,
        user_id=user.id,
        phone_hash=user.phone_hash,
        commit=commit,
    )


def _upsert_capability_entitlement(
    db: Session,
    *,
    phone_hash: str,
    owner: User | None,
    capability_code: CapabilityCode,
    action: CapabilityAction,
    effective_at: datetime,
    expires_at: datetime | None,
    source_order_id: str,
    event_id: str,
    replace_expiry: bool = False,
    force: bool = False,
) -> tuple[CapabilityEntitlement, bool]:
    entitlement = db.scalar(
        select(CapabilityEntitlement).where(
            CapabilityEntitlement.phone_hash == phone_hash,
            CapabilityEntitlement.capability_code == capability_code,
        )
    )
    if entitlement is None:
        entitlement = CapabilityEntitlement(
            user_id=owner.id if owner else None,
            phone_hash=phone_hash,
            capability_code=capability_code,
            effective_at=effective_at,
            expires_at=expires_at,
            source_order_id=source_order_id,
        )
        db.add(entitlement)
    elif owner is not None and entitlement.user_id is None:
        entitlement.user_id = owner.id

    if not force and entitlement.last_event_effective_at is not None:
        incoming = _utc(effective_at)
        current = _utc(entitlement.last_event_effective_at)
        if incoming < current or (
            incoming == current
            and entitlement.last_event_action == "revoke"
            and action != "revoke"
        ):
            return entitlement, False

    if action == "revoke":
        entitlement.status = "revoked"
    else:
        entitlement.status = "active"
        entitlement.effective_at = min(
            _utc(entitlement.effective_at),
            _utc(effective_at),
        )
        if action == "grant" and replace_expiry:
            entitlement.expires_at = expires_at
        else:
            entitlement.expires_at = _merge_expiry(entitlement.expires_at, expires_at)
    entitlement.source_order_id = source_order_id
    entitlement.last_event_effective_at = effective_at
    entitlement.last_event_action = action
    entitlement.last_event_id = event_id
    return entitlement, True


def _stage_product_capabilities(
    db: Session,
    command: EntitlementCommand,
    *,
    settings: Settings,
) -> tuple[int, int]:
    phone_hash = lookup_phone_hash(normalize_phone(command.phone), settings)
    owner = db.scalar(select(User).where(User.phone_hash == phone_hash))
    mappings = list(
        db.scalars(
            select(ProductCapabilityMapping).where(
                ProductCapabilityMapping.product_code == command.product_code,
                ProductCapabilityMapping.is_active.is_(True),
            )
        )
    )
    changed = 0
    for mapping in mappings:
        _, applied = _upsert_capability_entitlement(
            db,
            phone_hash=phone_hash,
            owner=owner,
            capability_code=validate_capability_code(mapping.capability_code),
            action=command.action,
            effective_at=command.effective_at,
            expires_at=command.expires_at,
            source_order_id=command.order_id,
            event_id=command.event_id,
        )
        changed += int(applied)
    return len(mappings), changed


def _new_shared_event(
    command: EntitlementCommand,
    *,
    payload_hash: str,
    result: str,
    settings: Settings,
    created_by: str,
    replayed_from_event_id: str | None,
) -> EntitlementEvent:
    phone = normalize_phone(command.phone)
    return EntitlementEvent(
        event_id=command.event_id,
        action=command.action,
        order_id=command.order_id,
        product_code=command.product_code,
        phone_hash=lookup_phone_hash(phone, settings),
        phone_ciphertext=encrypt_phone(phone, settings),
        payload_hash=payload_hash,
        result=result,
        effective_at=command.effective_at,
        expires_at=command.expires_at,
        created_by=created_by,
        replayed_from_event_id=replayed_from_event_id,
    )


def apply_benefit_entitlement_command(
    db: Session,
    command: EntitlementCommand,
    *,
    payload_hash: str,
    settings: Settings | None = None,
    created_by: str = "benefit_webhook",
    replayed_from_event_id: str | None = None,
) -> BenefitApplicationResult:
    configured = settings or get_settings()
    phone_hash = lookup_phone_hash(normalize_phone(command.phone), configured)
    lock_entitlements_for_phone_hash(db, phone_hash)
    has_course_mapping = db.scalar(
        select(ProductCourseMapping.id).where(
            ProductCourseMapping.product_code == command.product_code,
            ProductCourseMapping.is_active.is_(True),
        )
    ) is not None
    capability_mapping_count, capability_count = _stage_product_capabilities(
        db,
        command,
        settings=configured,
    )

    if not has_course_mapping and capability_mapping_count == 0:
        event = _new_shared_event(
            command,
            payload_hash=payload_hash,
            result="failed_mapping",
            settings=configured,
            created_by=created_by,
            replayed_from_event_id=replayed_from_event_id,
        )
        event.error_message = "商品码没有绑定任何有效课程或 AI 能力"
        db.add(event)
        db.commit()
        raise EntitlementApplicationError(event.error_message)

    if has_course_mapping:
        event, course_count = apply_entitlement_command(
            db,
            command,
            payload_hash=payload_hash,
            created_by=created_by,
            replayed_from_event_id=replayed_from_event_id,
            settings=configured,
        )
        total_changed = course_count + capability_count
        if total_changed and event.result != "applied":
            event.result = "applied"
            db.commit()
    else:
        event = _new_shared_event(
            command,
            payload_hash=payload_hash,
            result="applied" if capability_count else "ignored_stale",
            settings=configured,
            created_by=created_by,
            replayed_from_event_id=replayed_from_event_id,
        )
        db.add(event)
        db.commit()
        course_count = 0

    return BenefitApplicationResult(
        event=event,
        course_entitlements_changed=course_count,
        capability_entitlements_changed=capability_count,
    )


def replay_benefit_entitlement_event(
    db: Session,
    original: EntitlementEvent,
    settings: Settings | None = None,
) -> BenefitApplicationResult:
    configured = settings or get_settings()
    if original.result == "applied":
        raise EntitlementApplicationError("已成功事件无需重放")
    if original.effective_at is None:
        raise EntitlementApplicationError("原事件缺少生效时间，无法重放")
    command = EntitlementCommand(
        event_id=f"{original.event_id}:replay:{uuid.uuid4().hex[:12]}",
        order_id=original.order_id,
        product_code=original.product_code,
        phone=decrypt_phone(original.phone_ciphertext, configured),
        action=original.action,  # type: ignore[arg-type]
        effective_at=original.effective_at,
        expires_at=original.expires_at,
    )
    return apply_benefit_entitlement_command(
        db,
        command,
        payload_hash=canonical_command_hash(command),
        settings=configured,
        created_by="admin_replay",
        replayed_from_event_id=original.event_id,
    )


def set_manual_capability_entitlement(
    db: Session,
    *,
    user: User,
    capability_code: str,
    action: CapabilityAction,
    expires_at: datetime | None,
    effective_at: datetime | None = None,
) -> EntitlementEvent:
    if not user.phone_hash or not user.phone_ciphertext:
        raise CapabilityEntitlementError("用户尚未绑定手机号")
    lock_entitlements_for_phone_hash(db, user.phone_hash)
    if action not in {"grant", "renew", "revoke"}:
        raise CapabilityEntitlementError("不支持的权益操作")
    code = validate_capability_code(capability_code)
    now = _utc(effective_at or datetime.now(UTC))
    if expires_at is not None and _utc(expires_at) <= now:
        raise CapabilityEntitlementError("到期时间必须晚于生效时间")

    event_id = f"manual-capability:{uuid.uuid4().hex}"
    _upsert_capability_entitlement(
        db,
        phone_hash=user.phone_hash,
        owner=user,
        capability_code=code,
        action=action,
        effective_at=now,
        expires_at=expires_at,
        source_order_id="manual",
        event_id=event_id,
        replace_expiry=action == "grant",
        force=True,
    )
    event_payload = {
        "action": action,
        "capability_code": code,
        "event_id": event_id,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "user_id": user.id,
    }
    event = EntitlementEvent(
        event_id=event_id,
        action=action,
        order_id="manual",
        product_code=f"manual-capability:{code}",
        phone_hash=user.phone_hash,
        phone_ciphertext=user.phone_ciphertext,
        payload_hash=hashlib.sha256(
            json.dumps(event_payload, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        result="applied",
        effective_at=now,
        expires_at=expires_at,
        created_by="admin",
    )
    db.add(event)
    db.commit()
    return event
