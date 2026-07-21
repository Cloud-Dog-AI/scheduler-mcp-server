"""Jobs wiring via cloud_dog_jobs (RULES §1.4 + FR-011 + NFR-011).

The scheduler is a CONTROL PLANE only — execution is delegated to
cloud_dog_jobs. Phase 1 wires the JobQueue + ResourcePool surfaces so the
scheduler tick (Phase 2 W28K-1402) has a clean back-end to submit to.

For Phase 1 the default backend is ``memory`` (NFR-008 health probe needs a
queue that responds without Redis); preprod overrides to ``redis`` via
defaults.yaml + env vars.
"""

from __future__ import annotations

from cloud_dog_jobs import JobQueue, MemoryQueueBackend, ResourcePool, ResourcePoolConfig

from scheduler_mcp import config

_queue: JobQueue | None = None
_pools: dict[str, ResourcePool] = {}


def get_queue() -> JobQueue:
    """Return the process-wide JobQueue, lazily constructed."""
    global _queue
    if _queue is None:
        backend_kind = config.get("jobs.backend", "memory")
        if backend_kind == "memory":
            backend = MemoryQueueBackend()
        else:
            # Real services should configure Redis / SQL in defaults.yaml.
            # Phase 1 ships a memory backend; Redis/SQL backends wire up in Phase 2.
            backend = MemoryQueueBackend()
        _queue = JobQueue(backend=backend)
    return _queue


def get_resource_pool(name: str) -> ResourcePool:
    """Return the named resource pool, lazily constructed from defaults.yaml."""
    if name in _pools:
        return _pools[name]
    pools_cfg = config.get("jobs.resource_pools", {}) or {}
    pool_cfg = pools_cfg.get(name, {"capacity": 8})
    capacity = int(pool_cfg.get("capacity", 8)) if isinstance(pool_cfg, dict) else int(pool_cfg)
    pool = ResourcePool(config=ResourcePoolConfig(name=name, capacity=capacity))
    _pools[name] = pool
    return pool


def reset() -> None:
    """Reset cached queue/pools (tests)."""
    global _queue, _pools
    _queue = None
    _pools = {}
