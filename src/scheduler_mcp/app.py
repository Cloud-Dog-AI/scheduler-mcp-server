"""FastAPI application factory.

Phase 1 wires:
- /health aggregator + per-package probes
- /v1/schedules (RBAC-gated read-only)
- /v1/registry/projects (RBAC-gated read-only)
- /v1/auth/me (negative-auth contract per closeout §0C)

The app reads ``server.api.base_path`` from cloud_dog_config (default "/v1").
It also exposes local direct-server aliases under "/api" so the same SPA
runtime config works behind Traefik and in local Docker smoke.
"""

from __future__ import annotations

from cloud_dog_logging import get_logger
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from scheduler_mcp import __version__, config
from scheduler_mcp.api import (
    a2a,
    admin_config,
    approvals,
    audit,
    chains,
    context,
    health,
    nl_chain,
    registry,
    runs,
    schedules,
)

_log = get_logger(__name__)


def _git_head_commit() -> str:
    """Best-effort git HEAD for dev/source runs (empty string if unavailable).

    Mirrors the deployed file-mcp ``_git_head_commit`` reference (commit ``a282f7f``)
    so a local/source run still populates the WebUI About page when no container
    build-identity ENV is present.
    """
    try:
        import subprocess
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 - build identity must never crash a request
        return ""
    return ""


def _build_identity() -> dict[str, str]:
    """Return build/deploy identity for WSC-014 / PS-30 UI-R7.3.

    Source of truth is the container build: ``docker-build.sh`` stamps the image
    OCI ``org.opencontainers.image.revision`` label AND injects the matching runtime
    ENV, which ``cloud_dog_config`` surfaces as ``build.source_commit`` /
    ``build.source_branch`` / ``build.build_date`` / ``build.container_digest``
    (env keys ``CLOUD_DOG__BUILD__SOURCE_COMMIT`` … routed through cloud_dog_config,
    NOT direct os.environ — RULES §1.4.1). For a dev/source run (no container ENV)
    ``source_commit`` falls back to the working-tree git HEAD so the About page is
    still populated locally. Modelled on the file-mcp / search-mcp build-identity
    reference. W28E-1863 fix-wave-c.
    """
    commit = str(config.get("build.source_commit", "") or "").strip()
    if not commit or commit == "unknown":
        commit = _git_head_commit()
    branch = str(config.get("build.source_branch", "") or "").strip()
    if branch == "unknown":
        branch = ""
    build_date = str(config.get("build.build_date", "") or "").strip()
    digest = str(config.get("build.container_digest", "") or "").strip()
    env_name = str(config.get("service.environment", "") or config.get("env", "") or "").strip()
    return {
        "source_commit": commit,
        "source_branch": branch,
        "build_date": build_date,
        "container_digest": digest,
        "environment": env_name,
    }


def _run_alembic_upgrade_if_enabled() -> None:
    """Run alembic upgrade head via cloud_dog_db.MigrationRunner when
    `db.migrations.auto_upgrade_on_start` is true. Canonical pattern matches
    file-mcp-server/db/runtime.py:140-160. Called at app construction so
    tables exist before the first request.
    """
    if not bool(config.get("db.migrations.auto_upgrade_on_start", False)):
        return
    from pathlib import Path

    from cloud_dog_db import MigrationRunner
    from cloud_dog_db.migrations.runner import MigrationConfig

    # Script location is `src/scheduler_mcp/db/migrations` (matches alembic.ini).
    script_location = str(Path(__file__).resolve().parent / "db" / "migrations")
    sqlalchemy_url = config.require("db.url")
    runner = MigrationRunner(
        MigrationConfig(
            script_location=script_location,
            sqlalchemy_url=sqlalchemy_url,
        )
    )
    runner.upgrade("head")
    # AGENT-LESSONS §2.25 — cloud_dog_logging.AppLogger requires f-strings,
    # not %s positional args.
    _log.info(f"alembic upgrade head completed (script_location={script_location})")


def create_app() -> FastAPI:
    """Build the FastAPI app.

    Tries cloud_dog_api_kit.create_app first; falls back to a plain
    FastAPI() instance if the api-kit optional middleware can't initialise
    (Phase 1 keeps boot resilient; AGENT-LESSONS §6.94 still binds — the
    fallback path still wires the same routers + RBAC).
    """
    title = "scheduler-mcp-server"
    version = __version__
    base_path: str = str(config.get("server.api.base_path", "/v1"))

    # Run migrations BEFORE the FastAPI app starts serving — when enabled.
    _run_alembic_upgrade_if_enabled()

    try:  # prefer the platform factory
        from cloud_dog_api_kit import create_app as platform_create_app

        app = platform_create_app(
            title=title,
            version=version,
            api_prefix=base_path,
            enable_docs=True,
            enable_cors=True,
            cors_origins=config.get("server.api.cors.origins", ["*"]),
            timeout_seconds=float(config.get("server.api.timeout_seconds", 30)),
            enable_audit_logging=True,
            # W28K-1409 — the scheduler defines its OWN /health aggregator
            # (db/cache/jobs/registry subchecks). The api-kit factory's default
            # /health returns empty ``checks: {}`` and (on api-kit 0.13.2) is not
            # removed by the route-strip below, shadowing the real subchecks on
            # the deployed service. enable_health=False is the documented opt-out.
            enable_health=False,
        )
    except Exception:
        app = FastAPI(title=title, version=version)

    # cloud_dog_api_kit.create_app registers its own bare /health returning
    # {"checks":{}}, which would shadow our aggregator that probes
    # db/cache/jobs/registry. Strip it so OUR /health wins.
    app.router.routes[:] = [
        r
        for r in app.router.routes
        if not (getattr(r, "path", None) == "/health" and "GET" in (getattr(r, "methods", None) or set()))
    ]
    # PS-WEBUI-URL-CANONICAL makes /docs a legacy WebUI alias for the canonical
    # developer API-docs page. Keep /openapi.json for the raw OpenAPI contract.
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/docs"]

    # W28K-1407 NF-1407-1 — Prometheus /metrics at root (no auth; PS-40 §5).
    from fastapi import Response

    from scheduler_mcp import metrics as _metrics

    @app.get("/metrics", include_in_schema=False)
    def _prometheus_metrics() -> Response:
        body, content_type = _metrics.render()
        return Response(content=body, media_type=content_type)

    # W28E-1863 fix-wave-c (WSC-014 / PS-30 UI-R7.3): expose source commit + build
    # date + deployment identity at a root-level /version so the WebUI About page
    # can render build provenance. The scheduler serves its runtime-config.js as a
    # STATIC file (cannot carry per-build identity), so this JSON route is the
    # build-identity surface the SPA fetches. Registered here (root, no auth) — long
    # before the SPA catch-all @app.get("/{full_path:path}") below — so it is never
    # shadowed by the SPA fallback. Adopts the file-mcp / search-mcp / chart-mcp
    # reference pattern; config-routed via cloud_dog_config (RULES §1.4.1).
    @app.get("/version", include_in_schema=False)
    def _version_info() -> JSONResponse:
        _build = _build_identity()
        _app_version = str(config.get("service.version", __version__) or __version__)
        return JSONResponse(
            {
                "service": str(config.get("service.name", "scheduler-mcp-server") or "scheduler-mcp-server"),
                "version": _app_version,
                "appVersion": _app_version,
                "source_commit": _build["source_commit"],
                "source_branch": _build["source_branch"],
                "build_date": _build["build_date"],
                "container_digest": _build["container_digest"],
                "environment": _build["environment"],
                # legacy field name any VersionInfo consumer may already read
                "commit": _build["source_commit"],
            }
        )

    # Health routes at root (no base_path) so Docker healthcheck can hit /health.
    # The WebUI runtime uses API_BASE_URL=/api; expose /api/health locally too
    # because the local Docker smoke has no Traefik StripPrefix in front of it.
    app.include_router(health.router)
    app.include_router(health.router, prefix="/api")

    versioned_prefixes = [base_path]
    api_alias = f"/api{base_path}"
    if api_alias not in versioned_prefixes:
        versioned_prefixes.append(api_alias)

    # Versioned business routes under base_path (defaults to /v1) plus /api/v1
    # direct-server aliases for the same-origin WebUI smoke path.
    for prefix in versioned_prefixes:
        app.include_router(schedules.router, prefix=prefix)
        app.include_router(registry.router, prefix=prefix)
        app.include_router(runs.router, prefix=prefix)
        app.include_router(chains.router, prefix=prefix)
        app.include_router(context.router, prefix=prefix)
        # W28K-1407 F-1407-3 approval lifecycle (versioned)
        app.include_router(approvals.router, prefix=prefix)
        # W28K-1429 NL chain creation (versioned)
        app.include_router(nl_chain.router, prefix=prefix)
        # W28K-1404b audit query surface (versioned)
        app.include_router(audit.router, prefix=prefix)
        # W28K-1404e PS-73 effective-config surface (versioned, admin-scoped)
        app.include_router(admin_config.router, prefix=prefix)
    # W28K-1407 F-1407-8 PS-71 IDAM backing API — SQL-backed.
    # The W28K-1404c carve-out (in-memory cloud_dog_idam routers) is CLOSED:
    # users/groups/roles/api-keys/memberships/bindings now persist to SQL via
    # scheduler_mcp.api.idam_admin (host-app wiring over the platform
    # cloud_dog_idam.storage.sqlalchemy ORM + api_keys.hashing — the pattern
    # the platform router docstring endorses). The platform auth_router is kept
    # for the static /v1/auth surface; the SQL routers serve the CRUD +
    # /v1/idam/v1 (bindings + resource-registry). Persists across restart.
    from cloud_dog_idam.api.fastapi.router import auth_router as _idam_auth_router

    from scheduler_mcp.api import idam_admin

    for prefix in versioned_prefixes:
        admin_prefix = f"{prefix}/admin"
        app.include_router(idam_admin.user_router, prefix=admin_prefix)
        app.include_router(idam_admin.group_router, prefix=admin_prefix)
        app.include_router(idam_admin.role_router, prefix=admin_prefix)
        app.include_router(idam_admin.api_key_router, prefix=admin_prefix)
    # W28K-1409 F-1409-5 — WebUI username/password cookie login bridge, registered
    # BEFORE the platform auth_router so its /v1/auth/login + /v1/auth/logout
    # override the platform stub (username-only match, no password, no cookie).
    from scheduler_mcp import web_login

    for prefix in versioned_prefixes:
        app.include_router(web_login.router, prefix=prefix)
        app.include_router(_idam_auth_router, prefix=prefix)
        app.include_router(idam_admin.idam_v1_router, prefix=prefix)

    # Persist the bootstrap admin (user + api key) to SQL on first start so it
    # resolves after restart via the SQL api_keys table. Best-effort: a missing
    # table (auto_upgrade disabled) is tolerated.
    try:
        from scheduler_mcp.idam_sql import bootstrap_admin_sql

        bootstrap_admin_sql()
    except Exception:  # noqa: BLE001
        pass

    # W28K-1428 A2A consumer wiring — /.well-known/agent.json + /a2a/skills at root
    app.include_router(a2a.router)

    schedule_write_paths = {f"{prefix}/schedules" for prefix in versioned_prefixes}

    @app.middleware("http")
    async def _schedule_auth_before_validation(request: Request, call_next):
        if request.method == "POST" and request.url.path in schedule_write_paths:
            from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal

            principal = resolve_principal(request)
            if isinstance(principal, AnonymousPrincipal):
                return JSONResponse(status_code=401, content={"detail": "Authentication required"})
            if not principal.has_scope("schedules.write"):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Missing required scope: schedules.write"},
                )
        return await call_next(request)

    # W28K-1404d — register MCP JSON-RPC routes (POST /mcp + /messages). The
    # Ps72McpConsole hits this endpoint for tools/list + tools/call. Wrapped
    # in try/except for the host-test path where cloud_dog_api_kit is not
    # importable; in the container build it always loads.
    try:
        from cloud_dog_api_kit.mcp import register_mcp_routes

        from scheduler_mcp.mcp_tools import build_tool_contracts

        register_mcp_routes(app, build_tool_contracts())
    except ModuleNotFoundError:
        pass

    # W28K-1404a — register the long-running cloud_dog_jobs worker on
    # FastAPI startup and stop it cleanly on shutdown. The worker is a
    # consumer of the JobQueue submitted to by the tick (W28K-1414).
    if bool(config.get("worker.auto_start", True)):
        from scheduler_mcp.worker_lifecycle import start_worker, stop_worker

        # cloud_dog_api_kit installs its own lifespan and drives startup via
        # `app.state.lifecycle_hooks` (LifecycleHooks dataclass). Plain
        # `app.router.on_startup.append` is never invoked under that lifespan,
        # so register through the hooks dataclass instead. Fallback to
        # router.on_startup for the plain-FastAPI path.
        hooks = getattr(getattr(app, "state", None), "lifecycle_hooks", None)
        if hooks is not None:

            async def _on_post_router(_app):  # noqa: ANN001
                await start_worker()

            async def _on_shutdown(_app):  # noqa: ANN001
                await stop_worker()

            hooks.on_post_router = _on_post_router
            hooks.on_shutdown = _on_shutdown
        else:
            app.router.on_startup.append(start_worker)
            app.router.on_shutdown.append(stop_worker)

    # W28K-1421/1422 — mount the SPA bundle (built from
    # cloud-dog-ai-ui-monorepo/apps/scheduler-mcp via `vite build` and synced
    # into ui/dist per AGENT-LESSONS §2.29). The bundle is mounted last so it
    # cannot shadow API/health routes; client-side routes fall through to
    # index.html via the SPA catch-all below.
    from pathlib import Path

    from fastapi.responses import FileResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    ui_dist = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"
    if ui_dist.is_dir() and (ui_dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=str(ui_dist / "assets")), name="ui-assets")

        canonical_redirects = {
            "/ui/login": "/login",
            "/auth/login": "/login",
            "/audit": "/audit-log",
            "/diagnostics-audit": "/audit-log",
            "/observability": "/audit-log",
            "/logs": "/audit-log",
            "/idam/users": "/admin/users",
            "/idam/groups": "/admin/groups",
            "/idam/api-keys": "/admin/api-keys",
            "/apikeys": "/admin/api-keys",
            "/api-keys": "/admin/api-keys",
            "/idam/roles": "/admin/roles",
            "/idam/rbac": "/admin/rbac",
            "/rbac": "/admin/rbac",
            "/api-docs": "/developer/api-docs",
            "/docs": "/developer/api-docs",
            "/openapi": "/developer/api-docs",
            "/mcp-console": "/developer/mcp-console",
            "/a2a": "/developer/a2a-console",
            "/a2a-console": "/developer/a2a-console",
            "/jobs": "/system/jobs",
            "/settings": "/system/settings",
            "/about": "/system/about",
        }

        def _redirect_target(path: str, request: Request) -> str | None:
            target = canonical_redirects.get(path)
            if target is None:
                return None
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return target

        @app.get("/runtime-config.js", include_in_schema=False)
        def _runtime_config_js() -> FileResponse:
            return FileResponse(str(ui_dist / "runtime-config.js"), media_type="application/javascript")

        @app.get("/", include_in_schema=False)
        def _spa_root() -> FileResponse:
            return FileResponse(str(ui_dist / "index.html"))

        # SPA catch-all: canonical WebUI aliases return real HTTP 308s, and
        # known client-side routes still serve index.html after API/health/static
        # routes have had the first chance to match.
        @app.get("/{full_path:path}", include_in_schema=False)
        def _spa_catch_all(full_path: str, request: Request) -> FileResponse | RedirectResponse:
            target = _redirect_target(f"/{full_path}", request)
            if target is not None:
                return RedirectResponse(url=target, status_code=308)
            return FileResponse(str(ui_dist / "index.html"))

    return app


app = create_app()
