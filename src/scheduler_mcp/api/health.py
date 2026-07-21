"""Health endpoints — /health aggregator + per-package probes.

All sub-probes use the platform package's own probe API (no bespoke health
checks). Phase 1 contract:

    GET /health           -> 200 { status, service, version, checks: {...} }
    GET /health/db        -> 200 { status, engine, latency_ms }
    GET /health/cache     -> 200 { status, backend }
    GET /health/jobs      -> 200 { status, backend, queue_depth }
    GET /health/registry  -> 200 { status, projects, last_card_age_seconds }
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from scheduler_mcp import config
from scheduler_mcp.db import get_engine

router = APIRouter()


def _probe_db() -> dict[str, Any]:
    try:
        engine = get_engine()
        t0 = time.perf_counter()
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {"status": "ok", "engine": engine.dialect.name, "latency_ms": latency_ms}
    except Exception as exc:  # pragma: no cover — exercised via missing-config path
        return {"status": "down", "error": str(exc)}


def _probe_cache() -> dict[str, Any]:
    try:
        from scheduler_mcp import cache

        manager = cache.get_manager()
        return {"status": "ok", "backend": config.get("cache.backend", "memory"), "manager": type(manager).__name__}
    except Exception as exc:  # pragma: no cover
        return {"status": "down", "error": str(exc)}


def _probe_jobs() -> dict[str, Any]:
    try:
        from scheduler_mcp import jobs

        q = jobs.get_queue()
        depth = getattr(q, "size", None)
        depth_val: int = 0
        if callable(depth):
            try:
                depth_val = int(depth())
            except Exception:
                depth_val = 0
        # W28K-1404a — surface worker health (started_at, last_dispatch_at,
        # in_flight, dispatched_total, running) alongside queue depth.
        worker_info: dict[str, Any] = {}
        try:
            from scheduler_mcp.worker_lifecycle import get_worker_health

            worker_info = get_worker_health()
        except Exception:
            worker_info = {"running": False}
        return {
            "status": "ok",
            "backend": config.get("jobs.backend", "memory"),
            "queue_depth": depth_val,
            "worker_started_at": worker_info.get("started_at"),
            "last_dispatch_at": worker_info.get("last_dispatch_at"),
            "tick_started_at": worker_info.get("tick_started_at"),
            "last_tick_at": worker_info.get("last_tick_at"),
            "last_tick_error": worker_info.get("last_tick_error"),
            "ticks_total": worker_info.get("ticks_total", 0),
            "in_flight": worker_info.get("in_flight", 0),
            "dispatched_total": worker_info.get("dispatched_total", 0),
            "worker_running": worker_info.get("running", False),
            "tick_running": worker_info.get("tick_running", False),
        }
    except Exception as exc:  # pragma: no cover
        return {"status": "down", "error": str(exc)}


def _probe_registry() -> dict[str, Any]:
    try:
        from scheduler_mcp.registry import ProjectRegistryService

        entries = ProjectRegistryService().list_entries()
        last_card_age: float | None = None
        latest: datetime | None = None
        for e in entries:
            if e.last_card_at and (latest is None or e.last_card_at > latest):
                latest = e.last_card_at
        if latest is not None:
            last_card_age = (datetime.now(timezone.utc) - latest).total_seconds()
        return {"status": "ok", "projects": len(entries), "last_card_age_seconds": last_card_age}
    except Exception as exc:  # pragma: no cover
        return {"status": "down", "error": str(exc), "projects": 0, "last_card_age_seconds": None}


@router.get("/health", tags=["health"])
def health() -> dict[str, Any]:
    checks = {
        "db": _probe_db(),
        "cache": _probe_cache(),
        "jobs": _probe_jobs(),
        "registry": _probe_registry(),
    }
    overall = "ok" if all(c.get("status") == "ok" for c in checks.values()) else "degraded"
    return {
        "status": overall,
        "service": config.get("service.name", "scheduler-mcp-server"),
        "version": config.get("service.version", "0.1.0"),
        "checks": checks,
    }


@router.get("/health/db", tags=["health"])
def health_db() -> dict[str, Any]:
    return _probe_db()


@router.get("/health/cache", tags=["health"])
def health_cache() -> dict[str, Any]:
    return _probe_cache()


@router.get("/health/jobs", tags=["health"])
def health_jobs() -> dict[str, Any]:
    return _probe_jobs()


@router.get("/health/registry", tags=["health"])
def health_registry() -> dict[str, Any]:
    return _probe_registry()


@router.get("/health/leader", tags=["health"])
def health_leader(request: Request) -> dict[str, Any]:
    """W28K-1409 F-1409-3 — multi-instance leader-election observability.

    Default: read-only status (this node's id, the shared cache backend, the
    token currently holding ``scheduler:leader``). ``?contend=1`` runs one
    contention round (acquire + short auto-expiring lease) so two instances
    sharing a Valkey/Redis backend can prove mutual exclusion: exactly one
    returns ``i_am_leader=true`` while both report the SAME current token.
    """
    try:
        from scheduler_mcp import leader

        if request.query_params.get("contend") in ("1", "true", "yes"):
            secs = int(config.get("scheduler.leader.lease_seconds", 10) or 10)
            return {"status": "ok", **leader.try_acquire_and_hold(lease_seconds=secs)}
        return {"status": "ok", **leader.leader_status()}
    except Exception as exc:  # pragma: no cover
        return {"status": "down", "error": str(exc)}
