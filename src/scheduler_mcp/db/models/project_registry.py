"""ProjectRegistryRecord entity — the load-bearing W28K-1406 deliverable.

Closes Gary's brief: "the scheduler must know/register other projects in
preprod that it knows and can work with". Two-pronged registry: (a) static
seed from Terraform `*_containers.tf.json`, (b) live A2A agent-card discovery
populating tools/skills/RBAC scopes.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from scheduler_mcp.db.models.base import Base


class ServiceKind(str, enum.Enum):
    mcp_tool = "mcp_tool"
    a2a_call = "a2a_call"
    expert_agent = "expert_agent"
    code_runner = "code_runner"
    external = "external"


class HealthStatus(str, enum.Enum):
    ok = "ok"
    degraded = "degraded"
    down = "down"
    unknown = "unknown"


class ProjectRegistryRecord(Base):
    __tablename__ = "project_registry"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service_kind: Mapped[str] = mapped_column(
        Enum(ServiceKind, native_enum=False, length=32), nullable=False, default=ServiceKind.mcp_tool.value
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    mcp_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    a2a_card_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    health_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    terraform_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_pin: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tools_list_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    skills_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rbac_scopes_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_card_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_health_status: Mapped[str] = mapped_column(
        Enum(HealthStatus, native_enum=False, length=32), nullable=False, default=HealthStatus.unknown.value
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
