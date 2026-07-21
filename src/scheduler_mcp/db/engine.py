"""SQLAlchemy engine factory (cloud_dog_db wrapped).

A single sync engine + SyncSessionManager are constructed lazily from the
``db.url`` and ``db.pool_size`` config keys. There is no bespoke session
factory or sqlalchemy.create_engine() call in service code outside this
module (RULES §1.4).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.engine import Engine

from scheduler_mcp import config

# Lazy singletons
_engine: Optional[Engine] = None
_session_manager = None


def _build_engine() -> Engine:
    """Build the engine via cloud_dog_db.build_sync_engine.

    cloud_dog_db.build_sync_engine takes a DatabaseSettings (pydantic), NOT loose
    kwargs. Pattern matches file-mcp-server/db/runtime.py.

    W28K-1409 F-1409-3 — the SQLite default and the PostgreSQL variant
    (``postgresql+pg8000://…``) both go through the plain ``url=`` path. This
    requires **cloud-dog-db>=0.3.1**, which fixed the two 0.3.0 defects that
    previously broke a password-bearing Postgres URL + the pure-Python pg8000
    driver (BSD; chosen over psycopg/LGPL):
      1. ``to_sync_url()`` masked the URL password (``str(make_url)`` renders
         ``hide_password=True``) -> now ``render_as_string(hide_password=False)``.
      2. ``_base_connect_args`` injected psycopg-only ``connect_timeout`` -> now
         maps to pg8000's ``timeout`` per driver.
    The dep floor (pyproject ``cloud-dog-db>=0.3.1``) guarantees the fixed package
    is in the built image, so no service-side workaround is needed.
    """
    from cloud_dog_db import DatabaseSettings, build_sync_engine

    url = config.require("db.url")
    pool_size = int(config.get("db.pool_size", 5))
    echo = bool(config.get("db.echo", False))
    settings = DatabaseSettings(url=url, pool_size=pool_size, echo=echo)
    return build_sync_engine(settings)


def get_engine() -> Engine:
    """Return the process-wide sync engine, building it on first use."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_manager():
    """Return the cloud_dog_db SyncSessionManager bound to our engine."""
    from cloud_dog_db.session.session_manager import SyncSessionManager

    global _session_manager
    if _session_manager is None:
        _session_manager = SyncSessionManager(engine=get_engine())
    return _session_manager


def dispose_engine() -> None:
    """Tear down the engine (for tests, shutdown)."""
    global _engine, _session_manager
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_manager = None
