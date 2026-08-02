from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.alerts import send_feishu_alert
from backend.app.config import get_settings
from backend.app.models import EntitlementEvent, WebhookNonce
from backend.app.modules.api.dependencies import DBSession
from backend.app.modules.capabilities.service import apply_benefit_entitlement_command
from backend.app.modules.entitlements.service import (
    EntitlementApplicationError,
    EntitlementCommand,
    canonical_command_hash,
    webhook_signature_matches,
)

router = APIRouter(prefix="/api/v1/webhooks", tags=["course-entitlements"])


class EntitlementWebhookPayload(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    order_id: str = Field(min_length=1, max_length=160)
    product_code: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=6, max_length=32)
    action: Literal["grant", "renew", "revoke"]
    effective_at: datetime
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_times(self):
        if self.effective_at.tzinfo is None:
            raise ValueError("effective_at must include timezone")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None:
                raise ValueError("expires_at must include timezone")
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must be later than effective_at")
        return self

    def to_command(self) -> EntitlementCommand:
        return EntitlementCommand(**self.model_dump())


def _validate_signature_headers(
    body: bytes,
    timestamp_value: str,
    nonce: str,
    signature: str,
) -> None:
    settings = get_settings()
    if not settings.entitlement_webhook_secret:
        raise HTTPException(status_code=503, detail="entitlement webhook secret not configured")
    try:
        timestamp = int(timestamp_value)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid webhook timestamp") from exc
    if abs(int(time.time()) - timestamp) > settings.webhook_max_clock_skew_seconds:
        raise HTTPException(status_code=401, detail="webhook timestamp expired")
    if not nonce or len(nonce) > 160:
        raise HTTPException(status_code=401, detail="invalid webhook nonce")
    if not webhook_signature_matches(
        settings.entitlement_webhook_secret,
        timestamp_value,
        nonce,
        body,
        signature,
    ):
        raise HTTPException(status_code=401, detail="invalid webhook signature")


@router.post("/course-entitlements")
async def course_entitlement_webhook(
    request: Request,
    db: DBSession,
    x_avatar_timestamp: str = Header("", alias="X-Avatar-Timestamp"),
    x_avatar_nonce: str = Header("", alias="X-Avatar-Nonce"),
    x_avatar_signature: str = Header("", alias="X-Avatar-Signature"),
):
    body = await request.body()
    _validate_signature_headers(
        body,
        x_avatar_timestamp,
        x_avatar_nonce,
        x_avatar_signature,
    )
    try:
        payload = EntitlementWebhookPayload.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    command = payload.to_command()
    payload_hash = canonical_command_hash(command)
    existing = db.scalar(
        select(EntitlementEvent).where(EntitlementEvent.event_id == command.event_id)
    )
    if existing:
        if existing.payload_hash != payload_hash:
            send_feishu_alert(
                "课程权益事件冲突",
                f"event_id={command.event_id} 收到相同 ID 不同内容",
            )
            raise HTTPException(status_code=409, detail="event id payload conflict")
        return {"ok": True, "status": "duplicate", "event_id": command.event_id}

    nonce_record = db.scalar(
        select(WebhookNonce).where(
            WebhookNonce.provider == "course_entitlement",
            WebhookNonce.nonce == x_avatar_nonce,
        )
    )
    if nonce_record:
        send_feishu_alert(
            "课程权益 nonce 重放",
            f"nonce={x_avatar_nonce} 原事件={nonce_record.event_id} 新事件={command.event_id}",
        )
        raise HTTPException(status_code=409, detail="webhook nonce already used")
    db.add(
        WebhookNonce(
            provider="course_entitlement",
            nonce=x_avatar_nonce,
            event_id=command.event_id,
        )
    )
    try:
        result = apply_benefit_entitlement_command(
            db,
            command,
            payload_hash=payload_hash,
        )
    except EntitlementApplicationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raced = db.scalar(
            select(EntitlementEvent).where(EntitlementEvent.event_id == command.event_id)
        )
        if raced and raced.payload_hash == payload_hash:
            return {"ok": True, "status": "duplicate", "event_id": command.event_id}
        if raced:
            send_feishu_alert(
                "课程权益事件冲突",
                f"event_id={command.event_id} 并发收到相同 ID 不同内容",
            )
            raise HTTPException(status_code=409, detail="event id payload conflict") from exc
        raise HTTPException(status_code=409, detail="webhook event conflict") from exc
    return {
        "ok": True,
        "status": result.event.result,
        "event_id": result.event.event_id,
        "entitlements_changed": result.course_entitlements_changed,
        "capability_entitlements_changed": result.capability_entitlements_changed,
    }
