from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.models import AIModelBinding, LLMConfig
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.ai_models.service import (
    MODEL_SCENES,
    PROVIDER_PRESETS,
    SCENE_LABELS,
    embedding_model_version,
    embedding_model_version_from_fields,
    test_model_configuration,
    validate_model_configuration,
)
from backend.app.modules.knowledge.service import (
    has_published_knowledge,
    queue_published_knowledge_for_reindex,
)
from backend.app.security import encrypt_value
from backend.app.web import templates

router = APIRouter(prefix="/admin", tags=["admin-llm-config"])


def _generation_candidate_name(
    db: DBSession,
    *,
    desired_name: str,
    target_version: str,
) -> str:
    if not db.scalar(select(LLMConfig.id).where(LLMConfig.name == desired_name)):
        return desired_name
    base = f"{desired_name} · 重建 {target_version[-8:]}"
    candidate = base
    suffix = 2
    while db.scalar(select(LLMConfig.id).where(LLMConfig.name == candidate)):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _model_form_context(
    request: Request,
    admin: CurrentAdmin,
    *,
    config: LLMConfig | None = None,
    error: str = "",
) -> dict[str, object]:
    return admin_context(
        request,
        admin,
        config=config,
        error=error,
        providers=PROVIDER_PRESETS,
    )


@router.get("/llm-config", response_class=HTMLResponse, include_in_schema=False)
def llm_config_page(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
    error: str = "",
):
    configs = list(db.scalars(select(LLMConfig).order_by(LLMConfig.created_at)))
    bindings = {
        binding.scene: binding
        for binding in db.scalars(select(AIModelBinding).order_by(AIModelBinding.scene))
    }
    chat_configs = [config for config in configs if config.capability == "chat"]
    embedding_configs = [config for config in configs if config.capability == "embedding"]
    return templates.TemplateResponse(
        request=request,
        name="admin/llm_config.html",
        context=admin_context(
            request,
            admin,
            configs=configs,
            bindings=bindings,
            scenes=MODEL_SCENES,
            scene_labels=SCENE_LABELS,
            chat_configs=chat_configs,
            embedding_configs=embedding_configs,
            notice=notice,
            error=error,
        ),
    )


@router.get("/llm-config/new", response_class=HTMLResponse, include_in_schema=False)
def new_llm_config_page(request: Request, admin: CurrentAdmin):
    return templates.TemplateResponse(
        request=request,
        name="admin/llm_config_form.html",
        context=_model_form_context(request, admin),
    )


@router.get("/llm-config/{config_id}/edit", response_class=HTMLResponse, include_in_schema=False)
def edit_llm_config_page(
    config_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
):
    config = db.get(LLMConfig, config_id)
    if not config:
        return RedirectResponse("/admin/llm-config?error=missing", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="admin/llm_config_form.html",
        context=_model_form_context(request, admin, config=config),
    )


@router.post("/llm-config", include_in_schema=False)
def save_llm_config(
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    config_id: str = Form(""),
    name: str = Form(...),
    provider: str = Form("custom"),
    capability: str = Form("chat"),
    base_url: str = Form(...),
    model_name: str = Form(...),
    api_key: str = Form(""),
    system_prompt: str = Form(""),
    temperature: float = Form(0.5),
    embedding_dimension: int | None = Form(None),
    is_active: bool = Form(False),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    normalized_name = name.strip()
    normalized_base_url = base_url.strip().rstrip("/")
    normalized_model = model_name.strip()
    normalized_dimension = embedding_dimension if capability == "embedding" else None
    try:
        validate_model_configuration(
            provider=provider,
            capability=capability,
            base_url=normalized_base_url,
            model_name=normalized_model,
            embedding_dimension=normalized_dimension,
        )
    except ValueError:
        return RedirectResponse("/admin/llm-config?error=invalid", status_code=303)
    if not normalized_name or temperature < 0 or temperature > 2:
        return RedirectResponse("/admin/llm-config?error=invalid", status_code=303)

    config = db.get(LLMConfig, config_id) if config_id else None
    conflicting = db.scalar(select(LLMConfig).where(LLMConfig.name == normalized_name))
    if conflicting and (not config or conflicting.id != config.id):
        return RedirectResponse("/admin/llm-config?error=name-conflict", status_code=303)
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
    next_api_key_ciphertext = config.api_key_ciphertext
    if api_key.strip():
        next_api_key_ciphertext = encrypt_value(
            api_key.strip(),
            purpose="llm-api-key",
            key_material=settings.llm_encryption_key,
            settings=settings,
        )
    referenced_binding = (
        db.scalar(
            select(AIModelBinding).where(
                (AIModelBinding.primary_model_id == config.id)
                | (AIModelBinding.fallback_model_id == config.id)
                | (AIModelBinding.pending_primary_model_id == config.id)
            )
        )
        if config.id
        else None
    )
    if referenced_binding and config.capability != capability:
        return RedirectResponse("/admin/llm-config?error=bound-capability", status_code=303)

    old_embedding_version = (
        embedding_model_version(config) if config.id and config.capability == "embedding" else None
    )
    new_embedding_version = (
        embedding_model_version_from_fields(
            provider=provider,
            base_url=normalized_base_url,
            model_name=normalized_model,
            embedding_dimension=normalized_dimension,
        )
        if capability == "embedding"
        else None
    )
    embedding_binding = db.scalar(
        select(AIModelBinding).where(AIModelBinding.scene == "embedding")
    )
    needs_copy_on_write = bool(
        config.id
        and embedding_binding
        and embedding_binding.primary_model_id == config.id
        and old_embedding_version != new_embedding_version
        and has_published_knowledge(db)
    )
    if needs_copy_on_write:
        if not is_active:
            return RedirectResponse("/admin/llm-config?error=inactive-rebuild", status_code=303)
        candidate = LLMConfig(
            name=_generation_candidate_name(
                db,
                desired_name=normalized_name,
                target_version=new_embedding_version or "embedding-v1",
            ),
            provider=provider,
            capability="embedding",
            base_url=normalized_base_url,
            model_name=normalized_model,
            api_key_ciphertext=next_api_key_ciphertext,
            system_prompt=system_prompt.strip(),
            temperature_milli=int(round(temperature * 1000)),
            embedding_dimension=normalized_dimension,
            is_active=True,
        )
        db.add(candidate)
        db.flush()
        embedding_binding.pending_primary_model_id = candidate.id
        queue_published_knowledge_for_reindex(
            db,
            model_config_id=candidate.id,
            commit=False,
        )
        db.commit()
        return RedirectResponse("/admin/llm-config?notice=embedding-rebuilding", status_code=303)

    config.api_key_ciphertext = next_api_key_ciphertext
    config.name = normalized_name
    config.provider = provider
    config.capability = capability
    config.base_url = normalized_base_url
    config.model_name = normalized_model
    config.system_prompt = system_prompt.strip()
    config.temperature_milli = int(round(temperature * 1000))
    config.embedding_dimension = normalized_dimension
    config.is_active = is_active
    if (
        config.id
        and embedding_binding
        and embedding_binding.pending_primary_model_id == config.id
        and old_embedding_version != new_embedding_version
    ):
        queue_published_knowledge_for_reindex(
            db,
            model_config_id=config.id,
            commit=False,
        )
    db.commit()
    return RedirectResponse("/admin/llm-config?notice=saved", status_code=303)


@router.post("/llm-config/bindings", include_in_schema=False)
def save_model_bindings(
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    qa_primary: str = Form(""),
    qa_fallback: str = Form(""),
    copywriting_primary: str = Form(""),
    copywriting_fallback: str = Form(""),
    internal_classifier_primary: str = Form(""),
    internal_classifier_fallback: str = Form(""),
    embedding_primary: str = Form(""),
    embedding_fallback: str = Form(""),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    posted = {
        "qa": (qa_primary, qa_fallback),
        "copywriting": (copywriting_primary, copywriting_fallback),
        "internal_classifier": (internal_classifier_primary, internal_classifier_fallback),
        "embedding": (embedding_primary, embedding_fallback),
    }
    pending_embedding_model_id: str | None = None
    published_knowledge_exists = has_published_knowledge(db)
    for scene, (primary_id, fallback_id) in posted.items():
        binding = db.scalar(select(AIModelBinding).where(AIModelBinding.scene == scene))
        binding_was_missing = binding is None
        if not primary_id:
            if binding:
                db.delete(binding)
            continue
        expected_capability = "embedding" if scene == "embedding" else "chat"
        primary = db.get(LLMConfig, primary_id)
        fallback = db.get(LLMConfig, fallback_id) if fallback_id else None
        if (
            not primary
            or primary.capability != expected_capability
            or (fallback and fallback.capability != expected_capability)
            or (fallback and fallback.id == primary.id)
        ):
            db.rollback()
            return RedirectResponse("/admin/llm-config?error=invalid-binding", status_code=303)
        if not binding:
            binding = AIModelBinding(scene=scene, primary_model_id=primary.id)
            db.add(binding)
        if (
            scene == "embedding"
            and binding.primary_model_id != primary.id
            and published_knowledge_exists
        ):
            binding.pending_primary_model_id = primary.id
            pending_embedding_model_id = primary.id
        else:
            binding.primary_model_id = primary.id
            if scene == "embedding":
                binding.pending_primary_model_id = None
                if binding_was_missing and published_knowledge_exists:
                    pending_embedding_model_id = primary.id
        binding.fallback_model_id = fallback.id if fallback else None
    db.flush()
    if pending_embedding_model_id:
        queue_published_knowledge_for_reindex(
            db,
            model_config_id=pending_embedding_model_id,
            commit=False,
        )
    db.commit()
    return RedirectResponse("/admin/llm-config?notice=bindings-saved", status_code=303)


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
    config.is_active = not config.is_active
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
        response = test_model_configuration(config)
    except Exception:
        return RedirectResponse("/admin/llm-config?error=test-failed", status_code=303)
    outcome = "test-ok" if response else "test-empty"
    return RedirectResponse(f"/admin/llm-config?notice={outcome}", status_code=303)
