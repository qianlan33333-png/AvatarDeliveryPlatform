from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.models import LLMConfig
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.chat.service import test_llm_configuration
from backend.app.security import encrypt_value
from backend.app.web import templates

router = APIRouter(prefix="/admin", tags=["admin-llm-config"])


@router.get("/llm-config", response_class=HTMLResponse, include_in_schema=False)
def llm_config_page(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
    error: str = "",
):
    configs = list(db.scalars(select(LLMConfig).order_by(LLMConfig.created_at)))
    return templates.TemplateResponse(
        request=request,
        name="admin/llm_config.html",
        context=admin_context(
            request,
            admin,
            configs=configs,
            notice=notice,
            error=error,
        ),
    )


@router.post("/llm-config", include_in_schema=False)
def save_llm_config(
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    name: str = Form(...),
    base_url: str = Form(...),
    model_name: str = Form(...),
    api_key: str = Form(""),
    system_prompt: str = Form(""),
    temperature: float = Form(0.5),
    is_active: bool = Form(False),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    normalized_name = name.strip()
    normalized_base_url = base_url.strip().rstrip("/")
    normalized_model = model_name.strip()
    if (
        not normalized_name
        or not normalized_model
        or not normalized_base_url.startswith(("https://", "http://"))
        or temperature < 0
        or temperature > 2
    ):
        return RedirectResponse("/admin/llm-config?error=invalid", status_code=303)
    config = db.scalar(select(LLMConfig).where(LLMConfig.name == normalized_name))
    if not config:
        if not api_key.strip():
            return RedirectResponse("/admin/llm-config?error=api-key", status_code=303)
        config = LLMConfig(
            name=normalized_name,
            base_url=normalized_base_url,
            model_name=normalized_model,
            api_key_ciphertext="",
        )
        db.add(config)
    settings = get_settings()
    if api_key.strip():
        config.api_key_ciphertext = encrypt_value(
            api_key.strip(),
            purpose="llm-api-key",
            key_material=settings.llm_encryption_key,
            settings=settings,
        )
    config.base_url = normalized_base_url
    config.model_name = normalized_model
    config.system_prompt = system_prompt.strip()
    config.temperature_milli = int(round(temperature * 1000))
    db.flush()
    if is_active:
        for other in db.scalars(select(LLMConfig).where(LLMConfig.id != config.id)):
            other.is_active = False
    config.is_active = is_active
    db.commit()
    return RedirectResponse("/admin/llm-config?notice=saved", status_code=303)


@router.post("/llm-config/{config_id}/toggle", include_in_schema=False)
def toggle_llm_config(
    config_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    config = db.get(LLMConfig, config_id)
    if not config:
        return RedirectResponse("/admin/llm-config?error=missing", status_code=303)
    next_state = not config.is_active
    if next_state:
        for other in db.scalars(select(LLMConfig).where(LLMConfig.id != config.id)):
            other.is_active = False
    config.is_active = next_state
    db.commit()
    return RedirectResponse("/admin/llm-config?notice=updated", status_code=303)


@router.post("/llm-config/{config_id}/test", include_in_schema=False)
def test_llm_config(
    config_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    config = db.get(LLMConfig, config_id)
    if not config:
        return RedirectResponse("/admin/llm-config?error=missing", status_code=303)
    try:
        response = test_llm_configuration(config)
    except Exception:
        return RedirectResponse("/admin/llm-config?error=test-failed", status_code=303)
    outcome = "test-ok" if response else "test-empty"
    return RedirectResponse(f"/admin/llm-config?notice={outcome}", status_code=303)
