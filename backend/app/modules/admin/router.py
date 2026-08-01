from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models import AdminUser, User
from backend.app.modules.admin.auth import (
    authenticate_admin,
    clear_admin_session,
    establish_admin_session,
    require_admin,
    require_csrf,
)
from backend.app.web import templates

router = APIRouter(prefix="/admin", tags=["admin"])
DBSession = Annotated[Session, Depends(get_db)]


def current_admin(request: Request, db: DBSession) -> AdminUser:
    return require_admin(request, db)


CurrentAdmin = Annotated[AdminUser, Depends(current_admin)]


def admin_context(request: Request, admin: AdminUser, **values):
    return {
        "request": request,
        "admin": admin,
        "csrf_token": request.session.get("csrf_token", ""),
        **values,
    }


@router.get("", include_in_schema=False)
def admin_home(request: Request) -> RedirectResponse:
    target = "/admin/users" if request.session.get("admin_id") else "/admin/login"
    return RedirectResponse(target, status_code=303)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request):
    if request.session.get("admin_id"):
        return RedirectResponse("/admin/users", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="admin/login.html",
        context={"error": "", "next_url": request.query_params.get("next", "/admin/users")},
    )


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login(
    request: Request,
    db: DBSession,
    username: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/admin/users"),
):
    admin = authenticate_admin(db, username.strip(), password)
    if not admin:
        return templates.TemplateResponse(
            request=request,
            name="admin/login.html",
            context={"error": "用户名或密码错误", "next_url": next_url},
            status_code=401,
        )
    establish_admin_session(request, admin)
    safe_next = next_url if next_url.startswith("/admin/") else "/admin/users"
    return RedirectResponse(safe_next, status_code=303)


@router.post("/logout", include_in_schema=False)
def logout(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    require_csrf(request, csrf_token)
    clear_admin_session(request)
    return RedirectResponse("/admin/login", status_code=303)


@router.get("/users", response_class=HTMLResponse, include_in_schema=False)
def users_page(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    keyword: str = "",
    page: int = 1,
):
    page = max(page, 1)
    page_size = 30
    statement = select(User)
    normalized = keyword.strip()
    if normalized:
        like = f"%{normalized}%"
        statement = statement.where(
            or_(
                User.nickname.ilike(like),
                User.id.ilike(like),
                User.phone_last4.ilike(like[-4:]),
            )
        )
    users = list(
        db.scalars(
            statement.order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size + 1)
        )
    )
    has_next = len(users) > page_size
    users = users[:page_size]
    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context=admin_context(
            request,
            admin,
            users=users,
            keyword=normalized,
            page=page,
            has_next=has_next,
            encoded_keyword=quote(normalized),
        ),
    )


@router.post("/users/{user_id}/toggle", include_in_schema=False)
def toggle_user(
    user_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
) -> RedirectResponse:
    require_csrf(request, csrf_token)
    user = db.get(User, user_id)
    if user:
        user.is_active = not user.is_active
        db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/entitlements", response_class=HTMLResponse, include_in_schema=False)
def entitlements_placeholder(
    request: Request,
    admin: CurrentAdmin,
):
    return templates.TemplateResponse(
        request=request,
        name="admin/placeholder.html",
        context=admin_context(
            request,
            admin,
            page_title="开通记录",
            message="权益 webhook 与开通审计将在下一实施批次接入。",
        ),
    )


@router.get("/llm-config", response_class=HTMLResponse, include_in_schema=False)
def llm_placeholder(
    request: Request,
    admin: CurrentAdmin,
):
    return templates.TemplateResponse(
        request=request,
        name="admin/placeholder.html",
        context=admin_context(
            request,
            admin,
            page_title="模型配置",
            message="OpenAI-compatible 模型配置将在问答实施批次接入。",
        ),
    )
