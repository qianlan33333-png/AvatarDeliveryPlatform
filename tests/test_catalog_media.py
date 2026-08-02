import base64
import hashlib
import hmac
import json
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.db import get_session_factory
from backend.app.main import app
from backend.app.models import Course, VideoAsset, WebhookEvent
from backend.app.modules.media.service import build_upload_signature


def login_admin(client: TestClient) -> str:
    response = client.post(
        "/admin/login",
        data={
            "username": "admin",
            "password": "test-admin-password",
            "next_url": "/admin/courses",
        },
    )
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def test_course_publish_requires_ready_lesson_then_succeeds() -> None:
    with TestClient(app) as client:
        csrf_token = login_admin(client)
        created = client.post(
            "/admin/courses/new",
            data={
                "csrf_token": csrf_token,
                "title": "极简 AI 课程",
                "subtitle": "从零开始",
                "description": "一门用于验证交付链路的课程。",
                "cover_url": "https://cdn.example.com/cover.jpg",
                "keywords": "AI，课程, ai",
                "sort_order": "10",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        course_id = created.headers["location"].split("/")[3]

        blocked = client.post(
            f"/admin/courses/{course_id}/publish",
            data={"csrf_token": csrf_token},
        )
        assert blocked.status_code == 422
        assert "至少创建一个课节" in blocked.text

        session_factory = get_session_factory()
        with session_factory() as db:
            asset = VideoAsset(
                title="第一课视频",
                status="ready",
                provider_file_id="vod-file-1",
                playback_url="https://vod.example.com/adaptive.m3u8",
                duration_seconds=600,
            )
            db.add(asset)
            db.commit()
            asset_id = asset.id

        lesson = client.post(
            f"/admin/courses/{course_id}/lessons",
            data={
                "csrf_token": csrf_token,
                "title": "第一课",
                "description": "开始学习",
                "sort_order": "10",
                "video_asset_id": asset_id,
                "is_preview": "true",
            },
            follow_redirects=False,
        )
        assert lesson.status_code == 303

        published = client.post(
            f"/admin/courses/{course_id}/publish",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert published.status_code == 303

        with session_factory() as db:
            course = db.get(Course, course_id)
            assert course is not None
            assert course.status == "published"
            assert course.keywords == ["AI", "课程"]
            assert course.lessons[0].status == "published"
            assert course.lessons[0].is_preview is True
            lesson_id = course.lessons[0].id

        deleted = client.post(
            f"/admin/courses/{course_id}/lessons/{lesson_id}/delete",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        archived = client.post(
            f"/admin/media/{asset_id}/archive",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert archived.status_code == 303

        with session_factory() as db:
            course = db.get(Course, course_id)
            asset = db.get(VideoAsset, asset_id)
            assert course is not None and asset is not None
            assert course.status == "draft"
            assert course.lessons == []
            assert asset.status == "archived"


def test_vod_upload_signature_matches_tencent_algorithm() -> None:
    signature = build_upload_signature(
        secret_id="secret-id",
        secret_key="secret-key",
        session_context='{"asset_id":"asset-1"}',
        sub_app_id=123,
        procedure="adaptive-hls",
        storage_region="ap-beijing",
        current_timestamp=1_700_000_000,
        random_value=42,
        lifetime_seconds=600,
    )

    decoded = base64.b64decode(signature.value)
    digest, original = decoded[:20], decoded[20:].decode()
    expected = hmac.new(b"secret-key", original.encode(), hashlib.sha1).digest()

    assert digest == expected
    assert original == signature.original
    assert "secretId=secret-id" in original
    assert "sourceContext=%7B%22asset_id%22%3A%22asset-1%22%7D" in original
    assert "sessionContext=%7B%22asset_id%22%3A%22asset-1%22%7D" in original
    assert signature.expires_at == 1_700_000_600


def test_upload_signature_fails_closed_without_vod_credentials() -> None:
    with TestClient(app) as client:
        login_admin(client)
        session_factory = get_session_factory()
        with session_factory() as db:
            asset = VideoAsset(title="未配置上传", session_context='{"asset_id":"pending"}')
            db.add(asset)
            db.commit()
            asset_id = asset.id

        response = client.get(f"/admin/media/{asset_id}/upload-signature")

    assert response.status_code == 503
    assert "VOD" in response.json()["detail"]


def test_vod_callbacks_are_idempotent_and_mark_asset_ready(monkeypatch) -> None:
    import backend.app.modules.media.router as media_router

    monkeypatch.setattr(
        media_router,
        "get_settings",
        lambda: SimpleNamespace(tencent_vod_callback_token="callback-secret"),
    )
    session_factory = get_session_factory()
    with session_factory() as db:
        asset = VideoAsset(title="回调视频")
        db.add(asset)
        db.flush()
        asset.session_context = json.dumps({"asset_id": asset.id}, separators=(",", ":"))
        asset_id = asset.id
        session_context = asset.session_context
        db.commit()

    upload_payload = {
        "EventId": "upload-event-1",
        "EventType": "NewFileUpload",
        "FileUploadEvent": {
            "FileId": "vod-file-100",
            "MediaUrl": "https://vod.example.com/source.mp4",
            "CoverUrl": "https://vod.example.com/cover.jpg",
            "SessionContext": session_context,
        },
    }
    procedure_payload = {
        "EventId": "procedure-event-1",
        "EventType": "ProcedureStateChanged",
        "ProcedureStateChangeEvent": {
            "FileId": "vod-file-100",
            "Status": "FINISH",
            "SessionContext": session_context,
            "MetaData": {"VideoDuration": 601.4},
            "MediaProcessResultSet": [
                {
                    "Type": "AdaptiveDynamicStreaming",
                    "AdaptiveDynamicStreamingTask": {
                        "Status": "SUCCESS",
                        "Output": {"Url": "https://vod.example.com/adaptive/index.m3u8"},
                    },
                }
            ],
        },
    }

    with TestClient(app) as client:
        upload = client.post(
            "/api/v1/media/vod/callback",
            json=upload_payload,
            headers={"X-Avatar-Callback-Token": "callback-secret"},
        )
        duplicate = client.post(
            "/api/v1/media/vod/callback",
            json=upload_payload,
            headers={"X-Avatar-Callback-Token": "callback-secret"},
        )
        completed = client.post(
            "/api/v1/media/vod/callback",
            json=procedure_payload,
            headers={"X-Avatar-Callback-Token": "callback-secret"},
        )
        conflict_payload = {**procedure_payload, "EventType": "FileDeleted"}
        conflict = client.post(
            "/api/v1/media/vod/callback",
            json=conflict_payload,
            headers={"X-Avatar-Callback-Token": "callback-secret"},
        )

    assert upload.status_code == 200
    assert duplicate.json()["message"] == "duplicate"
    assert completed.status_code == 200
    assert conflict.status_code == 409
    with session_factory() as db:
        asset = db.get(VideoAsset, asset_id)
        assert asset is not None
        assert asset.status == "ready"
        assert asset.provider_file_id == "vod-file-100"
        assert asset.playback_url == "https://vod.example.com/adaptive/index.m3u8"
        assert asset.duration_seconds == 601
        events = list(db.query(WebhookEvent).all())
        assert len(events) == 2
