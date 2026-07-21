"""scheduler-mcp-server — Cloud-Dog AI scheduler MCP service.

Scheduling control plane on top of cloud_dog_jobs (AJOBS). Execution remains
in AJOBS; this service owns schedules, schedule runs, chain definitions,
context, policy, registry, management APIs and (Phase 3) WebUI.
"""

from __future__ import annotations

# W28R-3019 project-local runtime contract: fail closed under Python < 3.13 at
# import time (before any service/worker/CLI/test code runs). See _runtime.py.
from scheduler_mcp._runtime import enforce_runtime

enforce_runtime()

__version__ = "0.1.0"
