"""delivery_api_foundations

Revision ID: 6c42b8f09e31
Revises: eb4627863a5c
Create Date: 2026-08-01 23:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c42b8f09e31"
down_revision: str | None = "eb4627863a5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("entitlement_events") as batch_op:
        batch_op.add_column(
            sa.Column("phone_ciphertext", sa.Text(), server_default="", nullable=False)
        )
        batch_op.add_column(
            sa.Column("error_message", sa.Text(), server_default="", nullable=False)
        )
        batch_op.add_column(sa.Column("effective_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("replayed_from_event_id", sa.String(length=160)))
        batch_op.add_column(
            sa.Column("created_by", sa.String(length=30), server_default="webhook", nullable=False)
        )

    op.create_table(
        "webhook_nonces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("nonce", sa.String(length=160), nullable=False),
        sa.Column("event_id", sa.String(length=180), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "nonce", name="uq_webhook_provider_nonce"),
    )
    op.create_table(
        "chat_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ticket_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36)),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("candidate_course_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_hash"),
    )
    op.create_index(
        "ix_chat_reservation_status_expiry",
        "chat_reservations",
        ["status", "expires_at"],
    )
    op.create_index(
        op.f("ix_chat_reservations_user_id"),
        "chat_reservations",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_reservations_user_id"), table_name="chat_reservations")
    op.drop_index("ix_chat_reservation_status_expiry", table_name="chat_reservations")
    op.drop_table("chat_reservations")
    op.drop_table("webhook_nonces")

    with op.batch_alter_table("entitlement_events") as batch_op:
        batch_op.drop_column("created_by")
        batch_op.drop_column("replayed_from_event_id")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("effective_at")
        batch_op.drop_column("error_message")
        batch_op.drop_column("phone_ciphertext")
