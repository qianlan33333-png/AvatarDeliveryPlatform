from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.db import get_session_factory
from backend.app.main import app
from backend.app.models import (
    Course,
    CourseEntitlement,
    EntitlementEvent,
    ProductCourseMapping,
    User,
)
from backend.app.modules.entitlements.service import build_webhook_signature
from backend.app.modules.identity.wechat import WeChatSession, get_wechat_client


class FakeWeChatClient:
    def __init__(self) -> None:
        self.openid = "openid-user-1"
        self.phone = "13800138000"

    def exchange_login_code(self, code: str) -> WeChatSession:
        return WeChatSession(openid=self.openid, unionid=f"union-{code}")

    def exchange_phone_code(self, code: str) -> str:
        return self.phone


def _seed_product_mapping() -> str:
    session_factory = get_session_factory()
    with session_factory() as db:
        course = Course(
            title="内容表达入门",
            description="用于验证权益开通。",
            keywords=["表达"],
            status="published",
        )
        db.add(course)
        db.flush()
        db.add(ProductCourseMapping(product_code="COURSE-001", course_id=course.id))
        db.commit()
        return course.id


def _webhook_request(
    client: TestClient,
    payload: dict[str, object],
    *,
    nonce: str,
    timestamp: int | None = None,
    secret: str = "test-entitlement-webhook-secret",
):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp_value = str(timestamp if timestamp is not None else int(time.time()))
    signature = build_webhook_signature(secret, timestamp_value, nonce, body)
    return client.post(
        "/api/v1/webhooks/course-entitlements",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Avatar-Timestamp": timestamp_value,
            "X-Avatar-Nonce": nonce,
            "X-Avatar-Signature": signature,
        },
    )


def _grant_payload(event_id: str = "event-grant-1") -> dict[str, object]:
    effective_at = datetime.now(UTC) - timedelta(minutes=1)
    return {
        "event_id": event_id,
        "order_id": "order-100",
        "product_code": "COURSE-001",
        "phone": "13800138000",
        "action": "grant",
        "effective_at": effective_at.isoformat(),
        "expires_at": (effective_at + timedelta(days=365)).isoformat(),
    }


def test_payment_before_registration_is_claimed_after_phone_binding() -> None:
    course_id = _seed_product_mapping()
    fake_wechat = FakeWeChatClient()
    app.dependency_overrides[get_wechat_client] = lambda: fake_wechat
    try:
        with TestClient(app) as client:
            payload = _grant_payload()
            granted = _webhook_request(client, payload, nonce="nonce-grant-1")
            duplicate = _webhook_request(client, payload, nonce="nonce-grant-1")

            assert granted.status_code == 200
            assert granted.json()["entitlements_changed"] == 1
            assert duplicate.status_code == 200
            assert duplicate.json()["status"] == "duplicate"

            login = client.post("/api/v1/auth/wechat/login", json={"code": "login-code"})
            assert login.status_code == 200
            access_token = login.json()["access_token"]
            bound = client.post(
                "/api/v1/auth/phone/bind",
                json={"code": "phone-code"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert bound.status_code == 200
            assert bound.json()["claimed_entitlements"] == 1
            assert bound.json()["user"]["phone_last4"] == "8000"

        session_factory = get_session_factory()
        with session_factory() as db:
            entitlement = db.query(CourseEntitlement).one()
            user = db.get(User, entitlement.user_id)
            assert entitlement.course_id == course_id
            assert user is not None
            assert user.phone_last4 == "8000"
            assert "13800138000" not in (user.phone_ciphertext or "")
    finally:
        app.dependency_overrides.clear()


def test_webhook_rejects_bad_signature_stale_timestamp_nonce_reuse_and_conflict() -> None:
    _seed_product_mapping()
    with TestClient(app) as client:
        payload = _grant_payload()
        bad_signature = _webhook_request(
            client,
            payload,
            nonce="nonce-bad-signature",
            secret="wrong-secret",
        )
        stale = _webhook_request(
            client,
            payload,
            nonce="nonce-stale",
            timestamp=int(time.time()) - 1000,
        )
        first = _webhook_request(client, payload, nonce="nonce-shared")

        reused_payload = _grant_payload("event-grant-2")
        nonce_reuse = _webhook_request(client, reused_payload, nonce="nonce-shared")

        conflict_payload = {**payload, "order_id": "order-different"}
        conflict = _webhook_request(client, conflict_payload, nonce="nonce-conflict")

    assert bad_signature.status_code == 401
    assert stale.status_code == 401
    assert first.status_code == 200
    assert nonce_reuse.status_code == 409
    assert conflict.status_code == 409


def test_refund_revokes_entitlement_and_phone_conflict_is_safe() -> None:
    _seed_product_mapping()
    fake_wechat = FakeWeChatClient()
    app.dependency_overrides[get_wechat_client] = lambda: fake_wechat
    try:
        with TestClient(app) as client:
            grant = _webhook_request(client, _grant_payload(), nonce="nonce-grant")
            assert grant.status_code == 200

            login = client.post("/api/v1/auth/wechat/login", json={"code": "first"})
            first_token = login.json()["access_token"]
            first_bind = client.post(
                "/api/v1/auth/phone/bind",
                json={"code": "phone-first"},
                headers={"Authorization": f"Bearer {first_token}"},
            )
            assert first_bind.status_code == 200

            fake_wechat.openid = "openid-user-2"
            second_login = client.post("/api/v1/auth/wechat/login", json={"code": "second"})
            second_token = second_login.json()["access_token"]
            second_bind = client.post(
                "/api/v1/auth/phone/bind",
                json={"code": "phone-second"},
                headers={"Authorization": f"Bearer {second_token}"},
            )
            assert second_bind.status_code == 409

            revoke_payload = {
                **_grant_payload("event-revoke-1"),
                "action": "revoke",
                "order_id": "refund-100",
            }
            revoked = _webhook_request(client, revoke_payload, nonce="nonce-revoke")
            assert revoked.status_code == 200

        session_factory = get_session_factory()
        with session_factory() as db:
            entitlement = db.query(CourseEntitlement).one()
            assert entitlement.status == "revoked"
            assert db.query(EntitlementEvent).count() == 2
    finally:
        app.dependency_overrides.clear()
