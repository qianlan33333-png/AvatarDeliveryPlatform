"""knowledge v2 six-dimensional model

Revision ID: e51f60718293
Revises: d40e5f607182
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

try:
    from pgvector.sqlalchemy import Vector
except ImportError:  # pragma: no cover
    Vector = None

revision: str = "e51f60718293"
down_revision: str | None = "d40e5f607182"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _embedding_type():
    return Vector(1024) if op.get_bind().dialect.name == "postgresql" and Vector else sa.JSON()


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _embedding_table(name: str, owner_column: str, owner_table: str, constraint: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(owner_column, sa.String(36), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("model_version", sa.String(120), nullable=False, server_default="default"),
        sa.Column("dimension", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", _embedding_type(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint([owner_column], [f"{owner_table}.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            owner_column, "model_name", "model_version", "content_hash", name=constraint
        ),
    )
    op.create_index(f"ix_{name}_{owner_column}", name, [owner_column])


def upgrade() -> None:
    with op.batch_alter_table("knowledge_sources") as batch:
        batch.add_column(
            sa.Column(
                "schema_version",
                sa.String(60),
                nullable=False,
                server_default="avatar-knowledge/v1",
            )
        )
    with op.batch_alter_table("knowledge_source_versions") as batch:
        batch.add_column(
            sa.Column(
                "schema_version",
                sa.String(60),
                nullable=False,
                server_default="avatar-knowledge/v1",
            )
        )

    op.create_table(
        "knowledge_slices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_version_id", sa.String(36), nullable=False),
        sa.Column("import_id", sa.String(36), nullable=False),
        sa.Column("local_id", sa.String(80), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_excerpt", sa.Text(), nullable=False),
        sa.Column("structured_content", sa.Text(), nullable=False),
        sa.Column("usage_context", sa.Text(), nullable=False, server_default=""),
        sa.Column("golden_sentence", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(30), nullable=False),
        sa.Column("primary_domain", sa.String(40), nullable=False),
        sa.Column("secondary_domains", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("topic_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("industry_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("audience_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_evidence", sa.Text(), nullable=False),
        sa.Column("confirmation", sa.String(30), nullable=False, server_default="confirmed"),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="internal"),
        sa.Column("course_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["knowledge_source_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["import_id"], ["knowledge_imports.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("import_id", "local_id", name="uq_knowledge_slice_import_local"),
    )
    op.create_index("ix_knowledge_slices_source_id", "knowledge_slices", ["source_id"])
    op.create_index("ix_knowledge_slices_import_id", "knowledge_slices", ["import_id"])
    op.create_index(
        "ix_knowledge_slices_dimension_status", "knowledge_slices", ["dimension", "status"]
    )
    op.create_index(
        "ix_knowledge_slices_domain_type", "knowledge_slices", ["primary_domain", "content_type"]
    )

    op.create_table(
        "knowledge_products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_version_id", sa.String(36), nullable=False),
        sa.Column("import_id", sa.String(36), nullable=False),
        sa.Column("local_id", sa.String(80), nullable=False),
        sa.Column("product_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_slice_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("primary_domain", sa.String(40), nullable=False),
        sa.Column("secondary_domains", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_evidence", sa.Text(), nullable=False),
        sa.Column("confirmation", sa.String(30), nullable=False, server_default="confirmed"),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="internal"),
        sa.Column("course_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["knowledge_source_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["import_id"], ["knowledge_imports.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("import_id", "local_id", name="uq_knowledge_product_import_local"),
    )
    op.create_index("ix_knowledge_products_source_id", "knowledge_products", ["source_id"])
    op.create_index("ix_knowledge_products_import_id", "knowledge_products", ["import_id"])
    op.create_index(
        "ix_knowledge_products_type_status", "knowledge_products", ["product_type", "status"]
    )

    op.create_table(
        "style_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_version_id", sa.String(36), nullable=False),
        sa.Column("import_id", sa.String(36), nullable=False),
        sa.Column("local_id", sa.String(80), nullable=False),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("audiences", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("purposes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_evidence", sa.Text(), nullable=False),
        sa.Column("confirmation", sa.String(30), nullable=False, server_default="confirmed"),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="internal"),
        sa.Column("course_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["knowledge_source_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["import_id"], ["knowledge_imports.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("import_id", "local_id", name="uq_style_entry_import_local"),
    )
    op.create_index("ix_style_entries_source_id", "style_entries", ["source_id"])
    op.create_index("ix_style_entries_import_id", "style_entries", ["import_id"])
    op.create_index("ix_style_entries_type_status", "style_entries", ["entry_type", "status"])

    op.create_table(
        "qa_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("standard_question", sa.String(500), nullable=False),
        sa.Column("fixed_answer", sa.Text(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("keywords", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="internal"),
        sa.Column("course_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(80), nullable=False, server_default="admin"),
        *_timestamps(),
    )
    op.create_index("ix_qa_entries_status_visibility", "qa_entries", ["status", "visibility"])
    op.create_table(
        "qa_entry_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("qa_entry_id", sa.String(36), nullable=False),
        sa.Column("public_url", sa.String(2000), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False, server_default=""),
        sa.Column("alt_text", sa.String(200), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("asset_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["qa_entry_id"], ["qa_entries.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("qa_entry_id", "asset_hash", name="uq_qa_entry_asset_hash"),
    )
    op.create_index("ix_qa_entry_assets_qa_entry_id", "qa_entry_assets", ["qa_entry_id"])

    _embedding_table(
        "knowledge_slice_embeddings", "slice_id", "knowledge_slices", "uq_slice_embedding_version"
    )
    _embedding_table(
        "knowledge_product_embeddings",
        "product_id",
        "knowledge_products",
        "uq_product_embedding_version",
    )
    _embedding_table(
        "style_entry_embeddings", "style_entry_id", "style_entries", "uq_style_embedding_version"
    )
    _embedding_table("qa_entry_embeddings", "qa_entry_id", "qa_entries", "uq_qa_embedding_version")

    if op.get_bind().dialect.name == "postgresql":
        for table, column in (
            ("knowledge_slice_embeddings", "embedding"),
            ("knowledge_product_embeddings", "embedding"),
            ("style_entry_embeddings", "embedding"),
            ("qa_entry_embeddings", "embedding"),
        ):
            op.execute(
                sa.text(
                    f"CREATE INDEX ix_{table}_hnsw_cosine ON {table} "
                    f"USING hnsw ({column} vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
        op.execute(
            "CREATE INDEX ix_knowledge_slices_lexical_trgm ON knowledge_slices "
            "USING gin ((title || ' ' || summary || ' ' || structured_content) gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_qa_entries_question_trgm ON qa_entries "
            "USING gin ((standard_question || ' ' || fixed_answer) gin_trgm_ops)"
        )


def downgrade() -> None:
    for table in (
        "qa_entry_embeddings",
        "style_entry_embeddings",
        "knowledge_product_embeddings",
        "knowledge_slice_embeddings",
        "qa_entry_assets",
        "qa_entries",
        "style_entries",
        "knowledge_products",
        "knowledge_slices",
    ):
        op.drop_table(table)
    with op.batch_alter_table("knowledge_source_versions") as batch:
        batch.drop_column("schema_version")
    with op.batch_alter_table("knowledge_sources") as batch:
        batch.drop_column("schema_version")
