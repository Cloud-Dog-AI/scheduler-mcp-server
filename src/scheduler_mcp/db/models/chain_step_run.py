"""ChainStepRun entity — seed §8."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class StepRunStatus(str, enum.Enum):
    pending = "pending"
    blocked = "blocked"
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


class StepType(str, enum.Enum):
    mcp_tool = "mcp_tool"
    a2a_call = "a2a_call"
    rest_call = "rest_call"
    expert_agent = "expert_agent"
    rlm_job = "rlm_job"
    code_runner = "code_runner"
    external_http = "external_http"
    sandbox_command = "sandbox_command"
    context_read = "context_read"
    context_write = "context_write"
    condition_check = "condition_check"
    notification = "notification"


class ChainStepRun(Base):
    __tablename__ = "chain_step_runs"

    step_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chain_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chain_runs.chain_run_id"), nullable=False, index=True
    )
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step_type: Mapped[str] = mapped_column(Enum(StepType, native_enum=False, length=32), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(StepRunStatus, native_enum=False, length=32), nullable=False, default=StepRunStatus.pending.value
    )
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ajobs_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
