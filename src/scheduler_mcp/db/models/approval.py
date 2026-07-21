"""ApprovalRequest entity — W28K-1407 F-1407-3.

Backs the approval lifecycle REST surface. The worker dispatch gate
(``worker.py`` dispatch_run) already blocks a run while the parent
``Schedule.approval_status`` is not in {not_required, approved}; this table
records the request/decision lifecycle that drives that field.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class ApprovalRequestStatus(str, enum.Enum):
    pending = "pending"
    granted = "granted"
    rejected = "rejected"


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("schedules.schedule_id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    status: Mapped[str] = mapped_column(
        Enum(ApprovalRequestStatus, native_enum=False, length=32),
        nullable=False,
        default=ApprovalRequestStatus.pending.value,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_approval_requests_schedule_id_status", "schedule_id", "status"),)
