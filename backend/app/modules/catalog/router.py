from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from backend.app.models import (
    Course,
    CourseEntitlement,
    LearningProgress,
    Lesson,
    User,
    VideoAsset,
)
from backend.app.modules.admin.auth import require_csrf
from backend.app.modules.admin.dependencies import CurrentAdmin, DBSession, admin_context
from backend.app.modules.catalog.service import (
    CatalogValidationError,
    next_lesson_sort,
    normalize_keywords,
    publish_course,
    publish_issues,
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
    section: str = "basic",
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
    section: str = "basic",
):
    ready_assets = list(
        db.scalars(
            select(VideoAsset)
            .where(VideoAsset.status == "ready")
            .order_by(VideoAsset.created_at.desc())
        )
    )
    issues = publish_issues(course)
    preview_ok = any(lesson.is_preview for lesson in course.lessons)
    videos_ok = bool(course.lessons) and all(
        lesson.video_asset is not None and lesson.video_asset.status == "ready"
        for lesson in course.lessons
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
            section=section if section in {"basic", "lessons", "publish"} else "basic",
            publish_issues=issues,
            publish_checks={
                "basic": bool(course.title.strip() and course.description.strip()),
                "preview": preview_ok,
                "videos": videos_ok,
            },
        ),
        status_code=status_code,
    )


@router.get("/courses", response_class=HTMLResponse, include_in_schema=False)
def courses_page(request: Request, db: DBSession, admin: CurrentAdmin, page: int = 1):
    page = max(1, page)
    page_size = 20
    total = int(db.scalar(select(func.count(Course.id))) or 0)
    courses = list(
        db.scalars(
            select(Course)
            .order_by(Course.sort_order, Course.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    course_ids = [course.id for course in courses]
    student_counts = (
        {
            course_id: int(count)
            for course_id, count in db.execute(
                select(CourseEntitlement.course_id, func.count(CourseEntitlement.id))
                .where(
                    CourseEntitlement.course_id.in_(course_ids),
                    CourseEntitlement.status == "active",
                    CourseEntitlement.user_id.is_not(None),
                )
                .group_by(CourseEntitlement.course_id)
            )
        }
        if course_ids
        else {}
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/courses.html",
        context=admin_context(
            request,
            admin,
            courses=courses,
            student_counts=student_counts,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=max(1, (total + page_size - 1) // page_size),
        ),
    )


def _student_rows(db: DBSession, course: Course, keyword: str = "") -> list[dict[str, object]]:
    statement = (
        select(CourseEntitlement, User)
        .join(User, User.id == CourseEntitlement.user_id)
        .where(CourseEntitlement.course_id == course.id, CourseEntitlement.status == "active")
    )
    normalized = keyword.strip()
    if normalized:
        like = f"%{normalized}%"
        statement = statement.where(
            or_(User.nickname.ilike(like), User.id.ilike(like), User.phone_last4.ilike(like[-4:]))
        )
    entitlement_rows = list(db.execute(statement.order_by(CourseEntitlement.effective_at.desc())))
    published_lessons = [lesson for lesson in course.lessons if lesson.status == "published"]
    lesson_ids = [lesson.id for lesson in published_lessons]
    lesson_number = {lesson.id: index for index, lesson in enumerate(published_lessons, start=1)}
    rows: list[dict[str, object]] = []
    for entitlement, user in entitlement_rows:
        progress_items = (
            list(
                db.scalars(
                    select(LearningProgress)
                    .where(
                        LearningProgress.user_id == user.id,
                        LearningProgress.lesson_id.in_(lesson_ids),
                    )
                    .order_by(LearningProgress.updated_at.desc())
                )
            )
            if lesson_ids
            else []
        )
        completed = sum(1 for item in progress_items if item.completed)
        progress_percent = (
            round(completed * 100 / len(published_lessons)) if published_lessons else 0
        )
        current_lesson = max(
            (lesson_number.get(item.lesson_id, 0) for item in progress_items), default=0
        )
        rows.append(
            {
                "entitlement": entitlement,
                "user": user,
                "progress_percent": progress_percent,
                "current_lesson": current_lesson,
                "completed": bool(published_lessons) and completed == len(published_lessons),
                "started": bool(progress_items),
                "last_learning_at": progress_items[0].updated_at if progress_items else None,
            }
        )
    return rows


@router.get("/courses/{course_id}/students", response_class=HTMLResponse, include_in_schema=False)
def course_students_page(
    course_id: str,
    request: Request,
    db: DBSession,
    admin: CurrentAdmin,
    q: str = "",
    page: int = 1,
    selected_course_id: str = "",
):
    course = get_course_or_404(db, course_id)
    if (
        selected_course_id
        and selected_course_id != course_id
        and db.get(Course, selected_course_id)
    ):
        return RedirectResponse(
            f"/admin/courses/{selected_course_id}/students?q={quote(q.strip())}", status_code=303
        )
    all_rows = _student_rows(db, course, q)
    page = max(1, page)
    page_size = 20
    start = (page - 1) * page_size
    now = datetime.now(UTC)
    active_7d = sum(
        1
        for row in all_rows
        if row["last_learning_at"]
        and (
            now
            - (
                row["last_learning_at"]
                if row["last_learning_at"].tzinfo
                else row["last_learning_at"].replace(tzinfo=UTC)
            )
        ).days
        < 7
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/course_students.html",
        context=admin_context(
            request,
            admin,
            course=course,
            courses=list(db.scalars(select(Course).order_by(Course.sort_order, Course.title))),
            student_rows=all_rows[start : start + page_size],
            q=q.strip(),
            page=page,
            page_size=page_size,
            total=len(all_rows),
            total_pages=max(1, (len(all_rows) + page_size - 1) // page_size),
            stats={
                "active": len(all_rows),
                "started": sum(1 for row in all_rows if row["started"]),
                "completed": sum(1 for row in all_rows if row["completed"]),
                "active_7d": active_7d,
            },
        ),
    )


@router.get("/courses/{course_id}/students.csv", include_in_schema=False)
def course_students_csv(
    course_id: str,
    db: DBSession,
    _: CurrentAdmin,
    q: str = "",
):
    course = get_course_or_404(db, course_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["用户ID", "昵称", "手机号", "开通时间", "学习进度", "当前课节", "最后学习", "来源订单"]
    )
    for row in _student_rows(db, course, q):
        user = row["user"]
        entitlement = row["entitlement"]
        writer.writerow(
            [
                user.id,
                user.nickname or "未命名用户",
                f"****{user.phone_last4}" if user.phone_last4 else "未绑定",
                entitlement.effective_at.isoformat(),
                f"{row['progress_percent']}%",
                row["current_lesson"],
                row["last_learning_at"].isoformat() if row["last_learning_at"] else "",
                entitlement.source_order_id,
            ]
        )
    payload = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="course-{course.id}-students.csv"'},
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
        return render_course_form(request, admin, values=values, error=str(exc), status_code=422)
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
    section: str = "basic",
):
    return render_course_detail(
        request,
        db,
        admin,
        get_course_or_404(db, course_id),
        notice=notice,
        section=section,
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
        return render_course_detail(request, db, admin, course, error=str(exc), status_code=422)
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
        return render_course_detail(request, db, admin, course, error=str(exc), status_code=422)
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
