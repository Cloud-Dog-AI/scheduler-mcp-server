"""Audit emission + reader (RULES §1.4 + FR-034 + W28K-1404b).

Emission: every mutating service operation MUST call ``audit_event(...)``
so the PS-40 audit channel sees a uniform record. The platform
AuditLogger handles durable file rotation + signing.

Reader (W28K-1404b): file-tailer over cloud_dog_logging's JSONL audit
sink. `cloud_dog_logging` exposes only emission surfaces (AuditLogger,
AuditMiddleware, AuditSink), not a reader. The emitted shape on disk is
PS-40 §1 canonical:

    {timestamp, event_type, actor:{type,id,roles,ip,user_agent}, action,
     outcome, severity, correlation_id, trace_id, target:{type,id,name},
     details:{...}, duration_ms}

The reader path is the only audit surface; writes are owned by the
platform middleware. Configured via `logging.audit.path` (falls back to
`/app/logs/audit.log.jsonl`).

Carve-out: this is a named PS-40 §2 escape clause — when the platform
exposes a first-class AuditReader (proposed platform-extension lane),
swap the backing implementation behind the same `AuditReader.query()`
API.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from scheduler_mcp import config
from scheduler_mcp.obs import get_logger

_log = get_logger(__name__)

AUDIT_EVENTS = frozenset(
    {
        "schedule_created",
        "schedule_updated",
        "schedule_paused",
        "schedule_resumed",
        "schedule_archived",
        "schedule_triggered",
        "schedule_fired",
        "schedule_misfired",
        "context_written",
        "external_target_invoked",
        # W28K-1407 F-1407-4 — chain mutation lifecycle.
        "chain_updated",
        "approval_requested",
        "approval_granted",
        "approval_rejected",
        "registry_refreshed",
        "registry_record_updated",
        # W28K-1404g — run-lifecycle events used as the AT §5.1 step 7
        # `/v1/audit?correlation_id=<run_id>` proof-of-execution rows.
        # Emitted from worker.dispatch_run with the schedule_run_id as
        # correlation_id, exposing the canonical dispatched→started→{succeeded,
        # failed} transition for cross-tier audit-trace assertions.
        "dispatched",
        "started",
        "succeeded",
        "failed",
        "cancelled",
        "timeout",
    }
)


def audit_event(
    event: str,
    *,
    actor: str,
    target: str | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> None:
    """Emit an audit row. ``event`` MUST be one of ``AUDIT_EVENTS``.

    W28K-1404g: emits via ``AuditLogger.emit(AuditEvent(...))`` (the real
    cloud_dog_logging API). Previously the call site invoked a non-existent
    ``auditor.audit(...)`` method that silently raised AttributeError; no
    lifecycle audit row ever reached the JSONL sink, so AT-tier
    ``/v1/audit?correlation_id=<run_id>`` always returned zero rows.

    ``correlation_id`` carries the scheduler_run_id for lifecycle events so
    AT proof-of-execution `(dispatched → started → succeeded)` can be filtered
    via the audit reader's correlation_id index.
    """
    if event not in AUDIT_EVENTS:
        _log.warning(f"audit_event called with unrecognised event name: {event}")

    corr = correlation_id or (details or {}).get("correlation_id") or run_id_placeholder(event)
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    # W28K-1409 — write the canonical audit row DIRECTLY to the JSONL sink the
    # host AuditReader tails. The platform ``cloud_dog_logging.AuditEvent`` /
    # ``AuditLogger.emit`` surface has proven version-fragile (W28K-1404g fixed
    # an AttributeError; the published in-image build silently no-ops these
    # lifecycle rows so ``/v1/audit?correlation_id=<run>`` returned zero on the
    # DEPLOYED service). cloud_dog_logging exposes only emission surfaces; the
    # host already owns the reader (this file), so it owns the matching writer:
    # one JSONL line in the exact schema ``_parse_line`` consumes. The
    # cloud_dog_logging AuditMiddleware keeps emitting the http.* request rows.
    row: dict[str, Any] = {
        "timestamp": ts,
        "event_type": event,
        "action": event.split("_", 1)[-1] if "_" in event else event,
        "outcome": outcome,
        "severity": "INFO",
        "correlation_id": corr,
        "trace_id": corr,
        "request_id": corr,
        "service": "scheduler-mcp-server",
        "service_instance": "scheduler-mcp-server-0",
        "component": "scheduler",
        "actor": {"type": "system", "id": str(actor), "roles": [], "ip": None, "user_agent": None},
        "target": ({"type": "schedule_run", "id": str(target), "name": str(target)} if target is not None else None),
        "details": details or {},
        "duration_ms": None,
    }
    try:
        path = Path(str(config.get("logging.audit.path", None) or DEFAULT_AUDIT_PATH))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            f.flush()
    except Exception as e:  # noqa: BLE001 — audit write best-effort; never break the request
        _log.warning(f"audit_event direct-write failed: {type(e).__name__}: {e}")


def run_id_placeholder(event: str) -> str:
    """Fallback correlation_id when no run-id is available (e.g. non-run events
    like registry_refreshed). cloud_dog_logging requires non-empty
    correlation_id."""
    return f"scheduler-{event}"


# ---------------------------------------------------------------- reader
DEFAULT_AUDIT_PATH = "/app/logs/audit.log.jsonl"


@dataclass(frozen=True)
class AuditQuery:
    actor_id: str | None = None
    action: str | None = None
    outcome: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    correlation_id: str | None = None
    event_id: str | None = None
    limit: int = 50
    offset: int = 0
    order: str = "desc"


@dataclass
class AuditEventOut:
    event_id: str
    timestamp: str
    event_type: str | None
    action: str | None
    outcome: str | None
    severity: str | None
    correlation_id: str | None
    actor: dict[str, Any] = field(default_factory=dict)
    target: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "action": self.action,
            "outcome": self.outcome,
            "severity": self.severity,
            "correlation_id": self.correlation_id,
            "actor": self.actor,
            "target": self.target,
            "details": self.details,
            "duration_ms": self.duration_ms,
        }


def _event_id(raw: str) -> str:
    """Deterministic content-addressed id (NF-014 immutability)."""
    return "ev-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_actor(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": value.get("type"),
            "id": str(value.get("id")) if value.get("id") is not None else None,
            "roles": value.get("roles") if isinstance(value.get("roles"), list) else [],
            "ip": value.get("ip"),
            "user_agent": value.get("user_agent"),
        }
    return {"type": "system", "id": "anonymous", "roles": [], "ip": None, "user_agent": None}


def _coerce_target(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": value.get("type"),
            "id": str(value.get("id")) if value.get("id") is not None else None,
            "name": value.get("name"),
        }
    return {"type": None, "id": None, "name": None}


def _row_matches(row: AuditEventOut, q: AuditQuery, ts: datetime | None) -> bool:
    if q.actor_id and q.actor_id.lower() not in (row.actor.get("id") or "").lower():
        return False
    if q.action and row.action != q.action:
        return False
    if q.outcome and row.outcome != q.outcome:
        return False
    if q.target_type and (row.target.get("type") or "") != q.target_type:
        return False
    if q.target_id and (row.target.get("id") or "") != q.target_id:
        return False
    if q.correlation_id and row.correlation_id != q.correlation_id:
        return False
    if q.event_id and row.event_id != q.event_id:
        return False
    if q.since and ts and ts < q.since:
        return False
    if q.until and ts and ts > q.until:
        return False
    return True


class AuditReader:
    """File-backed reader over cloud_dog_logging's JSONL audit sink."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = config.get("logging.audit.path", None)
        chosen = path or configured or DEFAULT_AUDIT_PATH
        self._path = Path(str(chosen))

    @property
    def path(self) -> Path:
        return self._path

    def _iter_raw_lines(self) -> Iterable[str]:
        if not self._path.is_file():
            return
        with self._path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if line:
                    yield line

    def _parse_line(self, raw: str) -> tuple[AuditEventOut, datetime | None] | None:
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(obj, dict):
            return None
        # Skip integrity-check rows (they have no event_type and live in the
        # general log stream, not the audit stream).
        if "event_type" not in obj:
            return None
        raw_details = obj.get("details")
        details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
        evt = AuditEventOut(
            event_id=_event_id(raw),
            timestamp=str(obj.get("timestamp") or ""),
            event_type=obj.get("event_type"),
            action=obj.get("action"),
            outcome=obj.get("outcome"),
            severity=obj.get("severity"),
            correlation_id=obj.get("correlation_id"),
            actor=_coerce_actor(obj.get("actor")),
            target=_coerce_target(obj.get("target")),
            details=details,
            duration_ms=obj.get("duration_ms") if isinstance(obj.get("duration_ms"), int) else None,
        )
        return evt, _parse_iso(evt.timestamp)

    def query(self, q: AuditQuery) -> tuple[list[AuditEventOut], int]:
        """Return (page, total_matched). Pagination is post-filter."""
        matches: list[tuple[AuditEventOut, datetime | None]] = []
        for raw in self._iter_raw_lines():
            parsed = self._parse_line(raw)
            if parsed is None:
                continue
            evt, ts = parsed
            if _row_matches(evt, q, ts):
                matches.append((evt, ts))

        reverse = (q.order or "desc").lower() != "asc"
        matches.sort(
            key=lambda kv: kv[1] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=reverse,
        )
        total = len(matches)
        limit = max(1, min(int(q.limit or 50), 200))
        offset = max(0, int(q.offset or 0))
        page = [m[0] for m in matches[offset : offset + limit]]
        return page, total

    def get(self, event_id: str) -> AuditEventOut | None:
        page, _ = self.query(AuditQuery(event_id=event_id, limit=1))
        return page[0] if page else None
