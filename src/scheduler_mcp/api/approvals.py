"""/api/v1/approvals — approval lifecycle (W28K-1407 F-1407-3).

The worker dispatch gate (``scheduler_mcp.worker.dispatch_run``) already blocks
a run while ``Schedule.approval_status`` is not in {not_required, approved}.
This router exposes the lifecycle that drives that field:

  POST   /v1/approvals               create a request   -> approval_status=pending
  GET    /v1/approvals               list (status/schedule filter)
  GET    /v1/approvals/{id}          single request
  POST   /v1/approvals/{id}/grant    approve            -> approval_status=approved
  POST   /v1/approvals/{id}/reject   reject             -> approval_status=rejected

Each transition emits the matching PS-40 audit event
(approval_requested / approval_granted / approval_rejected), correlation_id =
approval_id, target = schedule_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from scheduler_mcp.audit import audit_event
from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.approval import ApprovalRequest, ApprovalRequestStatus
from scheduler_mcp.db.models.schedule import ApprovalStatus, Schedule
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal

router = APIRouter(tags=["approvals"])


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


class ApprovalCreate(BaseModel):
    schedule_id: str = Field(..., min_length=1, max_length=64)
    reason: str | None = None


def _to_dto(a: ApprovalRequest) -> dict[str, Any]:
    return {
        "approval_id": a.approval_id,
        "schedule_id": a.schedule_id,
        "tenant_id": a.tenant_id,
        "status": a.status,
        "reason": a.reason,
        "requested_by": a.requested_by,
        "decided_by": a.decided_by,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


@router.post("/approvals", status_code=201)
def create_approval(payload: ApprovalCreate, request: Request) -> dict[str, Any]:
    principal = _require(request, "schedules.write")
    sm = get_session_manager()
    now = _now()
    with sm.session() as session:
        sched = session.get(Schedule, payload.schedule_id)
        if sched is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        approval_id = f"apr-{uuid.uuid4().hex[:16]}"
        a = ApprovalRequest(
            approval_id=approval_id,
            schedule_id=sched.schedule_id,
            tenant_id=sched.tenant_id or "default",
            status=ApprovalRequestStatus.pending.value,
            reason=payload.reason,
            requested_by=principal.username,
            created_at=now,
            updated_at=now,
        )
        session.add(a)
        # Gate the schedule: dispatch_run blocks while approval_status is pending.
        sched.approval_status = ApprovalStatus.pending.value
        sched.updated_at = now
        session.commit()
        session.refresh(a)
        audit_event(
            "approval_requested",
            actor=principal.username,
            target=sched.schedule_id,
            outcome="success",
            correlation_id=approval_id,
            details={"approval_id": approval_id, "schedule_id": sched.schedule_id},
        )
        return _to_dto(a)


@router.get("/approvals")
def list_approvals(
    request: Request,
    status: str | None = None,
    schedule_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    _require(request, "schedules.read")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    sm = get_session_manager()
    with sm.session() as session:
        q = select(ApprovalRequest)
        if status:
            q = q.where(ApprovalRequest.status == status)
        if schedule_id:
            q = q.where(ApprovalRequest.schedule_id == schedule_id)
        rows = (
            session.execute(q.order_by(ApprovalRequest.created_at.desc()).limit(limit).offset(offset)).scalars().all()
        )
        return {"items": [_to_dto(a) for a in rows], "count": len(rows)}


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, request: Request) -> dict[str, Any]:
    _require(request, "schedules.read")
    sm = get_session_manager()
    with sm.session() as session:
        a = session.get(ApprovalRequest, approval_id)
        if a is None:
            raise HTTPException(status_code=404, detail="Approval request not found")
        return _to_dto(a)


def _decide(approval_id: str, request: Request, *, grant: bool) -> dict[str, Any]:
    principal = _require(request, "schedules.admin")
    sm = get_session_manager()
    now = _now()
    with sm.session() as session:
        a = session.get(ApprovalRequest, approval_id)
        if a is None:
            raise HTTPException(status_code=404, detail="Approval request not found")
        if a.status != ApprovalRequestStatus.pending.value:
            raise HTTPException(status_code=409, detail=f"Approval already {a.status}")
        sched = session.get(Schedule, a.schedule_id)
        if sched is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        if grant:
            a.status = ApprovalRequestStatus.granted.value
            sched.approval_status = ApprovalStatus.approved.value
            event = "approval_granted"
        else:
            a.status = ApprovalRequestStatus.rejected.value
            sched.approval_status = ApprovalStatus.rejected.value
            event = "approval_rejected"
        a.decided_by = principal.username
        a.decided_at = now
        a.updated_at = now
        sched.updated_at = now
        session.commit()
        session.refresh(a)
        audit_event(
            event,
            actor=principal.username,
            target=a.schedule_id,
            outcome="success",
            correlation_id=approval_id,
            details={"approval_id": approval_id, "schedule_id": a.schedule_id},
        )
        return _to_dto(a)


@router.post("/approvals/{approval_id}/grant")
def grant_approval(approval_id: str, request: Request) -> dict[str, Any]:
    return _decide(approval_id, request, grant=True)


@router.post("/approvals/{approval_id}/reject")
def reject_approval(approval_id: str, request: Request) -> dict[str, Any]:
    return _decide(approval_id, request, grant=False)
