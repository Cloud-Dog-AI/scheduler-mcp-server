"""Quota / limits / rate-budget enforcement — W28K-1420.

All quota values come from cloud_dog_config (defaults.yaml has the canonical
limits — see `scheduler.quotas.*`). Service code calls the predicates here
before persisting / enqueuing so violations raise a clean QuotaExceeded
that the API layer maps to HTTP 429.
"""

from __future__ import annotations

from dataclasses import dataclass

from scheduler_mcp import config


class QuotaExceeded(Exception):
    """A configured quota would be exceeded by the requested operation."""

    def __init__(self, *, code: str, message: str, observed: int | None = None, limit: int | None = None):
        super().__init__(message)
        self.code = code
        self.observed = observed
        self.limit = limit


@dataclass(frozen=True)
class Quotas:
    max_schedules_per_user: int
    min_interval_seconds: int
    max_run_duration_seconds: int
    max_active_runs_per_schedule: int
    max_context_size_bytes: int
    max_log_size_bytes: int

    @classmethod
    def from_config(cls) -> "Quotas":
        return cls(
            max_schedules_per_user=int(config.get("scheduler.quotas.max_schedules_per_user", 100)),
            min_interval_seconds=int(config.get("scheduler.quotas.min_interval_seconds", 60)),
            max_run_duration_seconds=int(config.get("scheduler.quotas.max_run_duration_seconds", 3600)),
            max_active_runs_per_schedule=int(config.get("scheduler.quotas.max_active_runs_per_schedule", 1)),
            max_context_size_bytes=int(config.get("scheduler.quotas.max_context_size_bytes", 65536)),
            max_log_size_bytes=int(config.get("scheduler.quotas.max_log_size_bytes", 1048576)),
        )


def check_min_interval(trigger_type: str, trigger_spec: dict, *, quotas: Quotas | None = None) -> None:
    q = quotas or Quotas.from_config()
    if trigger_type != "interval":
        return
    every = int(trigger_spec.get("every_seconds", 0) or 0)
    if every < q.min_interval_seconds:
        raise QuotaExceeded(
            code="min_interval_seconds",
            message=f"interval {every}s below configured minimum {q.min_interval_seconds}s",
            observed=every,
            limit=q.min_interval_seconds,
        )


def check_schedules_per_user(current_count: int, *, quotas: Quotas | None = None) -> None:
    q = quotas or Quotas.from_config()
    if current_count >= q.max_schedules_per_user:
        raise QuotaExceeded(
            code="max_schedules_per_user",
            message=f"user has {current_count} schedules; limit {q.max_schedules_per_user}",
            observed=current_count,
            limit=q.max_schedules_per_user,
        )


def check_context_size(payload_bytes: int, *, quotas: Quotas | None = None) -> None:
    q = quotas or Quotas.from_config()
    if payload_bytes > q.max_context_size_bytes:
        raise QuotaExceeded(
            code="max_context_size_bytes",
            message=f"context payload {payload_bytes} > limit {q.max_context_size_bytes}",
            observed=payload_bytes,
            limit=q.max_context_size_bytes,
        )


def check_active_runs(active_count: int, *, quotas: Quotas | None = None) -> None:
    q = quotas or Quotas.from_config()
    if active_count >= q.max_active_runs_per_schedule:
        raise QuotaExceeded(
            code="max_active_runs_per_schedule",
            message=f"{active_count} active runs; limit {q.max_active_runs_per_schedule}",
            observed=active_count,
            limit=q.max_active_runs_per_schedule,
        )
