from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.models import Course, Lesson, VideoAsset
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.catalog.service import (
    CatalogValidationError,
    next_lesson_sort,
    normalize_keywords,
    publish_course,
)
from backend.app.web import templates

router = APIRouter(prefix="/admin", tags=["admin-catalog"])


def get_course_or_404(db: DBSession, course_id: str) -> Course:
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="course not found")
    return course


def render_course_form(
    request: Request,
    admin: CurrentAdmin,
    *,
    course: Course | None = None,
    values: dict[str, object] | None = None,
    error: str = "",
    status_code: int = 200,
):
    form_values = values or {
        "title": course.title if course else "",
        "subtitle": course.subtitle if course else "",
        "description": course.description if course else "",
        "cover_url": course.cover_url if course else "",
        "keywords": "，".join(course.keywords) if course else "",
        "sort_order": course.sort_order if course else 0,
    }
    return templates.TemplateResponse(
        request=request,
        name="admin/course_form.html",
        context=admin_context(
            request,
            admin,
            course=course,
            form_values=form_values,
            error=error,
        ),
        status_code=status_code,
    )


def render_course_detail(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    course: Course,
    *,
    error: str = "",
    notice: str = "",
    status_code: int = 200,
):
    ready_assets = list(
        db.scalars(
            select(VideoAsset)
            .where(VideoAsset.status == "ready")
            .order_by(VideoAsset.created_at.desc())
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/course_detail.html",
        context=admin_context(
            request,
            admin,
            course=course,
            ready_assets=ready_assets,
            next_sort=next_lesson_sort(course),
            error=error,
            notice=notice,
        ),
        status_code=status_code,
    )


@router.get("/courses", response_class=HTMLResponse, include_in_schema=False)
def courses_page(request: Request, db: DBSession, admin: CurrentAdmin):
    courses = list(db.scalars(select(Course).order_by(Course.sort_order, Course.created_at.desc())))
    return templates.TemplateResponse(
        request=request,
        name="admin/courses.html",
        context=admin_context(request, admin, courses=courses),
    )


@router.get("/courses/new", response_class=HTMLResponse, include_in_schema=False)
def new_course_page(request: Request, admin: CurrentAdmin):
    return render_course_form(request, admin)


@router.post("/courses/new", response_class=HTMLResponse, include_in_schema=False)
def create_course(
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    title: str = Form(...),
    subtitle: str = Form(""),
    description: str = Form(""),
    cover_url: str = Form(""),
    keywords: str = Form(""),
    sort_order: int = Form(0),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    values = {
        "title": title.strip(),
        "subtitle": subtitle.strip(),
        "description": description.strip(),
        "cover_url": cover_url.strip(),
        "keywords": keywords.strip(),
        "sort_order": sort_order,
    }
    if not values["title"]:
        return render_course_form(
            request, admin, values=values, error="课程标题不能为空", status_code=422
        )
    try:
        normalized_keywords = normalize_keywords(keywords)
    except CatalogValidationError as exc:
        return render_course_form(
            request, admin, values=values, error=str(exc), status_code=422
        )
    course = Course(
        title=str(values["title"]),
        subtitle=str(values["subtitle"]),
        description=str(values["description"]),
        cover_url=str(values["cover_url"]),
        keywords=normalized_keywords,
        sort_order=sort_order,
    )
    db.add(course)
    db.commit()
    return RedirectResponse(f"/admin/courses/{course.id}/edit?notice=created", status_code=303)


@router.get("/courses/{course_id}/edit", response_class=HTMLResponse, include_in_schema=False)
def edit_course_page(
    course_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    notice: str = "",
):
    return render_course_detail(
        request,
        db,
        admin,
        get_course_or_404(db, course_id),
        notice=notice,
    )


@router.post("/courses/{course_id}", response_class=HTMLResponse, include_in_schema=False)
def update_course(
    course_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    title: str = Form(...),
    subtitle: str = Form(""),
    description: str = Form(""),
    cover_url: str = Form(""),
    keywords: str = Form(""),
    sort_order: int = Form(0),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    course = get_course_or_404(db, course_id)
    if not title.strip():
        return render_course_detail(
            request, db, admin, course, error="课程标题不能为空", status_code=422
        )
    try:
        course.keywords = normalize_keywords(keywords)
    except CatalogValidationError as exc:
        return render_course_detail(
            request, db, admin, course, error=str(exc), status_code=422
        )
    course.title = title.strip()
    course.subtitle = subtitle.strip()
    course.description = description.strip()
    course.cover_url = cover_url.strip()
    course.sort_order = sort_order
    db.commit()
    return RedirectResponse(f"/admin/courses/{course.id}/edit?notice=saved", status_code=303)


@router.post("/courses/{course_id}/publish", include_in_schema=False)
def publish(
    course_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    course = get_course_or_404(db, course_id)
    try:
        publish_course(db, course)
    except CatalogValidationError as exc:
        return render_course_detail(
            request, db, admin, course, error=str(exc), status_code=422
        )
    return RedirectResponse(f"/admin/courses/{course.id}/edit?notice=published", status_code=303)


@router.post("/courses/{course_id}/archive", include_in_schema=False)
def archive_course(
    course_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    course = get_course_or_404(db, course_id)
    course.status = "archived"
    db.commit()
    return RedirectResponse("/admin/courses", status_code=303)


@router.post("/courses/{course_id}/lessons", include_in_schema=False)
def create_lesson(
    course_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    title: str = Form(...),
    description: str = Form(""),
    sort_order: int = Form(...),
    video_asset_id: str = Form(""),
    is_preview: bool = Form(False),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    course = get_course_or_404(db, course_id)
    if not title.strip():
        return render_course_detail(
            request, db, admin, course, error="课节标题不能为空", status_code=422
        )
    asset = db.get(VideoAsset, video_asset_id) if video_asset_id else None
    if video_asset_id and (not asset or asset.status != "ready"):
        return render_course_detail(
            request, db, admin, course, error="只能绑定已转码完成的视频", status_code=422
        )
    lesson = Lesson(
        course=course,
        video_asset=asset,
        title=title.strip(),
        description=description.strip(),
        sort_order=sort_order,
        is_preview=is_preview,
    )
    db.add(lesson)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render_course_detail(
            request,
            db,
            admin,
            course,
            error="课节排序值已存在，请换一个数字",
            status_code=409,
        )
    return RedirectResponse(f"/admin/courses/{course.id}/edit?notice=lesson-added", status_code=303)


@router.post("/courses/{course_id}/lessons/{lesson_id}", include_in_schema=False)
def update_lesson(
    course_id: str,
    lesson_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    title: str = Form(...),
    description: str = Form(""),
    sort_order: int = Form(...),
    video_asset_id: str = Form(""),
    is_preview: bool = Form(False),
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    course = get_course_or_404(db, course_id)
    lesson = db.get(Lesson, lesson_id)
    if not lesson or lesson.course_id != course.id:
        raise HTTPException(status_code=404, detail="lesson not found")
    if not title.strip():
        return render_course_detail(
            request, db, admin, course, error="课节标题不能为空", status_code=422
        )
    asset = db.get(VideoAsset, video_asset_id) if video_asset_id else None
    if video_asset_id and (not asset or asset.status != "ready"):
        return render_course_detail(
            request, db, admin, course, error="只能绑定已转码完成的视频", status_code=422
        )
    lesson.title = title.strip()
    lesson.description = description.strip()
    lesson.sort_order = sort_order
    lesson.video_asset = asset
    lesson.is_preview = is_preview
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render_course_detail(
            request,
            db,
            admin,
            course,
            error="课节排序值已存在，请换一个数字",
            status_code=409,
        )
    return RedirectResponse(f"/admin/courses/{course.id}/edit?notice=lesson-saved", status_code=303)


@router.post("/courses/{course_id}/lessons/{lesson_id}/delete", include_in_schema=False)
def delete_lesson(
    course_id: str,
    lesson_id: str,
    request: Request,
    db: DBSession,
    _: CurrentAdmin,
    csrf_token: str = Form(...),
):
    require_csrf(request, csrf_token)
    course = get_course_or_404(db, course_id)
    lesson = db.get(Lesson, lesson_id)
    if not lesson or lesson.course_id != course.id:
        raise HTTPException(status_code=404, detail="lesson not found")
    db.delete(lesson)
    if course.status == "published":
        course.status = "draft"
    db.commit()
    return RedirectResponse(
        f"/admin/courses/{course.id}/edit?notice=lesson-deleted",
        status_code=303,
    )
