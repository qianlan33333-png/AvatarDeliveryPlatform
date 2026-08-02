from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.app.config import get_settings
from backend.app.models import (
    Course,
    CourseEntitlement,
    LearningProgress,
    Lesson,
)
from backend.app.modules.api.dependencies import CurrentUser, DBSession, OptionalCurrentUser
from backend.app.modules.capabilities.service import list_capability_states
from backend.app.modules.entitlements.service import entitlement_is_active
from backend.app.modules.learning.service import (
    PlaybackAdmissionError,
    active_playback_count,
    admit_playback,
    heartbeat_playback,
    lesson_can_be_viewed,
    load_progress_by_lesson,
    published_course_lessons,
    resolve_playback_redirect,
    summarize_course_progress,
)
from backend.app.security import SecurityValueError

router = APIRouter(prefix="/api/v1", tags=["course-delivery"])


class PlaybackAdmitRequest(BaseModel):
    lesson_id: str = Field(min_length=1, max_length=36)
    device_id: str = Field(min_length=8, max_length=160)


class PlaybackHeartbeatRequest(BaseModel):
    lease_id: str = Field(min_length=1, max_length=36)
    device_id: str = Field(min_length=8, max_length=160)


class ProgressRequest(BaseModel):
    position_seconds: int = Field(ge=0)
    completed: bool = False


def _active_course_ids(db: DBSession, user_id: str | None) -> set[str]:
    if not user_id:
        return set()
    entitlements = list(
        db.scalars(
            select(CourseEntitlement).where(CourseEntitlement.user_id == user_id)
        )
    )
    return {
        entitlement.course_id
        for entitlement in entitlements
        if entitlement_is_active(entitlement)
    }


def _course_summary(
    course: Course,
    has_access: bool,
    progress_by_lesson: dict[str, LearningProgress],
) -> dict[str, object]:
    lessons = published_course_lessons(course)
    progress = summarize_course_progress(course, progress_by_lesson)
    return {
        "id": course.id,
        "title": course.title,
        "subtitle": course.subtitle,
        "description": course.description,
        "cover_url": course.cover_url,
        "keywords": course.keywords,
        "lesson_count": progress.lesson_count,
        "preview_lesson_count": sum(1 for lesson in lessons if lesson.is_preview),
        "completed_lesson_count": progress.completed_lesson_count,
        "progress_percent": progress.progress_percent,
        "current_lesson_id": progress.current_lesson_id,
        "current_lesson_title": progress.current_lesson_title,
        "current_lesson_number": progress.current_lesson_number,
        "has_started": progress.has_started,
        "has_access": has_access,
        "locked": not has_access,
    }


@router.get("/courses")
def list_courses(db: DBSession, user: OptionalCurrentUser):
    courses = list(
        db.scalars(
            select(Course)
            .options(selectinload(Course.lessons))
            .where(Course.status == "published")
            .order_by(Course.sort_order, Course.created_at)
        )
    )
    active_course_ids = _active_course_ids(db, user.id if user else None)
    published_lesson_ids = [
        lesson.id for course in courses for lesson in published_course_lessons(course)
    ]
    progress_by_lesson = load_progress_by_lesson(
        db,
        user_id=user.id if user else None,
        lesson_ids=published_lesson_ids,
    )
    return {
        "items": [
            _course_summary(
                course,
                course.id in active_course_ids,
                progress_by_lesson,
            )
            for course in courses
        ]
    }


@router.get("/courses/{course_id}")
def course_detail(course_id: str, db: DBSession, user: OptionalCurrentUser):
    course = db.scalar(
        select(Course)
        .options(selectinload(Course.lessons).selectinload(Lesson.video_asset))
        .where(Course.id == course_id, Course.status == "published")
    )
    if not course:
        raise HTTPException(status_code=404, detail="course not found")
    has_access = course.id in _active_course_ids(db, user.id if user else None)
    published_lessons = published_course_lessons(course)
    progress_by_lesson = load_progress_by_lesson(
        db,
        user_id=user.id if user else None,
        lesson_ids=[lesson.id for lesson in published_lessons],
    )
    lessons = []
    for lesson in published_lessons:
        can_access_lesson = has_access or lesson.is_preview
        progress = progress_by_lesson.get(lesson.id)
        lessons.append(
            {
                "id": lesson.id,
                "title": lesson.title,
                "description": lesson.description if can_access_lesson else "",
                "sort_order": lesson.sort_order,
                "is_preview": lesson.is_preview,
                "locked": not can_access_lesson,
                "duration_seconds": lesson.video_asset.duration_seconds
                if lesson.video_asset
                else 0,
                "position_seconds": progress.position_seconds if progress else 0,
                "completed": progress.completed if progress else False,
            }
        )
    return {
        **_course_summary(course, has_access, progress_by_lesson),
        "lessons": lessons,
    }


@router.get("/lessons/{lesson_id}")
def lesson_detail(lesson_id: str, db: DBSession, user: OptionalCurrentUser):
    lesson = db.scalar(
        select(Lesson)
        .options(selectinload(Lesson.video_asset), selectinload(Lesson.course))
        .where(Lesson.id == lesson_id, Lesson.status == "published")
    )
    if not lesson or lesson.course.status != "published":
        raise HTTPException(status_code=404, detail="lesson not found")
    if not lesson_can_be_viewed(db, user, lesson):
        raise HTTPException(status_code=403, detail="course entitlement required")
    progress = None
    if user:
        progress = db.scalar(
            select(LearningProgress).where(
                LearningProgress.user_id == user.id,
                LearningProgress.lesson_id == lesson.id,
            )
        )
    return {
        "id": lesson.id,
        "course_id": lesson.course_id,
        "title": lesson.title,
        "description": lesson.description,
        "is_preview": lesson.is_preview,
        "duration_seconds": lesson.video_asset.duration_seconds if lesson.video_asset else 0,
        "position_seconds": progress.position_seconds if progress else 0,
        "completed": progress.completed if progress else False,
        "can_play": bool(lesson.video_asset and lesson.video_asset.status == "ready"),
    }


def _playback_http_error(exc: PlaybackAdmissionError) -> HTTPException:
    status_code = status.HTTP_429_TOO_MANY_REQUESTS if exc.code == "capacity_full" else 409
    if exc.code == "course_locked":
        status_code = status.HTTP_403_FORBIDDEN
    if exc.code in {"lease_not_found", "video_not_ready", "playback_url_missing"}:
        status_code = status.HTTP_404_NOT_FOUND
    return HTTPException(
        status_code=status_code,
        detail={
            "code": exc.code,
            "message": str(exc),
            "online_count": exc.online_count,
        },
    )


@router.post("/playback/admit")
def playback_admit(payload: PlaybackAdmitRequest, db: DBSession, user: CurrentUser):
    lesson = db.scalar(
        select(Lesson)
        .options(selectinload(Lesson.video_asset), selectinload(Lesson.course))
        .where(Lesson.id == payload.lesson_id, Lesson.status == "published")
    )
    if not lesson or lesson.course.status != "published":
        raise HTTPException(status_code=404, detail="lesson not found")
    try:
        lease, playback_url, online_count = admit_playback(
            db,
            user=user,
            lesson=lesson,
            device_id=payload.device_id,
        )
    except PlaybackAdmissionError as exc:
        raise _playback_http_error(exc) from exc
    settings = get_settings()
    return {
        "lease_id": lease.id,
        "playback_url": playback_url,
        "expires_at": lease.expires_at,
        "heartbeat_interval_seconds": settings.playback_heartbeat_interval_seconds,
        "online_count": online_count,
    }


@router.post("/playback/heartbeat")
def playback_heartbeat(
    payload: PlaybackHeartbeatRequest,
    db: DBSession,
    user: CurrentUser,
):
    try:
        lease = heartbeat_playback(
            db,
            user=user,
            lease_id=payload.lease_id,
            device_id=payload.device_id,
        )
    except PlaybackAdmissionError as exc:
        raise _playback_http_error(exc) from exc
    return {"ok": True, "lease_id": lease.id, "expires_at": lease.expires_at}


@router.get("/playback/url/{lease_id}", include_in_schema=False)
def playback_url(lease_id: str, db: DBSession, token: str = Query(...)):
    try:
        target = resolve_playback_redirect(db, lease_id=lease_id, token=token)
    except (PlaybackAdmissionError, SecurityValueError) as exc:
        raise HTTPException(status_code=403, detail="playback link invalid or expired") from exc
    return RedirectResponse(
        target,
        status_code=307,
        headers={"Cache-Control": "no-store, private"},
    )


@router.put("/learning-progress/{lesson_id}")
def update_progress(
    lesson_id: str,
    payload: ProgressRequest,
    db: DBSession,
    user: CurrentUser,
):
    lesson = db.get(Lesson, lesson_id)
    if not lesson or lesson.status != "published":
        raise HTTPException(status_code=404, detail="lesson not found")
    if not lesson_can_be_viewed(db, user, lesson):
        raise HTTPException(status_code=403, detail="course entitlement required")
    progress = db.scalar(
        select(LearningProgress).where(
            LearningProgress.user_id == user.id,
            LearningProgress.lesson_id == lesson.id,
        )
    )
    if not progress:
        progress = LearningProgress(user_id=user.id, lesson_id=lesson.id)
        db.add(progress)
    duration = lesson.video_asset.duration_seconds if lesson.video_asset else 0
    progress.position_seconds = min(payload.position_seconds, duration) if duration else 0
    progress.completed = progress.completed or payload.completed or (
        bool(duration) and progress.position_seconds >= max(duration - 10, 0)
    )
    db.commit()
    return {
        "lesson_id": lesson.id,
        "position_seconds": progress.position_seconds,
        "completed": progress.completed,
    }


@router.get("/me")
def current_profile(db: DBSession, user: CurrentUser):
    entitlements = list(
        db.scalars(
            select(CourseEntitlement).where(CourseEntitlement.user_id == user.id)
        )
    )
    active = [item for item in entitlements if entitlement_is_active(item)]
    progress_count = int(
        db.scalar(
            select(func.count(LearningProgress.id)).where(LearningProgress.user_id == user.id)
        )
        or 0
    )
    return {
        "id": user.id,
        "nickname": user.nickname,
        "avatar_url": user.avatar_url,
        "phone_bound": bool(user.phone_hash),
        "phone_last4": user.phone_last4,
        "active_course_count": len(active),
        "learning_lesson_count": progress_count,
        "entitlements": [
            {
                "course_id": item.course_id,
                "effective_at": item.effective_at,
                "expires_at": item.expires_at,
            }
            for item in active
        ],
        "capabilities": list_capability_states(db, user.id),
        "active_playback_count": active_playback_count(db, datetime.now(UTC)),
    }
