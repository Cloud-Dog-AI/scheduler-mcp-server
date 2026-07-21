"""ScheduleFireWindow — seed §5 (idempotent due-window claim)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class FireWindowStatus(str, enum.Enum):
    claimed = "claimed"
    submitted = "submitted"
    completed = "completed"
    failed = "failed"


class ScheduleFireWindow(Base):
    __tablename__ = "schedule_fire_windows"

    schedule_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("schedules.schedule_id", ondelete="CASCADE"), primary_key=True
    )
    fire_window: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    schedule_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(FireWindowStatus, native_enum=False, length=32), nullable=False, default=FireWindowStatus.claimed.value
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("schedule_id", "fire_window", name="uq_schedule_fire_windows_schedule_window"),)
