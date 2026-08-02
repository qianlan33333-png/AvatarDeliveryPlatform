from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.db import get_session_factory
from backend.app.models import ChatReservation, Conversation, Course, LLMConfig, Message, User
from backend.app.modules.api.dependencies import CurrentUser, DBSession
from backend.app.modules.chat.service import (
    CapabilityLockedError,
    ChatCapacityError,
    ChatTicketError,
    LLMAnswer,
    consume_chat_reservation,
    create_chat_reservation,
    generate_llm_answer,
    retrieve_runtime_knowledge,
    split_answer_chunks,
)
from backend.app.modules.entitlements.service import user_has_course_access
from backend.app.modules.knowledge.models import AIRun

router = APIRouter(prefix="/api/v1/chat", tags=["mini-program-chat"])


class ChatReservationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)
    mode: Literal["qa", "copywriting"] = "qa"


def _sse(event: str, data: dict[str, Any]) -> str:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"


@router.post("/reservations")
def reserve_chat(
    payload: ChatReservationRequest,
    db: DBSession,
    user: CurrentUser,
):
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt cannot be empty")
    if payload.conversation_id:
        conversation = db.get(Conversation, payload.conversation_id)
        if not conversation or conversation.user_id != user.id:
            raise HTTPException(status_code=404, detail="conversation not found")
        if conversation.mode != payload.mode:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "conversation_mode_mismatch",
                    "message": "问答和话术会话不能混用",
                },
            )
    try:
        ticket, reservation = create_chat_reservation(
            db,
            user=user,
            prompt=prompt,
            conversation_id=payload.conversation_id,
            mode=payload.mode,
        )
    except CapabilityLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "capability_locked", "message": str(exc)},
        ) from exc
    except ChatCapacityError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "ai_capacity_full", "message": str(exc)},
        ) from exc
    return {
        "ticket": ticket,
        "expires_at": reservation.expires_at,
        "candidate_count": len(reservation.candidate_course_ids),
        "mode": reservation.mode,
    }


def _stream_reservation(reservation_id: str, user_id: str):
    session_factory = get_session_factory()
    with session_factory() as db:
        reservation = db.get(ChatReservation, reservation_id)
        if not reservation or reservation.user_id != user_id:
            yield _sse("error", {"message": "问答票据不存在"})
            return
        run_started_at = perf_counter()
        conversation_id: str | None = reservation.conversation_id
        retrieved_unit_ids: list[str] = []
        candidate_course_ids = list(reservation.candidate_course_ids or [])
        try:
            conversation = (
                db.get(Conversation, reservation.conversation_id)
                if reservation.conversation_id
                else None
            )
            if not conversation:
                conversation = Conversation(
                    user_id=user_id,
                    title=reservation.prompt[:60] or "新对话",
                    mode=reservation.mode,
                )
                db.add(conversation)
                db.flush()
                reservation.conversation_id = conversation.id
            conversation_id = conversation.id
            recent = list(
                reversed(
                    list(
                        db.scalars(
                            select(Message)
                            .where(Message.conversation_id == conversation.id)
                            .order_by(Message.created_at.desc())
                            .limit(20)
                        )
                    )
                )
            )
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=reservation.prompt,
                )
            )
            db.commit()
            yield _sse("ready", {"conversation_id": conversation.id})

            candidates_by_id = {
                course.id: course
                for course in db.scalars(
                    select(Course).where(
                        Course.id.in_(reservation.candidate_course_ids),
                        Course.status == "published",
                    )
                )
            }
            candidates = [
                candidates_by_id[course_id]
                for course_id in reservation.candidate_course_ids
                if course_id in candidates_by_id
            ]
            user = db.get(User, user_id)
            if user is None:
                raise RuntimeError("user not found")
            runtime_knowledge = retrieve_runtime_knowledge(
                db,
                user=user,
                prompt=reservation.prompt,
                mode=reservation.mode,
            )
            reservation.knowledge_unit_ids = runtime_knowledge.unit_ids
            retrieved_unit_ids = list(runtime_knowledge.unit_ids)
            strict_qa = reservation.mode == "qa" and bool(runtime_knowledge.strict_answer)
            if strict_qa:
                result = LLMAnswer(
                    answer=runtime_knowledge.strict_answer,
                    course_ids=[course.id for course in candidates[:3]],
                )
            else:
                result = generate_llm_answer(
                    db,
                    user=user,
                    prompt=reservation.prompt,
                    candidates=candidates,
                    recent_messages=recent,
                    mode=reservation.mode,
                    knowledge_context=runtime_knowledge.context,
                )
            recommended = [
                candidates_by_id[course_id]
                for course_id in result.course_ids
                if course_id in candidates_by_id
            ][:3] if reservation.mode == "qa" else []
            cards = [
                {
                    "id": course.id,
                    "title": course.title,
                    "subtitle": course.subtitle,
                    "description": course.description,
                    "cover_url": course.cover_url,
                    "locked": not user_has_course_access(db, user_id, course.id),
                }
                for course in recommended
            ]
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=result.answer,
                    recommended_course_ids=[course.id for course in recommended],
                    knowledge_unit_ids=runtime_knowledge.unit_ids,
                )
            )
            reservation.status = "completed"
            reservation.completed_at = datetime.now(UTC)
            model_name = "pure_qa_store" if strict_qa else ""
            if result.model_config_id:
                model_config = db.get(LLMConfig, result.model_config_id)
                model_name = model_config.model_name if model_config else ""
            db.add(
                AIRun(
                    scene=reservation.mode,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    model_config_id=result.model_config_id,
                    model_name=model_name,
                    prompt_version=(
                        "avatar-strict-qa/v1" if strict_qa else "avatar-chat/v2"
                    ),
                    retrieved_unit_ids=runtime_knowledge.unit_ids,
                    candidate_course_ids=[course.id for course in candidates],
                    status="completed",
                    degraded=result.degraded or runtime_knowledge.degraded,
                    duration_ms=int((perf_counter() - run_started_at) * 1000),
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                )
            )
            # Persist the answer and immutable run audit before exposing the first
            # visible model token. A disconnect can no longer leave an unaudited
            # answer that was already shown to the user.
            db.commit()
            for chunk in split_answer_chunks(result.answer):
                yield _sse("delta", {"text": chunk})
            if strict_qa and runtime_knowledge.images:
                yield _sse("images", {"items": list(runtime_knowledge.images)})
            if cards and reservation.mode == "qa":
                yield _sse("recommendations", {"items": cards})
            yield _sse(
                "done",
                {
                    "degraded": result.degraded or runtime_knowledge.degraded,
                    "mode": reservation.mode,
                    "strict_qa": strict_qa,
                },
            )
        except Exception as exc:
            db.rollback()
            failed = db.get(ChatReservation, reservation_id)
            if failed:
                failed.status = "failed"
                failed.completed_at = datetime.now(UTC)
                db.add(
                    AIRun(
                        scene=failed.mode,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        model_name="",
                        prompt_version="avatar-chat/v2",
                        retrieved_unit_ids=retrieved_unit_ids,
                        candidate_course_ids=candidate_course_ids,
                        status="failed",
                        degraded=True,
                        duration_ms=int((perf_counter() - run_started_at) * 1000),
                        error_message=exc.__class__.__name__,
                    )
                )
                db.commit()
            label = "话术" if reservation.mode == "copywriting" else "问答"
            yield _sse("error", {"message": f"{label}暂时不可用，请稍后重试"})


@router.get("/stream/{ticket}")
def stream_chat(ticket: str, db: DBSession, user: CurrentUser):
    try:
        reservation = consume_chat_reservation(db, user=user, ticket=ticket)
    except ChatTicketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StreamingResponse(
        _stream_reservation(reservation.id, user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )
