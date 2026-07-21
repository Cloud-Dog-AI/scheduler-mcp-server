"""Scheduler tick — W28K-1414.

The tick scans schedules whose next_fire_at <= now AND status='active' AND
enabled=True AND paused=False, creates a ScheduleRun record (status=scheduled),
enqueues a cloud_dog_jobs job, and advances next_fire_at via the trigger
engine. Only the leader (scheduler_mcp.leader.acquire) runs the tick.

Deterministic via scheduler_mcp.clock so tests can freeze and step.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cloud_dog_logging import get_logger
from sqlalchemy import select

from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.schedule import Schedule, ScheduleStatus
from scheduler_mcp.db.models.schedule_run import ScheduleRun, ScheduleRunStatus
from scheduler_mcp.jobs import get_queue
from scheduler_mcp.leader import LeaderLease, acquire, release
from scheduler_mcp.trigger import compute_next_fire

_log = get_logger(__name__)


@dataclass
class TickResult:
    leader_held: bool
    schedules_scanned: int = 0
    runs_enqueued: int = 0
    advanced: int = 0
    enqueued_run_ids: list[str] = field(default_factory=list)
    lease_token: str = ""


def _utcnow() -> datetime:
    n = get_clock().now()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def tick_once(*, lease_seconds: int = 30) -> TickResult:
    """One tick. Returns counts + enqueued run IDs. Idempotent across
    non-leader callers (they see leader_held=False and do nothing).
    """
    lease: LeaderLease = acquire(lease_seconds=lease_seconds)
    if not lease.held:
        return TickResult(leader_held=False, lease_token=lease.token)

    now = _utcnow()
    sm = get_session_manager()
    enqueued: list[str] = []
    fired: list[tuple[str, str]] = []  # (schedule_run_id, schedule_id) for audit emission
    scanned = 0
    advanced = 0
    try:
        with sm.session() as session:
            stmt = select(Schedule).where(
                Schedule.status == ScheduleStatus.active.value,
                Schedule.enabled.is_(True),
                Schedule.paused.is_(False),
            )
            rows = session.execute(stmt).scalars().all()
            scanned = len(rows)
            for sched in rows:
                nfa = sched.next_fire_at
                if nfa is None:
                    continue
                if nfa.tzinfo is None:
                    nfa = nfa.replace(tzinfo=timezone.utc)
                if nfa > now:
                    continue

                run = ScheduleRun(
                    schedule_run_id=f"sr-{uuid.uuid4().hex[:16]}",
                    schedule_id=sched.schedule_id,
                    tenant_id=sched.tenant_id or "default",
                    scheduled_for=nfa,
                    status=ScheduleRunStatus.scheduled.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(run)
                enqueued.append(run.schedule_run_id)
                fired.append((run.schedule_run_id, sched.schedule_id))

                # Submit a JobRequest to cloud_dog_jobs (memory backend in dev; Redis
                # in preprod). W28K-1404g: was previously `queue.enqueue(job_type=, payload=)`
                # which silently AttributeError'd because JobQueue.enqueue doesn't exist;
                # the worker therefore NEVER picked up tick-fired runs and they sat in
                # `scheduled` forever. Correct API is JobRequest + submit.
                try:
                    from cloud_dog_jobs import JobRequest

                    get_queue().submit(
                        JobRequest(
                            job_type="schedule_run",
                            payload={
                                "schedule_run_id": run.schedule_run_id,
                                "schedule_id": sched.schedule_id,
                                "target_type": sched.target_type,
                                "target_ref": sched.target_ref,
                            },
                        )
                    )
                except Exception as e:  # noqa: BLE001
                    _log.warning(f"job submit failed schedule_id={sched.schedule_id} err={e}")

                # Advance next_fire_at via trigger engine
                try:
                    new_next = compute_next_fire(
                        sched.trigger_type,
                        sched.trigger_spec or {},
                        from_time=now,
                        last_fire_at=nfa,
                        timezone_name=getattr(sched, "timezone", None),
                    )
                    sched.last_fire_at = nfa
                    sched.next_fire_at = new_next
                    sched.updated_at = now
                    if new_next is None and sched.trigger_type == "one_shot":
                        sched.status = ScheduleStatus.completed.value
                    advanced += 1
                except Exception as e:  # noqa: BLE001
                    _log.warning(f"trigger advance failed schedule_id={sched.schedule_id} err={e}")
            session.commit()

        # W28K-1407 F-1407-7 — emit a schedule_fired audit row for each ScheduleRun
        # created from a fire-window (after commit, so only persisted runs fire).
        # target_id = schedule_id; correlation_id = schedule_run_id. Audit failure
        # must not break the tick path.
        for _run_id, _sched_id in fired:
            try:
                from scheduler_mcp.audit import audit_event

                audit_event(
                    "schedule_fired",
                    actor="scheduler-mcp",
                    target=_sched_id,
                    outcome="success",
                    correlation_id=_run_id,
                    details={"schedule_id": _sched_id, "schedule_run_id": _run_id},
                )
            except Exception:  # noqa: BLE001
                pass

    finally:
        # Release the lease so the next tick on any node can claim it cleanly.
        # AGENT-LESSONS §6.97 — cooperative leader handoff; production
        # multi-node deployments depend on each tick releasing so the next
        # tick races again.
        release(lease)
    _log.info(
        f"tick complete scanned={scanned} enqueued={len(enqueued)} advanced={advanced} leader_token={lease.token[:8]}"
    )
    return TickResult(
        leader_held=True,
        schedules_scanned=scanned,
        runs_enqueued=len(enqueued),
        advanced=advanced,
        enqueued_run_ids=enqueued,
        lease_token=lease.token,
    )
