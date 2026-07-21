"""/v1/admin/config/effective — PS-73 v2 effective config snapshot (W28K-1404e).

Returns the canonical merged config (defaults.yaml + env overlay + Vault
references resolved) via cloud_dog_config.export_config(). Secrets are
masked by default. `?reveal=true` requires the `settings.admin` scope
and audit-logs the reveal action.

Carve-out: this is a read-only surface; PS-73 v2 reserves write
semantics for a future lane (settings.write is on the scope catalogue
but no PATCH endpoint is mounted by this lane).
"""

from __future__ import annotations

from typing import Any

from cloud_dog_config import export_config, get_config
from cloud_dog_logging import get_logger
from fastapi import APIRouter, HTTPException, Query, Request

from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal

_log = get_logger(__name__)

router = APIRouter(tags=["admin-config"])


def _require_scope(request: Request, scope: str):
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal.has_scope(scope):
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
    return principal


def _redacted_paths(redacted: dict[str, Any], path: str = "") -> list[str]:
    """Walk the redacted snapshot and collect dotted paths where the
    placeholder REDACTED_VALUE appears."""
    from cloud_dog_config.redaction import REDACTED_VALUE

    out: list[str] = []
    if isinstance(redacted, dict):
        for k, v in redacted.items():
            sub = f"{path}.{k}" if path else k
            out.extend(_redacted_paths(v, sub))
    elif isinstance(redacted, list):
        for i, v in enumerate(redacted):
            out.extend(_redacted_paths(v, f"{path}[{i}]"))
    else:
        if redacted == REDACTED_VALUE:
            out.append(path)
    return out


@router.get("/admin/config/effective")
def get_effective_config(
    request: Request,
    reveal: bool = Query(False, description="If true and caller has settings.admin scope, return unmasked secrets."),
) -> dict[str, Any]:
    """Return the effective config snapshot (PS-73 §2.1).

    Envelope:
        {
          "config":         <merged config tree>,
          "redacted_keys":  ["dotted.paths"],
          "revealed":       bool,
          "service":        <service.name>,
        }
    """
    principal = _require_scope(request, "settings.read")
    if reveal:
        # W28K-1407 F-1407-2 — reveal requires the dedicated settings.reveal
        # scope (was settings.admin) AND emits an audit row.
        if not principal.has_scope("settings.reveal"):
            raise HTTPException(
                status_code=403,
                detail="Missing required scope for reveal=true: settings.reveal",
            )
        # The cloud_dog_api_kit AuditMiddleware captures the HTTP request
        # itself (PS-40 §1). Emit an additional structured app-log row so the
        # PS-73 §2.3 reveal action is explicitly traceable by correlation_id
        # in the audit reader (W28K-1404b).
        actor = getattr(principal, "username", "admin") or "admin"
        _log.info(f"settings.reveal granted actor={actor} target=/v1/admin/config/effective")

    try:
        cfg = get_config()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"config not loaded: {exc}") from exc

    masked_snapshot = export_config(cfg, redact=True)
    snapshot = export_config(cfg, redact=not reveal)
    return {
        "config": snapshot,
        "redacted_keys": _redacted_paths(masked_snapshot),
        "revealed": bool(reveal),
        "service": str(snapshot.get("service", {}).get("name", "scheduler-mcp-server")),
    }


@router.get("/admin/config/effective/export")
def export_effective_config(request: Request) -> dict[str, Any]:
    """PS-73 §2.4 — export the masked effective config; audited.

    Returns the same envelope as the read endpoint, never unmasked.
    """
    # W28K-1407 F-1407-2 — export requires the dedicated settings.export scope
    # (was settings.read).
    principal = _require_scope(request, "settings.export")
    actor = getattr(principal, "username", "admin") or "admin"
    _log.info(f"settings.export actor={actor} target=/v1/admin/config/effective/export")
    try:
        cfg = get_config()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"config not loaded: {exc}") from exc
    snapshot = export_config(cfg, redact=True)
    return {
        "config": snapshot,
        "redacted_keys": _redacted_paths(snapshot),
        "revealed": False,
        "service": str(snapshot.get("service", {}).get("name", "scheduler-mcp-server")),
    }
