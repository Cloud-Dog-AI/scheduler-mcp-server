"""Leader election — W28K-1414.

Uses the platform cloud_dog_cache backend for a TTL-based exclusive lock.
The first node to write the lock key with a unique token becomes leader for
`lease_seconds`; renewal extends the lease. Designed so the scheduler tick
(see scheduler_mcp.tick) runs on exactly one node at a time even with the
default memory backend (single-process) or Redis/Valkey backend (multi-node).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from scheduler_mcp.cache import get_manager
from scheduler_mcp.clock import get_clock

_LEADER_KEY = "scheduler:leader"

# Stable per-process identity so a multi-instance deployment can attribute the
# leader lease to a specific node (W28K-1409 F-1409-3 leader-election proof).
INSTANCE_ID = uuid.uuid4().hex[:12]


@dataclass
class LeaderLease:
    token: str
    leased_until: datetime
    held: bool = False
    backend_info: str = field(default="")


def _now() -> datetime:
    return get_clock().now().astimezone(timezone.utc)


_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_LOCK = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """A single long-lived background event loop for ALL leader cache ops.

    W28K-1409 F-1409-3 — the async cloud_dog_cache Redis/Valkey backend opens a
    connection pool bound to the event loop that first touched it. The prior
    ``asyncio.run(coro)`` per call created a NEW loop each time, so the second
    op hit "got Future attached to a different loop" / "Event loop is closed"
    against a shared backend — which is exactly the multi-node path. Running
    every coroutine on one persistent loop keeps the backend's connections
    valid across acquire/renew/release. The memory backend is unaffected.
    """
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None or _LOOP.is_closed():
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, daemon=True, name="scheduler-leader-loop").start()
        return _LOOP


def _run(coro):
    """Bridge cloud_dog_cache's async API into the sync scheduler tick by
    submitting the coroutine to the persistent leader loop (thread-safe from
    the tick's background thread or any caller)."""
    fut = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    return fut.result(timeout=30)


async def _aget(key: str):
    return await get_manager().get(key)


async def _aset(key: str, value, ttl: int):
    return await get_manager().set(key, value, ttl=ttl)


async def _adel(key: str):
    return await get_manager().delete(key)


def acquire(lease_seconds: int = 30) -> LeaderLease:
    """Attempt to claim leadership. Returns a LeaderLease whose ``held``
    reflects whether THIS caller is now leader. Idempotent under contention:
    only the writer that landed the SET will see ``held=True``.
    """
    token = uuid.uuid4().hex
    deadline = _now()
    backend = str(type(get_manager()).__name__)
    existing = _run(_aget(_LEADER_KEY))
    if existing is not None:
        return LeaderLease(token=token, leased_until=deadline, held=False, backend_info=backend)
    _run(_aset(_LEADER_KEY, token, lease_seconds))
    confirmed = _run(_aget(_LEADER_KEY))
    if confirmed == token:
        return LeaderLease(token=token, leased_until=_now(), held=True, backend_info=backend)
    return LeaderLease(token=token, leased_until=deadline, held=False, backend_info=backend)


def renew(lease: LeaderLease, lease_seconds: int = 30) -> bool:
    """Extend the lease iff this caller still holds the lock."""
    current = _run(_aget(_LEADER_KEY))
    if current != lease.token:
        return False
    _run(_aset(_LEADER_KEY, lease.token, lease_seconds))
    return True


def release(lease: LeaderLease) -> None:
    """Release the lock iff we own it (no-op otherwise)."""
    current = _run(_aget(_LEADER_KEY))
    if current == lease.token:
        _run(_adel(_LEADER_KEY))


def current_leader_token() -> str | None:
    """Diagnostic — return whichever token currently holds the lock."""
    return _run(_aget(_LEADER_KEY))


def leader_status() -> dict:
    """W28K-1409 F-1409-3 — observability for multi-instance leader election.

    Reports this process's identity, the shared cache backend, and the token
    currently holding the lock (if any). Two instances pointed at a SHARED
    cache backend (Valkey/Redis) will report the SAME ``current_leader_token``;
    with the per-process ``memory`` backend each process sees only its own
    lock, which is why genuine multi-node election requires a shared backend.
    """
    backend = str(type(get_manager()).__name__)
    try:
        holder = current_leader_token()
    except Exception:  # noqa: BLE001 — backend unreachable
        holder = None
    return {
        "instance_id": INSTANCE_ID,
        "cache_backend": backend,
        "leader_key": _LEADER_KEY,
        "current_leader_token": holder,
        "leader_held_by_someone": holder is not None,
    }


def try_acquire_and_hold(lease_seconds: int = 10) -> dict:
    """W28K-1409 F-1409-3 — single round of contention for the leader lock,
    used by the 2-container election proof and the ``/health/leader`` probe
    (``?contend=1``). Attempts to claim the lock; on success the caller owns it
    for ``lease_seconds`` (auto-expiry frees it — no permanent hold). Returns
    whether THIS instance won the round.
    """
    lease = acquire(lease_seconds=lease_seconds)
    holder = current_leader_token()
    return {
        "instance_id": INSTANCE_ID,
        "acquired": bool(lease.held),
        "my_token": lease.token,
        "current_leader_token": holder,
        "i_am_leader": bool(lease.held) and holder == lease.token,
        "cache_backend": lease.backend_info,
    }
