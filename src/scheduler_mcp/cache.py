"""Cache wiring via cloud_dog_cache (RULES section 1.7 + 1.4).

Service code calls ``get_manager`` or uses the ``cached`` decorator
re-exported from this module so no service file imports stdlib memoisation
or builds bespoke dict caches. The prefix is namespaced ``scheduler:`` so
we do not collide with AJOBS or other services sharing the backend.
"""

from __future__ import annotations

from typing import Any

from cloud_dog_cache import CacheManager, cached, init_cache_from_config
from cloud_dog_cache import get_cache_manager as _platform_cache_manager

from scheduler_mcp import config

_initialised = False


def init_cache() -> CacheManager:
    """Initialise the cloud_dog_cache manager from defaults.yaml + env config.

    Safe to call multiple times.

    W28K-1409 F-1409-3 — ``init_cache_from_config`` reads dotted ``cache.*`` keys
    (``cache.backend``, ``cache.redis_url``, ``cache.ttl_seconds``,
    ``cache.max_entries``) via its ``_get`` accessor. The prior wiring passed a
    dict keyed by bare names (``backend``/``prefix``), so ``_get("cache.backend")``
    always missed and the manager silently fell back to the per-process memory
    backend — which makes cross-node leader election (a shared Valkey/Redis lock)
    impossible. Key the dict by the dotted names the accessor actually looks up,
    and forward ``cache.redis_url`` so a multi-instance deployment shares one
    backend.
    """
    global _initialised
    if not _initialised:
        cfg: dict[str, Any] = {
            "cache.enabled": True,
            "cache.backend": config.get("cache.backend", "memory"),
            "cache.redis_url": config.get("cache.redis_url", "") or "",
            "cache.ttl_seconds": config.get("cache.default_ttl_seconds", 300),
            "cache.max_entries": config.get("cache.max_entries", 1000),
        }
        init_cache_from_config(cfg)
        _initialised = True
    return _platform_cache_manager()


def get_manager() -> CacheManager:
    """Return the initialised cache manager."""
    return init_cache()


__all__ = ["cached", "init_cache", "get_manager", "CacheManager"]
