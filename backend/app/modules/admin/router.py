from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select

from backend.app.models import Course, CourseEntitlement, User
from backend.app.modules.admin.auth import (
    authenticate_admin,
    clear_admin_session,
    establish_admin_session,
    require_csrf,
)
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.capabilities.models import CapabilityEntitlement
from backend.app.modules.capabilities.service import (
    CAPABILITY_CODES,
    CAPABILITY_LABELS,
    capability_entitlement_status,
)
from backend.app.modules.entitlements.service import (
    EntitlementApplicationError,
    set_manual_course_entitlement,
)
from backend.app.web import templates

router = APIRouter(prefix="/admin", tags=["admin"])


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
    page_size = 20
    statement = select(User)
    count_statement = select(func.count(User.id))
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
        count_statement = count_statement.where(
            or_(
                User.nickname.ilike(like),
                User.id.ilike(like),
                User.phone_last4.ilike(like[-4:]),
            )
        )
    total = int(db.scalar(count_statement) or 0)
    users = list(
        db.scalars(
            statement.order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    users_total = int(db.scalar(select(func.count(User.id))) or 0)
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    users_today = int(
        db.scalar(select(func.count(User.id)).where(User.created_at >= today_start)) or 0
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context=admin_context(
            request,
            admin,
            users=users,
            keyword=normalized,
            page=page,
            total=total,
            total_pages=total_pages,
            page_size=page_size,
            users_total=users_total,
            users_today=users_today,
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


@router.get("/users/{user_id}", response_class=HTMLResponse, include_in_schema=False)
def user_detail_page(
    user_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
    error: str = "",
    tab: str = "course",
):
    user = db.get(User, user_id)
    if not user:
        return RedirectResponse("/admin/users", status_code=303)
    entitlement_rows = list(
        db.execute(
            select(CourseEntitlement, Course)
            .join(Course, Course.id == CourseEntitlement.course_id)
            .where(CourseEntitlement.user_id == user.id)
            .order_by(Course.sort_order, Course.created_at)
        ).all()
    )
    capability_by_code = {
        item.capability_code: item
        for item in db.scalars(
            select(CapabilityEntitlement).where(CapabilityEntitlement.user_id == user.id)
        )
    }
    capability_rows = [
        {
            "code": code,
            "entitlement": capability_by_code.get(code),
            "display_status": capability_entitlement_status(capability_by_code.get(code)),
        }
        for code in CAPABILITY_CODES
    ]
    return templates.TemplateResponse(
        request=request,
        name="admin/user_detail.html",
        context=admin_context(
            request,
            admin,
            user=user,
            entitlement_rows=entitlement_rows,
            tab="ai" if tab == "ai" else "course",
            capability_rows=capability_rows,
            capability_labels=CAPABILITY_LABELS,
            notice=notice,
            error=error,
        ),
    )


@router.post("/users/{user_id}/entitlements", include_in_schema=False)
def manual_user_entitlement(
    user_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    course_id: str = Form(...),
    action: str = Form(...),
    duration_days: int = Form(0),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    user = db.get(User, user_id)
    course = db.get(Course, course_id)
    if not user or not course or action not in {"grant", "revoke"}:
        return RedirectResponse(f"/admin/users/{user_id}?error=invalid", status_code=303)
    if duration_days < 0 or duration_days > 3650:
        return RedirectResponse(f"/admin/users/{user_id}?error=duration", status_code=303)
    expires_at = None
    if action == "grant" and duration_days:
        expires_at = datetime.now(UTC) + timedelta(days=duration_days)
    try:
        set_manual_course_entitlement(
            db,
            user=user,
            course_id=course.id,
            action=action,  # type: ignore[arg-type]
            expires_at=expires_at,
        )
    except EntitlementApplicationError:
        return RedirectResponse(f"/admin/users/{user_id}?error=phone", status_code=303)
    return RedirectResponse(f"/admin/users/{user_id}?notice=entitlement", status_code=303)
