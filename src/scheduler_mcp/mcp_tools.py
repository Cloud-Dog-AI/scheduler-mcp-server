"""MCP tool catalogue (W28K-1404d).

Mirrors the A2A skill catalogue so the same 5 operations are reachable
via MCP JSON-RPC (POST /mcp) and via A2A POST /a2a/skills/{id}. The
Ps72McpConsole and Ps72A2aConsole pages call these surfaces directly.

Tool handlers reuse the same internal helpers as the A2A skill handlers
in scheduler_mcp.api.a2a — keeping a single source of truth for the
scheduler's externally callable verbs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from cloud_dog_api_kit.mcp import ToolContract

from scheduler_mcp.chain import ChainCompileError, compile_chain
from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.chain import Chain, ChainStatus
from scheduler_mcp.db.models.schedule import InvalidTargetType, Schedule, ScheduleStatus, validate_target_type
from scheduler_mcp.db.models.schedule_run import ScheduleRun, ScheduleRunStatus
from scheduler_mcp.trigger import TriggerSpecError, compute_next_fire


def _now() -> datetime:
    n = get_clock().now()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def _h_schedule_create(payload: dict[str, Any], *_args: Any) -> dict[str, Any]:
    name = payload.get("name")
    trigger_type = payload.get("trigger_type")
    trigger_spec = payload.get("trigger_spec", {})
    target_type = payload.get("target_type")
    target_ref = payload.get("target_ref")
    if not (name and trigger_type and target_type and target_ref):
        return {"error": "missing_required_fields", "required": ["name", "trigger_type", "target_type", "target_ref"]}
    try:
        validate_target_type(target_type)
    except InvalidTargetType as e:
        return {"error": "invalid_target_type", "detail": str(e)}
    try:
        next_fire = compute_next_fire(
            trigger_type,
            trigger_spec,
            from_time=_now(),
            timezone_name=payload.get("timezone", "UTC"),
        )
    except TriggerSpecError as e:
        return {"error": "invalid_trigger_spec", "detail": str(e)}
    sid = f"sch-{uuid.uuid4().hex[:16]}"
    now = _now()
    with get_session_manager().session() as session:
        session.add(
            Schedule(
                schedule_id=sid,
                tenant_id="default",
                name=name,
                enabled=True,
                paused=False,
                status=ScheduleStatus.active.value,
                trigger_type=trigger_type,
                trigger_spec=trigger_spec or {},
                target_type=target_type,
                target_ref=target_ref,
                target_spec=payload.get("target_spec", {}),
                timezone=payload.get("timezone", "UTC"),
                misfire_policy=payload.get("misfire_policy", "skip"),
                next_fire_at=next_fire,
                owner_user_id="mcp",
                created_by="mcp",
                updated_by="mcp",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return {"schedule_id": sid, "next_fire_at": next_fire.isoformat() if next_fire else None}


def _h_schedule_run_now(payload: dict[str, Any], *_args: Any) -> dict[str, Any]:
    sid = payload.get("schedule_id")
    if not sid:
        return {"error": "missing_schedule_id"}
    rid = f"sr-{uuid.uuid4().hex[:16]}"
    now = _now()
    target_type: str | None = None
    target_ref: str | None = None
    with get_session_manager().session() as session:
        sched = session.get(Schedule, sid)
        if sched is None:
            return {"error": "schedule_not_found", "schedule_id": sid}
        target_type = sched.target_type
        target_ref = sched.target_ref
        session.add(
            ScheduleRun(
                schedule_run_id=rid,
                schedule_id=sid,
                tenant_id=sched.tenant_id,
                scheduled_for=now,
                status=ScheduleRunStatus.scheduled.value,
                triggered_by="user",
                trigger_source_id="mcp.schedule_run_now",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    # W28K-1404g — actually fire the run: submit a JobRequest to the
    # cloud_dog_jobs queue so the worker drains it and invokes
    # `dispatch_run(rid)`. Prior to this fix the row sat in `scheduled` forever
    # and no manual-fire path was usable, breaking every AT scenario.
    try:
        from cloud_dog_jobs import JobRequest

        from scheduler_mcp.jobs import get_queue

        get_queue().submit(
            JobRequest(
                job_type="schedule_run",
                payload={
                    "schedule_run_id": rid,
                    "schedule_id": sid,
                    "target_type": target_type,
                    "target_ref": target_ref,
                },
            )
        )
    except Exception:  # noqa: BLE001
        pass
    return {"schedule_run_id": rid, "schedule_id": sid, "status": "scheduled"}


def _h_schedule_list_runs(payload: dict[str, Any], *_args: Any) -> dict[str, Any]:
    sid = payload.get("schedule_id")
    limit = int(payload.get("limit", 50))
    items: list[dict[str, Any]] = []
    with get_session_manager().session() as session:
        from sqlalchemy import select

        stmt = select(ScheduleRun).order_by(ScheduleRun.created_at.desc()).limit(limit)
        if sid:
            stmt = stmt.where(ScheduleRun.schedule_id == sid)
        for r in session.scalars(stmt):
            items.append(
                {
                    "schedule_run_id": r.schedule_run_id,
                    "schedule_id": r.schedule_id,
                    "status": r.status,
                    "scheduled_for": r.scheduled_for.isoformat() if r.scheduled_for else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
            )
    return {"items": items, "count": len(items)}


def _h_chain_compile(payload: dict[str, Any], *_args: Any) -> dict[str, Any]:
    definition = payload.get("definition")
    if not isinstance(definition, dict):
        return {"error": "missing_definition"}
    try:
        compiled = compile_chain("mcp-tools", 1, definition)
    except ChainCompileError as e:
        return {"valid": False, "error": str(e)}
    return {"valid": True, "steps": len(compiled.steps)}


def _h_chain_run(payload: dict[str, Any], *_args: Any) -> dict[str, Any]:
    name = payload.get("name", "mcp-chain")
    definition = payload.get("definition")
    if not isinstance(definition, dict):
        return {"error": "missing_definition"}
    try:
        compile_chain("mcp-tools", 1, definition)
    except ChainCompileError as e:
        return {"valid": False, "error": str(e)}
    cid = f"ch-{uuid.uuid4().hex[:16]}"
    now = _now()
    with get_session_manager().session() as session:
        session.add(
            Chain(
                chain_id=cid,
                name=name,
                description=payload.get("description"),
                version=1,
                definition=definition,
                status=ChainStatus.active.value,
                owner_user_id="mcp",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return {"chain_id": cid, "status": "created"}


SCHEDULE_CREATE_SCHEMA = {
    "type": "object",
    "required": ["name", "trigger_type", "trigger_spec", "target_type", "target_ref"],
    "properties": {
        "name": {"type": "string"},
        "trigger_type": {"type": "string", "enum": ["cron", "interval", "one_shot", "manual", "condition_watch"]},
        "trigger_spec": {"type": "object"},
        "target_type": {"type": "string"},
        "target_ref": {"type": "string"},
        "target_spec": {"type": "object"},
        "timezone": {"type": "string"},
    },
}


def build_tool_contracts() -> dict[str, ToolContract]:
    return {
        "schedule.create": ToolContract(
            name="schedule.create",
            handler=_h_schedule_create,
            description="Create a cron/interval/one_shot/manual/condition_watch schedule.",
            input_schema=SCHEDULE_CREATE_SCHEMA,
        ),
        "schedule.run_now": ToolContract(
            name="schedule.run_now",
            handler=_h_schedule_run_now,
            description="Trigger a schedule immediately (creates a ScheduleRun).",
            input_schema={
                "type": "object",
                "required": ["schedule_id"],
                "properties": {"schedule_id": {"type": "string"}},
            },
        ),
        "schedule.list_runs": ToolContract(
            name="schedule.list_runs",
            handler=_h_schedule_list_runs,
            description="List schedule runs, optionally filtered by schedule_id.",
            input_schema={
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
        ),
        "chain.compile": ToolContract(
            name="chain.compile",
            handler=_h_chain_compile,
            description="Validate a chain definition without persisting it.",
            input_schema={
                "type": "object",
                "required": ["definition"],
                "properties": {"definition": {"type": "object"}},
            },
        ),
        "chain.run": ToolContract(
            name="chain.run",
            handler=_h_chain_run,
            description="Compile + persist a chain (returns chain_id).",
            input_schema={
                "type": "object",
                "required": ["definition"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "definition": {"type": "object"},
                },
            },
        ),
    }
