"""/api/v1/chains — chain CRUD (W28K-1417 + W28K-1418)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from scheduler_mcp.audit import audit_event
from scheduler_mcp.chain import ChainCompileError, compile_chain
from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.chain import Chain, ChainStatus
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal

router = APIRouter(tags=["chains"])


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


class ChainCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    definition: dict = Field(...)


class ChainPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    definition: dict | None = None


def _to_dto(c: Chain, *, with_definition: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "chain_id": c.chain_id,
        "name": c.name,
        "description": c.description,
        "version": c.version,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if with_definition:
        out["definition"] = c.definition or {}
    return out


@router.get("/chains")
def list_chains(request: Request, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    _require(request, "schedules.read")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    sm = get_session_manager()
    with sm.session() as session:
        total = int(session.execute(select(func.count(Chain.chain_id))).scalar() or 0)
        rows = (
            session.execute(select(Chain).order_by(Chain.created_at.desc()).limit(limit).offset(offset)).scalars().all()
        )
        return {
            "items": [_to_dto(c, with_definition=False) for c in rows],
            "count": total,
            "limit": limit,
            "offset": offset,
        }


@router.post("/chains", status_code=201)
def create_chain(payload: ChainCreate, request: Request) -> dict[str, Any]:
    principal = _require(request, "schedules.write")
    chain_id = f"ch-{uuid.uuid4().hex[:16]}"
    try:
        compile_chain(chain_id, 1, payload.definition)
    except ChainCompileError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    sm = get_session_manager()
    now = _now()
    with sm.session() as session:
        c = Chain(
            chain_id=chain_id,
            name=payload.name,
            description=payload.description,
            version=1,
            definition=payload.definition,
            status=ChainStatus.active.value,
            owner_user_id=principal.username,
            created_at=now,
            updated_at=now,
        )
        session.add(c)
        session.commit()
        session.refresh(c)
        return _to_dto(c)


@router.get("/chains/{chain_id}")
def get_chain(chain_id: str, request: Request) -> dict[str, Any]:
    _require(request, "schedules.read")
    sm = get_session_manager()
    with sm.session() as session:
        c = session.get(Chain, chain_id)
        if not c:
            raise HTTPException(status_code=404, detail="Chain not found")
        return _to_dto(c)


@router.patch("/chains/{chain_id}")
def patch_chain(chain_id: str, payload: ChainPatch, request: Request) -> dict[str, Any]:
    """W28K-1407 F-1407-4 — update name/description/definition; bump version.

    A changed ``definition`` is re-compiled (cycle/step-type validation); a
    compile failure is a 400. Every successful update bumps ``chain.version``
    and emits a ``chain_updated`` audit row.
    """
    principal = _require(request, "schedules.write")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")
    sm = get_session_manager()
    with sm.session() as session:
        c = session.get(Chain, chain_id)
        if not c:
            raise HTTPException(status_code=404, detail="Chain not found")
        if "definition" in changes and changes["definition"] is not None:
            try:
                compile_chain(chain_id, (c.version or 1) + 1, changes["definition"])
            except ChainCompileError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            c.definition = changes["definition"]
        if "name" in changes and changes["name"] is not None:
            c.name = changes["name"]
        if "description" in changes:
            c.description = changes["description"]
        c.version = (c.version or 1) + 1
        c.updated_at = _now()
        session.commit()
        session.refresh(c)
        audit_event(
            "chain_updated",
            actor=principal.username,
            target=c.chain_id,
            outcome="success",
            correlation_id=c.chain_id,
            details={"chain_id": c.chain_id, "version": c.version},
        )
        return _to_dto(c)


@router.delete("/chains/{chain_id}", status_code=204)
def delete_chain(chain_id: str, request: Request):
    _require(request, "schedules.admin")
    sm = get_session_manager()
    with sm.session() as session:
        c = session.get(Chain, chain_id)
        if not c:
            raise HTTPException(status_code=404, detail="Chain not found")
        session.delete(c)
        session.commit()
    return None
