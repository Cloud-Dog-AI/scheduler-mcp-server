"""SchedulerWorker — long-running cloud_dog_jobs consumer (W28K-1404a).

Builds a cloud_dog_jobs.Worker against the shared JobQueue, registers a
handler for `schedule_run` jobs that calls `dispatch_run` (which goes
through the existing _REGISTRY + adapters/). Lifespan helpers
`start_worker` / `stop_worker` are invoked by app.py FastAPI lifespan.

Health introspection (queue_depth, in_flight, worker_started_at,
last_dispatch_at) is read via `get_worker_health()` and surfaced by
`/health/jobs`.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cloud_dog_jobs import ResourcePool, Worker
from cloud_dog_logging import get_logger

from scheduler_mcp import config
from scheduler_mcp.jobs import get_queue
from scheduler_mcp.worker import dispatch_run

_log = get_logger(__name__)


@dataclass
class WorkerHealth:
    started_at: datetime | None = None
    last_dispatch_at: datetime | None = None
    tick_started_at: datetime | None = None
    last_tick_at: datetime | None = None
    last_tick_error: str | None = None
    ticks_total: int = 0
    in_flight: int = 0
    dispatched_total: int = 0
    queue_backend: str = "memory"


_HEALTH = WorkerHealth()
_TASK: asyncio.Task | None = None
_TICK_TASK: asyncio.Task | None = None
_WORKER: Worker | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tick_auto_start_enabled() -> bool:
    return bool(config.get("scheduler.tick.auto_start", True))


def _ensure_tick_task_started() -> None:
    global _TICK_TASK
    if not _tick_auto_start_enabled():
        return
    if _TICK_TASK is not None and not _TICK_TASK.done():
        return
    _HEALTH.tick_started_at = _now()
    _TICK_TASK = asyncio.create_task(_run_tick_loop(), name="scheduler-mcp-tick")


def _tick_once(*, lease_seconds: int = 30):
    """Lazy tick import so unit tests that only exercise worker handlers do not
    import cache/leader dependencies before app configuration is initialised."""
    from scheduler_mcp.tick import tick_once

    return tick_once(lease_seconds=lease_seconds)


def _handle_schedule_run(ctx: Any) -> dict[str, Any]:
    """cloud_dog_jobs handler — runs once per dispatched schedule_run job.

    cloud_dog_jobs.Worker invokes handlers synchronously with a JobContext
    wrapper (worker.py:127 `handler(ctx)` where ctx = JobContext(job=...)).
    The payload lives at `ctx.job.payload`. Prior to W28K-1404g this read
    `job.payload` directly, treating ctx as the Job; that crashed the worker
    on the first dispatched run with AttributeError on 'payload', leaving
    every tick-/run_now-enqueued run stuck in `scheduled`.

    dispatch_run is itself synchronous and the only branch that may block
    briefly.
    """
    _HEALTH.in_flight += 1
    _HEALTH.last_dispatch_at = _now()
    try:
        job = getattr(ctx, "job", ctx)  # tolerate both wrappers and bare-Job seam
        payload = getattr(job, "payload", None) or {}
        run_id = payload.get("schedule_run_id")
        if not run_id:
            return {"outcome": "failed", "error_code": "missing_run_id"}
        # W28K-1409 — the handler MUST NOT raise: cloud_dog_jobs Worker.run_once
        # re-raises an un-caught handler exception when no fallback policy is set,
        # which propagates out of run_forever_async and KILLS the worker task
        # (worker_running=false), so every later run_now/tick job stays `scheduled`
        # forever on the deployed service. Catch defensively + return a failed
        # outcome so the loop survives any single bad run.
        try:
            run = dispatch_run(run_id)
        except Exception as e:  # noqa: BLE001 — keep the worker loop alive
            _log.exception(f"dispatch_run failed run_id={run_id}: {type(e).__name__}: {e}")
            return {"outcome": "failed", "error_code": "dispatch_error", "schedule_run_id": run_id}
        _HEALTH.dispatched_total += 1
        return {
            "outcome": run.status,
            "schedule_run_id": run.schedule_run_id,
            "error_code": run.error_code,
            "result_ref": run.result_ref,
        }
    finally:
        _HEALTH.in_flight -= 1


def get_worker_health() -> dict[str, Any]:
    return {
        "started_at": _HEALTH.started_at.isoformat() if _HEALTH.started_at else None,
        "last_dispatch_at": _HEALTH.last_dispatch_at.isoformat() if _HEALTH.last_dispatch_at else None,
        "tick_started_at": _HEALTH.tick_started_at.isoformat() if _HEALTH.tick_started_at else None,
        "last_tick_at": _HEALTH.last_tick_at.isoformat() if _HEALTH.last_tick_at else None,
        "last_tick_error": _HEALTH.last_tick_error,
        "ticks_total": _HEALTH.ticks_total,
        "in_flight": _HEALTH.in_flight,
        "dispatched_total": _HEALTH.dispatched_total,
        "queue_backend": _HEALTH.queue_backend,
        "running": _TASK is not None and not _TASK.done(),
        "tick_running": _TICK_TASK is not None and not _TICK_TASK.done(),
    }


async def start_worker() -> None:
    """Start the background worker task. Idempotent."""
    global _TASK, _TICK_TASK, _WORKER
    if _TASK is not None and not _TASK.done():
        _ensure_tick_task_started()
        return
    queue = get_queue()
    backend = queue._backend if hasattr(queue, "_backend") else queue.backend  # noqa: SLF001
    pool_cap = int(config.get("jobs.resource_pools.llm.capacity", 2) or 2)
    pool = ResourcePool()
    _WORKER = Worker(
        backend=backend,
        host_id=str(config.get("service.name", "scheduler-mcp-server")),
        worker_id=f"scheduler-worker-{uuid.uuid4().hex[:8]}",
        resource_pool=pool,
    )
    _WORKER.register_handler("schedule_run", _handle_schedule_run)
    _HEALTH.started_at = _now()
    _HEALTH.queue_backend = type(backend).__name__
    _TASK = asyncio.create_task(_run_forever_with_logging(), name="scheduler-mcp-worker")
    _ensure_tick_task_started()
    _log.info(f"scheduler worker started backend={_HEALTH.queue_backend} pool_capacity={pool_cap}")


async def _run_forever_with_logging() -> None:
    # W28K-1409 — self-healing loop: if run_forever_async ever exits abnormally
    # (a crash that escaped the handler guard), log and RESTART rather than
    # leaving the worker dead (which silently strands every queued run). A clean
    # stop (the _stopped flag → normal return) or cancellation ends the loop.
    while True:
        try:
            worker = _WORKER
            if worker is None:
                raise RuntimeError("scheduler worker is not initialized")
            await worker.run_forever_async()
            return  # clean stop (stop_worker set _stopped)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _log.exception(f"scheduler worker loop crashed; restarting in 1s: {type(e).__name__}: {e}")
            await asyncio.sleep(1)


async def _run_tick_loop() -> None:
    """Run the cron/interval tick periodically for deployed services.

    The worker consumes queued jobs; this loop is the producer for due cron and
    interval schedules. It intentionally swallows per-tick exceptions so one bad
    row cannot permanently stop daily schedule dispatch.
    """
    interval = float(config.get("scheduler.tick.interval_seconds", 30) or 30)
    lease_seconds = int(config.get("scheduler.leader.lease_seconds", 30) or 30)
    while True:
        try:
            result = _tick_once(lease_seconds=lease_seconds)
            _HEALTH.last_tick_at = _now()
            _HEALTH.last_tick_error = None
            _HEALTH.ticks_total += 1
            if result.runs_enqueued:
                _log.info(f"scheduler tick enqueued runs={result.runs_enqueued} advanced={result.advanced}")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _HEALTH.last_tick_at = _now()
            _HEALTH.last_tick_error = f"{type(e).__name__}: {e}"
            _HEALTH.ticks_total += 1
            _log.exception(f"scheduler tick failed; continuing: {type(e).__name__}: {e}")
        await asyncio.sleep(max(interval, 1.0))


async def stop_worker(*, timeout_seconds: float = 5.0) -> None:
    """Stop the background worker. Idempotent."""
    global _TASK, _TICK_TASK, _WORKER
    if _TICK_TASK is not None and not _TICK_TASK.done():
        _TICK_TASK.cancel()
        try:
            await asyncio.wait_for(_TICK_TASK, timeout=timeout_seconds)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _TICK_TASK = None
    if _WORKER is not None:
        try:
            _WORKER.stop()
        except Exception:  # noqa: BLE001
            pass
    if _TASK is not None and not _TASK.done():
        _TASK.cancel()
        try:
            await asyncio.wait_for(_TASK, timeout=timeout_seconds)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _TASK = None
    _WORKER = None
    _log.info("scheduler worker stopped")


async def run_one_for_test() -> int:
    """Test seam: dispatch one job through the queue + worker.
    Returns the count of jobs dispatched (0 if queue empty)."""
    if _WORKER is None:
        # Construct a one-shot worker bound to the queue's backend
        queue = get_queue()
        backend = queue._backend if hasattr(queue, "_backend") else queue.backend  # noqa: SLF001
        worker = Worker(
            backend=backend,
            host_id="scheduler-test",
            worker_id=f"scheduler-test-{uuid.uuid4().hex[:8]}",
        )
        worker.register_handler("schedule_run", _handle_schedule_run)
    else:
        worker = _WORKER
    try:
        result = await worker.run_once_async()
    except Exception as e:  # noqa: BLE001
        _log.warning(f"run_once raised: {e}")
        return 0
    return 1 if result else 0
