from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    nickname: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    avatar_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    phone_ciphertext: Mapped[str | None] = mapped_column(Text)
    phone_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    phone_last4: Mapped[str] = mapped_column(String(4), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identities: Mapped[list[WeChatIdentity]] = relationship(back_populates="user")
    entitlements: Mapped[list[CourseEntitlement]] = relationship(back_populates="user")


class WeChatIdentity(Base, TimestampMixin):
    __tablename__ = "wechat_identities"
    __table_args__ = (UniqueConstraint("appid", "openid", name="uq_wechat_appid_openid"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    appid: Mapped[str] = mapped_column(String(80), nullable=False)
    openid: Mapped[str] = mapped_column(String(128), nullable=False)
    unionid: Mapped[str | None] = mapped_column(String(128), index=True)

    user: Mapped[User] = relationship(back_populates="identities")


class Course(Base, TimestampMixin):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subtitle: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cover_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    lessons: Mapped[list[Lesson]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="Lesson.sort_order"
    )


class VideoAsset(Base, TimestampMixin):
    __tablename__ = "video_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), default="tencent_vod", nullable=False)
    provider_file_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    source_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    playback_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    cover_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending_upload", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    session_context: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    provider_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    lessons: Mapped[list[Lesson]] = relationship(back_populates="video_asset")


class Lesson(Base, TimestampMixin):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("course_id", "sort_order", name="uq_lesson_course_sort"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    video_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_assets.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_preview: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    course: Mapped[Course] = relationship(back_populates="lessons")
    video_asset: Mapped[VideoAsset | None] = relationship(back_populates="lessons")


class ProductCourseMapping(Base, TimestampMixin):
    __tablename__ = "product_course_mappings"
    __table_args__ = (
        UniqueConstraint("product_code", "course_id", name="uq_product_course"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    product_code: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CourseEntitlement(Base, TimestampMixin):
    __tablename__ = "course_entitlements"
    __table_args__ = (
        UniqueConstraint("phone_hash", "course_id", name="uq_entitlement_phone_course"),
        Index("ix_entitlement_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_order_id: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    last_event_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_action: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    last_event_id: Mapped[str] = mapped_column(String(160), default="", nullable=False)

    user: Mapped[User | None] = relationship(back_populates="entitlements")


class EntitlementEvent(Base):
    __tablename__ = "entitlement_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    event_id: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    order_id: Mapped[str] = mapped_column(String(160), nullable=False)
    product_code: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_ciphertext: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replayed_from_event_id: Mapped[str | None] = mapped_column(String(160))
    created_by: Mapped[str] = mapped_column(String(30), default="webhook", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookNonce(Base):
    __tablename__ = "webhook_nonces"
    __table_args__ = (UniqueConstraint("provider", "nonce", name="uq_webhook_provider_nonce"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    nonce: Mapped[str] = mapped_column(String(160), nullable=False)
    event_id: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(180), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="received", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_webhook_provider_event"),
    )


class PlaybackLease(Base, TimestampMixin):
    __tablename__ = "playback_leases"
    __table_args__ = (Index("ix_playback_active_expiry", "status", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    device_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningProgress(Base, TimestampMixin):
    __tablename__ = "learning_progress"
    __table_args__ = (UniqueConstraint("user_id", "lesson_id", name="uq_progress_user_lesson"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    position_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话", nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="qa", nullable=False, index=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_course_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    knowledge_unit_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMConfig(Base, TimestampMixin):
    __tablename__ = "llm_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="custom", nullable=False)
    capability: Mapped[str] = mapped_column(String(20), default="chat", nullable=False, index=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    temperature_milli: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AIModelBinding(Base, TimestampMixin):
    __tablename__ = "ai_model_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    scene: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    primary_model_id: Mapped[str] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="CASCADE"), nullable=False
    )
    fallback_model_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )
    pending_primary_model_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )


class ChatReservation(Base):
    __tablename__ = "chat_reservations"
    __table_args__ = (Index("ix_chat_reservation_status_expiry", "status", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    ticket_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    mode: Mapped[str] = mapped_column(String(20), default="qa", nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_course_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    knowledge_unit_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="reserved", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
