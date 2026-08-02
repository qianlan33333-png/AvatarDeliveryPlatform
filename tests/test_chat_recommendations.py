from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.db import get_session_factory
from backend.app.main import app
from backend.app.models import ChatReservation, Course, Lesson, LLMConfig, Message, User
from backend.app.modules.chat.service import generate_llm_answer
from backend.app.security import encrypt_value, issue_user_token


def _seed_chat_data() -> dict[str, str]:
    session_factory = get_session_factory()
    with session_factory() as db:
        user = User(nickname="问课用户")
        course = Course(
            title="内容表达训练",
            subtitle="让表达更清楚",
            description="公开课程简介",
            keywords=["表达", "内容"],
            status="published",
        )
        db.add_all([user, course])
        db.flush()
        db.add(
            Lesson(
                course_id=course.id,
                title="付费课节",
                description="这是绝不能暴露给未购买用户的付费秘密",
                sort_order=10,
                status="published",
            )
        )
        db.commit()
        return {"user_id": user.id, "course_id": course.id}


def _headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_user_token(user_id)}"}


def test_chat_reservation_streams_keyword_fallback_and_recommendation_card() -> None:
    data = _seed_chat_data()
    with TestClient(app) as client:
        reserved = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "我想提升内容表达能力"},
            headers=_headers(data["user_id"]),
        )
        assert reserved.status_code == 200
        assert reserved.json()["candidate_count"] == 1
        ticket = reserved.json()["ticket"]

        streamed = client.get(
            f"/api/v1/chat/stream/{ticket}",
            headers=_headers(data["user_id"]),
        )
        replayed = client.get(
            f"/api/v1/chat/stream/{ticket}",
            headers=_headers(data["user_id"]),
        )

    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "event: delta" in streamed.text
    assert "event: recommendations" in streamed.text
    assert data["course_id"] in streamed.text
    assert '"degraded":true' in streamed.text
    assert replayed.status_code == 409

    session_factory = get_session_factory()
    with session_factory() as db:
        assert db.query(Message).count() == 2
        reservation = db.query(ChatReservation).one()
        assert reservation.status == "completed"


def test_chat_capacity_is_reserved_before_stream(monkeypatch) -> None:
    import backend.app.modules.chat.service as chat_service

    data = _seed_chat_data()
    limited = get_settings().model_copy(update={"llm_concurrency_limit": 1})
    monkeypatch.setattr(chat_service, "get_settings", lambda: limited)
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "表达课程"},
            headers=_headers(data["user_id"]),
        )
        second = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "再问一个问题"},
            headers=_headers(data["user_id"]),
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "ai_capacity_full"


def test_model_can_only_return_candidate_ids_and_never_sees_paid_lesson(monkeypatch) -> None:
    import backend.app.modules.chat.service as chat_service

    data = _seed_chat_data()
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            content = (
                '{"answer":"推荐这门表达课程",'
                f'"course_ids":["invented-course","{data["course_id"]}"]}}'
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(chat_service, "OpenAI", lambda **_: fake_client)

    session_factory = get_session_factory()
    with session_factory() as db:
        settings = get_settings()
        db.add(
            LLMConfig(
                name="测试模型",
                base_url="https://model.example.com/v1",
                model_name="test-model",
                api_key_ciphertext=encrypt_value(
                    "test-api-key",
                    purpose="llm-api-key",
                    key_material=settings.llm_encryption_key,
                    settings=settings,
                ),
                system_prompt="你是课程助手",
                is_active=True,
            )
        )
        db.commit()
        user = db.get(User, data["user_id"])
        course = db.get(Course, data["course_id"])
        assert user is not None and course is not None
        result = generate_llm_answer(
            db,
            user=user,
            prompt="表达课程",
            candidates=[course],
            recent_messages=[],
        )

    assert result.course_ids == [data["course_id"]]
    assert "invented-course" not in result.course_ids
    serialized_messages = str(captured["messages"])
    assert "公开课程简介" in serialized_messages
    assert "绝不能暴露" not in serialized_messages
    assert "extra_body" not in captured


def test_deepseek_v4_disables_thinking_for_predictable_json(monkeypatch) -> None:
    import backend.app.modules.chat.service as chat_service

    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(chat_service, "OpenAI", lambda **_: fake_client)
    settings = get_settings()
    config = LLMConfig(
        name="DeepSeek V4 Flash",
        base_url="https://api.deepseek.com",
        model_name="deepseek-v4-flash",
        api_key_ciphertext=encrypt_value(
            "test-api-key",
            purpose="llm-api-key",
            key_material=settings.llm_encryption_key,
            settings=settings,
        ),
    )

    assert chat_service.test_llm_configuration(config) == "OK"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
