from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.db import get_session_factory
from backend.app.main import app
from backend.app.models import (
    Course,
    CourseEntitlement,
    LearningProgress,
    Lesson,
    User,
    VideoAsset,
)
from backend.app.security import issue_user_token


def _seed_delivery_data() -> dict[str, str]:
    session_factory = get_session_factory()
    with session_factory() as db:
        course = Course(
            title="稳定交付课",
            subtitle="从试听到完整学习",
            description="一门公开可见、购买后解锁的课程。",
            cover_url="https://cdn.example.com/course.jpg",
            keywords=["交付", "视频"],
            status="published",
            sort_order=10,
        )
        preview_asset = VideoAsset(
            title="试听视频",
            status="ready",
            provider_file_id="vod-preview",
            playback_url="https://vod.example.com/preview/index.m3u8",
            duration_seconds=120,
        )
        paid_asset = VideoAsset(
            title="正式视频",
            status="ready",
            provider_file_id="vod-paid",
            playback_url="https://vod.example.com/paid/index.m3u8",
            duration_seconds=600,
        )
        db.add_all([course, preview_asset, paid_asset])
        db.flush()
        preview = Lesson(
            course_id=course.id,
            video_asset_id=preview_asset.id,
            title="试听：课程介绍",
            description="公开试听内容",
            sort_order=10,
            is_preview=True,
            status="published",
        )
        paid = Lesson(
            course_id=course.id,
            video_asset_id=paid_asset.id,
            title="第一课：稳定交付",
            description="购买后可见内容",
            sort_order=20,
            status="published",
        )
        buyer = User(nickname="已购用户")
        guest = User(nickname="试听用户")
        db.add_all([preview, paid, buyer, guest])
        db.flush()
        db.add(
            CourseEntitlement(
                user_id=buyer.id,
                phone_hash="buyer-phone-hash",
                course_id=course.id,
                status="active",
                effective_at=datetime.now(UTC) - timedelta(minutes=5),
                expires_at=datetime.now(UTC) + timedelta(days=30),
                source_order_id="order-buyer",
            )
        )
        db.commit()
        return {
            "course_id": course.id,
            "preview_lesson_id": preview.id,
            "paid_lesson_id": paid.id,
            "buyer_id": buyer.id,
            "guest_id": guest.id,
        }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_user_token(user_id)}"}


def test_visible_locked_course_preview_and_paid_lesson_access() -> None:
    data = _seed_delivery_data()
    with TestClient(app) as client:
        guest_list = client.get("/api/v1/courses", headers=_auth(data["guest_id"]))
        buyer_list = client.get("/api/v1/courses", headers=_auth(data["buyer_id"]))
        guest_detail = client.get(
            f"/api/v1/courses/{data['course_id']}",
            headers=_auth(data["guest_id"]),
        )
        preview = client.get(
            f"/api/v1/lessons/{data['preview_lesson_id']}",
            headers=_auth(data["guest_id"]),
        )
        paid_locked = client.get(
            f"/api/v1/lessons/{data['paid_lesson_id']}",
            headers=_auth(data["guest_id"]),
        )
        paid_open = client.get(
            f"/api/v1/lessons/{data['paid_lesson_id']}",
            headers=_auth(data["buyer_id"]),
        )

    assert guest_list.status_code == 200
    assert guest_list.json()["items"][0]["locked"] is True
    assert guest_list.json()["items"][0]["lesson_count"] == 2
    assert guest_list.json()["items"][0]["completed_lesson_count"] == 0
    assert guest_list.json()["items"][0]["progress_percent"] == 0
    assert guest_list.json()["items"][0]["has_started"] is False
    assert guest_list.json()["items"][0]["current_lesson_id"] is None
    assert buyer_list.json()["items"][0]["has_access"] is True
    lessons = guest_detail.json()["lessons"]
    assert lessons[0]["locked"] is False
    assert lessons[1]["locked"] is True
    assert lessons[1]["description"] == ""
    assert preview.status_code == 200
    assert paid_locked.status_code == 403
    assert paid_open.status_code == 200


def test_playback_admit_redirect_heartbeat_and_progress() -> None:
    data = _seed_delivery_data()
    headers = _auth(data["guest_id"])
    device_id = "device-guest-0001"
    with TestClient(app) as client:
        admitted = client.post(
            "/api/v1/playback/admit",
            json={"lesson_id": data["preview_lesson_id"], "device_id": device_id},
            headers=headers,
        )
        assert admitted.status_code == 200
        body = admitted.json()
        assert body["heartbeat_interval_seconds"] == 30

        playback = client.get(body["playback_url"], follow_redirects=False)
        assert playback.status_code == 307
        assert playback.headers["location"].endswith("/preview/index.m3u8")
        assert playback.headers["cache-control"] == "no-store, private"

        heartbeat = client.post(
            "/api/v1/playback/heartbeat",
            json={"lease_id": body["lease_id"], "device_id": device_id},
            headers=headers,
        )
        assert heartbeat.status_code == 200

        other_device = client.post(
            "/api/v1/playback/admit",
            json={
                "lesson_id": data["preview_lesson_id"],
                "device_id": "device-guest-0002",
            },
            headers=headers,
        )
        assert other_device.status_code == 409
        assert other_device.json()["detail"]["code"] == "another_device_active"

        progress = client.put(
            f"/api/v1/learning-progress/{data['preview_lesson_id']}",
            json={"position_seconds": 115, "completed": False},
            headers=headers,
        )
        assert progress.status_code == 200
        assert progress.json()["completed"] is True

        replayed = client.put(
            f"/api/v1/learning-progress/{data['preview_lesson_id']}",
            json={"position_seconds": 30, "completed": False},
            headers=headers,
        )
        assert replayed.status_code == 200
        assert replayed.json()["position_seconds"] == 30
        assert replayed.json()["completed"] is True

    session_factory = get_session_factory()
    with session_factory() as db:
        saved = db.query(LearningProgress).one()
        assert saved.position_seconds == 30
        assert saved.completed is True


def test_course_progress_summary_uses_only_published_lessons_and_latest_activity() -> None:
    now = datetime.now(UTC)
    session_factory = get_session_factory()
    with session_factory() as db:
        course = Course(
            title="进度展示课",
            subtitle="列表与详情同口径",
            description="用于验证课程级学习进度。",
            cover_url="https://cdn.example.com/progress.jpg",
            status="published",
            sort_order=10,
        )
        empty_course = Course(
            title="空课程",
            description="兼容历史异常数据。",
            status="published",
            sort_order=20,
        )
        user = User(nickname="进度用户")
        other_user = User(nickname="另一位进度用户")
        db.add_all([course, empty_course, user, other_user])
        db.flush()
        first = Lesson(
            course_id=course.id,
            title="第一讲：已完成",
            sort_order=10,
            status="published",
        )
        second = Lesson(
            course_id=course.id,
            title="第二讲：学习中",
            sort_order=20,
            status="published",
        )
        third = Lesson(
            course_id=course.id,
            title="第三讲：未开始",
            sort_order=30,
            status="published",
        )
        draft = Lesson(
            course_id=course.id,
            title="草稿课节",
            sort_order=40,
            status="draft",
        )
        db.add_all([first, second, third, draft])
        db.flush()
        db.add_all(
            [
                CourseEntitlement(
                    user_id=user.id,
                    phone_hash="progress-user-phone-hash",
                    course_id=course.id,
                    status="active",
                    effective_at=now - timedelta(minutes=30),
                    expires_at=now + timedelta(days=30),
                    source_order_id="order-progress-user",
                ),
                CourseEntitlement(
                    user_id=other_user.id,
                    phone_hash="other-progress-user-phone-hash",
                    course_id=course.id,
                    status="active",
                    effective_at=now - timedelta(minutes=30),
                    expires_at=now + timedelta(days=30),
                    source_order_id="order-other-progress-user",
                ),
                LearningProgress(
                    user_id=user.id,
                    lesson_id=first.id,
                    position_seconds=100,
                    completed=True,
                    created_at=now - timedelta(minutes=20),
                    updated_at=now - timedelta(minutes=20),
                ),
                LearningProgress(
                    user_id=user.id,
                    lesson_id=second.id,
                    position_seconds=45,
                    completed=False,
                    created_at=now - timedelta(minutes=10),
                    updated_at=now - timedelta(minutes=10),
                ),
                LearningProgress(
                    user_id=user.id,
                    lesson_id=draft.id,
                    position_seconds=100,
                    completed=True,
                    created_at=now - timedelta(minutes=5),
                    updated_at=now - timedelta(minutes=5),
                ),
                LearningProgress(
                    user_id=other_user.id,
                    lesson_id=third.id,
                    position_seconds=80,
                    completed=True,
                    created_at=now - timedelta(minutes=2),
                    updated_at=now - timedelta(minutes=2),
                ),
            ]
        )
        db.commit()
        course_id = course.id
        empty_course_id = empty_course.id
        user_id = user.id
        other_user_id = other_user.id
        second_id = second.id
        third_id = third.id

    with TestClient(app) as client:
        course_list = client.get("/api/v1/courses", headers=_auth(user_id))
        other_user_list = client.get(
            "/api/v1/courses",
            headers=_auth(other_user_id),
        )
        detail = client.get(f"/api/v1/courses/{course_id}", headers=_auth(user_id))
        anonymous_list = client.get("/api/v1/courses")
        empty_detail = client.get(f"/api/v1/courses/{empty_course_id}")

    assert course_list.status_code == 200
    summary = next(
        item for item in course_list.json()["items"] if item["id"] == course_id
    )
    detail_body = detail.json()
    for payload in (summary, detail_body):
        assert payload["lesson_count"] == 3
        assert payload["completed_lesson_count"] == 1
        assert payload["progress_percent"] == 33
        assert payload["has_started"] is True
        assert payload["current_lesson_id"] == second_id
        assert payload["current_lesson_title"] == "第二讲：学习中"
        assert payload["current_lesson_number"] == 2
    assert len(detail_body["lessons"]) == 3
    assert all(item["title"] != "草稿课节" for item in detail_body["lessons"])

    other_summary = next(
        item for item in other_user_list.json()["items"] if item["id"] == course_id
    )
    assert other_summary["completed_lesson_count"] == 1
    assert other_summary["progress_percent"] == 33
    assert other_summary["current_lesson_id"] == third_id
    assert other_summary["current_lesson_number"] == 3

    anonymous = next(
        item for item in anonymous_list.json()["items"] if item["id"] == course_id
    )
    assert anonymous["completed_lesson_count"] == 0
    assert anonymous["progress_percent"] == 0
    assert anonymous["has_started"] is False
    assert anonymous["current_lesson_id"] is None

    empty = empty_detail.json()
    assert empty["lesson_count"] == 0
    assert empty["completed_lesson_count"] == 0
    assert empty["progress_percent"] == 0
    assert empty["has_started"] is False
    assert empty["current_lesson_id"] is None
    assert empty["lessons"] == []


def test_capacity_rejects_new_user_but_existing_lease_can_renew(monkeypatch) -> None:
    import backend.app.modules.learning.service as learning_service

    data = _seed_delivery_data()
    limited_settings = get_settings().model_copy(update={"playback_session_limit": 1})
    monkeypatch.setattr(learning_service, "get_settings", lambda: limited_settings)
    first_headers = _auth(data["guest_id"])
    second_headers = _auth(data["buyer_id"])
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/playback/admit",
            json={
                "lesson_id": data["preview_lesson_id"],
                "device_id": "device-first-0001",
            },
            headers=first_headers,
        )
        assert first.status_code == 200

        rejected = client.post(
            "/api/v1/playback/admit",
            json={
                "lesson_id": data["paid_lesson_id"],
                "device_id": "device-second-001",
            },
            headers=second_headers,
        )
        assert rejected.status_code == 429
        assert rejected.json()["detail"]["code"] == "capacity_full"
        assert rejected.json()["detail"]["online_count"] == 1

        renewed = client.post(
            "/api/v1/playback/heartbeat",
            json={
                "lease_id": first.json()["lease_id"],
                "device_id": "device-first-0001",
            },
            headers=first_headers,
        )
        assert renewed.status_code == 200
