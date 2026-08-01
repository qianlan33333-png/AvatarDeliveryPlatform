import secrets
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.db import get_session_factory
from backend.app.models import AdminUser

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def bootstrap_admin() -> None:
    settings = get_settings()
    if not settings.admin_bootstrap_password:
        return
    session_factory = get_session_factory()
    with session_factory() as db:
        existing = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
        if existing:
            return
        db.add(
            AdminUser(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_bootstrap_password),
            )
        )
        db.commit()


def authenticate_admin(db: Session, username: str, password: str) -> AdminUser | None:
    admin = db.scalar(
        select(AdminUser).where(
            AdminUser.username == username,
            AdminUser.is_active.is_(True),
        )
    )
    if not admin or not verify_password(admin.password_hash, password):
        return None
    admin.last_login_at = datetime.now(UTC)
    db.commit()
    return admin


def establish_admin_session(request: Request, admin: AdminUser) -> None:
    request.session.clear()
    request.session["admin_id"] = admin.id
    request.session["csrf_token"] = secrets.token_urlsafe(32)


def clear_admin_session(request: Request) -> None:
    request.session.clear()


def require_admin(request: Request, db: Session) -> AdminUser:
    admin_id = request.session.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="admin login required")
    admin = db.get(AdminUser, admin_id)
    if not admin or not admin.is_active:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="admin login required")
    return admin


def require_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid csrf token")
