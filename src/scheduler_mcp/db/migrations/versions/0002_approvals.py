"""Approval requests — W28K-1407 F-1407-3.

Revision ID: 0002_approvals
Revises: 0001_initial
Create Date: 2026-06-15

Adds the ``approval_requests`` table that drives the approval lifecycle REST
surface. All DDL via op.create_table — never SQLite ``executescript()``
(AGENT-LESSONS §6.43), so upgrade→downgrade→upgrade replays cleanly.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_approvals"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("approval_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.String(length=64),
            sa.ForeignKey("schedules.schedule_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approval_requests_schedule_id", "approval_requests", ["schedule_id"])
    op.create_index("ix_approval_requests_schedule_id_status", "approval_requests", ["schedule_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_approval_requests_schedule_id_status", table_name="approval_requests")
    op.drop_table("approval_requests")
