from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from openai import OpenAI
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.models import ChatReservation, Course, LLMConfig, Message, User
from backend.app.modules.entitlements.service import user_has_course_access
from backend.app.security import decrypt_value

_reservation_lock = threading.Lock()
_POSTGRES_CHAT_LOCK_ID = 8_240_812


class ChatCapacityError(ValueError):
    pass


class ChatTicketError(ValueError):
    pass


@dataclass(frozen=True)
class LLMAnswer:
    answer: str
    course_ids: list[str]
    degraded: bool = False


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _ticket_hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def _lock_chat_capacity(db: Session) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _POSTGRES_CHAT_LOCK_ID},
        )


def _expire_reservations(db: Session, now: datetime) -> None:
    expired = list(
        db.scalars(
            select(ChatReservation).where(
                ChatReservation.status.in_(["reserved", "active"]),
                ChatReservation.expires_at <= now,
            )
        )
    )
    for reservation in expired:
        reservation.status = "expired"


def active_chat_count(db: Session, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    return int(
        db.scalar(
            select(func.count(ChatReservation.id)).where(
                ChatReservation.status.in_(["reserved", "active"]),
                ChatReservation.expires_at > current,
            )
        )
        or 0
    )


def find_candidate_courses(db: Session, prompt: str, limit: int = 12) -> list[Course]:
    normalized_prompt = prompt.casefold()
    prompt_parts = [
        part for part in re.split(r"[\s,，。！？!?、;；:：]+", normalized_prompt) if len(part) >= 2
    ]
    courses = list(
        db.scalars(
            select(Course)
            .where(Course.status == "published")
            .order_by(Course.sort_order, Course.created_at)
        )
    )
    scored: list[tuple[int, Course]] = []
    for course in courses:
        title = course.title.casefold()
        subtitle = course.subtitle.casefold()
        description = course.description.casefold()
        score = 0
        if title and title in normalized_prompt:
            score += 12
        for keyword in course.keywords:
            normalized_keyword = str(keyword).casefold().strip()
            if normalized_keyword and normalized_keyword in normalized_prompt:
                score += 6 + min(len(normalized_keyword), 6)
        for part in prompt_parts:
            if part in title:
                score += 4
            elif part in subtitle:
                score += 2
            elif part in description:
                score += 1
        if score:
            scored.append((score, course))
    scored.sort(key=lambda item: (-item[0], item[1].sort_order, item[1].created_at))
    return [course for _, course in scored[:limit]]


def create_chat_reservation(
    db: Session,
    *,
    user: User,
    prompt: str,
    conversation_id: str | None,
    settings: Settings | None = None,
) -> tuple[str, ChatReservation]:
    configured = settings or get_settings()
    now = datetime.now(UTC)
    candidates = find_candidate_courses(db, prompt)
    with _reservation_lock:
        _lock_chat_capacity(db)
        _expire_reservations(db, now)
        if active_chat_count(db, now) >= configured.llm_concurrency_limit:
            raise ChatCapacityError("当前问答人数较多，请稍后再试；课程学习不受影响")
        ticket = secrets.token_urlsafe(32)
        reservation = ChatReservation(
            ticket_hash=_ticket_hash(ticket),
            user_id=user.id,
            conversation_id=conversation_id,
            prompt=prompt,
            candidate_course_ids=[course.id for course in candidates],
            expires_at=now + timedelta(seconds=configured.chat_reservation_ttl_seconds),
        )
        db.add(reservation)
        db.commit()
    return ticket, reservation


def consume_chat_reservation(
    db: Session,
    *,
    user: User,
    ticket: str,
    settings: Settings | None = None,
) -> ChatReservation:
    configured = settings or get_settings()
    reservation = db.scalar(
        select(ChatReservation).where(ChatReservation.ticket_hash == _ticket_hash(ticket))
    )
    now = datetime.now(UTC)
    if not reservation or reservation.user_id != user.id:
        raise ChatTicketError("问答票据不存在")
    if reservation.status != "reserved" or _utc(reservation.expires_at) <= now:
        if reservation.status == "reserved":
            reservation.status = "expired"
            db.commit()
        raise ChatTicketError("问答票据已使用或过期")
    reservation.status = "active"
    reservation.expires_at = now + timedelta(
        seconds=max(
            configured.chat_reservation_ttl_seconds,
            configured.llm_request_timeout_seconds + 30,
        )
    )
    db.commit()
    return reservation


def _fallback_answer(candidates: list[Course]) -> LLMAnswer:
    if candidates:
        return LLMAnswer(
            answer="根据你的问题，我先找到几门相关课程。你可以点开查看简介和试听课节，再决定从哪门开始。",
            course_ids=[course.id for course in candidates[:3]],
            degraded=True,
        )
    return LLMAnswer(
        answer="我暂时没有匹配到相关课程。你可以换个说法，或者先到“课程”查看全部内容。",
        course_ids=[],
        degraded=True,
    )


def _parse_json_answer(content: str) -> dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized)
        normalized = re.sub(r"\s*```$", "", normalized)
    parsed = json.loads(normalized)
    if not isinstance(parsed, dict):
        raise ValueError("model output must be an object")
    return parsed


def generate_llm_answer(
    db: Session,
    *,
    user: User,
    prompt: str,
    candidates: list[Course],
    recent_messages: list[Message],
    settings: Settings | None = None,
) -> LLMAnswer:
    configured = settings or get_settings()
    llm_config = db.scalar(
        select(LLMConfig).where(LLMConfig.is_active.is_(True)).order_by(LLMConfig.updated_at.desc())
    )
    if not llm_config:
        return _fallback_answer(candidates)
    try:
        api_key = decrypt_value(
            llm_config.api_key_ciphertext,
            purpose="llm-api-key",
            key_material=configured.llm_encryption_key,
            settings=configured,
        )
        candidate_payload = [
            {
                "id": course.id,
                "title": course.title,
                "subtitle": course.subtitle,
                "description": course.description,
                "keywords": course.keywords,
                "locked": not user_has_course_access(db, user.id, course.id),
            }
            for course in candidates
        ]
        system_prompt = llm_config.system_prompt.strip() or "你是一个简洁、可靠的课程助手。"
        policy = (
            "\n你只能从给定候选课程中推荐，不得虚构课程或课程ID。"
            "未购课程只允许使用公开简介，不得推测或泄露付费课节内容。"
            "输出严格JSON：{\"answer\":\"回答\",\"course_ids\":[\"候选ID\"]}，"
            "course_ids最多3个。候选课程如下："
            + json.dumps(candidate_payload, ensure_ascii=False, separators=(",", ":"))
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt + policy}]
        for message in recent_messages[-20:]:
            if message.role in {"user", "assistant"}:
                messages.append({"role": message.role, "content": message.content})
        messages.append({"role": "user", "content": prompt})
        client = OpenAI(
            api_key=api_key,
            base_url=llm_config.base_url,
            timeout=configured.llm_request_timeout_seconds,
        )
        completion = client.chat.completions.create(
            model=llm_config.model_name,
            messages=messages,  # type: ignore[arg-type]
            temperature=llm_config.temperature_milli / 1000,
        )
        content = completion.choices[0].message.content or ""
        parsed = _parse_json_answer(content)
        answer = parsed.get("answer")
        raw_ids = parsed.get("course_ids", [])
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("model answer is empty")
        allowed = {course.id for course in candidates}
        ranked: list[str] = []
        if isinstance(raw_ids, list):
            for course_id in raw_ids:
                if isinstance(course_id, str) and course_id in allowed and course_id not in ranked:
                    ranked.append(course_id)
                if len(ranked) == 3:
                    break
        if candidates and not ranked:
            ranked = [course.id for course in candidates[:3]]
        return LLMAnswer(answer=answer.strip(), course_ids=ranked)
    except Exception:
        return _fallback_answer(candidates)


def split_answer_chunks(answer: str, chunk_size: int = 12) -> list[str]:
    return [answer[index : index + chunk_size] for index in range(0, len(answer), chunk_size)]


def test_llm_configuration(
    llm_config: LLMConfig,
    settings: Settings | None = None,
) -> str:
    configured = settings or get_settings()
    api_key = decrypt_value(
        llm_config.api_key_ciphertext,
        purpose="llm-api-key",
        key_material=configured.llm_encryption_key,
        settings=configured,
    )
    client = OpenAI(
        api_key=api_key,
        base_url=llm_config.base_url,
        timeout=configured.llm_request_timeout_seconds,
    )
    completion = client.chat.completions.create(
        model=llm_config.model_name,
        messages=[{"role": "user", "content": "只回复 OK"}],
        temperature=0,
        max_tokens=8,
    )
    return (completion.choices[0].message.content or "").strip()
