"""avatar knowledge foundations

Revision ID: a17b2c3d4e5f
Revises: 6c42b8f09e31
Create Date: 2026-08-02 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "a17b2c3d4e5f"
down_revision: str | None = "6c42b8f09e31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _embedding_type() -> sa.types.TypeEngine:
    if _is_postgresql():
        return Vector(1024)
    return sa.JSON()


def upgrade() -> None:
    if _is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("course_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("processing_status", sa.String(length=30), nullable=False),
        sa.Column("current_version_number", sa.Integer(), nullable=False),
        sa.Column("published_version_number", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_sources_status", "knowledge_sources", ["status"])
    op.create_index(
        "ix_knowledge_sources_processing_status", "knowledge_sources", ["processing_status"]
    )

    op.create_table(
        "knowledge_source_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("course_ids", sa.JSON(), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("confirmed_facts", sa.Text(), nullable=False),
        sa.Column("pending_confirmation_points", sa.Text(), nullable=False),
        sa.Column("cleaning_requirements", sa.Text(), nullable=False),
        sa.Column("prohibited_content", sa.Text(), nullable=False),
        sa.Column("source_authorization", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "version_number", name="uq_knowledge_source_version"),
        sa.UniqueConstraint("source_id", "source_sha256", name="uq_knowledge_source_hash"),
    )
    op.create_index(
        "ix_knowledge_source_versions_source_id", "knowledge_source_versions", ["source_id"]
    )

    op.create_table(
        "knowledge_imports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_number", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=60), nullable=False),
        sa.Column("processor", sa.String(length=80), nullable=False),
        sa.Column("processor_version", sa.String(length=120), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("raw_markdown", sa.Text(), nullable=False),
        sa.Column("cleaned_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("imported_by", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["knowledge_source_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_knowledge_imports_source_id", "knowledge_imports", ["source_id"])
    op.create_index("ix_knowledge_imports_status", "knowledge_imports", ["status"])

    op.create_table(
        "knowledge_units",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("local_id", sa.String(length=80), nullable=False),
        sa.Column("unit_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("standard_question", sa.String(length=500), nullable=False),
        sa.Column("standard_answer", sa.Text(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("source_evidence", sa.Text(), nullable=False),
        sa.Column("confirmation", sa.String(length=30), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("course_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["knowledge_source_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["import_id"], ["knowledge_imports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_id", "local_id", name="uq_knowledge_import_local_unit"),
    )
    op.create_index("ix_knowledge_units_source_id", "knowledge_units", ["source_id"])
    op.create_index("ix_knowledge_units_status_type", "knowledge_units", ["status", "unit_type"])

    op.create_table(
        "knowledge_unit_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("unit_id", sa.String(length=36), nullable=False),
        sa.Column("asset_type", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("public_url", sa.String(length=2000), nullable=False),
        sa.Column("alt_text", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("asset_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["knowledge_units.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("unit_id", "asset_hash", name="uq_knowledge_unit_asset_hash"),
    )
    op.create_index(
        "ix_knowledge_unit_assets_unit_status",
        "knowledge_unit_assets",
        ["unit_id", "status"],
    )

    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("unit_id", sa.String(length=36), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", _embedding_type(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["knowledge_units.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "unit_id",
            "model_name",
            "model_version",
            "content_hash",
            name="uq_knowledge_embedding_version",
        ),
    )
    op.create_index("ix_knowledge_embeddings_unit_id", "knowledge_embeddings", ["unit_id"])

    op.create_table(
        "knowledge_index_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("model_config_id", sa.String(length=36)),
        sa.Column("job_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["knowledge_source_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["import_id"], ["knowledge_imports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["model_config_id"], ["llm_configs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_key"),
    )
    op.create_index(
        "ix_knowledge_index_jobs_status_created_at",
        "knowledge_index_jobs",
        ["status", "created_at"],
    )

    op.create_table(
        "ai_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scene", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=36)),
        sa.Column("conversation_id", sa.String(length=36)),
        sa.Column("model_config_id", sa.String(length=36)),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("retrieved_unit_ids", sa.JSON(), nullable=False),
        sa.Column("candidate_course_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_config_id"], ["llm_configs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_runs_scene_created_at", "ai_runs", ["scene", "created_at"])
    op.create_index("ix_ai_runs_user_id", "ai_runs", ["user_id"])

    if _is_postgresql():
        op.execute(
            "CREATE INDEX ix_knowledge_embeddings_hnsw_cosine "
            "ON knowledge_embeddings USING hnsw (embedding vector_cosine_ops) "
            "WHERE is_active"
        )
        op.execute(
            "CREATE INDEX ix_knowledge_units_lexical_trgm ON knowledge_units USING gin "
            "((coalesce(title, '') || ' ' || coalesce(content, '')) gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_knowledge_units_qa_question_trgm ON knowledge_units USING gin "
            "(standard_question gin_trgm_ops) WHERE unit_type = 'qa' AND status = 'published'"
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS ix_knowledge_units_qa_question_trgm")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_units_lexical_trgm")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_embeddings_hnsw_cosine")
    op.drop_index("ix_ai_runs_user_id", table_name="ai_runs")
    op.drop_index("ix_ai_runs_scene_created_at", table_name="ai_runs")
    op.drop_table("ai_runs")
    op.drop_index(
        "ix_knowledge_index_jobs_status_created_at", table_name="knowledge_index_jobs"
    )
    op.drop_table("knowledge_index_jobs")
    op.drop_index("ix_knowledge_embeddings_unit_id", table_name="knowledge_embeddings")
    op.drop_table("knowledge_embeddings")
    op.drop_index(
        "ix_knowledge_unit_assets_unit_status", table_name="knowledge_unit_assets"
    )
    op.drop_table("knowledge_unit_assets")
    op.drop_index("ix_knowledge_units_status_type", table_name="knowledge_units")
    op.drop_index("ix_knowledge_units_source_id", table_name="knowledge_units")
    op.drop_table("knowledge_units")
    op.drop_index("ix_knowledge_imports_status", table_name="knowledge_imports")
    op.drop_index("ix_knowledge_imports_source_id", table_name="knowledge_imports")
    op.drop_table("knowledge_imports")
    op.drop_index(
        "ix_knowledge_source_versions_source_id", table_name="knowledge_source_versions"
    )
    op.drop_table("knowledge_source_versions")
    op.drop_index("ix_knowledge_sources_processing_status", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_status", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
