"""SQLAlchemy ORM models for scheduler-mcp-server.

All models inherit from cloud_dog_db.models.base.PlatformBase so the naming
convention and timestamp mixins are consistent with every other Cloud-Dog
service. The Base.metadata is exposed for Alembic autogenerate.
"""

from __future__ import annotations

from scheduler_mcp.db.models.approval import ApprovalRequest
from scheduler_mcp.db.models.base import Base, metadata
from scheduler_mcp.db.models.chain import Chain
from scheduler_mcp.db.models.chain_run import ChainRun
from scheduler_mcp.db.models.chain_step_run import ChainStepRun
from scheduler_mcp.db.models.external_target import ExternalTarget
from scheduler_mcp.db.models.project_registry import ProjectRegistryRecord
from scheduler_mcp.db.models.schedule import Schedule
from scheduler_mcp.db.models.schedule_fire_window import ScheduleFireWindow
from scheduler_mcp.db.models.schedule_run import ScheduleRun
from scheduler_mcp.db.models.scheduler_context import SchedulerContext

__all__ = [
    "Base",
    "metadata",
    "Schedule",
    "ScheduleRun",
    "ScheduleFireWindow",
    "Chain",
    "ChainRun",
    "ChainStepRun",
    "SchedulerContext",
    "ExternalTarget",
    "ProjectRegistryRecord",
    "ApprovalRequest",
]
