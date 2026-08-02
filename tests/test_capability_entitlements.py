from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware

import backend.app.modules.capabilities.router as capability_router_module
from backend.app.db import get_session_factory
from backend.app.models import (
    Course,
    CourseEntitlement,
    EntitlementEvent,
    ProductCourseMapping,
    User,
)
from backend.app.modules.admin.auth import bootstrap_admin
from backend.app.modules.admin.router import router as base_admin_router
from backend.app.modules.capabilities.admin import router as capability_admin_router
from backend.app.modules.capabilities.models import (
    CapabilityEntitlement,
    ProductCapabilityMapping,
)
from backend.app.modules.capabilities.router import router as capability_api_router
from backend.app.modules.capabilities.service import (
    apply_benefit_entitlement_command,
    capability_is_active,
    claim_capability_entitlements_for_user,
    list_active_capabilities,
    list_capability_states,
    replay_benefit_entitlement_event,
    set_manual_capability_entitlement,
)
from backend.app.modules.entitlements.service import (
    EntitlementCommand,
    build_webhook_signature,
    canonical_command_hash,
    entitlement_phone_lock_key,
)
from backend.app.security import encrypt_phone, lookup_phone_hash


def _user_with_phone(phone: str = "13800138000") -> User:
    return User(
        nickname="测试学员",
        phone_hash=lookup_phone_hash(phone),
        phone_ciphertext=encrypt_phone(phone),
        phone_last4=phone[-4:],
    )


def _webhook_app() -> FastAPI:
    application = FastAPI()
    application.include_router(capability_api_router)
    return application


def _admin_app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        SessionMiddleware,
        secret_key="test-app-secret-key-with-at-least-32-characters",
    )
    application.include_router(base_admin_router)
    application.include_router(capability_admin_router)
    return application


def _webhook_request(
    client: TestClient,
    payload: dict[str, object],
    *,
    nonce: str,
):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp_value = str(int(time.time()))
    signature = build_webhook_signature(
        "test-entitlement-webhook-secret",
        timestamp_value,
        nonce,
        body,
    )
    return client.post(
        "/api/v1/webhooks/benefit-entitlements",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Avatar-Timestamp": timestamp_value,
            "X-Avatar-Nonce": nonce,
            "X-Avatar-Signature": signature,
        },
    )


def _grant_payload(event_id: str = "benefit-event-1") -> dict[str, object]:
    effective_at = datetime.now(UTC) - timedelta(minutes=1)
    return {
        "event_id": event_id,
        "order_id": "order-combo-1",
        "product_code": "COMBO-001",
        "phone": "13800138000",
        "action": "grant",
        "effective_at": effective_at.isoformat(),
        "expires_at": (effective_at + timedelta(days=30)).isoformat(),
    }


def test_manual_capability_grant_renew_revoke_and_profile_states() -> None:
    session_factory = get_session_factory()
    now = datetime.now(UTC)
    with session_factory() as db:
        user = _user_with_phone()
        db.add(user)
        db.commit()

        set_manual_capability_entitlement(
            db,
            user=user,
            capability_code="chat_qa",
            action="grant",
            effective_at=now,
            expires_at=now + timedelta(days=10),
        )
        assert capability_is_active(db, user.id, "chat_qa", now + timedelta(days=1))
        assert list_active_capabilities(db, user.id, now + timedelta(days=1)) == ["chat_qa"]
        states = list_capability_states(db, user.id, now + timedelta(days=1))
        assert states[0]["status"] == "active"
        assert states[1] == {
            "code": "copywriting",
            "status": "not_granted",
            "effective_at": None,
            "expires_at": None,
        }

        set_manual_capability_entitlement(
            db,
            user=user,
            capability_code="chat_qa",
            action="renew",
            effective_at=now,
            expires_at=now + timedelta(days=20),
        )
        entitlement = db.query(CapabilityEntitlement).one()
        assert entitlement.expires_at is not None
        assert entitlement.expires_at.replace(tzinfo=UTC) >= now + timedelta(days=19)

        set_manual_capability_entitlement(
            db,
            user=user,
            capability_code="chat_qa",
            action="revoke",
            effective_at=now,
            expires_at=None,
        )
        assert not capability_is_active(db, user.id, "chat_qa", now + timedelta(days=1))
        assert list_capability_states(db, user.id, now)[0]["status"] == "revoked"
        assert db.query(EntitlementEvent).count() == 3


def test_capability_paid_before_registration_is_claimed_by_phone() -> None:
    session_factory = get_session_factory()
    phone = "13900139000"
    phone_hash = lookup_phone_hash(phone)
    now = datetime.now(UTC)
    with session_factory() as db:
        db.add(
            CapabilityEntitlement(
                phone_hash=phone_hash,
                capability_code="copywriting",
                status="active",
                effective_at=now,
                expires_at=now + timedelta(days=90),
                source_order_id="order-before-registration",
            )
        )
        db.commit()
        user = _user_with_phone(phone)
        db.add(user)
        db.commit()

        assert claim_capability_entitlements_for_user(db, user) == 1
        entitlement = db.query(CapabilityEntitlement).one()
        assert entitlement.user_id == user.id
        assert capability_is_active(db, user.id, "copywriting", now)


def test_benefit_webhook_atomically_applies_course_and_capability() -> None:
    session_factory = get_session_factory()
    with session_factory() as db:
        course = Course(title="组合商品课程", status="published")
        db.add(course)
        db.flush()
        db.add(ProductCourseMapping(product_code="COMBO-001", course_id=course.id))
        db.add(
            ProductCapabilityMapping(
                product_code="COMBO-001",
                capability_code="chat_qa",
            )
        )
        db.commit()

    payload = _grant_payload()
    with TestClient(_webhook_app()) as client:
        granted = _webhook_request(client, payload, nonce="benefit-nonce-1")
        duplicate = _webhook_request(client, payload, nonce="benefit-nonce-1")
        conflict = _webhook_request(
            client,
            {**payload, "order_id": "different-order"},
            nonce="benefit-nonce-2",
        )

    assert granted.status_code == 200
    assert granted.json()["course_entitlements_changed"] == 1
    assert granted.json()["capability_entitlements_changed"] == 1
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert conflict.status_code == 409

    with session_factory() as db:
        assert db.query(CourseEntitlement).count() == 1
        capability = db.query(CapabilityEntitlement).one()
        assert capability.user_id is None
        event = db.query(EntitlementEvent).one()
        assert event.created_by == "benefit_webhook"
        assert event.result == "applied"


def test_capability_only_product_works_without_course_mapping() -> None:
    session_factory = get_session_factory()
    with session_factory() as db:
        db.add(
            ProductCapabilityMapping(
                product_code="COMBO-001",
                capability_code="copywriting",
            )
        )
        db.commit()

    with TestClient(_webhook_app()) as client:
        response = _webhook_request(client, _grant_payload(), nonce="capability-only-nonce")

    assert response.status_code == 200
    assert response.json()["course_entitlements_changed"] == 0
    assert response.json()["capability_entitlements_changed"] == 1
    with session_factory() as db:
        assert db.query(CapabilityEntitlement).one().capability_code == "copywriting"


def test_newer_revoke_cannot_be_overwritten_by_delayed_grant() -> None:
    session_factory = get_session_factory()
    phone = "13800138000"
    now = datetime.now(UTC)
    with session_factory() as db:
        course = Course(title="乱序权益课程", status="published")
        db.add(course)
        db.flush()
        db.add(ProductCourseMapping(product_code="COMBO-001", course_id=course.id))
        db.add(
            ProductCapabilityMapping(
                product_code="COMBO-001",
                capability_code="chat_qa",
            )
        )
        db.commit()

        commands = [
            EntitlementCommand(
                event_id="ordered-grant",
                order_id="order-ordered",
                product_code="COMBO-001",
                phone=phone,
                action="grant",
                effective_at=now,
                expires_at=now + timedelta(days=30),
            ),
            EntitlementCommand(
                event_id="newer-revoke",
                order_id="refund-ordered",
                product_code="COMBO-001",
                phone=phone,
                action="revoke",
                effective_at=now + timedelta(minutes=2),
                expires_at=None,
            ),
            EntitlementCommand(
                event_id="delayed-old-grant",
                order_id="order-delayed",
                product_code="COMBO-001",
                phone=phone,
                action="grant",
                effective_at=now + timedelta(minutes=1),
                expires_at=now + timedelta(days=60),
            ),
        ]
        results = [
            apply_benefit_entitlement_command(
                db,
                command,
                payload_hash=canonical_command_hash(command),
            )
            for command in commands
        ]

        assert results[-1].event.result == "ignored_stale"
        assert results[-1].course_entitlements_changed == 0
        assert results[-1].capability_entitlements_changed == 0
        assert db.query(CourseEntitlement).one().status == "revoked"
        assert db.query(CapabilityEntitlement).one().status == "revoked"


def test_entitlement_phone_lock_key_is_stable_signed_bigint() -> None:
    phone_hash = lookup_phone_hash("13800138000")

    first = entitlement_phone_lock_key(phone_hash)
    second = entitlement_phone_lock_key(phone_hash)

    assert first == second
    assert -(2**63) <= first < 2**63
    assert first != entitlement_phone_lock_key(lookup_phone_hash("13900139000"))


@pytest.mark.parametrize(
    ("stored_hash", "expected_status"),
    [(None, 200), ("different-payload-hash", 409)],
)
def test_integrity_race_rereads_event_for_duplicate_or_conflict(
    monkeypatch: pytest.MonkeyPatch,
    stored_hash: str | None,
    expected_status: int,
) -> None:
    payload = _grant_payload("benefit-event-raced")

    def simulate_concurrent_commit(db, command, *, payload_hash):
        # Model another transaction winning the unique event_id race. Rolling
        # back first discards this request's pending nonce in the SQLite test.
        db.rollback()
        db.add(
            EntitlementEvent(
                event_id=command.event_id,
                action=command.action,
                order_id=command.order_id,
                product_code=command.product_code,
                phone_hash=lookup_phone_hash(command.phone),
                phone_ciphertext=encrypt_phone(command.phone),
                payload_hash=stored_hash or payload_hash,
                result="applied",
                effective_at=command.effective_at,
                expires_at=command.expires_at,
                created_by="benefit_webhook",
            )
        )
        db.commit()
        raise IntegrityError("INSERT entitlement_events", {}, RuntimeError("unique"))

    monkeypatch.setattr(
        capability_router_module,
        "apply_benefit_entitlement_command",
        simulate_concurrent_commit,
    )
    with TestClient(_webhook_app()) as client:
        response = _webhook_request(client, payload, nonce="benefit-raced-nonce")

    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json() == {
            "ok": True,
            "status": "duplicate",
            "event_id": "benefit-event-raced",
        }
    else:
        assert response.json()["detail"] == "event id payload conflict"


def test_failed_generic_event_can_be_replayed_after_mapping_is_fixed() -> None:
    session_factory = get_session_factory()
    with TestClient(_webhook_app()) as client:
        failed = _webhook_request(
            client,
            _grant_payload("benefit-event-replay"),
            nonce="benefit-replay-nonce",
        )
    assert failed.status_code == 422

    with session_factory() as db:
        original = db.query(EntitlementEvent).one()
        assert original.result == "failed_mapping"
        db.add(
            ProductCapabilityMapping(
                product_code="COMBO-001",
                capability_code="chat_qa",
            )
        )
        db.commit()

        replayed = replay_benefit_entitlement_event(db, original)
        assert replayed.capability_entitlements_changed == 1
        assert replayed.event.replayed_from_event_id == original.event_id
        assert replayed.event.created_by == "admin_replay"
        assert db.query(CapabilityEntitlement).one().capability_code == "chat_qa"


def test_capability_admin_uses_level_two_mapping_form_and_csrf() -> None:
    bootstrap_admin()
    with TestClient(_admin_app()) as client:
        login = client.post(
            "/admin/login",
            data={
                "username": "admin",
                "password": "test-admin-password",
                "next_url": "/admin/capabilities",
            },
            follow_redirects=False,
        )
        assert login.status_code == 303
        listing = client.get("/admin/capabilities")
        assert listing.status_code == 200
        assert listing.text.count("<h1>") == 1
        assert 'href="/admin/capabilities/mappings/new"' in listing.text
        assert 'name="product_code"' not in listing.text

        create_page = client.get("/admin/capabilities/mappings/new")
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', create_page.text)
        assert create_page.status_code == 200
        assert create_page.text.count("<h1>") == 1
        assert csrf_match

        rejected = client.post(
            "/admin/capabilities/mappings/new",
            data={
                "csrf_token": "wrong",
                "product_code": "AI-001",
                "capability_code": "chat_qa",
            },
            follow_redirects=False,
        )
        assert rejected.status_code == 403

        created = client.post(
            "/admin/capabilities/mappings/new",
            data={
                "csrf_token": csrf_match.group(1),
                "product_code": "AI-001",
                "capability_code": "chat_qa",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert created.headers["location"].startswith("/admin/capabilities")
