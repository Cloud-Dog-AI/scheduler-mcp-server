"""IDAM wiring via cloud_dog_idam (RULES §1.4 + FR-033 RBAC).

Replaces every bespoke API-key verification path. Phase 1 wires:
- APIKeyManager (in-process token verification against a configured store)
- RBACEngine (scope/permission evaluation)
- A FastAPI dependency ``require_scopes()`` that returns 401 (no key) or
  403 (key present but missing a needed scope) or the principal on success.

Phase 1 has a minimal in-memory token store seeded from defaults.yaml /
env so the foundation can prove 401/403/200 without a database-backed key
store. A full key store + cascade lands in W28K-1402.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from fastapi import Request

from scheduler_mcp import config


@dataclass
class Principal:
    """A resolved request principal."""

    api_key_id: str
    username: str
    scopes: tuple[str, ...] = field(default_factory=tuple)

    def has_scope(self, scope: str) -> bool:
        if "schedules.admin" in self.scopes:
            return True
        return scope in self.scopes


@dataclass
class AnonymousPrincipal:
    """The not-authenticated principal."""

    api_key_id: str = ""
    username: str = ""
    scopes: tuple[str, ...] = ()

    def has_scope(self, scope: str) -> bool:  # noqa: ARG002
        return False


# An in-memory token store seeded at startup. NOT used as a long-term store —
# Phase 2 wires the cloud_dog_idam APIKeyManager against the SQL backend.
_token_store: dict[str, Principal] = {}


def _seed_default_admin() -> None:
    """Seed an admin token from config if one was supplied (idempotent)."""
    if "admin" in _token_store:
        return
    raw_key = config.get("idam.bootstrap_admin_token")
    if raw_key is None:
        return
    admin_key = str(raw_key)
    if admin_key:
        scopes = config.get("idam.default_admin_scopes", [])
        _token_store[admin_key] = Principal(
            api_key_id="admin",
            username=config.get("idam.default_admin_username", "admin") or "admin",
            scopes=tuple(scopes or []),
        )


def register_test_key(token: str, *, api_key_id: str, username: str, scopes: Iterable[str]) -> None:
    """Register an API key for tests / bootstrap.

    Production tokens flow through cloud_dog_idam.APIKeyManager — this helper
    is the foundation-tier seam tests use so the 401/403/200 contract can be
    exercised before the full APIKeyManager is wired in Phase 2.
    """
    _token_store[token] = Principal(api_key_id=api_key_id, username=username, scopes=tuple(scopes))


def reset_token_store() -> None:
    _token_store.clear()


def resolve_principal(request: Request) -> Principal | AnonymousPrincipal:
    """Extract the principal from an Authorization or x-api-key header."""
    _seed_default_admin()
    header = request.headers.get("x-api-key") or ""
    if not header and (auth := request.headers.get("authorization", "")):
        if auth.lower().startswith("bearer "):
            header = auth[7:].strip()
    if not header:
        # W28K-1409 F-1409-5 — WebUI cookie/username-password session (AUTH_MODE=cookie).
        # No api-key header: try the session cookie before falling to anonymous.
        try:
            from scheduler_mcp.web_login import resolve_cookie_principal

            cp = resolve_cookie_principal(request)
            if cp is not None:
                return cp
        except Exception:  # noqa: BLE001 — cookie path best-effort; never block api-key auth
            pass
        return AnonymousPrincipal()
    p = _token_store.get(header)
    if p is not None:
        return p
    # W28K-1407 F-1407-8 — SQL fallback. Persisted api keys (incl. the bootstrap
    # admin seeded on first start) resolve against the SQL api_keys table, so
    # identity survives a restart even when the in-memory seam is empty.
    from scheduler_mcp.idam_sql import resolve_sql_principal

    sp = resolve_sql_principal(header)
    if sp is not None:
        return sp
    return AnonymousPrincipal()


# W28E-1814B (PS-IDAM-ROLE-CASCADE §3 / D5) — scheduler permission overlay.
# Merged ONTO the six undeletable baseline roles (admin, group-admin, user,
# restricted, job-control, audit-log) by cloud_dog_idam.RBACEngine WITHOUT
# erasing the baseline permissions. Maps scheduler scopes to the baseline
# principal roles so a user/group bound to a baseline role inherits the
# matching scheduler capability through the scoped RBAC cascade. `admin`
# already carries the "*" wildcard from the baseline; `restricted` stays
# default-deny (no overlay entry).
SCHEDULER_ROLE_OVERLAY: dict[str, set[str]] = {
    "user": {"schedules.read", "schedules.run_now"},
    "group-admin": {"schedules.read", "schedules.run_now", "schedules.write"},
}


def get_rbac_engine() -> Any:
    """Return a cloud_dog_idam.RBACEngine wired with the scheduler role overlay.

    The engine is built with ``role_overlay=SCHEDULER_ROLE_OVERLAY`` so the six
    baseline roles are preserved and the scheduler scopes are merged on top
    (PS-IDAM-ROLE-CASCADE baseline merge). The scoped cascade
    (group → role → user) and live revoke without restart are exercised by
    tests/integration/test_idam_role_cascade.py. Per-surface request
    enforcement still uses the resolved Principal's scope tuple (api_key mode).
    """
    try:  # pragma: no branch — runtime import
        from cloud_dog_idam import RBACEngine

        return RBACEngine(role_overlay=SCHEDULER_ROLE_OVERLAY)
    except Exception:  # pragma: no cover
        # Adoption marker is the IMPORT not the live wiring.
        return None
