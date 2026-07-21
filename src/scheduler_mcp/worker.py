"""Minimal in-process worker — W28K-1404a / W28K-1430.

The tick enqueues `schedule_run` jobs into cloud_dog_jobs; this worker consumes
them and dispatches to a target adapter. Run record transitions: scheduled →
claimed → running → (succeeded | failed | cancelled | timeout). All audit
events emit with correlation_id = schedule_run_id.

The worker is sync and synchronous for testability; production multi-node
horizontal scaling lives in W28K-1404a's full implementation. This file
satisfies the W28K-1430 F-scenario tests today.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from cloud_dog_logging import get_logger

from scheduler_mcp import config
from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.schedule import Schedule
from scheduler_mcp.db.models.schedule_run import ScheduleRun, ScheduleRunStatus

_log = get_logger(__name__)


class AdapterMisconfigured(RuntimeError):
    """W28K-1407 F-1407-6 — a production dispatch would fall back to a stub
    adapter: ``CLOUD_DOG__SCHEDULER__USE_REAL_ADAPTERS=true`` but no real
    adapter is registered in ``scheduler_mcp.adapters.ASYNC_ADAPTERS`` for the
    target_type (only a ``_REGISTRY`` stub exists). Surfaced as a failed run
    with ``error_code='stub_adapter_in_production'`` rather than a silent mock
    success."""


@dataclass
class AdapterResult:
    outcome: str  # 'succeeded' | 'failed' | 'cancelled'
    error_code: str | None = None
    error_summary: str | None = None
    result_ref: str | None = None
    duration_ms: int = 0


# ---------- Adapter registry --------------------------------------------
Adapter = Callable[[Schedule, ScheduleRun, dict[str, Any]], AdapterResult]


def _adapter_registered_tool(s: Schedule, r: ScheduleRun, payload: dict) -> AdapterResult:
    """Stub for MCP registered_tool target. AT lane 1404g calls real siblings."""
    return AdapterResult(outcome="succeeded", result_ref=f"mcp:{s.target_ref}", duration_ms=1)


def _adapter_external_http(s: Schedule, r: ScheduleRun, payload: dict) -> AdapterResult:
    return AdapterResult(outcome="succeeded", result_ref=f"http:{(s.target_spec or {}).get('url', '-')}", duration_ms=1)


def _adapter_code_runner(s: Schedule, r: ScheduleRun, payload: dict) -> AdapterResult:
    return AdapterResult(outcome="succeeded", result_ref="code_runner:ok", duration_ms=1)


def _adapter_sandbox_command(s: Schedule, r: ScheduleRun, payload: dict) -> AdapterResult:
    return AdapterResult(outcome="succeeded", result_ref="sandbox:ok", duration_ms=1)


def _adapter_chain(s: Schedule, r: ScheduleRun, payload: dict) -> AdapterResult:
    return AdapterResult(outcome="succeeded", result_ref=f"chain:{s.target_ref}", duration_ms=1)


_REGISTRY: dict[str, Adapter] = {
    "registered_tool": _adapter_registered_tool,
    "external_http": _adapter_external_http,
    "code_runner": _adapter_code_runner,
    "sandbox_command": _adapter_sandbox_command,
    "chain": _adapter_chain,
}


def register_adapter(target_type: str, adapter: Adapter) -> None:
    """Test seam — register an alternate adapter (e.g. one that hangs for timeout testing)."""
    _REGISTRY[target_type] = adapter


def get_adapter(target_type: str) -> Adapter | None:
    return _REGISTRY.get(target_type)


def _resolve_target_api_key(spec: dict[str, Any]) -> str | None:
    """Resolve downstream target credentials without requiring schedules to
    persist raw API keys in target_spec.

    Backwards compatibility is preserved for existing schedules using
    target_spec.api_key. New schedules can use target_spec.api_key_config_key,
    for example "siblings.expert_agent.api_key".
    """
    explicit = spec.get("api_key")
    if explicit:
        return str(explicit)

    for field in ("api_key_config_key", "credential_config_key", "credential_ref", "api_key_ref"):
        key = spec.get(field)
        if not key:
            continue
        resolved = config.get(str(key))
        if resolved:
            return str(resolved)

    fallback = config.get("scheduler.admin_api_key")
    # Config backends may decode an all-numeric credential as an integer.
    # HTTP header libraries accept only str/bytes, so keep the adapter context
    # contract true for the fallback path just as for explicit/config-ref keys.
    return str(fallback) if fallback else None


def _iso_z(value: datetime | None) -> str:
    if value is None:
        value = _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _render_runtime_templates(value: Any, *, schedule: Schedule, run: ScheduleRun) -> Any:
    """Render safe scheduler runtime placeholders in target_spec.

    This keeps schedules durable while allowing each dispatch to produce unique
    output paths/idempotency keys. It is deliberately a simple exact-token
    replacement, not a general expression evaluator.
    """
    if isinstance(value, dict):
        return {key: _render_runtime_templates(item, schedule=schedule, run=run) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_runtime_templates(item, schedule=schedule, run=run) for item in value]
    if not isinstance(value, str):
        return value

    ts = _iso_z(run.started_at or run.scheduled_for or run.created_at)
    scheduled_ts = _iso_z(run.scheduled_for)
    replacements = {
        "{{run.timestamp_utc}}": ts,
        "{{run.scheduled_for_utc}}": scheduled_ts,
        "{{run.date_utc}}": ts[:8],
        "{{schedule_run_id}}": run.schedule_run_id,
        "{{schedule_id}}": schedule.schedule_id,
    }
    rendered = value
    for token, replacement in replacements.items():
        rendered = rendered.replace(token, replacement)

    def _render_daily_choice(match: re.Match[str]) -> str:
        choices = [item.strip() for item in match.group(1).split("|") if item.strip()]
        if not choices:
            return match.group(0)
        seed_date = scheduled_ts[:8]
        seed = f"{schedule.schedule_id}:{seed_date}:{match.group(1)}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return choices[int(digest[:8], 16) % len(choices)]

    rendered = re.sub(r"\{\{run\.choice:([^{}]+)\}\}", _render_daily_choice, rendered)
    return rendered


# ---------- Worker -------------------------------------------------------
def _now() -> datetime:
    n = get_clock().now()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def _audit_lifecycle(
    event: str, run_id: str, schedule_id: str, *, outcome: str = "success", details: dict[str, Any] | None = None
) -> None:
    """W28K-1404g — emit a run-lifecycle audit row keyed by schedule_run_id
    so AT-tier `/v1/audit?correlation_id=<run_id>` returns the canonical
    dispatched→started→{succeeded,failed} 3-row trace. Swallows all
    exceptions: audit failure must not break the dispatch path."""
    try:
        from scheduler_mcp.audit import audit_event

        merged = {"schedule_id": schedule_id, "schedule_run_id": run_id}
        if details:
            merged.update(details)
        audit_event(
            event,
            actor="scheduler-mcp",
            target=run_id,
            outcome=outcome,
            details=merged,
            correlation_id=run_id,
        )
    except Exception:  # noqa: BLE001
        pass


def dispatch_run(run_id: str, *, timeout_seconds: float | None = None) -> ScheduleRun:
    """Execute one ScheduleRun and observe NF-1407-1 dispatch metrics once at the
    single exit (schedule_runs_total + schedule_runs_failed)."""
    run = _dispatch_run_impl(run_id, timeout_seconds=timeout_seconds)
    try:
        from scheduler_mcp.metrics import observe_dispatch

        observe_dispatch(run.status)
    except Exception:  # noqa: BLE001 — metrics must never break dispatch
        pass
    return run


def _dispatch_run_impl(run_id: str, *, timeout_seconds: float | None = None) -> ScheduleRun:
    """Execute one ScheduleRun synchronously through its target adapter.
    Returns the updated run row. Honours target_spec.timeout_seconds.
    """
    sm = get_session_manager()
    with sm.session() as session:
        run = session.get(ScheduleRun, run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        _audit_lifecycle("dispatched", run_id, run.schedule_id)
        sched = session.get(Schedule, run.schedule_id)
        if sched is None:
            run.status = ScheduleRunStatus.failed.value
            run.error_code = "schedule_missing"
            run.error_summary = "Schedule row not found"
            run.finished_at = _now()
            run.updated_at = _now()
            session.commit()
            session.refresh(run)
            return run

        # F8 approval gate
        if (sched.approval_status or "not_required") not in ("not_required", "approved"):
            run.status = ScheduleRunStatus.blocked.value
            run.error_code = "approval_pending"
            run.error_summary = f"approval_status={sched.approval_status}"
            run.finished_at = _now()
            run.updated_at = _now()
            session.commit()
            session.refresh(run)
            _log.info(f"run.blocked correlation_id={run.schedule_run_id} reason=approval_pending")
            return run

        adapter = get_adapter(sched.target_type)
        # W28K-1404g — real-adapter path. When CLOUD_DOG__SCHEDULER__USE_REAL_ADAPTERS
        # is true AND scheduler_mcp.adapters has an async adapter for this target_type,
        # dispatch through the real adapter that actually calls the sibling. UT/IT
        # leave the flag off so they keep using `_REGISTRY` (incl. monkeypatched stubs).
        real_adapter = None
        if config.get("scheduler.use_real_adapters", False):
            try:
                from scheduler_mcp import adapters as _ad

                real_adapter = _ad.ASYNC_ADAPTERS.get(sched.target_type)
            except Exception:  # noqa: BLE001
                real_adapter = None

        # F-1407-6 — production stub-adapter guard. When real adapters are
        # enabled but this target_type resolves only to a _REGISTRY stub (no
        # entry in adapters.ASYNC_ADAPTERS), refuse to run: a stub would return
        # a mock success and mask the misconfiguration. Fail the run with the
        # AdapterMisconfigured condition instead.
        if config.get("scheduler.use_real_adapters", False) and real_adapter is None and adapter is not None:
            run.status = ScheduleRunStatus.failed.value
            run.error_code = "stub_adapter_in_production"
            run.error_summary = (
                f"{AdapterMisconfigured.__name__}: target_type={sched.target_type!r} resolves to a stub "
                f"adapter while use_real_adapters=true and no real adapter is registered in ASYNC_ADAPTERS"
            )
            run.finished_at = _now()
            run.updated_at = _now()
            session.commit()
            session.refresh(run)
            _audit_lifecycle(
                "failed", run_id, run.schedule_id, outcome="failure", details={"error_code": run.error_code}
            )
            return run

        if adapter is None and real_adapter is None:
            run.status = ScheduleRunStatus.failed.value
            run.error_code = "unknown_target_type"
            run.error_summary = f"no adapter for {sched.target_type!r}"
            run.finished_at = _now()
            run.updated_at = _now()
            session.commit()
            session.refresh(run)
            return run

        # transition to running
        run.status = ScheduleRunStatus.running.value
        run.started_at = _now()
        run.updated_at = _now()
        session.commit()
        _audit_lifecycle("started", run_id, run.schedule_id)

        # honor target_spec.timeout_seconds (F3)
        spec = _render_runtime_templates(
            copy.deepcopy(sched.target_spec or {}),
            schedule=sched,
            run=run,
        )
        eff_timeout = timeout_seconds if timeout_seconds is not None else float(spec.get("timeout_seconds") or 0)

        t0 = _now()
        result: AdapterResult
        try:
            if real_adapter is not None:
                from scheduler_mcp.adapters.base import AdapterContext

                ctx_api_key = _resolve_target_api_key(spec)
                ctx = AdapterContext(
                    correlation_id=run.schedule_run_id,
                    schedule_id=sched.schedule_id,
                    target_type=sched.target_type,
                    target_ref=sched.target_ref or "",
                    target_spec=spec,
                    timeout_seconds=float(eff_timeout) if eff_timeout > 0 else 60.0,
                    api_key=ctx_api_key,
                )
                coro = real_adapter.execute(ctx)
                if eff_timeout > 0:
                    result = asyncio.run(asyncio.wait_for(coro, timeout=eff_timeout))
                else:
                    result = asyncio.run(coro)
            elif eff_timeout > 0:
                assert adapter is not None

                async def _runner():
                    return adapter(sched, run, spec)

                result = asyncio.run(asyncio.wait_for(_runner(), timeout=eff_timeout))
            else:
                assert adapter is not None
                result = adapter(sched, run, spec)
        except asyncio.TimeoutError:
            result = AdapterResult(
                outcome="failed", error_code="timeout", error_summary=f"adapter exceeded {eff_timeout}s"
            )
        except Exception as e:  # noqa: BLE001
            result = AdapterResult(outcome="failed", error_code="adapter_error", error_summary=str(e))

        # apply result
        run.status = (
            ScheduleRunStatus.succeeded.value
            if result.outcome == "succeeded"
            else (
                ScheduleRunStatus.cancelled.value if result.outcome == "cancelled" else ScheduleRunStatus.failed.value
            )
        )
        run.error_code = result.error_code
        run.error_summary = result.error_summary
        run.result_ref = result.result_ref
        run.finished_at = _now()
        run.updated_at = _now()
        if result.duration_ms == 0:
            result.duration_ms = max(1, int((run.finished_at - t0).total_seconds() * 1000))
        session.commit()
        session.refresh(run)

        _log.info(
            f"run.completed correlation_id={run.schedule_run_id} status={run.status} "
            f"error_code={run.error_code or ''} duration_ms={result.duration_ms}"
        )
        # Terminal lifecycle event — uses the run.status name directly so the
        # AT proof-of-execution row (`succeeded`/`failed`/`cancelled`/`timeout`)
        # matches the AT contract §5.1 step 7.
        _audit_lifecycle(
            run.status,
            run_id,
            run.schedule_id,
            outcome="success" if result.outcome == "succeeded" else "failure",
            details={"error_code": run.error_code, "duration_ms": result.duration_ms},
        )
        return run


def apply_backpressure(due_runs: list[str], pool_capacity: int) -> list[str]:
    """F10 back-pressure: only the first `pool_capacity` runs proceed; the
    remainder are queued (returned as-is for the caller to defer)."""
    return list(due_runs[: max(0, int(pool_capacity))])
