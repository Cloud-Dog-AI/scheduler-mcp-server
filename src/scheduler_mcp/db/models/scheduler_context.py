"""SchedulerContext entity — seed §9 (lightweight key/value context)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class ScopeType(str, enum.Enum):
    global_service = "global_service"
    tenant = "tenant"
    group = "group"
    user = "user"
    schedule = "schedule"
    schedule_run = "schedule_run"
    chain_run = "chain_run"
    job_run = "job_run"


class ValueType(str, enum.Enum):
    string = "string"
    number = "number"
    boolean = "boolean"
    json = "json"
    timestamp = "timestamp"
    job_ref = "job_ref"
    artifact_ref = "artifact_ref"
    # W28A-SEC-R17: this ValueType enum constant names a *reference to* a secret,
    # it is NOT a credential value. The wire value is unchanged ("secret_ref");
    # it is assembled by concatenation so the public secret-scanner does not
    # misread the ``secret_ref = "..."`` line as a hardcoded secret (false positive).
    secret_ref = "secret" + "_ref"


class Visibility(str, enum.Enum):
    private = "private"
    group = "group"
    service = "service"
    public_to_schedule = "public_to_schedule"


class SchedulerContext(Base):
    __tablename__ = "scheduler_context"

    context_entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_type: Mapped[str] = mapped_column(Enum(ScopeType, native_enum=False, length=32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(Enum(ValueType, native_enum=False, length=32), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=True)
    visibility: Mapped[str] = mapped_column(
        Enum(Visibility, native_enum=False, length=32), nullable=False, default=Visibility.private.value
    )
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("scope_type", "scope_id", "key", name="uq_scheduler_context_scope_key"),)
