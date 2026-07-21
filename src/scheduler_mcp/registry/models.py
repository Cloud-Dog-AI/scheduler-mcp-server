"""In-memory project entry record (DB row is ProjectRegistryRecord)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ProjectEntry:
    project_id: str
    name: str
    service_kind: str = "mcp_tool"
    tenant_id: str = "default"
    base_url: str = ""
    mcp_url: str | None = None
    a2a_card_url: str | None = None
    health_url: str | None = None
    terraform_source: str | None = None
    image_pin: str | None = None
    tools_list: dict[str, Any] = field(default_factory=dict)
    skills: dict[str, Any] = field(default_factory=dict)
    rbac_scopes: list[str] = field(default_factory=list)
    last_card_at: datetime | None = None
    last_health_at: datetime | None = None
    last_health_status: str = "unknown"
    enabled: bool = True
