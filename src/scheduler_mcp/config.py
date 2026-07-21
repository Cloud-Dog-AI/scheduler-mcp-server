"""Configuration accessor for scheduler-mcp-server.

This is the ONLY place service code reads configuration. All values flow
through cloud_dog_config (PS-80 precedence chain: process env, env file,
config.yaml, defaults.yaml). RULES section 1.4.1 mandates zero direct
environment reads in service code; every config read goes through ``get``
below which delegates to the platform package's resolver.
"""

from __future__ import annotations

from typing import Any

from cloud_dog_config import get_config as _platform_get_config

_loaded = False


def _ensure_loaded() -> None:
    """Auto-load cloud_dog_config on first read.

    The platform loader needs ``load_config()`` once per process before
    ``get_config()`` will return values. Tests and the API entry point both
    benefit from a transparent auto-load.
    """
    global _loaded
    if _loaded:
        return
    try:
        from cloud_dog_config import load_config, resolve_runtime_env_files

        # The platform resolver owns the process-env lookup and path
        # normalisation; this wrapper only passes the resolved files into the
        # canonical loader.
        load_config(env_files=resolve_runtime_env_files())
    except Exception:
        # Best effort: if the loader is misconfigured, keep going so
        # ``get(..., default=...)`` calls can still return defaults.
        pass
    _loaded = True


def get(key: str, default: Any = None) -> Any:
    """Fetch a config value by dotted path.

    Args:
        key: dotted path e.g. ``"scheduler.quotas.min_interval_seconds"``.
        default: value to return when the key is absent. Use a sentinel
            object to detect "missing" vs "explicit None" if needed.

    Returns:
        The resolved value from the platform config snapshot.
    """
    _ensure_loaded()
    try:
        value = _platform_get_config(key)
    except Exception:
        return default
    if value is None:
        return default
    return value


def require(key: str) -> Any:
    """Fetch a required config value or raise ``KeyError``."""
    _ensure_loaded()
    try:
        value = _platform_get_config(key)
    except Exception as exc:
        raise KeyError(f"required configuration key not found: {key!r}") from exc
    if value is None:
        raise KeyError(f"required configuration key not found: {key!r}")
    return value
