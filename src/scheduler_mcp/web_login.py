"""WebUI username/password cookie login — W28K-1409 F-1409-5.

The WebUI MUST authenticate with a username/password cookie session, NOT an
api-key (recurring platform requirement; the SPA `AUTH_MODE: "cookie"` path uses
`/v1/auth/login` + `/v1/auth/me` + `/v1/auth/logout`). The platform
``cloud_dog_idam`` ``auth_router`` ``/login`` is a stub (username-only match,
no password, no cookie), so this host-app router provides the real bridge built
on platform primitives:

- ``cloud_dog_idam.providers.local_password.LocalPasswordProvider`` (Argon2id verify)
- ``cloud_dog_idam.tokens.sessions.SessionManager`` (server-side session lifecycle)

Web logins (role-scoped, for the F-1409-5 Admin/User distinction + F-1409-10
role-visibility):
- ``admin``    — ``server.web.username`` / ``server.web.password`` (cloud_dog_config;
  Terraform injects ``CLOUD_DOG__SERVER__WEB__USERNAME`` / ``__PASSWORD`` from Vault); scope ``schedules.admin``.
- ``operator`` — read-write role; password from ``web_login.read_write_password`` (cloud_dog_config)
  or the resolved admin password when unset; scopes ``schedules.read`` + ``schedules.run_now`` (User role).
- ``viewer``   — read-only role; password from ``web_login.read_only_password`` (cloud_dog_config)
  or the resolved admin password when unset; scope ``schedules.read`` (read-only role).

A login mints a server-side session and sets an HttpOnly+Secure cookie carrying
the opaque session id; ``resolve_principal`` (idam.py) resolves the cookie back
to the role-scoped Principal so every scope-gated route accepts cookie auth.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloud_dog_idam.domain.enums import UserStatus
from cloud_dog_idam.domain.errors import AuthenticationError
from cloud_dog_idam.providers.local_password import LocalPasswordProvider, LocalUser
from cloud_dog_idam.tokens.sessions import SessionManager
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from scheduler_mcp import config
from scheduler_mcp.idam import Principal

router = APIRouter(tags=["auth"])

COOKIE_NAME = "scheduler_session"


@dataclass(frozen=True)
class WebUser:
    username: str
    password_hash: str
    scopes: tuple[str, ...]
    role: str


_sessions = SessionManager()
_web_users: dict[str, WebUser] = {}
_provider: LocalPasswordProvider | None = None


def _session_ttl_seconds() -> int:
    minutes = int(config.get("server.web.session_timeout_minutes", 30) or 30)
    return max(60, minutes * 60)


def _cookie_secure() -> bool:
    """Whether the session cookie carries the ``Secure`` flag.

    Defaults to ``True`` — the preprod/deployed service is always fronted by TLS
    (Traefik terminates HTTPS), so the cookie must be Secure. Configurable to
    ``False`` for local http development/testing via ``server.web.cookie_secure``
    (env ``CLOUD_DOG__SERVER__WEB__COOKIE_SECURE=false``); a Secure cookie is not
    stored/returned over plain http, which would otherwise break a local browser
    login flow. The deployed default is unchanged.
    """
    val = config.get("server.web.cookie_secure", True)
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(val)


def _admin_credentials() -> tuple[str, str]:
    """Resolve the WebUI admin credential through ``cloud_dog_config`` (RULES §1.4/§1.4.1).

    W28R-3019 R4: this previously read ``CLOUD_DOG_WEB_LOGIN_USERNAME`` /
    ``CLOUD_DOG_WEB_LOGIN_PASSWORD`` directly from ``os.environ``. Those names are NOT in the
    ratified §1.4.1 carve-out list (only the four VAULT_* bootstrap names,
    ``CLOUD_DOG_ENV_FILES`` and the index-retriever bootstrap-seed key are), so the read was a
    real violation. Service code now reads the platform config path and nothing else.

    Supplying the value is a CONFIG concern, not a service-code concern (precedence
    os.environ -> env-file -> config.yaml -> defaults.yaml):
      - deployed: Terraform injects ``CLOUD_DOG__SERVER__WEB__USERNAME`` / ``__PASSWORD``
        from the same Vault-backed values it already uses;
      - local tiers: the same overlay keys come from ``tests/env-*``.

    Single documented source, not a fallback chain (RULES §2.4). The ``admin`` default keeps a
    mis-templated empty value from ever becoming a usable password.
    """
    username = str(config.get("server.web.username", "") or "") or "admin"
    password = str(config.get("server.web.password", "") or "")
    return username, password


def _ensure_provider() -> LocalPasswordProvider:
    global _provider
    if _provider is None:
        _provider = LocalPasswordProvider(_lookup_local_user)
    return _provider


def _role_password(config_key: str, admin_password: str) -> str:
    """Resolve a non-admin web-login role password — never a hardcoded literal.

    W28A-SEC-R17: the ``operator`` (read-write) and ``viewer`` (read-only) role
    passwords previously used shipped demo literals in this module. They now come
    from ``cloud_dog_config`` only: the role's own key when the operator has set
    one (``web_login.read_write_password`` / ``web_login.read_only_password``; env
    ``CLOUD_DOG__WEB_LOGIN__READ_WRITE_PASSWORD`` /
    ``CLOUD_DOG__WEB_LOGIN__READ_ONLY_PASSWORD``), otherwise a fall-back to the
    resolved admin password (``server.web.password``, Terraform-injected from Vault
    in preprod). Falling back to the admin secret keeps all three roles logging in
    without a Vault/Terraform change while removing the public demo value — strictly
    more secure than a shipped default. Returns ``""`` when neither is configured,
    in which case the role is not seeded (no empty-password user).
    """
    val = config.get(config_key, "")
    resolved = str(val).strip() if val is not None else ""
    return resolved or admin_password


def _build_web_users() -> dict[str, WebUser]:
    prov = _ensure_provider()
    admin_user, admin_pw = _admin_credentials()
    users: dict[str, WebUser] = {}
    if admin_pw:
        users[admin_user] = WebUser(
            username=admin_user,
            password_hash=prov.hash_password(admin_pw),
            scopes=("schedules.admin",),
            role="Admin",
        )
    operator_pw = _role_password("web_login.read_write_password", admin_pw)
    if operator_pw:
        users["operator"] = WebUser(
            username="operator",
            password_hash=prov.hash_password(operator_pw),
            scopes=("schedules.read", "schedules.run_now"),
            role="User",
        )
    viewer_pw = _role_password("web_login.read_only_password", admin_pw)
    if viewer_pw:
        users["viewer"] = WebUser(
            username="viewer",
            password_hash=prov.hash_password(viewer_pw),
            scopes=("schedules.read",),
            role="ReadOnly",
        )
    return users


def _web_user_registry() -> dict[str, WebUser]:
    global _web_users
    if not _web_users:
        _web_users = _build_web_users()
    return _web_users


def _lookup_local_user(username: str) -> LocalUser | None:
    wu = _web_user_registry().get(username)
    if wu is None:
        return None
    return LocalUser(username=wu.username, password_hash=wu.password_hash, status=UserStatus.ACTIVE)


def reset_web_logins() -> None:
    """Test seam — rebuild the registry (e.g. after changing env creds)."""
    global _web_users, _provider
    _web_users = {}
    _provider = None
    _sessions.__init__()  # fresh session store


class LoginPayload(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def web_login(payload: LoginPayload, response: Response) -> dict:
    """W28K-1409 F-1409-5 — username/password cookie login. 200 + session cookie
    on success; 401 on unknown user / bad password."""
    wu = _web_user_registry().get(payload.username)
    if wu is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    prov = _ensure_provider()
    try:
        _verify_sync(prov, payload.username, payload.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc
    sess = _sessions.create(user_id=wu.username, ttl_seconds=_session_ttl_seconds())
    response.set_cookie(
        COOKIE_NAME,
        sess.session_id,
        max_age=_session_ttl_seconds(),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    return {"ok": True, "user": {"username": wu.username, "role": wu.role, "scopes": list(wu.scopes)}}


def _verify_sync(prov: LocalPasswordProvider, username: str, password: str) -> None:
    """Synchronous Argon2 verify via the platform provider's hasher (the route is
    sync; authenticate() is async but only awaits CPU-bound verify)."""
    user = _lookup_local_user(username)
    if user is None:
        raise AuthenticationError("unknown user")
    from argon2.exceptions import VerifyMismatchError

    try:
        prov._hasher.verify(user.password_hash, password)  # noqa: SLF001 — platform hasher reuse
    except VerifyMismatchError as exc:
        raise AuthenticationError("invalid password") from exc


@router.post("/auth/logout")
def web_logout(request: Request, response: Response) -> dict:
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        _sessions.end(sid)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


def resolve_cookie_principal(request: Request) -> Principal | None:
    """Resolve a session-cookie to a role-scoped Principal, or None. Called by
    scheduler_mcp.idam.resolve_principal after the api-key paths miss."""
    sid = request.cookies.get(COOKIE_NAME)
    if not sid:
        return None
    sess = _sessions.get(sid)
    if sess is None or sess.state != "active":
        return None
    from datetime import datetime, timezone

    exp = sess.expires_at
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= datetime.now(timezone.utc):
            return None
    wu = _web_user_registry().get(sess.user_id)
    if wu is None:
        return None
    return Principal(api_key_id=f"session:{sid[:8]}", username=wu.username, scopes=wu.scopes)
