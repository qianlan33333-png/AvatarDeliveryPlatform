from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.models import User
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.capabilities.models import (
    CapabilityEntitlement,
    ProductCapabilityMapping,
)
from backend.app.modules.capabilities.service import (
    CAPABILITY_CODES,
    CAPABILITY_LABELS,
    CapabilityEntitlementError,
    capability_entitlement_status,
    set_manual_capability_entitlement,
    validate_capability_code,
)
from backend.app.web import templates

router = APIRouter(prefix="/admin/capabilities", tags=["admin-capabilities"])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def capabilities_page(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
    error: str = "",
):
    mappings = list(
        db.scalars(
            select(ProductCapabilityMapping).order_by(
                ProductCapabilityMapping.product_code,
                ProductCapabilityMapping.capability_code,
            )
        )
    )
    entitlements = list(
        db.scalars(
            select(CapabilityEntitlement)
            .order_by(CapabilityEntitlement.updated_at.desc())
            .limit(100)
        )
    )
    user_ids = {item.user_id for item in entitlements if item.user_id}
    users = {
        user.id: user
        for user in db.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}
    entitlement_rows = [
        {
            "entitlement": entitlement,
            "user": users.get(entitlement.user_id),
            "display_status": capability_entitlement_status(entitlement),
        }
        for entitlement in entitlements
    ]
    return templates.TemplateResponse(
        request=request,
        name="admin/capabilities.html",
        context=admin_context(
            request,
            admin,
            mappings=mappings,
            entitlement_rows=entitlement_rows,
            capability_labels=CAPABILITY_LABELS,
            notice=notice,
            error=error,
        ),
    )


@router.get("/mappings/new", response_class=HTMLResponse, include_in_schema=False)
def new_mapping_page(request: Request, admin: CurrentAdmin):
    return templates.TemplateResponse(
        request=request,
        name="admin/capability_mapping_new.html",
        context=admin_context(
            request,
            admin,
            capability_codes=CAPABILITY_CODES,
            capability_labels=CAPABILITY_LABELS,
        ),
    )


@router.post("/mappings/new", include_in_schema=False)
def create_mapping(
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    product_code: str = Form(...),
    capability_code: str = Form(...),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    normalized_product = product_code.strip()
    try:
        normalized_capability = validate_capability_code(capability_code)
    except CapabilityEntitlementError:
        return RedirectResponse(
            "/admin/capabilities/mappings/new?error=invalid-capability",
            status_code=303,
        )
    if not normalized_product:
        return RedirectResponse(
            "/admin/capabilities/mappings/new?error=invalid-product",
            status_code=303,
        )
    existing = db.scalar(
        select(ProductCapabilityMapping).where(
            ProductCapabilityMapping.product_code == normalized_product,
            ProductCapabilityMapping.capability_code == normalized_capability,
        )
    )
    if existing:
        existing.is_active = True
    else:
        db.add(
            ProductCapabilityMapping(
                product_code=normalized_product,
                capability_code=normalized_capability,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            "/admin/capabilities/mappings/new?error=mapping-conflict",
            status_code=303,
        )
    return RedirectResponse("/admin/capabilities?notice=mapping-saved", status_code=303)


@router.post("/mappings/{mapping_id}/toggle", include_in_schema=False)
def toggle_mapping(
    mapping_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    mapping = db.get(ProductCapabilityMapping, mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="mapping not found")
    mapping.is_active = not mapping.is_active
    db.commit()
    return RedirectResponse("/admin/capabilities?notice=mapping-updated", status_code=303)


@router.get("/users/{user_id}", response_class=HTMLResponse, include_in_schema=False)
def capability_user_page(
    user_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
    error: str = "",
):
    user = db.get(User, user_id)
    if user is None:
        return RedirectResponse("/admin/users", status_code=303)
    entitlements = {
        item.capability_code: item
        for item in db.scalars(
            select(CapabilityEntitlement).where(CapabilityEntitlement.user_id == user.id)
        )
    }
    entitlement_rows = [
        {
            "code": code,
            "entitlement": entitlements.get(code),
            "display_status": capability_entitlement_status(entitlements.get(code)),
        }
        for code in CAPABILITY_CODES
    ]
    return templates.TemplateResponse(
        request=request,
        name="admin/capability_user_detail.html",
        context=admin_context(
            request,
            admin,
            user=user,
            entitlement_rows=entitlement_rows,
            capability_codes=CAPABILITY_CODES,
            capability_labels=CAPABILITY_LABELS,
            notice=notice,
            error=error,
        ),
    )


@router.post("/users/{user_id}/entitlements", include_in_schema=False)
def set_user_capability(
    user_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    capability_code: str = Form(...),
    action: str = Form(...),
    duration_days: int = Form(0),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    user = db.get(User, user_id)
    if user is None or action not in {"grant", "renew", "revoke"}:
        return RedirectResponse(
            f"/admin/capabilities/users/{user_id}?error=invalid",
            status_code=303,
        )
    if duration_days < 0 or duration_days > 3650:
        return RedirectResponse(
            f"/admin/capabilities/users/{user_id}?error=duration",
            status_code=303,
        )
    now = datetime.now(UTC)
    expires_at = None
    if action != "revoke" and duration_days:
        renewal_base = now
        if action == "renew":
            current = db.scalar(
                select(CapabilityEntitlement).where(
                    CapabilityEntitlement.user_id == user.id,
                    CapabilityEntitlement.capability_code == capability_code,
                )
            )
            if current and current.expires_at:
                current_expiry = current.expires_at
                if current_expiry.tzinfo is None:
                    current_expiry = current_expiry.replace(tzinfo=UTC)
                renewal_base = max(now, current_expiry)
        expires_at = renewal_base + timedelta(days=duration_days)
    try:
        set_manual_capability_entitlement(
            db,
            user=user,
            capability_code=capability_code,
            action=action,  # type: ignore[arg-type]
            expires_at=expires_at,
            effective_at=now,
        )
    except CapabilityEntitlementError:
        return RedirectResponse(
            f"/admin/capabilities/users/{user_id}?error=entitlement",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/capabilities/users/{user_id}?notice=entitlement",
        status_code=303,
    )
