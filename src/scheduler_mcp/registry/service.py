"""Project registry service.

Owns persistence + cache for project entries. Static seed runs at startup
from the terraform_loader; live agent-card polling runs on demand (Phase 1)
and on a periodic schedule (Phase 2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select

from scheduler_mcp import config
from scheduler_mcp.audit import audit_event
from scheduler_mcp.db import ProjectRegistryRecord, get_session_manager
from scheduler_mcp.obs import get_logger
from scheduler_mcp.registry.models import ProjectEntry
from scheduler_mcp.registry.terraform_loader import load_entries

_log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _entry_to_record(entry: ProjectEntry, *, now: datetime) -> ProjectRegistryRecord:
    return ProjectRegistryRecord(
        project_id=entry.project_id,
        name=entry.name,
        service_kind=entry.service_kind,
        tenant_id=entry.tenant_id,
        base_url=entry.base_url,
        mcp_url=entry.mcp_url,
        a2a_card_url=entry.a2a_card_url,
        health_url=entry.health_url,
        terraform_source=entry.terraform_source,
        image_pin=entry.image_pin,
        tools_list_json=entry.tools_list or {},
        skills_json=entry.skills or {},
        rbac_scopes_json=entry.rbac_scopes or [],
        last_card_at=entry.last_card_at,
        last_health_at=entry.last_health_at,
        last_health_status=entry.last_health_status,
        enabled=entry.enabled,
        created_at=now,
        updated_at=now,
    )


def _record_to_entry(record: ProjectRegistryRecord) -> ProjectEntry:
    return ProjectEntry(
        project_id=record.project_id,
        name=record.name,
        service_kind=record.service_kind,
        tenant_id=record.tenant_id,
        base_url=record.base_url,
        mcp_url=record.mcp_url,
        a2a_card_url=record.a2a_card_url,
        health_url=record.health_url,
        terraform_source=record.terraform_source,
        image_pin=record.image_pin,
        tools_list=record.tools_list_json or {},
        skills=record.skills_json or {},
        rbac_scopes=list(record.rbac_scopes_json or []),
        last_card_at=record.last_card_at,
        last_health_at=record.last_health_at,
        last_health_status=record.last_health_status,
        enabled=record.enabled,
    )


class ProjectRegistryService:
    """Read/refresh the project registry.

    Persistence is via ``cloud_dog_db``; no bespoke dict store (RULES §1.4).
    """

    def seed_from_terraform(self, *, actor: str = "system:registry") -> int:
        """Static-seed from Terraform globs. Idempotent — upserts by project_id.

        Returns the number of entries seeded/updated.
        """
        globs = config.get("scheduler.registry.terraform_source_globs", []) or []
        entries = load_entries(globs)
        now = _now()
        sm = get_session_manager()
        with sm.session() as session:
            for entry in entries:
                existing = session.get(ProjectRegistryRecord, entry.project_id)
                if existing is None:
                    session.add(_entry_to_record(entry, now=now))
                else:
                    existing.name = entry.name
                    existing.base_url = entry.base_url
                    existing.mcp_url = entry.mcp_url
                    existing.a2a_card_url = entry.a2a_card_url
                    existing.health_url = entry.health_url
                    existing.terraform_source = entry.terraform_source
                    existing.image_pin = entry.image_pin
                    existing.updated_at = now
            session.commit()
        audit_event("registry_refreshed", actor=actor, details={"source": "terraform", "count": len(entries)})
        return len(entries)

    def list_entries(self) -> list[ProjectEntry]:
        sm = get_session_manager()
        with sm.session() as session:
            stmt = select(ProjectRegistryRecord)
            rows: Iterable[ProjectRegistryRecord] = session.execute(stmt).scalars().all()
            return [_record_to_entry(r) for r in rows]

    def get_entry(self, project_id: str) -> ProjectEntry | None:
        sm = get_session_manager()
        with sm.session() as session:
            row = session.get(ProjectRegistryRecord, project_id)
            return _record_to_entry(row) if row is not None else None
