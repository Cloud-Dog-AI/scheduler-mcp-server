"""Adapter base class — W28K-1404a.

Defines the contract every target adapter implements. The result shape is
shared with the existing W28K-1430 sync registry (scheduler_mcp.worker.
AdapterResult) so the worker can dispatch through either surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Re-export AdapterResult from worker so adapters and the F-tests use the
# same dataclass instance (avoids the §1.4 violation of forking it here).
from scheduler_mcp.worker import AdapterResult  # noqa: F401


@dataclass(frozen=True)
class AdapterContext:
    """Per-dispatch context passed to an adapter's execute() coroutine.

    `correlation_id` is the schedule_run_id (PS-95 + AGENT-LESSONS §6.96).
    `cancel_event` is set by the worker when the run is cancelled; long-
    running adapters MUST poll between long ops and abort cooperatively.
    """

    correlation_id: str
    schedule_id: str
    target_type: str
    target_ref: str
    target_spec: dict[str, Any]
    timeout_seconds: float
    api_key: str | None = None  # auth pass-through for downstream MCP/HTTP
    cancel_event: Any = None  # asyncio.Event when present


class AdapterBase(ABC):
    """Contract: every adapter implements `async execute(ctx) -> AdapterResult`."""

    target_type: str  # subclasses MUST set

    @abstractmethod
    async def execute(self, ctx: AdapterContext) -> AdapterResult:  # pragma: no cover - ABC
        ...
