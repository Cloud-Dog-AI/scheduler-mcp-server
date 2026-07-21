"""/api/v1/registry — read-only RBAC-gated registry surface for Phase 1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal
from scheduler_mcp.registry import ProjectRegistryService

router = APIRouter(tags=["registry"])


@router.get("/registry/projects")
def list_projects(request: Request) -> dict[str, Any]:
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal.has_scope("registry.read"):
        raise HTTPException(status_code=403, detail="Missing required scope: registry.read")
    entries = ProjectRegistryService().list_entries()
    return {
        "items": [
            {
                "project_id": e.project_id,
                "name": e.name,
                "service_kind": e.service_kind,
                "base_url": e.base_url,
                "last_health_status": e.last_health_status,
                "enabled": e.enabled,
            }
            for e in entries
        ],
        "count": len(entries),
    }
