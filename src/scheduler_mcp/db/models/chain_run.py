"""ChainRun entity — seed §7."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class ChainRunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    partially_failed = "partially_failed"
    cancelled = "cancelled"


class ChainRun(Base):
    __tablename__ = "chain_runs"

    chain_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chain_id: Mapped[str] = mapped_column(String(64), ForeignKey("chains.chain_id"), nullable=False, index=True)
    schedule_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("schedule_runs.schedule_run_id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        Enum(ChainRunStatus, native_enum=False, length=32), nullable=False, default=ChainRunStatus.pending.value
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_context_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
