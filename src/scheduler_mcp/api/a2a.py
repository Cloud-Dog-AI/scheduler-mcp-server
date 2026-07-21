"""/a2a + /.well-known/agent.json — W28K-1428 A2A consumer wiring.

Exposes the scheduler as a discoverable A2A agent. Skills mirror the existing
/v1 surfaces so other agents (chat-client, expert-agent, sql-agent) can
submit schedules + chains via A2A protocol:

  - schedule.create     → POST /v1/schedules
  - schedule.run_now    → POST /v1/runs (manual trigger; persists a run)
  - schedule.list_runs  → GET  /v1/runs?schedule_id=...
  - chain.compile       → validate definition via scheduler_mcp.chain.compile_chain
  - chain.run           → create a chain + run record
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from scheduler_mcp import config
from scheduler_mcp.chain import ChainCompileError, compile_chain
from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.chain import Chain, ChainStatus
from scheduler_mcp.db.models.schedule import InvalidTargetType, Schedule, ScheduleStatus, validate_target_type
from scheduler_mcp.db.models.schedule_run import ScheduleRun, ScheduleRunStatus
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal
from scheduler_mcp.trigger import TriggerSpecError, compute_next_fire

router = APIRouter(tags=["a2a"])


SKILLS = [
    {
        "id": "schedule.create",
        "name": "Create a schedule",
        "description": "Create a cron/interval/one_shot/manual/condition_watch schedule",
        "input_schema": {
            "type": "object",
            "required": ["name", "trigger_type", "trigger_spec", "target_type", "target_ref"],
            "properties": {
                "name": {"type": "string"},
                "trigger_type": {
                    "type": "string",
                    "enum": ["cron", "interval", "one_shot", "manual", "condition_watch"],
                },
                "trigger_spec": {"type": "object"},
                "target_type": {"type": "string"},
                "target_ref": {"type": "string"},
            },
        },
    },
    {
        "id": "schedule.run_now",
        "name": "Trigger a schedule immediately",
        "description": "Create a manual ScheduleRun for an existing schedule",
        "input_schema": {
            "type": "object",
            "required": ["schedule_id"],
            "properties": {"schedule_id": {"type": "string"}},
        },
    },
    {
        "id": "schedule.list_runs",
        "name": "List runs for a schedule",
        "description": "Return recent runs filtered by schedule_id",
        "input_schema": {
            "type": "object",
            "required": ["schedule_id"],
            "properties": {"schedule_id": {"type": "string"}, "limit": {"type": "integer"}},
        },
    },
    {
        "id": "chain.compile",
        "name": "Compile a chain definition",
        "description": "Validate chain JSON (cycle detection + step type whitelist)",
        "input_schema": {
            "type": "object",
            "required": ["definition"],
            "properties": {"definition": {"type": "object"}},
        },
    },
    {
        "id": "chain.run",
        "name": "Persist a chain and prepare for execution",
        "description": "Create the Chain row + return chain_id",
        "input_schema": {
            "type": "object",
            "required": ["name", "definition"],
            "properties": {"name": {"type": "string"}, "definition": {"type": "object"}},
        },
    },
]


def _require_auth(request: Request, scope: str = "schedules.write"):
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal.has_scope(scope):
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
    return principal


def _now() -> datetime:
    n = get_clock().now()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


@router.get("/.well-known/agent.json", include_in_schema=False)
def agent_card(request: Request) -> dict[str, Any]:
    """A2A agent discovery card. Public — no auth required (consumers
    must be able to discover before they can authenticate)."""
    base = str(request.base_url).rstrip("/")
    return {
        "schema_version": "0.2",
        "name": str(config.get("service.name", "scheduler-mcp-server")),
        "version": str(config.get("service.version", "0.1.0")),
        "description": "Scheduler MCP — schedules, chains, and runs control plane (W28K).",
        "url": base,
        "skills_url": f"{base}/a2a/skills",
        "auth": {
            "modes": ["api_key", "cookie"],
            "headers": ["x-api-key", "authorization"],
        },
        "skills": [{"id": s["id"], "name": s["name"], "description": s["description"]} for s in SKILLS],
    }


@router.get("/a2a/health")
def a2a_health() -> dict[str, str]:
    return {"status": "ok", "transport": "https"}


@router.get("/a2a/skills")
def list_skills() -> dict[str, Any]:
    return {"items": SKILLS, "count": len(SKILLS)}


class SkillCall(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


@router.post("/a2a/skills/{skill_id}")
def call_skill(skill_id: str, payload: SkillCall, request: Request) -> dict[str, Any]:
    """Execute an A2A skill. All skills require schedules.write or
    schedules.read scope (per-skill enforced)."""
    correlation_id = payload.correlation_id or uuid.uuid4().hex[:16]

    if skill_id == "schedule.create":
        principal = _require_auth(request, scope="schedules.write")
        inp = payload.input
        try:
            validate_target_type(inp.get("target_type", ""))
        except InvalidTargetType as e:
            raise HTTPException(status_code=422, detail={"code": "invalid_target_type", "message": str(e)}) from e
        try:
            nxt = compute_next_fire(inp.get("trigger_type", ""), inp.get("trigger_spec", {}))
        except TriggerSpecError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        sm = get_session_manager()
        now = _now()
        with sm.session() as session:
            s = Schedule(
                schedule_id=f"sch-{uuid.uuid4().hex[:16]}",
                tenant_id="default",
                name=inp["name"],
                description=inp.get("description"),
                enabled=True,
                paused=False,
                status=ScheduleStatus.active.value,
                trigger_type=inp["trigger_type"],
                trigger_spec=inp["trigger_spec"],
                timezone=inp.get("timezone", "UTC"),
                next_fire_at=nxt,
                target_type=inp["target_type"],
                target_ref=inp["target_ref"],
                target_spec=inp.get("target_spec", {}),
                owner_user_id=principal.username,
                created_by=principal.username,
                updated_by=principal.username,
                created_at=now,
                updated_at=now,
            )
            session.add(s)
            session.commit()
            session.refresh(s)
            return {
                "correlation_id": correlation_id,
                "output": {
                    "schedule_id": s.schedule_id,
                    "next_fire_at": s.next_fire_at.isoformat() if s.next_fire_at else None,
                },
            }

    if skill_id == "schedule.run_now":
        _require_auth(request, scope="schedules.run_now")
        sid = payload.input.get("schedule_id")
        if not sid:
            raise HTTPException(status_code=400, detail="schedule_id required")
        sm = get_session_manager()
        with sm.session() as session:
            s = session.get(Schedule, sid)
            if not s:
                raise HTTPException(status_code=404, detail="Schedule not found")
            now = _now()
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
            # W28K-1404g — fire the run by submitting a JobRequest to the
            # cloud_dog_jobs queue. The worker drains it and invokes
            # dispatch_run() which goes through scheduler_mcp.adapters and
            # calls the real sibling MCP / code-runner / external HTTP / etc.
            # Without this the row sits in `scheduled` forever and every AT
            # scenario FAILs on the polling timeout (rung-(a)/(b)/(c) is
            # unreachable without actual dispatch).
            try:
                from cloud_dog_jobs import JobRequest

                from scheduler_mcp.jobs import get_queue

                get_queue().submit(
                    JobRequest(
                        job_type="schedule_run",
                        payload={
                            "schedule_run_id": run.schedule_run_id,
                            "schedule_id": s.schedule_id,
                            "target_type": s.target_type,
                            "target_ref": s.target_ref,
                        },
                    )
                )
            except Exception:  # noqa: BLE001
                pass
            return {"correlation_id": correlation_id, "output": {"schedule_run_id": run.schedule_run_id}}

    if skill_id == "schedule.list_runs":
        _require_auth(request, scope="schedules.read")
        sid = payload.input.get("schedule_id")
        limit = int(payload.input.get("limit", 25) or 25)
        if not sid:
            raise HTTPException(status_code=400, detail="schedule_id required")
        from sqlalchemy import select

        sm = get_session_manager()
        with sm.session() as session:
            rows = (
                session.execute(
                    select(ScheduleRun)
                    .where(ScheduleRun.schedule_id == sid)
                    .order_by(ScheduleRun.scheduled_for.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return {
                "correlation_id": correlation_id,
                "output": {
                    "items": [
                        {
                            "schedule_run_id": r.schedule_run_id,
                            "status": r.status,
                            "scheduled_for": r.scheduled_for.isoformat() if r.scheduled_for else None,
                        }
                        for r in rows
                    ],
                    "count": len(rows),
                },
            }

    if skill_id == "chain.compile":
        _require_auth(request, scope="schedules.read")
        definition = payload.input.get("definition")
        if not isinstance(definition, dict):
            raise HTTPException(status_code=400, detail="definition (object) required")
        try:
            compiled = compile_chain("ch-validation", 1, definition)
        except ChainCompileError as e:
            return {"correlation_id": correlation_id, "output": {"valid": False, "error": str(e)}}
        return {
            "correlation_id": correlation_id,
            "output": {"valid": True, "execution_order": compiled.execution_order, "step_count": len(compiled.steps)},
        }

    if skill_id == "chain.run":
        principal = _require_auth(request, scope="schedules.write")
        name = payload.input.get("name")
        definition = payload.input.get("definition")
        if not name or not isinstance(definition, dict):
            raise HTTPException(status_code=400, detail="name + definition required")
        chain_id = f"ch-{uuid.uuid4().hex[:16]}"
        try:
            compile_chain(chain_id, 1, definition)
        except ChainCompileError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        sm = get_session_manager()
        now = _now()
        with sm.session() as session:
            c = Chain(
                chain_id=chain_id,
                name=name,
                description=payload.input.get("description"),
                version=1,
                definition=definition,
                status=ChainStatus.active.value,
                owner_user_id=principal.username,
                created_at=now,
                updated_at=now,
            )
            session.add(c)
            session.commit()
            return {"correlation_id": correlation_id, "output": {"chain_id": chain_id}}

    raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}")
