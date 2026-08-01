from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.models import CourseEntitlement, User, WeChatIdentity
from backend.app.security import encrypt_phone, lookup_phone_hash, normalize_phone


class PhoneBindingConflict(ValueError):
    pass


def login_wechat_user(
    db: Session,
    *,
    appid: str,
    openid: str,
    unionid: str | None,
) -> User:
    identity = db.scalar(
        select(WeChatIdentity).where(
            WeChatIdentity.appid == appid,
            WeChatIdentity.openid == openid,
        )
    )
    if identity:
        user = identity.user
        if unionid and identity.unionid != unionid:
            identity.unionid = unionid
    else:
        user = User()
        db.add(user)
        db.flush()
        db.add(
            WeChatIdentity(
                user_id=user.id,
                appid=appid,
                openid=openid,
                unionid=unionid,
            )
        )
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return user


def bind_user_phone(
    db: Session,
    user: User,
    raw_phone: str,
    settings: Settings | None = None,
) -> tuple[User, int]:
    configured = settings or get_settings()
    phone = normalize_phone(raw_phone)
    phone_hash = lookup_phone_hash(phone, configured)
    owner = db.scalar(select(User).where(User.phone_hash == phone_hash))
    if owner and owner.id != user.id:
        raise PhoneBindingConflict("该手机号已经绑定其他账号，请联系运营处理")
    if user.phone_hash and user.phone_hash != phone_hash:
        raise PhoneBindingConflict("当前账号已经绑定其他手机号")

    user.phone_ciphertext = encrypt_phone(phone, configured)
    user.phone_hash = phone_hash
    user.phone_last4 = phone[-4:]
    pending = list(
        db.scalars(
            select(CourseEntitlement).where(
                CourseEntitlement.phone_hash == phone_hash,
                CourseEntitlement.user_id.is_(None),
            )
        )
    )
    for entitlement in pending:
        entitlement.user_id = user.id
    db.commit()
    return user, len(pending)
