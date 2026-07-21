"""/api/v1/schedules — full CRUD surface (W28K-1415).

Phase 2 adds POST / GET-by-id / PATCH / DELETE on top of the Phase 1 list.
RBAC scopes:
  schedules.read   — list + get
  schedules.write  — create + patch
  schedules.admin  — delete + force re-enable
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.schedule import InvalidTargetType, Schedule, ScheduleStatus, validate_target_type
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal
from scheduler_mcp.quotas import QuotaExceeded, Quotas, check_min_interval, check_schedules_per_user
from scheduler_mcp.trigger import TriggerSpecError, compute_next_fire

router = APIRouter(tags=["schedules"])


def _require(request: Request, scope: str):
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal.has_scope(scope):
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
    return principal


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    trigger_type: str
    trigger_spec: dict = Field(default_factory=dict)
    target_type: str
    target_ref: str
    target_spec: dict = Field(default_factory=dict)
    timezone: str = "UTC"
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger_type: str | None = None
    trigger_spec: dict | None = None
    timezone: str | None = None
    target_type: str | None = None
    target_ref: str | None = None
    target_spec: dict | None = None
    enabled: bool | None = None
    paused: bool | None = None
    status: str | None = None


def _to_dto(s: Schedule) -> dict[str, Any]:
    return {
        "schedule_id": s.schedule_id,
        "tenant_id": s.tenant_id,
        "name": s.name,
        "description": s.description,
        "enabled": bool(s.enabled),
        "paused": bool(s.paused),
        "status": s.status,
        "trigger_type": s.trigger_type,
        "trigger_spec": s.trigger_spec or {},
        "timezone": s.timezone,
        "next_fire_at": s.next_fire_at.isoformat() if s.next_fire_at else None,
        "last_fire_at": s.last_fire_at.isoformat() if s.last_fire_at else None,
        "target_type": s.target_type,
        "target_ref": s.target_ref,
        "target_spec": s.target_spec or {},
        "owner_user_id": s.owner_user_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _now() -> datetime:
    n = get_clock().now()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


@router.get("/schedules")
def list_schedules(request: Request, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    _require(request, "schedules.read")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    sm = get_session_manager()
    with sm.session() as session:
        total = int(session.execute(select(func.count(Schedule.schedule_id))).scalar() or 0)
        rows = (
            session.execute(select(Schedule).order_by(Schedule.created_at.desc()).limit(limit).offset(offset))
            .scalars()
            .all()
        )
        return {
            "items": [_to_dto(s) for s in rows],
            "count": total,
            "limit": limit,
            "offset": offset,
        }


@router.post("/schedules", status_code=201)
def create_schedule(payload: ScheduleCreate, request: Request) -> dict[str, Any]:
    principal = _require(request, "schedules.write")
    quotas = Quotas.from_config()
    sm = get_session_manager()
    with sm.session() as session:
        count = int(
            session.execute(
                select(func.count(Schedule.schedule_id)).where(Schedule.owner_user_id == principal.username)
            ).scalar()
            or 0
        )
        try:
            check_schedules_per_user(count, quotas=quotas)
            check_min_interval(payload.trigger_type, payload.trigger_spec, quotas=quotas)
        except QuotaExceeded as q:
            raise HTTPException(
                status_code=429, detail={"code": q.code, "message": str(q), "limit": q.limit, "observed": q.observed}
            ) from q

        try:
            validate_target_type(payload.target_type)
        except InvalidTargetType as e:
            raise HTTPException(status_code=422, detail={"code": "invalid_target_type", "message": str(e)}) from e

        try:
            nxt = compute_next_fire(payload.trigger_type, payload.trigger_spec, timezone_name=payload.timezone)
        except TriggerSpecError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        now = _now()
        s = Schedule(
            schedule_id=f"sch-{uuid.uuid4().hex[:16]}",
            tenant_id="default",
            name=payload.name,
            description=payload.description,
            enabled=payload.enabled,
            paused=False,
            status=ScheduleStatus.active.value,
            trigger_type=payload.trigger_type,
            trigger_spec=payload.trigger_spec,
            timezone=payload.timezone,
            next_fire_at=nxt,
            target_type=payload.target_type,
            target_ref=payload.target_ref,
            target_spec=payload.target_spec,
            owner_user_id=principal.username,
            created_by=principal.username,
            updated_by=principal.username,
            created_at=now,
            updated_at=now,
        )
        session.add(s)
        session.commit()
        session.refresh(s)
        return _to_dto(s)


@router.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: str, request: Request) -> dict[str, Any]:
    _require(request, "schedules.read")
    sm = get_session_manager()
    with sm.session() as session:
        s = session.get(Schedule, schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return _to_dto(s)


@router.patch("/schedules/{schedule_id}")
def patch_schedule(schedule_id: str, payload: SchedulePatch, request: Request) -> dict[str, Any]:
    principal = _require(request, "schedules.write")
    sm = get_session_manager()
    with sm.session() as session:
        s = session.get(Schedule, schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail="Schedule not found")
        changes = payload.model_dump(exclude_unset=True)
        if changes.get("target_type") is not None:
            try:
                validate_target_type(str(changes["target_type"]))
            except InvalidTargetType as e:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "invalid_target_type", "message": str(e)},
                ) from e
        recompute_next = False
        for k, v in changes.items():
            # W28K-1409 F-1409-9 — a timezone change also re-anchors the cron fire.
            if k in ("trigger_type", "trigger_spec", "timezone") and v is not None:
                recompute_next = True
            setattr(s, k, v)
        if recompute_next:
            try:
                s.next_fire_at = compute_next_fire(s.trigger_type, s.trigger_spec or {}, timezone_name=s.timezone)
            except TriggerSpecError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        s.updated_at = _now()
        s.updated_by = principal.username
        session.commit()
        session.refresh(s)
        return _to_dto(s)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, request: Request):
    _require(request, "schedules.admin")
    sm = get_session_manager()
    with sm.session() as session:
        s = session.get(Schedule, schedule_id)
        if not s:
            raise HTTPException(status_code=404, detail="Schedule not found")
        session.delete(s)
        session.commit()
    return None


# Negative-auth §0C — keep at this router, same as Phase 1.
@router.get("/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail={"user": None})
    return {
        "user": {
            "api_key_id": principal.api_key_id,
            "username": principal.username,
            "scopes": list(principal.scopes),
        }
    }
