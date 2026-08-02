from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.config import get_settings
from backend.app.modules.api.dependencies import CurrentUser, DBSession
from backend.app.modules.identity.service import (
    PhoneBindingConflict,
    bind_user_phone,
    login_wechat_user,
)
from backend.app.modules.identity.wechat import WeChatAPIError, WeChatClient, get_wechat_client
from backend.app.security import SecurityValueError, issue_user_token

router = APIRouter(prefix="/api/v1/auth", tags=["mini-program-auth"])
WeChatClientDependency = Annotated[WeChatClient, Depends(get_wechat_client)]


class WeChatLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class PhoneBindRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)


def _user_payload(user_id: str, phone_last4: str) -> dict[str, object]:
    return {
        "id": user_id,
        "phone_bound": bool(phone_last4),
        "phone_last4": phone_last4,
    }


@router.post("/wechat/login")
def wechat_login(
    payload: WeChatLoginRequest,
    db: DBSession,
    client: WeChatClientDependency,
):
    settings = get_settings()
    try:
        session = client.exchange_login_code(payload.code)
    except WeChatAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    user = login_wechat_user(
        db,
        appid=settings.wechat_app_id,
        openid=session.openid,
        unionid=session.unionid,
    )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user disabled")
    return {
        "access_token": issue_user_token(user.id),
        "token_type": "bearer",
        "user": _user_payload(user.id, user.phone_last4),
    }


@router.post("/phone/bind")
def phone_bind(
    payload: PhoneBindRequest,
    db: DBSession,
    user: CurrentUser,
    client: WeChatClientDependency,
):
    try:
        phone = client.exchange_phone_code(payload.code)
        bound_user, claimed_count, claimed_capabilities = bind_user_phone(db, user, phone)
    except WeChatAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (PhoneBindingConflict, SecurityValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "access_token": issue_user_token(bound_user.id),
        "token_type": "bearer",
        "claimed_entitlements": claimed_count,
        "claimed_capabilities": claimed_capabilities,
        "user": _user_payload(bound_user.id, bound_user.phone_last4),
    }
