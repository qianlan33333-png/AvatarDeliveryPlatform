"""capability_entitlements

Revision ID: b28c3d4e5f60
Revises: a17b2c3d4e5f
Create Date: 2026-08-02 08:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b28c3d4e5f60"
down_revision: str | None = "a17b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_capability_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("product_code", sa.String(length=120), nullable=False),
        sa.Column("capability_code", sa.String(length=40), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capability_code IN ('chat_qa', 'copywriting')",
            name="ck_product_capability_code",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_code",
            "capability_code",
            name="uq_product_capability",
        ),
    )
    op.create_index(
        op.f("ix_product_capability_mappings_product_code"),
        "product_capability_mappings",
        ["product_code"],
        unique=False,
    )

    op.create_table(
        "capability_entitlements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36)),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("capability_code", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("source_order_id", sa.String(length=160), server_default="", nullable=False),
        sa.Column("last_event_effective_at", sa.DateTime(timezone=True)),
        sa.Column("last_event_action", sa.String(length=20), server_default="", nullable=False),
        sa.Column("last_event_id", sa.String(length=160), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capability_code IN ('chat_qa', 'copywriting')",
            name="ck_capability_entitlement_code",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_capability_entitlement_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "phone_hash",
            "capability_code",
            name="uq_capability_entitlement_phone_code",
        ),
    )
    op.create_index(
        op.f("ix_capability_entitlements_user_id"),
        "capability_entitlements",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_capability_entitlement_user_status",
        "capability_entitlements",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capability_entitlement_user_status",
        table_name="capability_entitlements",
    )
    op.drop_index(
        op.f("ix_capability_entitlements_user_id"),
        table_name="capability_entitlements",
    )
    op.drop_table("capability_entitlements")
    op.drop_index(
        op.f("ix_product_capability_mappings_product_code"),
        table_name="product_capability_mappings",
    )
    op.drop_table("product_capability_mappings")
