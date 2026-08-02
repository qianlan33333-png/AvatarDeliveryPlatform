from __future__ import annotations

import re

from sqlalchemy.orm import Session

from backend.app.models import Course


class CatalogValidationError(ValueError):
    pass


def normalize_keywords(raw: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,，\n]+", raw):
        keyword = item.strip()
        normalized = keyword.casefold()
        if not keyword or normalized in seen:
            continue
        if len(keyword) > 50:
            raise CatalogValidationError("单个关键词不能超过 50 个字符")
        seen.add(normalized)
        values.append(keyword)
    if len(values) > 30:
        raise CatalogValidationError("每门课程最多维护 30 个关键词")
    return values


def publish_issues(course: Course) -> list[str]:
    issues: list[str] = []
    if not course.title.strip():
        issues.append("课程标题不能为空")
    if not course.description.strip():
        issues.append("课程简介不能为空")
    if not course.lessons:
        issues.append("至少创建一个课节")
    elif not any(lesson.is_preview for lesson in course.lessons):
        issues.append("至少设置一个独立试听课节")
    for lesson in course.lessons:
        if lesson.video_asset is None:
            issues.append(f"课节“{lesson.title}”尚未绑定视频")
        elif lesson.video_asset.status != "ready":
            issues.append(f"课节“{lesson.title}”的视频尚未转码完成")
    return issues


def publish_course(db: Session, course: Course) -> None:
    issues = publish_issues(course)
    if issues:
        raise CatalogValidationError("；".join(issues))
    course.status = "published"
    for lesson in course.lessons:
        lesson.status = "published"
    db.commit()


def next_lesson_sort(course: Course) -> int:
    return max((lesson.sort_order for lesson in course.lessons), default=0) + 10
