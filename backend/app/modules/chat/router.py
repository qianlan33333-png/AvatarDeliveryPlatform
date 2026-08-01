from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.db import get_session_factory
from backend.app.models import ChatReservation, Conversation, Course, Message, User
from backend.app.modules.api.dependencies import CurrentUser, DBSession
from backend.app.modules.chat.service import (
    ChatCapacityError,
    ChatTicketError,
    consume_chat_reservation,
    create_chat_reservation,
    generate_llm_answer,
    split_answer_chunks,
)
from backend.app.modules.entitlements.service import user_has_course_access

router = APIRouter(prefix="/api/v1/chat", tags=["mini-program-chat"])


class ChatReservationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)


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
    try:
        ticket, reservation = create_chat_reservation(
            db,
            user=user,
            prompt=prompt,
            conversation_id=payload.conversation_id,
        )
    except ChatCapacityError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "ai_capacity_full", "message": str(exc)},
        ) from exc
    return {
        "ticket": ticket,
        "expires_at": reservation.expires_at,
        "candidate_count": len(reservation.candidate_course_ids),
    }


def _stream_reservation(reservation_id: str, user_id: str):
    session_factory = get_session_factory()
    with session_factory() as db:
        reservation = db.get(ChatReservation, reservation_id)
        if not reservation or reservation.user_id != user_id:
            yield _sse("error", {"message": "问答票据不存在"})
            return
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
                )
                db.add(conversation)
                db.flush()
                reservation.conversation_id = conversation.id
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
            result = generate_llm_answer(
                db,
                user=user,
                prompt=reservation.prompt,
                candidates=candidates,
                recent_messages=recent,
            )
            for chunk in split_answer_chunks(result.answer):
                yield _sse("delta", {"text": chunk})

            recommended = [
                candidates_by_id[course_id]
                for course_id in result.course_ids
                if course_id in candidates_by_id
            ][:3]
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
                )
            )
            reservation.status = "completed"
            reservation.completed_at = datetime.now(UTC)
            db.commit()
            if cards:
                yield _sse("recommendations", {"items": cards})
            yield _sse("done", {"degraded": result.degraded})
        except Exception:
            db.rollback()
            failed = db.get(ChatReservation, reservation_id)
            if failed:
                failed.status = "failed"
                failed.completed_at = datetime.now(UTC)
                db.commit()
            yield _sse("error", {"message": "问答暂时不可用，请稍后重试"})


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
