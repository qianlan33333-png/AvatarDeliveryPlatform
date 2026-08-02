from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProductCapabilityMapping(Base, TimestampMixin):
    __tablename__ = "product_capability_mappings"
    __table_args__ = (
        UniqueConstraint(
            "product_code",
            "capability_code",
            name="uq_product_capability",
        ),
        CheckConstraint(
            "capability_code IN ('chat_qa', 'copywriting')",
            name="ck_product_capability_code",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    product_code: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    capability_code: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CapabilityEntitlement(Base, TimestampMixin):
    __tablename__ = "capability_entitlements"
    __table_args__ = (
        UniqueConstraint(
            "phone_hash",
            "capability_code",
            name="uq_capability_entitlement_phone_code",
        ),
        CheckConstraint(
            "capability_code IN ('chat_qa', 'copywriting')",
            name="ck_capability_entitlement_code",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_capability_entitlement_status",
        ),
        Index(
            "ix_capability_entitlement_user_status",
            "user_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_order_id: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    last_event_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_action: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    last_event_id: Mapped[str] = mapped_column(String(160), default="", nullable=False)
