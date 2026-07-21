"""Chain entity — seed §6."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class ChainStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    deprecated = "deprecated"
    archived = "archived"


class Chain(Base):
    __tablename__ = "chains"

    chain_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        Enum(ChainStatus, native_enum=False, length=32), nullable=False, default=ChainStatus.draft.value
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
