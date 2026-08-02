from __future__ import annotations

import hmac
import os
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Body,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.models import Course
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.knowledge.markdown import SOURCE_TYPES
from backend.app.modules.knowledge.models import KnowledgeSource
from backend.app.modules.knowledge.service import (
    KnowledgeConflictError,
    KnowledgeError,
    KnowledgeNotFoundError,
    archive_knowledge_source,
    create_knowledge_source,
    create_knowledge_source_version,
    export_source_markdown,
    get_knowledge_import,
    import_cleaned_markdown,
    list_ready_sources,
    mark_source_ready_for_agent,
    publish_knowledge_import,
    reject_knowledge_import,
    review_knowledge_unit,
)
from backend.app.web import templates

admin_router = APIRouter(prefix="/admin/knowledge", tags=["admin-knowledge"])
internal_router = APIRouter(prefix="/api/internal/v1/knowledge", tags=["internal-knowledge"])
KNOWLEDGE_UPLOAD = File(...)


SOURCE_TYPE_LABELS = {
    "transcript": "访谈/逐字稿",
    "article": "文章",
    "faq": "问答",
    "notes": "笔记",
    "course_material": "课程资料",
    "interview": "采访",
    "pure_qa": "纯 QA 库",
    "other": "其他",
}
VISIBILITY_LABELS = {"public": "公开", "course": "指定课程", "internal": "内部"}
STATUS_LABELS = {
    "draft": "草稿",
    "approved": "已审核",
    "published": "已发布",
    "rejected": "已驳回",
    "archived": "已归档",
}


def _split_course_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(result))


def _parse_image_lines(value: str) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for line in value.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        url, separator, alt_text = normalized.partition("|")
        images.append(
            {
                "url": url.strip(),
                "alt_text": alt_text.strip() if separator else "",
            }
        )
    return images


def _split_metadata(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace("\n", ",")
    return list(dict.fromkeys(item.strip() for item in normalized.split(",") if item.strip()))


def _source_form_values(form: Any) -> dict[str, Any]:
    return {
        "title": str(form.get("title", "")),
        "source_type": str(form.get("source_type", "transcript")),
        "visibility": str(form.get("visibility", "public")),
        "course_ids": list(form.getlist("course_ids")),
        "raw_content": str(form.get("raw_content", "")),
        "confirmed_facts": str(form.get("confirmed_facts", "")),
        "pending_confirmation_points": str(form.get("pending_confirmation_points", "")),
        "cleaning_requirements": str(form.get("cleaning_requirements", "")),
        "prohibited_content": str(form.get("prohibited_content", "")),
        "source_authorization": str(form.get("source_authorization", "")),
    }


def _render_source_form(
    request: Request,
    admin: Any,
    db: DBSession,
    *,
    form_values: dict[str, Any],
    error: str = "",
    source: KnowledgeSource | None = None,
    status_code: int = 200,
):
    courses = list(db.scalars(select(Course).order_by(Course.sort_order, Course.title)))
    return templates.TemplateResponse(
        request=request,
        name="admin/knowledge_form.html",
        context=admin_context(
            request,
            admin,
            source=source,
            form_values=form_values,
            courses=courses,
            source_types=SOURCE_TYPE_LABELS,
            visibility_labels=VISIBILITY_LABELS,
            error=error,
        ),
        status_code=status_code,
    )


def _redirect_error(path: str, exc: KnowledgeError) -> RedirectResponse:
    return RedirectResponse(f"{path}?error={quote(str(exc))}", status_code=303)


@admin_router.get("", response_class=HTMLResponse, include_in_schema=False)
def knowledge_list_page(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    q: str = "",
    lifecycle: str = "",
    source_type: str = "",
):
    statement = select(KnowledgeSource)
    if q.strip():
        statement = statement.where(KnowledgeSource.title.ilike(f"%{q.strip()}%"))
    if lifecycle in {"draft", "approved", "published", "rejected", "archived"}:
        statement = statement.where(KnowledgeSource.status == lifecycle)
    if source_type in SOURCE_TYPES:
        statement = statement.where(KnowledgeSource.source_type == source_type)
    sources = list(
        db.scalars(
            statement.order_by(KnowledgeSource.updated_at.desc(), KnowledgeSource.id)
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/knowledge_list.html",
        context=admin_context(
            request,
            admin,
            sources=sources,
            q=q,
            lifecycle=lifecycle,
            source_type=source_type,
            source_types=SOURCE_TYPE_LABELS,
            visibility_labels=VISIBILITY_LABELS,
            status_labels=STATUS_LABELS,
        ),
    )


@admin_router.get("/new", response_class=HTMLResponse, include_in_schema=False)
def knowledge_new_page(request: Request, db: DBSession, admin: CurrentAdmin):
    return _render_source_form(
        request,
        admin,
        db,
        form_values={
            "title": "",
            "source_type": "transcript",
            "visibility": "public",
            "course_ids": [],
            "raw_content": "",
            "confirmed_facts": "",
            "pending_confirmation_points": "",
            "cleaning_requirements": "",
            "prohibited_content": "",
            "source_authorization": "",
        },
    )


@admin_router.post("/new", response_class=HTMLResponse, include_in_schema=False)
async def knowledge_create(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
):
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token", "")))
    values = _source_form_values(form)
    try:
        source = create_knowledge_source(
            db,
            **{**values, "course_ids": _split_course_ids(values["course_ids"])},
            created_by=admin.username,
        )
    except KnowledgeError as exc:
        return _render_source_form(
            request,
            admin,
            db,
            form_values=values,
            error=str(exc),
            status_code=422,
        )
    return RedirectResponse(f"/admin/knowledge/{source.id}?notice=created", status_code=303)


@admin_router.get("/import", response_class=HTMLResponse, include_in_schema=False)
def knowledge_import_page(
    request: Request,
    admin: CurrentAdmin,
    error: str = "",
):
    return templates.TemplateResponse(
        request=request,
        name="admin/knowledge_import.html",
        context=admin_context(request, admin, error=error),
    )


@admin_router.post("/import", include_in_schema=False)
async def knowledge_import_upload(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    document: UploadFile = KNOWLEDGE_UPLOAD,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    if not document.filename or not document.filename.lower().endswith((".md", ".markdown")):
        return RedirectResponse("/admin/knowledge/import?error=文件必须是MD", status_code=303)
    payload = await document.read(5 * 1024 * 1024 + 1)
    if len(payload) > 5 * 1024 * 1024:
        return RedirectResponse("/admin/knowledge/import?error=文件不得超过5MB", status_code=303)
    try:
        markdown = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return RedirectResponse("/admin/knowledge/import?error=文件必须是UTF-8", status_code=303)
    try:
        result = import_cleaned_markdown(db, markdown=markdown, imported_by=admin.username)
    except KnowledgeError as exc:
        return _redirect_error("/admin/knowledge/import", exc)
    suffix = "duplicate" if not result.created else "imported"
    return RedirectResponse(
        f"/admin/knowledge/imports/{result.knowledge_import.id}?notice={suffix}",
        status_code=303,
    )


@admin_router.get("/imports/{import_id}", response_class=HTMLResponse, include_in_schema=False)
def knowledge_review_page(
    import_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
    error: str = "",
):
    try:
        knowledge_import = get_knowledge_import(db, import_id)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="admin/knowledge_review.html",
        context=admin_context(
            request,
            admin,
            knowledge_import=knowledge_import,
            source=knowledge_import.source,
            version=knowledge_import.source_version,
            notice=notice,
            error=error,
            status_labels=STATUS_LABELS,
            visibility_labels=VISIBILITY_LABELS,
            courses=list(
                db.scalars(select(Course).order_by(Course.sort_order, Course.title))
            ),
        ),
    )


@admin_router.post("/imports/{import_id}/units/{unit_id}/review", include_in_schema=False)
def knowledge_unit_review(
    import_id: str,
    unit_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    decision: str = Form(...),
    title: str = Form(""),
    content: str = Form(""),
    standard_question: str = Form(""),
    standard_answer: str = Form(""),
    images_text: str = Form(""),
    aliases_text: str | None = Form(None),
    keywords_text: str | None = Form(None),
    channels_text: str | None = Form(None),
    visibility: str | None = Form(None),
    course_ids_text: str | None = Form(None),
    source_evidence: str = Form(...),
    confirmation: str = Form(...),
    review_note: str = Form(""),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    try:
        review_knowledge_unit(
            db,
            unit_id=unit_id,
            decision=decision,
            title=title,
            content=content,
            source_evidence=source_evidence,
            confirmation=confirmation,
            review_note=review_note,
            standard_question=standard_question,
            standard_answer=standard_answer,
            images=_parse_image_lines(images_text),
            aliases=_split_metadata(aliases_text) if aliases_text is not None else None,
            keywords=_split_metadata(keywords_text) if keywords_text is not None else None,
            channels=_split_metadata(channels_text) if channels_text is not None else None,
            visibility=visibility,
            course_ids=(
                _split_metadata(course_ids_text) if course_ids_text is not None else None
            ),
        )
    except KnowledgeError as exc:
        return _redirect_error(f"/admin/knowledge/imports/{import_id}", exc)
    return RedirectResponse(
        f"/admin/knowledge/imports/{import_id}?notice=reviewed", status_code=303
    )


@admin_router.post("/imports/{import_id}/publish", include_in_schema=False)
def knowledge_import_publish(
    import_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    try:
        publish_knowledge_import(db, import_id=import_id)
    except KnowledgeError as exc:
        return _redirect_error(f"/admin/knowledge/imports/{import_id}", exc)
    return RedirectResponse(
        f"/admin/knowledge/imports/{import_id}?notice=published", status_code=303
    )


@admin_router.post("/imports/{import_id}/reject", include_in_schema=False)
def knowledge_import_reject(
    import_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    review_note: str = Form(""),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    try:
        reject_knowledge_import(db, import_id=import_id, review_note=review_note)
    except KnowledgeError as exc:
        return _redirect_error(f"/admin/knowledge/imports/{import_id}", exc)
    return RedirectResponse(
        f"/admin/knowledge/imports/{import_id}?notice=rejected", status_code=303
    )


@admin_router.get("/{source_id}", response_class=HTMLResponse, include_in_schema=False)
def knowledge_detail_page(
    source_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
    error: str = "",
):
    source = db.scalar(
        select(KnowledgeSource)
        .where(KnowledgeSource.id == source_id)
        .options(
            selectinload(KnowledgeSource.versions),
            selectinload(KnowledgeSource.imports),
        )
    )
    if not source:
        raise HTTPException(status_code=404, detail="语料不存在")
    current_version = next(
        (item for item in source.versions if item.version_number == source.current_version_number),
        None,
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/knowledge_detail.html",
        context=admin_context(
            request,
            admin,
            source=source,
            current_version=current_version,
            notice=notice,
            error=error,
            source_types=SOURCE_TYPE_LABELS,
            visibility_labels=VISIBILITY_LABELS,
            status_labels=STATUS_LABELS,
        ),
    )


@admin_router.get("/{source_id}/versions/new", response_class=HTMLResponse, include_in_schema=False)
def knowledge_new_version_page(
    source_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
):
    source = db.get(KnowledgeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="语料不存在")
    try:
        version = next(
            item for item in source.versions if item.version_number == source.current_version_number
        )
    except StopIteration as exc:
        raise HTTPException(status_code=409, detail="当前版本不存在") from exc
    return _render_source_form(
        request,
        admin,
        db,
        source=source,
        form_values={
            "title": version.title,
            "source_type": version.source_type,
            "visibility": version.visibility,
            "course_ids": list(version.course_ids or []),
            "raw_content": version.raw_content,
            "confirmed_facts": version.confirmed_facts,
            "pending_confirmation_points": version.pending_confirmation_points,
            "cleaning_requirements": version.cleaning_requirements,
            "prohibited_content": version.prohibited_content,
            "source_authorization": version.source_authorization,
        },
    )


@admin_router.post("/{source_id}/versions", response_class=HTMLResponse, include_in_schema=False)
async def knowledge_create_version(
    source_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
):
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token", "")))
    values = _source_form_values(form)
    try:
        create_knowledge_source_version(
            db,
            source_id=source_id,
            **{**values, "course_ids": _split_course_ids(values["course_ids"])},
            created_by=admin.username,
        )
    except KnowledgeError as exc:
        source = db.get(KnowledgeSource, source_id)
        return _render_source_form(
            request,
            admin,
            db,
            source=source,
            form_values=values,
            error=str(exc),
            status_code=422,
        )
    return RedirectResponse(f"/admin/knowledge/{source_id}?notice=versioned", status_code=303)


@admin_router.post("/{source_id}/ready", include_in_schema=False)
def knowledge_ready(
    source_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    try:
        mark_source_ready_for_agent(db, source_id)
    except KnowledgeError as exc:
        return _redirect_error(f"/admin/knowledge/{source_id}", exc)
    return RedirectResponse(f"/admin/knowledge/{source_id}?notice=ready", status_code=303)


@admin_router.get("/{source_id}/export.md", include_in_schema=False)
def knowledge_export(source_id: str, db: DBSession, _: CurrentAdmin):
    try:
        markdown = export_source_markdown(db, source_id)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="knowledge-{source_id}.md"'},
    )


@admin_router.post("/{source_id}/archive", include_in_schema=False)
def knowledge_archive(
    source_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    try:
        archive_knowledge_source(db, source_id=source_id)
    except KnowledgeError as exc:
        return _redirect_error(f"/admin/knowledge/{source_id}", exc)
    return RedirectResponse("/admin/knowledge", status_code=303)


def require_knowledge_internal_token(
    authorization: Annotated[str, Header()] = "",
    x_avatar_internal_token: Annotated[str, Header()] = "",
) -> None:
    configured = os.getenv("KNOWLEDGE_INTERNAL_TOKEN", "").strip()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="knowledge internal token is not configured",
        )
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    provided = x_avatar_internal_token.strip() or bearer
    if not provided or not hmac.compare_digest(configured, provided):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


class InternalKnowledgeImportRequest(BaseModel):
    markdown: str = Field(min_length=1, max_length=5 * 1024 * 1024)


def _internal_error(exc: KnowledgeError) -> HTTPException:
    if isinstance(exc, KnowledgeNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, KnowledgeConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@internal_router.get("/sources")
def internal_knowledge_sources(
    db: DBSession,
    status_filter: str = Query("ready_for_agent", alias="status"),
    authorization: Annotated[str, Header()] = "",
    x_avatar_internal_token: Annotated[str, Header()] = "",
):
    require_knowledge_internal_token(authorization, x_avatar_internal_token)
    if status_filter != "ready_for_agent":
        raise HTTPException(status_code=422, detail="only ready_for_agent is supported")
    sources = list_ready_sources(db)
    return {
        "items": [
            {
                "id": source.id,
                "title": source.title,
                "source_type": source.source_type,
                "visibility": source.visibility,
                "course_ids": source.course_ids,
                "version": source.current_version_number,
                "updated_at": source.updated_at,
                "export_url": f"/api/internal/v1/knowledge/sources/{source.id}/export.md",
            }
            for source in sources
        ]
    }


@internal_router.get("/sources/{source_id}/export.md")
def internal_knowledge_export(
    source_id: str,
    db: DBSession,
    authorization: Annotated[str, Header()] = "",
    x_avatar_internal_token: Annotated[str, Header()] = "",
):
    require_knowledge_internal_token(authorization, x_avatar_internal_token)
    source = db.get(KnowledgeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="语料不存在")
    if source.processing_status != "ready_for_agent" or source.status == "archived":
        raise HTTPException(status_code=409, detail="语料尚未标记为待 Agent 清洗")
    try:
        markdown = export_source_markdown(db, source_id)
    except KnowledgeError as exc:
        raise _internal_error(exc) from exc
    return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")


@internal_router.post("/imports")
def internal_knowledge_import(
    payload: Annotated[InternalKnowledgeImportRequest, Body()],
    db: DBSession,
    authorization: Annotated[str, Header()] = "",
    x_avatar_internal_token: Annotated[str, Header()] = "",
):
    require_knowledge_internal_token(authorization, x_avatar_internal_token)
    try:
        result = import_cleaned_markdown(
            db,
            markdown=payload.markdown,
            imported_by="agent",
            require_ready_for_agent=True,
        )
    except KnowledgeError as exc:
        raise _internal_error(exc) from exc
    return {
        "id": result.knowledge_import.id,
        "source_id": result.knowledge_import.source_id,
        "source_version": result.knowledge_import.source_version_number,
        "processor": result.knowledge_import.processor,
        "processor_version": result.knowledge_import.processor_version,
        "status": result.knowledge_import.status,
        "duplicate": not result.created,
        "unit_count": len(result.knowledge_import.units),
    }


@internal_router.get("/imports/{import_id}")
def internal_knowledge_import_status(
    import_id: str,
    db: DBSession,
    authorization: Annotated[str, Header()] = "",
    x_avatar_internal_token: Annotated[str, Header()] = "",
):
    require_knowledge_internal_token(authorization, x_avatar_internal_token)
    try:
        knowledge_import = get_knowledge_import(db, import_id)
    except KnowledgeError as exc:
        raise _internal_error(exc) from exc
    return {
        "id": knowledge_import.id,
        "source_id": knowledge_import.source_id,
        "source_version": knowledge_import.source_version_number,
        "processor": knowledge_import.processor,
        "processor_version": knowledge_import.processor_version,
        "status": knowledge_import.status,
        "error_message": knowledge_import.error_message,
        "units": [
            {
                "id": unit.id,
                "local_id": unit.local_id,
                "type": unit.unit_type,
                "confirmation": unit.confirmation,
                "status": unit.status,
                "review_note": unit.review_note,
                "question": unit.standard_question,
                "answer": unit.standard_answer,
                "images": [
                    {
                        "url": asset.public_url,
                        "alt_text": asset.alt_text,
                        "sort_order": asset.sort_order,
                        "status": asset.status,
                    }
                    for asset in unit.assets
                ],
            }
            for unit in knowledge_import.units
        ],
    }


__all__ = ["admin_router", "internal_router", "require_knowledge_internal_token"]
