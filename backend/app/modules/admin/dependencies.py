from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models import AdminUser
from backend.app.modules.admin.auth import require_admin

DBSession = Annotated[Session, Depends(get_db)]


def current_admin(request: Request, db: DBSession) -> AdminUser:
    return require_admin(request, db)


CurrentAdmin = Annotated[AdminUser, Depends(current_admin)]


def admin_context(request: Request, admin: AdminUser, **values: Any) -> dict[str, Any]:
    return {
        "request": request,
        "admin": admin,
        "csrf_token": request.session.get("csrf_token", ""),
        **values,
    }
