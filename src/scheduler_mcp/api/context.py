"""/api/v1/context — scheduler context key/value store (W28K-1419)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.scheduler_context import SchedulerContext
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal
from scheduler_mcp.quotas import QuotaExceeded, check_context_size

router = APIRouter(tags=["context"])


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


class ContextPut(BaseModel):
    scope_type: str = Field(default="schedule_run")
    scope_id: str
    key: str = Field(..., min_length=1, max_length=255)
    value: Any
    value_type: str = Field(default="json")
    visibility: str = Field(default="private")


def _to_dto(c: SchedulerContext) -> dict[str, Any]:
    return {
        "context_entry_id": c.context_entry_id,
        "scope_type": c.scope_type,
        "scope_id": c.scope_id,
        "key": c.key,
        "value": c.value,
        "value_type": c.value_type,
        "visibility": c.visibility,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.put("/context")
def put_context(payload: ContextPut, request: Request) -> dict[str, Any]:
    principal = _require(request, "schedules.write")
    try:
        size = len(json.dumps(payload.value).encode("utf-8"))
        check_context_size(size)
    except QuotaExceeded as q:
        raise HTTPException(
            status_code=429, detail={"code": q.code, "message": str(q), "limit": q.limit, "observed": q.observed}
        ) from q

    sm = get_session_manager()
    now = _now()
    with sm.session() as session:
        existing = session.execute(
            select(SchedulerContext).where(
                SchedulerContext.scope_type == payload.scope_type,
                SchedulerContext.scope_id == payload.scope_id,
                SchedulerContext.key == payload.key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.value = payload.value
            existing.value_type = payload.value_type
            existing.visibility = payload.visibility
            existing.updated_at = now
            existing.updated_by = principal.username
            session.commit()
            session.refresh(existing)
            return _to_dto(existing)
        c = SchedulerContext(
            context_entry_id=f"ctx-{uuid.uuid4().hex[:16]}",
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            key=payload.key,
            value=payload.value,
            value_type=payload.value_type,
            visibility=payload.visibility,
            created_by=principal.username,
            updated_by=principal.username,
            created_at=now,
            updated_at=now,
        )
        session.add(c)
        session.commit()
        session.refresh(c)
        return _to_dto(c)


@router.get("/context")
def list_context(scope_type: str, scope_id: str, request: Request) -> dict[str, Any]:
    _require(request, "schedules.read")
    sm = get_session_manager()
    with sm.session() as session:
        rows = (
            session.execute(
                select(SchedulerContext)
                .where(
                    SchedulerContext.scope_type == scope_type,
                    SchedulerContext.scope_id == scope_id,
                )
                .order_by(SchedulerContext.key)
            )
            .scalars()
            .all()
        )
        return {"items": [_to_dto(c) for c in rows], "count": len(rows)}


@router.get("/context/{entry_id}")
def get_context_entry(entry_id: str, request: Request) -> dict[str, Any]:
    """W28K-1407 F-1407-5 — single context entry by id (404 when absent)."""
    _require(request, "schedules.read")
    sm = get_session_manager()
    with sm.session() as session:
        c = session.get(SchedulerContext, entry_id)
        if not c:
            raise HTTPException(status_code=404, detail="Context entry not found")
        return _to_dto(c)


@router.delete("/context/{context_entry_id}", status_code=204)
def delete_context(context_entry_id: str, request: Request):
    _require(request, "schedules.write")
    sm = get_session_manager()
    with sm.session() as session:
        c = session.get(SchedulerContext, context_entry_id)
        if not c:
            raise HTTPException(status_code=404, detail="Context entry not found")
        session.delete(c)
        session.commit()
    return None
