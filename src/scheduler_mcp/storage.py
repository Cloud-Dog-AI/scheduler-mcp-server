"""Storage wiring via cloud_dog_storage (RULES §1.4 + FR-024).

Large logs, run artefacts, and report blobs MUST flow through cloud_dog_storage
backends; SQL keeps references via the ``*_ref`` columns on schedule_runs /
chain_step_runs.
"""

from __future__ import annotations

from typing import Any

from cloud_dog_storage import StorageBackend, build_storage_backend

from scheduler_mcp import config

_backend: StorageBackend | None = None


def get_backend() -> StorageBackend:
    """Return the configured storage backend, building it on first use."""
    global _backend
    if _backend is None:
        backend_kind = config.get("storage.backend", "local")
        backend_config: dict[str, Any] = {
            "backend": backend_kind,
            "root": config.get("storage.root", "/app/data/storage"),
        }
        _backend = build_storage_backend(backend_config)
    return _backend


def reset_backend() -> None:
    """Reset the backend cache (tests)."""
    global _backend
    _backend = None
