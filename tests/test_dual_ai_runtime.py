from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.db import get_session_factory
from backend.app.main import app
from backend.app.models import (
    AIModelBinding,
    ChatReservation,
    Conversation,
    LLMConfig,
    Message,
    User,
)
from backend.app.modules.capabilities.models import CapabilityEntitlement
from backend.app.modules.chat.service import generate_llm_answer
from backend.app.modules.knowledge.models import AIRun
from backend.app.modules.knowledge.service import (
    create_knowledge_source,
    export_source_markdown,
    import_cleaned_markdown,
    publish_knowledge_import,
    review_knowledge_unit,
)
from backend.app.security import encrypt_value, issue_user_token


def _headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_user_token(user_id)}"}


def _seed_user(*capabilities: str) -> str:
    session_factory = get_session_factory()
    now = datetime.now(UTC) - timedelta(minutes=1)
    with session_factory() as db:
        user = User(nickname="双入口测试")
        db.add(user)
        db.flush()
        for code in capabilities:
            db.add(
                CapabilityEntitlement(
                    user_id=user.id,
                    phone_hash=f"{user.id}:{code}",
                    capability_code=code,
                    effective_at=now,
                    expires_at=now + timedelta(days=30),
                )
            )
        db.commit()
        return user.id


def test_chat_membership_is_checked_before_capacity() -> None:
    user_id = _seed_user()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "你好", "mode": "qa"},
            headers=_headers(user_id),
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "capability_locked"


def test_ten_mixed_ai_reservations_are_allowed_and_eleventh_is_rejected(
    monkeypatch,
) -> None:
    import backend.app.modules.chat.service as chat_service

    user_id = _seed_user("chat_qa", "copywriting")
    limited = get_settings().model_copy(update={"llm_concurrency_limit": 10})
    monkeypatch.setattr(chat_service, "get_settings", lambda: limited)
    with TestClient(app) as client:
        accepted = [
            client.post(
                "/api/v1/chat/reservations",
                json={
                    "prompt": f"混合请求 {index}",
                    "mode": "qa" if index % 2 == 0 else "copywriting",
                },
                headers=_headers(user_id),
            )
            for index in range(10)
        ]
        rejected = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "第十一个请求", "mode": "qa"},
            headers=_headers(user_id),
        )

    assert all(response.status_code == 200 for response in accepted)
    assert rejected.status_code == 429
    assert rejected.json()["detail"]["code"] == "ai_capacity_full"


def test_qa_and_copywriting_use_separate_conversations_and_copy_has_no_cards() -> None:
    user_id = _seed_user("chat_qa", "copywriting")
    with TestClient(app) as client:
        qa_reserved = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "先问一个问题", "mode": "qa"},
            headers=_headers(user_id),
        )
        assert qa_reserved.status_code == 200
        qa_stream = client.get(
            f"/api/v1/chat/stream/{qa_reserved.json()['ticket']}",
            headers=_headers(user_id),
        )
        assert qa_stream.status_code == 200

        session_factory = get_session_factory()
        with session_factory() as db:
            qa_conversation = db.query(Conversation).one()
            qa_conversation_id = qa_conversation.id
            assert qa_conversation.mode == "qa"

        mismatched = client.post(
            "/api/v1/chat/reservations",
            json={
                "prompt": "帮我写一段",
                "mode": "copywriting",
                "conversation_id": qa_conversation_id,
            },
            headers=_headers(user_id),
        )
        assert mismatched.status_code == 409
        assert mismatched.json()["detail"]["code"] == "conversation_mode_mismatch"

        copy_reserved = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "帮我写一段朋友圈", "mode": "copywriting"},
            headers=_headers(user_id),
        )
        copy_stream = client.get(
            f"/api/v1/chat/stream/{copy_reserved.json()['ticket']}",
            headers=_headers(user_id),
        )

    assert copy_reserved.status_code == 200
    assert '"mode":"copywriting"' in copy_stream.text
    assert "event: recommendations" not in copy_stream.text
    session_factory = get_session_factory()
    with session_factory() as db:
        modes = sorted(item.mode for item in db.query(Conversation).all())
        assert modes == ["copywriting", "qa"]
        copy_run = db.query(AIRun).filter(AIRun.scene == "copywriting").one()
        assert copy_run.prompt_version == "avatar-chat/v2"


def test_profile_returns_both_capability_states() -> None:
    user_id = _seed_user("chat_qa")
    with TestClient(app) as client:
        response = client.get("/api/v1/me", headers=_headers(user_id))

    assert response.status_code == 200
    states = {item["code"]: item for item in response.json()["capabilities"]}
    assert states["chat_qa"]["status"] == "active"
    assert states["copywriting"]["status"] == "not_granted"


def test_primary_model_failure_falls_back_before_output(monkeypatch) -> None:
    import backend.app.modules.chat.service as chat_service

    user_id = _seed_user("chat_qa")
    settings = get_settings()
    calls: list[str] = []

    class FakeCompletions:
        def __init__(self, base_url: str):
            self.base_url = base_url

        def create(self, **_: object):
            calls.append(self.base_url)
            if "primary" in self.base_url:
                raise RuntimeError("primary unavailable")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"answer":"备用模型回答","course_ids":[]}'
                        )
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            )

    def fake_openai(**kwargs: object):
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=FakeCompletions(str(kwargs["base_url"])),
            )
        )

    monkeypatch.setattr(chat_service, "OpenAI", fake_openai)
    session_factory = get_session_factory()
    with session_factory() as db:
        encrypted = encrypt_value(
            "test-key",
            purpose="llm-api-key",
            key_material=settings.llm_encryption_key,
            settings=settings,
        )
        primary = LLMConfig(
            name="primary",
            provider="custom",
            capability="chat",
            base_url="https://primary.example.com/v1",
            model_name="primary-model",
            api_key_ciphertext=encrypted,
            is_active=True,
        )
        fallback = LLMConfig(
            name="fallback",
            provider="custom",
            capability="chat",
            base_url="https://fallback.example.com/v1",
            model_name="fallback-model",
            api_key_ciphertext=encrypted,
            is_active=True,
        )
        db.add_all([primary, fallback])
        db.flush()
        db.add(
            AIModelBinding(
                scene="qa",
                primary_model_id=primary.id,
                fallback_model_id=fallback.id,
            )
        )
        db.commit()
        user = db.get(User, user_id)
        assert user is not None
        result = generate_llm_answer(
            db,
            user=user,
            prompt="问题",
            candidates=[],
            recent_messages=[],
        )

    assert result.answer == "备用模型回答"
    assert result.model_config_id == fallback.id
    assert result.degraded is True
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 7
    assert calls == ["https://primary.example.com/v1", "https://fallback.example.com/v1"]


def test_chat_audit_is_durable_before_first_visible_delta(monkeypatch) -> None:
    import backend.app.modules.chat.router as chat_router

    user_id = _seed_user("chat_qa")
    observed: dict[str, bool] = {}

    def inspected_chunks(answer: str, chunk_size: int = 12) -> list[str]:
        session_factory = get_session_factory()
        with session_factory() as db:
            observed["run"] = db.query(AIRun).filter(AIRun.status == "completed").count() == 1
            observed["message"] = (
                db.query(Message).filter(Message.role == "assistant").count() == 1
            )
            observed["reservation"] = (
                db.query(ChatReservation).filter(ChatReservation.status == "completed").count()
                == 1
            )
        return [answer]

    monkeypatch.setattr(chat_router, "split_answer_chunks", inspected_chunks)
    with TestClient(app) as client:
        reserved = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "数据库审计什么时候落库", "mode": "qa"},
            headers=_headers(user_id),
        )
        streamed = client.get(
            f"/api/v1/chat/stream/{reserved.json()['ticket']}",
            headers=_headers(user_id),
        )

    assert streamed.status_code == 200
    assert "event: delta" in streamed.text
    assert observed == {"run": True, "message": True, "reservation": True}


def test_chat_failure_records_failed_ai_run(monkeypatch) -> None:
    import backend.app.modules.chat.router as chat_router

    user_id = _seed_user("chat_qa")

    def fail_retrieval(*_: object, **__: object):
        raise RuntimeError("retrieval exploded")

    monkeypatch.setattr(chat_router, "retrieve_runtime_knowledge", fail_retrieval)
    with TestClient(app) as client:
        reserved = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "触发失败审计", "mode": "qa"},
            headers=_headers(user_id),
        )
        streamed = client.get(
            f"/api/v1/chat/stream/{reserved.json()['ticket']}",
            headers=_headers(user_id),
        )

    assert streamed.status_code == 200
    assert "event: error" in streamed.text
    session_factory = get_session_factory()
    with session_factory() as db:
        run = db.query(AIRun).one()
        reservation = db.query(ChatReservation).one()
        assert run.status == "failed"
        assert run.error_message == "RuntimeError"
        assert reservation.status == "failed"


def test_pure_qa_stream_returns_stored_answer_and_images_without_model(monkeypatch) -> None:
    import backend.app.modules.chat.router as chat_router
    import backend.app.modules.chat.service as chat_service

    user_id = _seed_user("chat_qa")
    settings = get_settings().model_copy(update={"knowledge_injection_enabled": True})
    monkeypatch.setattr(chat_service, "get_settings", lambda: settings)

    session_factory = get_session_factory()
    with session_factory() as db:
        source = create_knowledge_source(
            db,
            title="课程有效期纯 QA",
            source_type="pure_qa",
            visibility="public",
            raw_content="课程报名后可以看多久？课程开通后可在会员有效期内反复观看。",
            confirmed_facts="课程开通后可在会员有效期内反复观看",
        )
        exported = export_source_markdown(db, source.id)
        cleaned = exported.replace(
            "units: []",
            """units:
  - local_id: "QA-STREAM-001"
    type: "qa"
    question: "课程报名后可以看多久？"
    answer: "课程开通后可在会员有效期内反复观看。"
    aliases: ["课程有效期多久"]
    keywords: ["有效期"]
    channels: []
    source_evidence: "课程开通后可在会员有效期内反复观看"
    confirmation: "confirmed"
    images: [{"url":"https://cdn.example.com/qa/validity.png","alt_text":"有效期示意图"}]""",
        )
        imported = import_cleaned_markdown(db, markdown=cleaned).knowledge_import
        unit = imported.units[0]
        unit_id = unit.id
        review_knowledge_unit(db, unit_id=unit.id, decision="approved")
        publish_knowledge_import(db, import_id=imported.id)

    def model_must_not_run(*_: object, **__: object):
        raise AssertionError("strict QA must bypass the model")

    monkeypatch.setattr(chat_router, "generate_llm_answer", model_must_not_run)
    with TestClient(app) as client:
        reserved = client.post(
            "/api/v1/chat/reservations",
            json={"prompt": "课程有效期多久", "mode": "qa"},
            headers=_headers(user_id),
        )
        streamed = client.get(
            f"/api/v1/chat/stream/{reserved.json()['ticket']}",
            headers=_headers(user_id),
        )

    assert streamed.status_code == 200
    deltas = re.findall(r"event: delta\ndata: (\{[^\n]+\})", streamed.text)
    answer = "".join(str(json.loads(item)["text"]) for item in deltas)
    assert answer == "课程开通后可在会员有效期内反复观看。"
    assert "event: images" in streamed.text
    assert "https://cdn.example.com/qa/validity.png" in streamed.text
    assert '"strict_qa":true' in streamed.text
    with session_factory() as db:
        run = db.query(AIRun).one()
        assert run.model_name == "pure_qa_store"
        assert run.prompt_version == "avatar-strict-qa/v1"
        assert run.retrieved_unit_ids == [unit_id]
