"""atomic embedding generation switch

Revision ID: d40e5f607182
Revises: c39d4e5f6071
Create Date: 2026-08-02 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d40e5f607182"
down_revision: str | None = "c39d4e5f6071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_model_bindings") as batch_op:
        batch_op.add_column(sa.Column("pending_primary_model_id", sa.String(length=36)))
        batch_op.create_foreign_key(
            "fk_ai_model_bindings_pending_primary_model_id_llm_configs",
            "llm_configs",
            ["pending_primary_model_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("knowledge_index_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "target_model_version",
                sa.String(length=120),
                nullable=False,
                server_default="unconfigured",
            )
        )
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_index_jobs") as batch_op:
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("target_model_version")
    with op.batch_alter_table("ai_model_bindings") as batch_op:
        batch_op.drop_constraint(
            "fk_ai_model_bindings_pending_primary_model_id_llm_configs",
            type_="foreignkey",
        )
        batch_op.drop_column("pending_primary_model_id")
