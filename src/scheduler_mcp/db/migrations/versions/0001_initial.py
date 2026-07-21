"""Initial schema — schedules, runs, fire windows, chains, context, external targets, project registry.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-10

W28K-1408: cloud_dog_db migration runner produces this initial chain. Avoids
SQLite ``executescript()`` per AGENT-LESSONS §6.43 — all DDL is via op.create_table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("schedule_id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("trigger_spec", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_ref", sa.String(length=255), nullable=False),
        sa.Column("target_spec", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("misfire_policy", sa.String(length=32), nullable=False, server_default="skip"),
        sa.Column("concurrency_policy", sa.String(length=32), nullable=False, server_default="forbid"),
        sa.Column("max_active_runs", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retry_policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("context_policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("owner_group_id", sa.String(length=64), nullable=True),
        sa.Column("rbac_policy_ref", sa.String(length=255), nullable=True),
        sa.Column("approval_status", sa.String(length=32), nullable=False, server_default="not_required"),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_schedules_tenant_id", "schedules", ["tenant_id"])
    op.create_index("ix_schedules_next_fire_at", "schedules", ["next_fire_at"])
    op.create_index("ix_schedules_archived_at", "schedules", ["archived_at"])
    op.create_index("ix_schedules_tenant_status_next_fire_at", "schedules", ["tenant_id", "status", "next_fire_at"])

    op.create_table(
        "schedule_runs",
        sa.Column("schedule_run_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.String(length=64),
            sa.ForeignKey("schedules.schedule_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("triggered_by", sa.String(length=32), nullable=False, server_default="scheduler"),
        sa.Column("trigger_source_id", sa.String(length=64), nullable=True),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("root_job_id", sa.String(length=64), nullable=True),
        sa.Column("chain_run_id", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("stdout_ref", sa.String(length=255), nullable=True),
        sa.Column("stderr_ref", sa.String(length=255), nullable=True),
        sa.Column("context_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_schedule_runs_schedule_id", "schedule_runs", ["schedule_id"])
    op.create_index("ix_schedule_runs_tenant_id", "schedule_runs", ["tenant_id"])
    op.create_index("ix_schedule_runs_scheduled_for", "schedule_runs", ["scheduled_for"])
    op.create_index("ix_schedule_runs_schedule_id_scheduled_for", "schedule_runs", ["schedule_id", "scheduled_for"])

    op.create_table(
        "schedule_fire_windows",
        sa.Column(
            "schedule_id",
            sa.String(length=64),
            sa.ForeignKey("schedules.schedule_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fire_window", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("schedule_run_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="claimed"),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("schedule_id", "fire_window", name="uq_schedule_fire_windows_schedule_window"),
    )

    op.create_table(
        "chains",
        sa.Column("chain_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("definition", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("owner_group_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "chain_runs",
        sa.Column("chain_run_id", sa.String(length=64), primary_key=True),
        sa.Column("chain_id", sa.String(length=64), sa.ForeignKey("chains.chain_id"), nullable=False),
        sa.Column(
            "schedule_run_id", sa.String(length=64), sa.ForeignKey("schedule_runs.schedule_run_id"), nullable=True
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_context_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_chain_runs_chain_id", "chain_runs", ["chain_id"])
    op.create_index("ix_chain_runs_schedule_run_id", "chain_runs", ["schedule_run_id"])

    op.create_table(
        "chain_step_runs",
        sa.Column("step_run_id", sa.String(length=64), primary_key=True),
        sa.Column("chain_run_id", sa.String(length=64), sa.ForeignKey("chain_runs.chain_run_id"), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("step_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("depends_on", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("ajobs_job_id", sa.String(length=64), nullable=True),
        sa.Column("input_ref", sa.String(length=255), nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_chain_step_runs_chain_run_id", "chain_step_runs", ["chain_run_id"])

    op.create_table(
        "scheduler_context",
        sa.Column("context_entry_id", sa.String(length=64), primary_key=True),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("visibility", sa.String(length=32), nullable=False, server_default="private"),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope_type", "scope_id", "key", name="uq_scheduler_context_scope_key"),
    )

    op.create_table(
        "external_targets",
        sa.Column("target_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_spec", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("secret_refs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("allowed_methods", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("allowed_by_policy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "project_registry",
        sa.Column("project_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("service_kind", sa.String(length=32), nullable=False, server_default="mcp_tool"),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("mcp_url", sa.String(length=255), nullable=True),
        sa.Column("a2a_card_url", sa.String(length=255), nullable=True),
        sa.Column("health_url", sa.String(length=255), nullable=True),
        sa.Column("terraform_source", sa.String(length=255), nullable=True),
        sa.Column("image_pin", sa.String(length=255), nullable=True),
        sa.Column("tools_list_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("skills_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("rbac_scopes_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("last_card_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_registry_tenant_id", "project_registry", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("project_registry")
    op.drop_table("external_targets")
    op.drop_table("scheduler_context")
    op.drop_table("chain_step_runs")
    op.drop_table("chain_runs")
    op.drop_table("chains")
    op.drop_table("schedule_fire_windows")
    op.drop_table("schedule_runs")
    op.drop_table("schedules")
