from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
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


def _uuid_str() -> str:
    return str(uuid.uuid4())


EmbeddingColumnType = JSON().with_variant(Vector(1024), "postgresql")


class KnowledgeTimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KnowledgeSource(Base, KnowledgeTimestampMixin):
    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="public")
    course_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    processing_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_ready", index=True
    )
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_version_number: Mapped[int | None] = mapped_column(Integer)

    versions: Mapped[list[KnowledgeSourceVersion]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="KnowledgeSourceVersion.version_number",
    )
    imports: Mapped[list[KnowledgeImport]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    units: Mapped[list[KnowledgeUnit]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class KnowledgeSourceVersion(Base):
    __tablename__ = "knowledge_source_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "version_number", name="uq_knowledge_source_version"),
        UniqueConstraint("source_id", "source_sha256", name="uq_knowledge_source_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False)
    course_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confirmed_facts: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pending_confirmation_points: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cleaning_requirements: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prohibited_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_authorization: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(80), nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped[KnowledgeSource] = relationship(back_populates="versions")
    imports: Mapped[list[KnowledgeImport]] = relationship(back_populates="source_version")
    units: Mapped[list[KnowledgeUnit]] = relationship(back_populates="source_version")


class KnowledgeImport(Base):
    __tablename__ = "knowledge_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_source_versions.id", ondelete="CASCADE")
    )
    source_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String(60), nullable=False, default="avatar-knowledge/v1"
    )
    processor: Mapped[str] = mapped_column(String(80), nullable=False, default="manual")
    processor_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default="unspecified"
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    raw_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    cleaned_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    imported_by: Mapped[str] = mapped_column(String(80), nullable=False, default="agent")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[KnowledgeSource] = relationship(back_populates="imports")
    source_version: Mapped[KnowledgeSourceVersion] = relationship(back_populates="imports")
    units: Mapped[list[KnowledgeUnit]] = relationship(
        back_populates="knowledge_import",
        cascade="all, delete-orphan",
        order_by="KnowledgeUnit.local_id",
    )


class KnowledgeUnit(Base, KnowledgeTimestampMixin):
    __tablename__ = "knowledge_units"
    __table_args__ = (
        UniqueConstraint("import_id", "local_id", name="uq_knowledge_import_local_unit"),
        Index("ix_knowledge_units_status_type", "status", "unit_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_source_versions.id", ondelete="CASCADE")
    )
    import_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_imports.id", ondelete="CASCADE")
    )
    local_id: Mapped[str] = mapped_column(String(80), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    standard_question: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    standard_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    channels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation: Mapped[str] = mapped_column(
        String(30), nullable=False, default="needs_confirmation"
    )
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="public")
    course_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    review_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[KnowledgeSource] = relationship(back_populates="units")
    source_version: Mapped[KnowledgeSourceVersion] = relationship(back_populates="units")
    knowledge_import: Mapped[KnowledgeImport] = relationship(back_populates="units")
    embeddings: Mapped[list[KnowledgeEmbedding]] = relationship(
        back_populates="unit", cascade="all, delete-orphan"
    )
    assets: Mapped[list[KnowledgeUnitAsset]] = relationship(
        back_populates="unit",
        cascade="all, delete-orphan",
        order_by="KnowledgeUnitAsset.sort_order",
    )


class KnowledgeUnitAsset(Base, KnowledgeTimestampMixin):
    __tablename__ = "knowledge_unit_assets"
    __table_args__ = (
        UniqueConstraint("unit_id", "asset_hash", name="uq_knowledge_unit_asset_hash"),
        Index("ix_knowledge_unit_assets_unit_status", "unit_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    unit_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_units.id", ondelete="CASCADE")
    )
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False, default="image")
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    public_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    asset_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    unit: Mapped[KnowledgeUnit] = relationship(back_populates="assets")


class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "unit_id",
            "model_name",
            "model_version",
            "content_hash",
            name="uq_knowledge_embedding_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    unit_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_units.id", ondelete="CASCADE"), index=True
    )
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingColumnType, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    unit: Mapped[KnowledgeUnit] = relationship(back_populates="embeddings")


class KnowledgeIndexJob(Base):
    __tablename__ = "knowledge_index_jobs"
    __table_args__ = (Index("ix_knowledge_index_jobs_status_created_at", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE")
    )
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_source_versions.id", ondelete="CASCADE")
    )
    import_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_imports.id", ondelete="CASCADE")
    )
    model_config_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )
    target_model_version: Mapped[str] = mapped_column(
        String(120), nullable=False, default="unconfigured", server_default="unconfigured"
    )
    job_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIRun(Base):
    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_runs_scene_created_at", "scene", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    scene: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    model_config_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )
    model_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    retrieved_unit_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    candidate_course_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="started")
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
