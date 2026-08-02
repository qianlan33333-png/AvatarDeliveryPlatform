from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.models import Course, EntitlementEvent, ProductCourseMapping
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.capabilities.service import replay_benefit_entitlement_event
from backend.app.modules.entitlements.service import (
    EntitlementApplicationError,
)
from backend.app.security import SecurityValueError, decrypt_phone
from backend.app.web import templates

router = APIRouter(prefix="/admin", tags=["admin-entitlements"])


@router.get("/entitlements", response_class=HTMLResponse, include_in_schema=False)
def entitlements_page(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
    error: str = "",
):
    mappings = list(
        db.execute(
            select(ProductCourseMapping, Course)
            .join(Course, Course.id == ProductCourseMapping.course_id)
            .order_by(ProductCourseMapping.product_code, Course.sort_order)
        ).all()
    )
    courses = list(
        db.scalars(select(Course).where(Course.status != "archived").order_by(Course.sort_order))
    )
    events = list(
        db.scalars(select(EntitlementEvent).order_by(EntitlementEvent.created_at.desc()).limit(100))
    )
    event_rows: list[dict[str, object]] = []
    for event in events:
        phone_last4 = "----"
        if event.phone_ciphertext:
            try:
                phone_last4 = decrypt_phone(event.phone_ciphertext)[-4:]
            except SecurityValueError:
                phone_last4 = "异常"
        event_rows.append({"event": event, "phone_last4": phone_last4})
    return templates.TemplateResponse(
        request=request,
        name="admin/entitlements.html",
        context=admin_context(
            request,
            admin,
            mappings=mappings,
            courses=courses,
            event_rows=event_rows,
            notice=notice,
            error=error,
        ),
    )


@router.post("/entitlements/mappings", include_in_schema=False)
def create_mapping(
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    product_code: str = Form(...),
    course_id: str = Form(...),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    normalized = product_code.strip()
    course = db.get(Course, course_id)
    if not normalized or not course:
        return RedirectResponse(
            "/admin/entitlements?error=invalid-mapping",
            status_code=303,
        )
    existing = db.scalar(
        select(ProductCourseMapping).where(
            ProductCourseMapping.product_code == normalized,
            ProductCourseMapping.course_id == course.id,
        )
    )
    if existing:
        existing.is_active = True
    else:
        db.add(ProductCourseMapping(product_code=normalized, course_id=course.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            "/admin/entitlements?error=mapping-conflict",
            status_code=303,
        )
    return RedirectResponse("/admin/entitlements?notice=mapping-saved", status_code=303)


@router.post("/entitlements/mappings/{mapping_id}/toggle", include_in_schema=False)
def toggle_mapping(
    mapping_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    mapping = db.get(ProductCourseMapping, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="mapping not found")
    mapping.is_active = not mapping.is_active
    db.commit()
    return RedirectResponse("/admin/entitlements?notice=mapping-updated", status_code=303)


@router.post("/entitlements/events/{event_id}/replay", include_in_schema=False)
def replay_event(
    event_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    event = db.get(EntitlementEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    try:
        replay_benefit_entitlement_event(db, event)
    except EntitlementApplicationError:
        return RedirectResponse("/admin/entitlements?error=replay-failed", status_code=303)
    return RedirectResponse("/admin/entitlements?notice=replayed", status_code=303)
