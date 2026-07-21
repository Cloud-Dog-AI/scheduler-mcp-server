"""/v1/audit — audit log query surface (W28K-1404b).

Reads PS-40 §1 canonical audit events emitted by cloud_dog_logging's
AuditMiddleware. RBAC scope: ``audit.read``.

Endpoints:
- GET /v1/audit          — paginated, filterable list
- GET /v1/audit/{event_id} — single event detail
- DELETE /v1/audit/{event_id} — 405 (NF-014 immutability) + audited

The reader (scheduler_mcp.audit.AuditReader) is a file-tailer over
``logging.audit.path`` (defaults to ``/app/logs/audit.log.jsonl``).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from scheduler_mcp.audit import AuditQuery, AuditReader
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal

router = APIRouter(tags=["audit"])

# W28K-1409 F-1409-4 — export field order (CSV header + JSON/JSONL key order).
_EXPORT_FIELDS = (
    "event_id",
    "timestamp",
    "event_type",
    "action",
    "outcome",
    "severity",
    "correlation_id",
    "actor",
    "target",
    "details",
    "duration_ms",
)
_EXPORT_PAGE = 500  # AuditReader paging batch while streaming the full match set


def _require_audit_read(request: Request):
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal.has_scope("audit.read"):
        raise HTTPException(status_code=403, detail="Missing required scope: audit.read")
    return principal


def _parse_iso(value: str | None, *, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field_name} (must be ISO-8601): {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/audit")
def list_audit(
    request: Request,
    actor_id: str | None = Query(None),
    action: str | None = Query(None),
    outcome: str | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    since: str | None = Query(None, description="ISO-8601"),
    until: str | None = Query(None, description="ISO-8601"),
    correlation_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> dict[str, Any]:
    _require_audit_read(request)
    q = AuditQuery(
        actor_id=actor_id,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=target_id,
        since=_parse_iso(since, field_name="since"),
        until=_parse_iso(until, field_name="until"),
        correlation_id=correlation_id,
        limit=limit,
        offset=offset,
        order=order,
    )
    reader = AuditReader()
    rows, total = reader.query(q)
    return {
        "items": [r.to_dict() for r in rows],
        "count": len(rows),
        "total": total,
        "limit": limit,
        "offset": offset,
        "order": order,
    }


def _iter_all_matching(base: AuditQuery) -> Iterator[dict[str, Any]]:
    """Page through AuditReader for the full match set so large exports stream
    without holding everything in memory at once."""
    reader = AuditReader()
    offset = 0
    seen = 0
    total = None
    while True:
        page_q = AuditQuery(
            actor_id=base.actor_id,
            action=base.action,
            outcome=base.outcome,
            target_type=base.target_type,
            target_id=base.target_id,
            since=base.since,
            until=base.until,
            correlation_id=base.correlation_id,
            event_id=base.event_id,
            limit=_EXPORT_PAGE,
            offset=offset,
            order=base.order,
        )
        rows, total = reader.query(page_q)
        if not rows:
            break
        for r in rows:
            yield r.to_dict()
            seen += 1
        offset += len(rows)
        if total is not None and seen >= total:
            break
        if len(rows) < _EXPORT_PAGE:
            break


def _row_for(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for f in _EXPORT_FIELDS:
        v = d.get(f)
        if isinstance(v, (dict, list)):
            out.append(json.dumps(v, separators=(",", ":"), sort_keys=True))
        elif v is None:
            out.append("")
        else:
            out.append(str(v))
    return out


@router.post("/audit/export")
def export_audit(
    request: Request,
    format: str = Query("json", pattern="^(csv|json|jsonl)$"),
    actor_id: str | None = Query(None),
    action: str | None = Query(None),
    outcome: str | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    since: str | None = Query(None, description="ISO-8601"),
    until: str | None = Query(None, description="ISO-8601"),
    correlation_id: str | None = Query(None),
    order: str = Query("asc", pattern="^(asc|desc)$"),
):
    """W28K-1409 F-1409-4 — export the (filtered) audit trail as csv/json/jsonl.

    Requires scope ``audit.read``. The full filtered match set is streamed
    (paged from the JSONL reader) so large datasets do not buffer in memory.
    csv -> text/csv (header + a row per event, dict fields JSON-encoded);
    jsonl -> application/x-ndjson (one JSON object per line);
    json -> application/json ({"items":[...], "count":N, "format":"json"}).
    """
    _require_audit_read(request)
    base = AuditQuery(
        actor_id=actor_id,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=target_id,
        since=_parse_iso(since, field_name="since"),
        until=_parse_iso(until, field_name="until"),
        correlation_id=correlation_id,
        limit=_EXPORT_PAGE,
        offset=0,
        order=order,
    )

    if format == "csv":

        def _csv() -> Iterator[str]:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(_EXPORT_FIELDS)
            yield buf.getvalue()
            for d in _iter_all_matching(base):
                buf.seek(0)
                buf.truncate(0)
                w.writerow(_row_for(d))
                yield buf.getvalue()

        return StreamingResponse(
            _csv(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="audit-export.csv"'},
        )

    if format == "jsonl":

        def _jsonl() -> Iterator[str]:
            for d in _iter_all_matching(base):
                yield json.dumps(d, separators=(",", ":"), sort_keys=True) + "\n"

        return StreamingResponse(
            _jsonl(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="audit-export.jsonl"'},
        )

    # json — collect the match set into a single array document.
    items = list(_iter_all_matching(base))
    return {"items": items, "count": len(items), "format": "json"}


@router.get("/audit/{event_id}")
def get_audit(event_id: str, request: Request) -> dict[str, Any]:
    _require_audit_read(request)
    evt = AuditReader().get(event_id)
    if evt is None:
        raise HTTPException(status_code=404, detail="event not found")
    return evt.to_dict()


@router.api_route("/audit/{event_id}", methods=["DELETE", "PATCH", "PUT"])
def immutable_audit(event_id: str, request: Request) -> None:  # noqa: ARG001
    """NF-014 immutability — any mutation attempt returns 405.

    The api-kit AuditMiddleware emits an audit row for the 405 itself.
    """
    _require_audit_read(request)
    raise HTTPException(status_code=405, detail="Audit events are immutable")
