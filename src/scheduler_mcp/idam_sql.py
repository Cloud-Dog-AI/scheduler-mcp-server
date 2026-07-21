"""SQL-backed IDAM persistence — W28K-1407 F-1407-8 (closes the W28K-1404c carve-out).

The pre-instantiated ``cloud_dog_idam.api.fastapi.router`` routers store
users/groups/roles/api-keys/bindings in process memory; several of their
endpoints bypass the optional repository entirely (group/role update+delete,
api-keys, bindings), so injecting a repository does not yield full SQL
persistence. The platform router docstring states host applications SHOULD
provide the SQL-backed wiring at mount time — this module + ``api/idam_admin.py``
are that wiring.

All persistence uses the platform's own SQL surface — the
``cloud_dog_idam.storage.sqlalchemy`` ORM models + repositories + the
``api_keys.hashing`` primitives — over the scheduler's ``SyncSessionManager``
session (one session per request). No bespoke persistence logic is duplicated
(RULES §1.4/§1.6).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from cloud_dog_idam.api_keys.hashing import hash_api_key
from cloud_dog_idam.storage.sqlalchemy import models as m
from sqlalchemy import select

from scheduler_mcp import config
from scheduler_mcp.db import get_session_manager

# The IDAM ORM tables this lane persists. The migration (0003_idam_sql_backend)
# creates exactly these by their ORM __tablename__.
IDAM_ORM_MODELS = (
    m.UserORM,
    m.GroupORM,
    m.RoleORM,
    m.GroupMembershipORM,
    m.UserRoleORM,
    m.RBACBindingORM,
    m.APIKeyORM,
)
IDAM_TABLE_NAMES = tuple(model.__tablename__ for model in IDAM_ORM_MODELS)

_KEY_PREFIX = "cd_"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def mint_api_key(prefix: str = _KEY_PREFIX) -> tuple[str, str]:
    """Return (raw_key, key_hash). Uses the platform hashing primitive; the raw
    secret is a urlsafe token with the canonical ``cd_`` prefix."""
    raw = f"{prefix}{secrets.token_urlsafe(32)}"
    return raw, hash_api_key(raw)


def _scopes_to_json(scopes: list[str]) -> dict[str, Any]:
    return {"scopes": list(scopes)}


def _scopes_from_json(value: Any) -> list[str]:
    if isinstance(value, dict):
        out = value.get("scopes", [])
        return list(out) if isinstance(out, list) else []
    if isinstance(value, list):
        return list(value)
    return []


def bootstrap_admin_sql() -> None:
    """Idempotently persist the bootstrap admin (User + API key) to SQL on first
    start. The api-key hash matches ``idam.bootstrap_admin_token`` and carries
    ``idam.default_admin_scopes`` so the persisted admin resolves after restart.
    Best-effort: any failure (table absent in a stripped context) is swallowed.
    """
    token = config.get("idam.bootstrap_admin_token")
    if not token:
        return
    username = config.get("idam.default_admin_username", "admin") or "admin"
    scopes = list(config.get("idam.default_admin_scopes", []) or [])
    key_hash = hash_api_key(str(token))
    try:
        sm = get_session_manager()
        with sm.session() as session:
            user = session.scalar(select(m.UserORM).where(m.UserORM.username == username))
            if user is None:
                uid = new_id("usr")
                session.add(
                    m.UserORM(
                        user_id=uid,
                        username=username,
                        email=f"{username}@scheduler.local",
                        role="admin",
                        status="active",
                    )
                )
                # Flush so the api_keys FK (owner_user_id -> users.user_id) is
                # satisfied within this transaction (SQLite FK enforcement is ON
                # and there is no ORM relationship to auto-order the inserts).
                session.flush()
            else:
                uid = user.user_id
            existing_key = session.scalar(select(m.APIKeyORM).where(m.APIKeyORM.key_hash == key_hash))
            if existing_key is None:
                session.add(
                    m.APIKeyORM(
                        api_key_id=new_id("key"),
                        owner_user_id=uid,
                        key_hash=key_hash,
                        key_prefix=_KEY_PREFIX,
                        status="active",
                        scopes=_scopes_to_json(scopes),
                    )
                )
            session.commit()
    except Exception:  # noqa: BLE001 — bootstrap is best-effort
        return


def resolve_sql_principal(presented_key: str):
    """Resolve a presented api-key against the persisted SQL api_keys table.

    Returns a ``scheduler_mcp.idam.Principal`` when the key matches an active,
    unexpired persisted key; otherwise ``None``. Used as the fallback resolution
    path after the in-memory test-seam store, so persisted keys (incl. the
    bootstrap admin) survive a restart.
    """
    if not presented_key:
        return None
    try:
        sm = get_session_manager()
        key_hash = hash_api_key(presented_key)
        with sm.session() as session:
            row = session.scalar(select(m.APIKeyORM).where(m.APIKeyORM.key_hash == key_hash))
            if row is None or row.status != "active":
                return None
            if row.expires_at is not None:
                exp = row.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp <= _now():
                    return None
            scopes = _scopes_from_json(row.scopes)
            user = session.get(m.UserORM, row.owner_user_id)
            username = user.username if user is not None else row.owner_user_id
    except Exception:  # noqa: BLE001 — SQL unavailable -> no SQL principal
        return None
    from scheduler_mcp.idam import Principal

    return Principal(api_key_id=row.api_key_id, username=username, scopes=tuple(scopes))
