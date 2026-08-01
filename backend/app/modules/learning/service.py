from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.alerts import send_feishu_alert
from backend.app.config import Settings, get_settings
from backend.app.models import Lesson, PlaybackLease, User
from backend.app.modules.entitlements.service import user_has_course_access
from backend.app.security import issue_scoped_token, load_scoped_token

_admission_lock = threading.Lock()
_POSTGRES_ADVISORY_LOCK_ID = 8_240_811


class PlaybackAdmissionError(ValueError):
    def __init__(self, message: str, *, code: str, online_count: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.online_count = online_count


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def lesson_can_be_viewed(db: Session, user: User | None, lesson: Lesson) -> bool:
    if lesson.is_preview:
        return True
    return bool(user and user_has_course_access(db, user.id, lesson.course_id))


def _lock_admission_capacity(db: Session) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _POSTGRES_ADVISORY_LOCK_ID},
        )


def _expire_stale_leases(db: Session, now: datetime) -> None:
    stale = list(
        db.scalars(
            select(PlaybackLease).where(
                PlaybackLease.status == "active",
                PlaybackLease.expires_at <= now,
            )
        )
    )
    for lease in stale:
        lease.status = "expired"


def active_playback_count(db: Session, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    return int(
        db.scalar(
            select(func.count(PlaybackLease.id)).where(
                PlaybackLease.status == "active",
                PlaybackLease.expires_at > current,
            )
        )
        or 0
    )


def issue_playback_redirect_token(
    lease: PlaybackLease,
    settings: Settings | None = None,
) -> str:
    return issue_scoped_token(
        {"lease_id": lease.id, "user_id": lease.user_id},
        salt="playback-redirect-v1",
        settings=settings,
    )


def validate_playback_redirect_token(
    token: str,
    settings: Settings | None = None,
) -> tuple[str, str]:
    configured = settings or get_settings()
    payload = load_scoped_token(
        token,
        salt="playback-redirect-v1",
        max_age_seconds=configured.playback_redirect_token_max_age_seconds,
        settings=configured,
    )
    lease_id = payload.get("lease_id")
    user_id = payload.get("user_id")
    if not isinstance(lease_id, str) or not isinstance(user_id, str):
        raise PlaybackAdmissionError("播放令牌无效", code="invalid_token")
    return lease_id, user_id


def _signed_redirect_url(lease: PlaybackLease, settings: Settings) -> str:
    token = issue_playback_redirect_token(lease, settings)
    return (
        f"{settings.public_base_url.rstrip('/')}/api/v1/playback/url/{lease.id}"
        f"?token={quote(token, safe='')}"
    )


def admit_playback(
    db: Session,
    *,
    user: User,
    lesson: Lesson,
    device_id: str,
    settings: Settings | None = None,
) -> tuple[PlaybackLease, str, int]:
    configured = settings or get_settings()
    if not lesson_can_be_viewed(db, user, lesson):
        raise PlaybackAdmissionError("尚未开通这门课程", code="course_locked")
    if not lesson.video_asset or lesson.video_asset.status != "ready":
        raise PlaybackAdmissionError("课节视频尚未准备完成", code="video_not_ready")
    if not lesson.video_asset.playback_url:
        raise PlaybackAdmissionError("课节缺少播放地址", code="playback_url_missing")

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=configured.playback_lease_ttl_seconds)
    with _admission_lock:
        _lock_admission_capacity(db)
        _expire_stale_leases(db, now)
        existing = db.scalar(
            select(PlaybackLease)
            .where(
                PlaybackLease.user_id == user.id,
                PlaybackLease.status == "active",
                PlaybackLease.expires_at > now,
            )
            .order_by(PlaybackLease.updated_at.desc())
        )
        if existing:
            if existing.device_id != device_id:
                raise PlaybackAdmissionError(
                    "当前账号正在另一台设备播放",
                    code="another_device_active",
                )
            existing.lesson_id = lesson.id
            existing.expires_at = expires_at
            lease = existing
        else:
            online_count = active_playback_count(db, now)
            if online_count >= configured.playback_session_limit:
                send_feishu_alert(
                    "播放并发已满",
                    f"在线播放会话={online_count}，已拒绝新的播放请求",
                )
                raise PlaybackAdmissionError(
                    "当前在线人数较多，请稍后再试",
                    code="capacity_full",
                    online_count=online_count,
                )
            lease = PlaybackLease(
                user_id=user.id,
                lesson_id=lesson.id,
                device_id=device_id,
                expires_at=expires_at,
            )
            db.add(lease)
            db.flush()
        db.commit()
        online_count = active_playback_count(db, now)

    if online_count in {
        configured.playback_alert_threshold,
        configured.playback_priority_alert_threshold,
    }:
        level = (
            "高优先级"
            if online_count >= configured.playback_priority_alert_threshold
            else "预警"
        )
        send_feishu_alert("播放并发预警", f"{level}：当前在线播放会话={online_count}")
    return lease, _signed_redirect_url(lease, configured), online_count


def heartbeat_playback(
    db: Session,
    *,
    user: User,
    lease_id: str,
    device_id: str,
    settings: Settings | None = None,
) -> PlaybackLease:
    configured = settings or get_settings()
    lease = db.get(PlaybackLease, lease_id)
    now = datetime.now(UTC)
    if not lease or lease.user_id != user.id:
        raise PlaybackAdmissionError("播放会话不存在", code="lease_not_found")
    if lease.status != "active" or _utc(lease.expires_at) <= now:
        if lease.status == "active":
            lease.status = "expired"
            db.commit()
        raise PlaybackAdmissionError("播放会话已过期，请重新进入", code="lease_expired")
    if lease.device_id != device_id:
        raise PlaybackAdmissionError("播放设备不匹配", code="device_mismatch")
    lease.expires_at = now + timedelta(seconds=configured.playback_lease_ttl_seconds)
    db.commit()
    return lease


def resolve_playback_redirect(
    db: Session,
    *,
    lease_id: str,
    token: str,
    settings: Settings | None = None,
) -> str:
    token_lease_id, user_id = validate_playback_redirect_token(token, settings)
    if token_lease_id != lease_id:
        raise PlaybackAdmissionError("播放令牌不匹配", code="invalid_token")
    lease = db.get(PlaybackLease, lease_id)
    now = datetime.now(UTC)
    if (
        not lease
        or lease.user_id != user_id
        or lease.status != "active"
        or _utc(lease.expires_at) <= now
    ):
        raise PlaybackAdmissionError("播放会话无效", code="lease_expired")
    lesson = db.get(Lesson, lease.lesson_id)
    if not lesson or not lesson.video_asset or lesson.video_asset.status != "ready":
        raise PlaybackAdmissionError("视频不可用", code="video_not_ready")
    return lesson.video_asset.playback_url
