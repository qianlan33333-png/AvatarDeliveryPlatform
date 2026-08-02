"""ai modes and model bindings

Revision ID: c39d4e5f6071
Revises: b28c3d4e5f60
Create Date: 2026-08-02 09:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c39d4e5f6071"
down_revision: str | None = "b28c3d4e5f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course_entitlements",
        sa.Column("last_event_effective_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "course_entitlements",
        sa.Column("last_event_action", sa.String(length=20), server_default="", nullable=False),
    )
    op.add_column(
        "course_entitlements",
        sa.Column("last_event_id", sa.String(length=160), server_default="", nullable=False),
    )
    # Existing entitlements predate ordered event metadata.  Treat their current
    # row timestamp as the last authoritative operation so a delayed historic
    # webhook cannot resurrect a revoked entitlement after this migration.
    op.execute(
        sa.text(
            """
            UPDATE course_entitlements
            SET last_event_effective_at = COALESCE(updated_at, effective_at),
                last_event_action = CASE
                    WHEN status = 'revoked' THEN 'revoke'
                    ELSE 'grant'
                END,
                last_event_id = 'migration-backfill'
            WHERE last_event_effective_at IS NULL
            """
        )
    )
    op.add_column(
        "conversations",
        sa.Column("mode", sa.String(length=20), server_default="qa", nullable=False),
    )
    op.create_index("ix_conversations_mode", "conversations", ["mode"])
    op.add_column(
        "messages",
        sa.Column("knowledge_unit_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )

    op.add_column(
        "llm_configs",
        sa.Column("provider", sa.String(length=40), server_default="custom", nullable=False),
    )
    op.add_column(
        "llm_configs",
        sa.Column("capability", sa.String(length=20), server_default="chat", nullable=False),
    )
    op.add_column("llm_configs", sa.Column("embedding_dimension", sa.Integer()))
    op.create_index("ix_llm_configs_capability", "llm_configs", ["capability"])

    op.create_table(
        "ai_model_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scene", sa.String(length=40), nullable=False),
        sa.Column("primary_model_id", sa.String(length=36), nullable=False),
        sa.Column("fallback_model_id", sa.String(length=36)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["primary_model_id"], ["llm_configs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["fallback_model_id"], ["llm_configs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scene"),
    )

    op.add_column(
        "chat_reservations",
        sa.Column("mode", sa.String(length=20), server_default="qa", nullable=False),
    )
    op.create_index("ix_chat_reservations_mode", "chat_reservations", ["mode"])
    op.add_column(
        "chat_reservations",
        sa.Column("knowledge_unit_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chat_reservations", "knowledge_unit_ids")
    op.drop_index("ix_chat_reservations_mode", table_name="chat_reservations")
    op.drop_column("chat_reservations", "mode")
    op.drop_table("ai_model_bindings")
    op.drop_index("ix_llm_configs_capability", table_name="llm_configs")
    op.drop_column("llm_configs", "embedding_dimension")
    op.drop_column("llm_configs", "capability")
    op.drop_column("llm_configs", "provider")
    op.drop_column("messages", "knowledge_unit_ids")
    op.drop_index("ix_conversations_mode", table_name="conversations")
    op.drop_column("conversations", "mode")
    op.drop_column("course_entitlements", "last_event_id")
    op.drop_column("course_entitlements", "last_event_action")
    op.drop_column("course_entitlements", "last_event_effective_at")
