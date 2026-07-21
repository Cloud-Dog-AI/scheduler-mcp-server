"""Logging + correlation accessor wrappers (PS-40 via cloud_dog_logging).

Service code calls ``get_logger(__name__)`` and ``get_audit_logger()`` from
here so the service never imports the stdlib log module directly
(RULES section 1.4 platform-first; AGENT-LESSONS 2.25 -- never use percent
positional args; use f-strings).
"""

from __future__ import annotations

from typing import Any

from cloud_dog_logging import AppLogger, AuditLogger
from cloud_dog_logging import get_audit_logger as _platform_audit
from cloud_dog_logging import get_logger as _platform_get_logger


def get_logger(name: str) -> AppLogger:
    """Return a PS-40 compliant application logger."""
    return _platform_get_logger(name)


def get_audit_logger() -> AuditLogger:
    """Return the PS-40 audit logger (separate stream from app log)."""
    return _platform_audit()


def emit_audit(
    event: str, *, actor: str, target: str | None = None, outcome: str = "success", extra: dict[str, Any] | None = None
) -> None:
    """Convenience wrapper around the audit logger for service mutating ops.

    The platform AuditLogger is the durable audit channel; service mutating
    operations (schedule_created, schedule_updated, schedule_paused, etc. —
    FR-034) MUST call this rather than building bespoke audit records.
    """
    auditor = get_audit_logger()
    payload: dict[str, Any] = {"event": event, "actor": actor, "outcome": outcome}
    if target is not None:
        payload["target"] = target
    if extra:
        payload["extra"] = extra
    auditor.audit(event_name=event, actor=actor, target=target, outcome=outcome, details=extra or {})
