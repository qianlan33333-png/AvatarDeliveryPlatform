from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.knowledge.models import KnowledgeSlice, QAEntry
from backend.app.modules.knowledge.service import KnowledgeError
from backend.app.modules.knowledge.v2_markdown import (
    CONTENT_TYPE_LABELS,
    DIMENSION_LABELS,
    DOMAIN_LABELS,
)
from backend.app.modules.knowledge.v2_service import create_qa_entry, set_qa_status
from backend.app.web import templates

router = APIRouter(tags=["admin-knowledge-v2"])


def _split(value: str) -> list[str]:
    return list(
        dict.fromkeys(
            item.strip()
            for item in value.replace("，", ",").replace("\n", ",").split(",")
            if item.strip()
        )
    )


def _images(value: str) -> list[dict[str, str]]:
    result = []
    for line in value.splitlines():
        url, separator, alt = line.strip().partition("|")
        if url:
            result.append({"url": url, "alt_text": alt if separator else ""})
    return result


@router.get("/admin/knowledge-slices", response_class=HTMLResponse, include_in_schema=False)
def slice_list(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    dimension: str = "values",
    q: str = "",
    domain: str = "",
    content_type: str = "",
):
    if dimension not in DIMENSION_LABELS:
        dimension = "values"
    statement = select(KnowledgeSlice).where(KnowledgeSlice.dimension == dimension)
    if q.strip():
        query = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                KnowledgeSlice.title.ilike(query),
                KnowledgeSlice.summary.ilike(query),
                KnowledgeSlice.structured_content.ilike(query),
            )
        )
    if domain in DOMAIN_LABELS:
        statement = statement.where(KnowledgeSlice.primary_domain == domain)
    if content_type in CONTENT_TYPE_LABELS:
        statement = statement.where(KnowledgeSlice.content_type == content_type)
    items = list(
        db.scalars(
            statement.options(selectinload(KnowledgeSlice.source)).order_by(
                KnowledgeSlice.updated_at.desc()
            )
        )
    )
    counts = {
        code: int(
            db.scalar(select(func.count(KnowledgeSlice.id)).where(KnowledgeSlice.dimension == code))
            or 0
        )
        for code in DIMENSION_LABELS
    }
    return templates.TemplateResponse(
        request=request,
        name="admin/knowledge_slices.html",
        context=admin_context(
            request,
            admin,
            items=items,
            counts=counts,
            dimension=dimension,
            q=q,
            domain=domain,
            content_type=content_type,
            dimension_labels=DIMENSION_LABELS,
            domain_labels=DOMAIN_LABELS,
            content_type_labels=CONTENT_TYPE_LABELS,
        ),
    )


@router.get(
    "/admin/knowledge-slices/{slice_id}", response_class=HTMLResponse, include_in_schema=False
)
def slice_detail(slice_id: str, request: Request, db: DBSession, admin: CurrentAdmin):
    item = db.scalar(
        select(KnowledgeSlice)
        .where(KnowledgeSlice.id == slice_id)
        .options(selectinload(KnowledgeSlice.source))
    )
    if not item:
        raise HTTPException(status_code=404, detail="知识切片不存在")
    return templates.TemplateResponse(
        request=request,
        name="admin/knowledge_slice_detail.html",
        context=admin_context(
            request,
            admin,
            item=item,
            dimension_labels=DIMENSION_LABELS,
            domain_labels=DOMAIN_LABELS,
            content_type_labels=CONTENT_TYPE_LABELS,
        ),
    )


@router.get("/admin/qa-library", response_class=HTMLResponse, include_in_schema=False)
def qa_list(request: Request, db: DBSession, admin: CurrentAdmin, q: str = ""):
    statement = select(QAEntry)
    if q.strip():
        query = f"%{q.strip()}%"
        statement = statement.where(
            or_(QAEntry.standard_question.ilike(query), QAEntry.fixed_answer.ilike(query))
        )
    items = list(
        db.scalars(
            statement.options(selectinload(QAEntry.assets)).order_by(QAEntry.updated_at.desc())
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/qa_library.html",
        context=admin_context(request, admin, items=items, q=q),
    )


@router.get("/admin/qa-library/new", response_class=HTMLResponse, include_in_schema=False)
def qa_new(request: Request, admin: CurrentAdmin):
    return templates.TemplateResponse(
        request=request,
        name="admin/qa_form.html",
        context=admin_context(request, admin, error="", values={}),
    )


@router.post("/admin/qa-library/new", response_class=HTMLResponse, include_in_schema=False)
async def qa_create(request: Request, db: DBSession, admin: CurrentAdmin):
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token", "")))
    values = {
        key: str(form.get(key, ""))
        for key in (
            "question",
            "answer",
            "aliases",
            "keywords",
            "visibility",
            "course_ids",
            "images",
        )
    }
    try:
        entry = create_qa_entry(
            db,
            question=values["question"],
            answer=values["answer"],
            aliases=_split(values["aliases"]),
            keywords=_split(values["keywords"]),
            visibility=values["visibility"] or "internal",
            course_ids=_split(values["course_ids"]),
            images=_images(values["images"]),
            created_by=admin.username,
        )
    except KnowledgeError as exc:
        return templates.TemplateResponse(
            request=request,
            name="admin/qa_form.html",
            status_code=422,
            context=admin_context(request, admin, error=str(exc), values=values),
        )
    return RedirectResponse(f"/admin/qa-library?notice=created&id={entry.id}", status_code=303)


@router.post("/admin/qa-library/{entry_id}/status", include_in_schema=False)
def qa_status(
    entry_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    next_status: str = Form(...),
    review_note: str = Form(""),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    try:
        set_qa_status(db, entry_id=entry_id, status=next_status, review_note=review_note)
    except KnowledgeError as exc:
        return RedirectResponse(f"/admin/qa-library?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/admin/qa-library", status_code=303)


__all__ = ["router"]
