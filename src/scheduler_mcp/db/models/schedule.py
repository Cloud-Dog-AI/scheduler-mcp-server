"""Schedule entity — FR-001 + seed §3 (data-model)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class TriggerType(str, enum.Enum):
    cron = "cron"
    interval = "interval"
    one_shot = "one_shot"
    manual = "manual"
    condition_watch = "condition_watch"


class ScheduleStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    disabled = "disabled"
    archived = "archived"
    completed = "completed"
    errored = "errored"


class TargetType(str, enum.Enum):
    registered_tool = "registered_tool"
    chain = "chain"
    external_http = "external_http"
    sandbox_command = "sandbox_command"
    code_runner = "code_runner"
    # W28K-1407 F-1407-9 — generic MCP target for non-registered services.
    external_mcp = "external_mcp"


class InvalidTargetType(ValueError):
    """W28E-1814B CS-003 — schedule create was given a target_type that is not a
    member of TargetType. The column is a non-native Enum (a VARCHAR), so an
    unchecked write would persist an unreadable value that later breaks ORM
    list/scan reads (LookupError on the enum). Create surfaces reject before
    persistence."""


def validate_target_type(value: str) -> str:
    """Validate ``value`` against the supported TargetType set (CS-003).

    Sibling services (e.g. expert-agent) are scheduled via
    ``target_type='registered_tool'`` with ``target_ref`` naming the registry
    project — there is no per-service target_type. Unknown values are rejected
    before persistence rather than silently stored as an unreadable enum value.
    """
    allowed = {t.value for t in TargetType}
    if value not in allowed:
        raise InvalidTargetType(f"invalid target_type {value!r}; allowed: {sorted(allowed)}")
    return value


class MisfirePolicy(str, enum.Enum):
    skip = "skip"
    run_once_now = "run_once_now"
    backfill_limited = "backfill_limited"


class ConcurrencyPolicy(str, enum.Enum):
    allow = "allow"
    forbid = "forbid"
    queue = "queue"
    replace = "replace"  # type: ignore[assignment]  # Enum value intentionally shadows str.replace.


class ApprovalStatus(str, enum.Enum):
    not_required = "not_required"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Schedule(Base):
    __tablename__ = "schedules"

    schedule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="default")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        Enum(ScheduleStatus, native_enum=False, length=32), nullable=False, default=ScheduleStatus.active.value
    )
    trigger_type: Mapped[str] = mapped_column(Enum(TriggerType, native_enum=False, length=32), nullable=False)
    trigger_spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_type: Mapped[str] = mapped_column(Enum(TargetType, native_enum=False, length=32), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    target_spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    misfire_policy: Mapped[str] = mapped_column(
        Enum(MisfirePolicy, native_enum=False, length=32), nullable=False, default=MisfirePolicy.skip.value
    )
    concurrency_policy: Mapped[str] = mapped_column(
        Enum(ConcurrencyPolicy, native_enum=False, length=32), nullable=False, default=ConcurrencyPolicy.forbid.value
    )
    max_active_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    context_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rbac_policy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approval_status: Mapped[str] = mapped_column(
        Enum(ApprovalStatus, native_enum=False, length=32), nullable=False, default=ApprovalStatus.not_required.value
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (Index("ix_schedules_tenant_status_next_fire_at", "tenant_id", "status", "next_fire_at"),)
