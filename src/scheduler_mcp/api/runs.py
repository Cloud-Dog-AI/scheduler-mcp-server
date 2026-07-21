"""/api/v1/runs — schedule_runs read + cancel (W28K-1416)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, true

from scheduler_mcp.audit import AuditQuery, AuditReader, audit_event
from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.chain_step_run import ChainStepRun
from scheduler_mcp.db.models.schedule import Schedule, ScheduleStatus
from scheduler_mcp.db.models.schedule_run import ScheduleRun, ScheduleRunStatus
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal
from scheduler_mcp.quotas import QuotaExceeded, Quotas, check_active_runs
from scheduler_mcp.report import render_run_report_pdf

router = APIRouter(tags=["runs"])


class RunTrigger(BaseModel):
    schedule_id: str = Field(..., min_length=1, max_length=64)


_TERMINAL = {
    ScheduleRunStatus.succeeded.value,
    ScheduleRunStatus.failed.value,
    ScheduleRunStatus.cancelled.value,
    ScheduleRunStatus.skipped.value,
    ScheduleRunStatus.misfired.value,
}

# W28K-1409 F-1409-11 — in-flight (non-terminal, not gate-blocked) statuses. A
# schedule with >= max_active_runs_per_schedule of these is at its concurrency
# quota; a further manual fire is rejected 429.
_ACTIVE = {
    ScheduleRunStatus.scheduled.value,
    ScheduleRunStatus.claimed.value,
    ScheduleRunStatus.queued.value,
    ScheduleRunStatus.running.value,
}

# W28K-1408 — dead-letter set (terminal-failure runs surfaced by the Jobs PS-76
# dead-letter view) and the set a run may be retried/deleted from (finished or
# gate-blocked, never in-flight).
_DEAD_LETTER = {
    ScheduleRunStatus.failed.value,
    ScheduleRunStatus.blocked.value,
    ScheduleRunStatus.misfired.value,
}
_RETRIABLE = _TERMINAL | {ScheduleRunStatus.blocked.value}


def _require(request: Request, scope: str):
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal.has_scope(scope):
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
    return principal


def _now() -> datetime:
    n = get_clock().now()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def _to_dto(r: ScheduleRun) -> dict[str, Any]:
    return {
        "schedule_run_id": r.schedule_run_id,
        "schedule_id": r.schedule_id,
        "tenant_id": r.tenant_id,
        "status": r.status,
        "scheduled_for": r.scheduled_for.isoformat() if r.scheduled_for else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "attempt": r.attempt,
        "trigger_type": r.trigger_type,
        "trigger_source_id": r.trigger_source_id,
        "error_code": r.error_code,
        "root_job_id": r.root_job_id,
        "chain_run_id": r.chain_run_id,
        # W28K-1404g — expose result_ref so AT-tier proof-of-execution rung-(b)
        # (sentinel echoed in captured response body) is observable from the
        # /v1/runs/{id} surface without a second endpoint round-trip.
        "result_ref": r.result_ref,
        "error_summary": r.error_summary,
    }


@router.get("/runs")
def list_runs(
    request: Request,
    schedule_id: str | None = None,
    status: str | None = None,
    dead_letter: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _require(request, "schedules.read")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    sm = get_session_manager()
    with sm.session() as session:
        q = select(ScheduleRun)
        if schedule_id:
            q = q.where(ScheduleRun.schedule_id == schedule_id)
        if status:
            q = q.where(ScheduleRun.status == status)
        # W28K-1408 — Jobs PS-76 dead-letter view: terminal-failure runs.
        if dead_letter:
            q = q.where(ScheduleRun.status.in_(sorted(_DEAD_LETTER)))
        total = int(
            session.execute(
                select(func.count(ScheduleRun.schedule_run_id)).where(
                    q.whereclause if q.whereclause is not None else true()
                )
            ).scalar()
            or 0
        )
        rows = session.execute(q.order_by(ScheduleRun.scheduled_for.desc()).limit(limit).offset(offset)).scalars().all()
        return {
            "items": [_to_dto(r) for r in rows],
            "count": total,
            "limit": limit,
            "offset": offset,
        }


@router.post("/runs", status_code=201)
def trigger_run(payload: RunTrigger, request: Request) -> dict[str, Any]:
    """W28K-1407 F-1407-1 — direct REST manual trigger (mirrors the A2A
    ``schedule.run_now`` skill). Requires scope ``schedules.run_now``.

    201 -> {run_id, schedule_run_id, status, schedule_id}; 404 schedule absent;
    409 schedule paused/not-active. Emits a ``schedule_triggered`` audit row
    (correlation_id = schedule_run_id); the worker then emits the
    dispatched -> started -> {succeeded|failed} lifecycle for that run.
    """
    principal = _require(request, "schedules.run_now")
    sm = get_session_manager()
    now = _now()
    with sm.session() as session:
        s = session.get(Schedule, payload.schedule_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        if bool(s.paused) or s.status != ScheduleStatus.active.value:
            raise HTTPException(
                status_code=409,
                detail=f"Schedule not runnable (paused={bool(s.paused)}, status={s.status})",
            )
        # W28K-1409 F-1409-11 — concurrency quota: a schedule may have at most
        # ``max_active_runs_per_schedule`` in-flight runs. Blocks concurrent
        # manual fires; QuotaExceeded -> HTTP 429.
        active_count = int(
            session.execute(
                select(func.count(ScheduleRun.schedule_run_id)).where(
                    ScheduleRun.schedule_id == s.schedule_id,
                    ScheduleRun.status.in_(sorted(_ACTIVE)),
                )
            ).scalar()
            or 0
        )
        try:
            check_active_runs(active_count, quotas=Quotas.from_config())
        except QuotaExceeded as q:
            raise HTTPException(
                status_code=429,
                detail={"code": q.code, "message": str(q), "limit": q.limit, "observed": q.observed},
            ) from q
        run = ScheduleRun(
            schedule_run_id=f"sr-{uuid.uuid4().hex[:16]}",
            schedule_id=s.schedule_id,
            tenant_id=s.tenant_id or "default",
            trigger_type="manual",
            scheduled_for=now,
            status=ScheduleRunStatus.scheduled.value,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()
        run_id = run.schedule_run_id
        schedule_id = s.schedule_id
        target_type = s.target_type
        target_ref = s.target_ref

    # Fire the run by submitting a schedule_run job to the queue (same path as
    # the A2A run_now skill + the tick). The worker drains it and dispatches.
    try:
        from cloud_dog_jobs import JobRequest

        from scheduler_mcp.jobs import get_queue

        get_queue().submit(
            JobRequest(
                job_type="schedule_run",
                payload={
                    "schedule_run_id": run_id,
                    "schedule_id": schedule_id,
                    "target_type": target_type,
                    "target_ref": target_ref,
                },
            )
        )
    except Exception:  # noqa: BLE001 — audit + run row already persisted; queue submit best-effort
        pass

    audit_event(
        "schedule_triggered",
        actor=principal.username,
        target=schedule_id,
        outcome="success",
        correlation_id=run_id,
        details={"source": "rest", "action": "run_now", "schedule_id": schedule_id, "schedule_run_id": run_id},
    )
    return {
        "run_id": run_id,
        "schedule_run_id": run_id,
        "status": ScheduleRunStatus.scheduled.value,
        "schedule_id": schedule_id,
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    _require(request, "schedules.read")
    sm = get_session_manager()
    with sm.session() as session:
        r = session.get(ScheduleRun, run_id)
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        return _to_dto(r)


@router.post("/runs/{run_id}/report")
def run_report(run_id: str, request: Request, format: str = Query("pdf", pattern="^(pdf)$")) -> Response:
    """W28K-1409 F-1409-6 — render a run's result + chain step trace + audit
    lifecycle as a PDF. Requires scope ``schedules.read`` (read-only, mirrors
    GET /runs/{id}). 404 if the run is absent. Returns ``application/pdf``."""
    _require(request, "schedules.read")
    sm = get_session_manager()
    with sm.session() as session:
        r = session.get(ScheduleRun, run_id)
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        run_dto = _to_dto(r)
        steps: list[dict[str, Any]] = []
        if r.chain_run_id:
            step_rows = (
                session.execute(
                    select(ChainStepRun)
                    .where(ChainStepRun.chain_run_id == r.chain_run_id)
                    .order_by(ChainStepRun.started_at.asc().nulls_last(), ChainStepRun.step_id.asc())
                )
                .scalars()
                .all()
            )
            steps = [
                {
                    "step_id": s.step_id,
                    "step_type": s.step_type,
                    "status": s.status,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                    "attempt": s.attempt,
                    "error_summary": s.error_summary,
                    "result_ref": s.result_ref,
                }
                for s in step_rows
            ]

    # Audit lifecycle for this run (correlation_id == run id), oldest first.
    try:
        events, _ = AuditReader().query(AuditQuery(correlation_id=run_id, limit=200, order="asc"))
        audit_events = [e.to_dict() for e in events]
    except Exception:  # noqa: BLE001 — report still renders without the audit section
        audit_events = []

    pdf = render_run_report_pdf(run_dto, steps, audit_events)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="run-report-{run_id}.pdf"'},
    )


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    _require(request, "schedules.write")
    sm = get_session_manager()
    with sm.session() as session:
        r = session.get(ScheduleRun, run_id)
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        if r.status in _TERMINAL:
            raise HTTPException(status_code=409, detail=f"Run is already terminal: {r.status}")
        r.status = ScheduleRunStatus.cancelled.value
        r.finished_at = _now()
        r.updated_at = _now()
        session.commit()
        session.refresh(r)
        return _to_dto(r)


@router.post("/runs/{run_id}/retry", status_code=201)
def retry_run(run_id: str, request: Request) -> dict[str, Any]:
    """W28K-1408 — re-dispatch a finished/gate-blocked run as a new retry run
    (trigger_type=retry, trigger_source_id=<original>). 201 with the new run;
    404 run/schedule absent; 409 if the original run is still in-flight or the
    schedule is paused/not-active. Requires scope schedules.run_now."""
    principal = _require(request, "schedules.run_now")
    sm = get_session_manager()
    now = _now()
    with sm.session() as session:
        orig = session.get(ScheduleRun, run_id)
        if orig is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if orig.status not in _RETRIABLE:
            raise HTTPException(status_code=409, detail=f"Run is still in-flight: {orig.status}")
        s = session.get(Schedule, orig.schedule_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        if bool(s.paused) or s.status != ScheduleStatus.active.value:
            raise HTTPException(
                status_code=409, detail=f"Schedule not runnable (paused={bool(s.paused)}, status={s.status})"
            )
        new = ScheduleRun(
            schedule_run_id=f"sr-{uuid.uuid4().hex[:16]}",
            schedule_id=s.schedule_id,
            tenant_id=s.tenant_id or "default",
            triggered_by="user",
            trigger_type="retry",
            trigger_source_id=run_id,
            attempt=int(orig.attempt or 1) + 1,
            scheduled_for=now,
            status=ScheduleRunStatus.scheduled.value,
            created_at=now,
            updated_at=now,
        )
        session.add(new)
        session.commit()
        new_run_id = new.schedule_run_id
        schedule_id = s.schedule_id
        target_type = s.target_type
        target_ref = s.target_ref

    try:
        from cloud_dog_jobs import JobRequest

        from scheduler_mcp.jobs import get_queue

        get_queue().submit(
            JobRequest(
                job_type="schedule_run",
                payload={
                    "schedule_run_id": new_run_id,
                    "schedule_id": schedule_id,
                    "target_type": target_type,
                    "target_ref": target_ref,
                },
            )
        )
    except Exception:  # noqa: BLE001 — run row + audit already persisted; queue submit best-effort
        pass

    audit_event(
        "schedule_triggered",
        actor=principal.username,
        target=schedule_id,
        outcome="success",
        correlation_id=new_run_id,
        details={
            "source": "retry",
            "original_run_id": run_id,
            "schedule_id": schedule_id,
            "schedule_run_id": new_run_id,
        },
    )
    return {
        "run_id": new_run_id,
        "schedule_run_id": new_run_id,
        "status": ScheduleRunStatus.scheduled.value,
        "schedule_id": schedule_id,
        "retried_from": run_id,
    }


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request):
    """W28K-1408 — delete a finished/gate-blocked run record (never an in-flight
    run). 404 absent; 409 in-flight. Requires scope schedules.admin."""
    _require(request, "schedules.admin")
    sm = get_session_manager()
    with sm.session() as session:
        r = session.get(ScheduleRun, run_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if r.status not in _RETRIABLE:
            raise HTTPException(status_code=409, detail=f"Cannot delete an in-flight run: {r.status}")
        session.delete(r)
        session.commit()
    return None
