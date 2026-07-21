"""ExternalTarget entity — seed §11 (FR-014 allow-list)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class ExternalTargetKind(str, enum.Enum):
    http = "http"
    webhook = "webhook"
    sandbox_command = "sandbox_command"
    code_runner = "code_runner"


class ExternalTarget(Base):
    __tablename__ = "external_targets"

    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(Enum(ExternalTargetKind, native_enum=False, length=32), nullable=False)
    target_spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    secret_refs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    allowed_methods: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    allowed_by_policy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
