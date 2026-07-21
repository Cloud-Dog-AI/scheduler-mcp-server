"""Database wiring — engine, sessions, migrations, models.

Service code MUST consume cloud_dog_db here; no direct sqlalchemy.create_engine
in service modules outside this package (RULES §1.4).
"""

from __future__ import annotations

from scheduler_mcp.db.engine import dispose_engine, get_engine, get_session_manager
from scheduler_mcp.db.models import (
    Chain,
    ChainRun,
    ChainStepRun,
    ExternalTarget,
    ProjectRegistryRecord,
    Schedule,
    ScheduleFireWindow,
    SchedulerContext,
    ScheduleRun,
    metadata,
)

__all__ = [
    "Schedule",
    "ScheduleRun",
    "ScheduleFireWindow",
    "Chain",
    "ChainRun",
    "ChainStepRun",
    "SchedulerContext",
    "ExternalTarget",
    "ProjectRegistryRecord",
    "metadata",
    "get_engine",
    "get_session_manager",
    "dispose_engine",
]
