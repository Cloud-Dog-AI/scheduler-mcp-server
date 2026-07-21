"""ScheduleRun entity — FR-010 + seed §4 (data-model)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class ScheduleRunStatus(str, enum.Enum):
    scheduled = "scheduled"
    claimed = "claimed"
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    partially_failed = "partially_failed"
    cancelled = "cancelled"
    skipped = "skipped"
    misfired = "misfired"
    blocked = "blocked"


class TriggerSource(str, enum.Enum):
    scheduler = "scheduler"
    user = "user"
    agent = "agent"
    api = "api"
    recovery = "recovery"


class RunTriggerType(str, enum.Enum):
    scheduled = "scheduled"
    manual = "manual"
    retry = "retry"
    recovery = "recovery"
    backfill = "backfill"


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    schedule_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("schedules.schedule_id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    triggered_by: Mapped[str] = mapped_column(
        Enum(TriggerSource, native_enum=False, length=32), nullable=False, default=TriggerSource.scheduler.value
    )
    trigger_source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_type: Mapped[str] = mapped_column(
        Enum(RunTriggerType, native_enum=False, length=32), nullable=False, default=RunTriggerType.scheduled.value
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(ScheduleRunStatus, native_enum=False, length=32), nullable=False, default=ScheduleRunStatus.scheduled.value
    )
    root_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chain_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    stdout_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stderr_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_schedule_runs_schedule_id_scheduled_for", "schedule_id", "scheduled_for"),)
