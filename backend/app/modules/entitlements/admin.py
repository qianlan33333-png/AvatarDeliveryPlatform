from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.models import Course, EntitlementEvent, ProductCourseMapping
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.capabilities.models import ProductCapabilityMapping
from backend.app.modules.capabilities.service import (
    CAPABILITY_LABELS,
    replay_benefit_entitlement_event,
)
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
    phone: str = "",
    product: str = "",
    page: int = 1,
    size: int = 20,
):
    course_mappings = list(
        db.execute(
            select(ProductCourseMapping, Course)
            .join(Course, Course.id == ProductCourseMapping.course_id)
            .order_by(ProductCourseMapping.product_code, Course.sort_order)
        ).all()
    )
    capability_mappings = list(db.scalars(select(ProductCapabilityMapping)))
    product_names: dict[str, list[str]] = {}
    for mapping, course in course_mappings:
        product_names.setdefault(mapping.product_code, []).append(course.title)
    for mapping in capability_mappings:
        product_names.setdefault(mapping.product_code, []).append(
            CAPABILITY_LABELS.get(mapping.capability_code, mapping.capability_code)
        )
    events = list(db.scalars(select(EntitlementEvent).order_by(EntitlementEvent.created_at.desc())))
    event_rows: list[dict[str, object]] = []
    phone_query = phone.strip()
    product_query = product.strip().casefold()
    for event in events:
        phone_masked = "未记录"
        plain_phone = ""
        if event.phone_ciphertext:
            try:
                plain_phone = decrypt_phone(event.phone_ciphertext)
                phone_masked = f"{plain_phone[:3]}****{plain_phone[-4:]}"
            except SecurityValueError:
                phone_masked = "解密异常"
        names = product_names.get(event.product_code, [])
        product_name = " / ".join(dict.fromkeys(names)) or f"未知商品（{event.product_code}）"
        if phone_query and phone_query not in plain_phone:
            continue
        if (
            product_query
            and product_query not in product_name.casefold()
            and product_query not in event.product_code.casefold()
        ):
            continue
        event_rows.append(
            {"event": event, "phone_masked": phone_masked, "product_name": product_name}
        )
    size = min(100, max(1, size))
    page = max(1, page)
    total = len(event_rows)
    total_pages = max(1, (total + size - 1) // size)
    page = min(page, total_pages)
    start = (page - 1) * size
    return templates.TemplateResponse(
        request=request,
        name="admin/entitlements.html",
        context=admin_context(
            request,
            admin,
            event_rows=event_rows[start : start + size],
            phone=phone_query,
            product=product.strip(),
            page=page,
            size=size,
            total=total,
            start=start + 1 if total else 0,
            end=min(start + size, total),
            total_pages=total_pages,
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
            "/admin/capabilities?error=invalid-mapping",
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
            "/admin/capabilities?error=mapping-conflict",
            status_code=303,
        )
    return RedirectResponse("/admin/capabilities?notice=course-mapping-saved", status_code=303)


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
    return RedirectResponse("/admin/capabilities?notice=course-mapping-updated", status_code=303)


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
