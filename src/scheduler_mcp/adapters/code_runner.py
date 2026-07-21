"""code_runner adapter — dispatch to the configured code-runner endpoint.

The code-runner base URL is not hardcoded; it is resolved via config
(``adapters.code_runner.base_url``), which flows through cloud_dog_config's
PS-80 precedence chain (process env first). Set it per-deployment, e.g.::

    SCHEDULER__ADAPTERS__CODE_RUNNER__BASE_URL=https://code-runner-host.example.com

An explicit per-target ``target_spec.base_url`` still takes precedence.
"""

from __future__ import annotations

import time

import httpx

from scheduler_mcp import config
from scheduler_mcp.adapters.base import AdapterBase, AdapterContext, AdapterResult

_CLIENT: httpx.AsyncClient | None = None  # UT seam


def _shared_client() -> httpx.AsyncClient:
    """See registered_tool._shared_client — UT override preserved."""
    if _CLIENT is not None:
        return _CLIENT
    return httpx.AsyncClient(timeout=httpx.Timeout(60.0))


class CodeRunnerAdapter(AdapterBase):
    target_type = "code_runner"

    async def execute(self, ctx: AdapterContext) -> AdapterResult:
        spec = ctx.target_spec or {}
        base = spec.get("base_url") or config.get("adapters.code_runner.base_url", "")
        if not base:
            return AdapterResult(
                outcome="failed",
                error_code="missing_base_url",
                error_summary="code-runner base_url not configured; set "
                "adapters.code_runner.base_url or target_spec.base_url",
            )
        runtime = spec.get("runtime") or "python"  # 'python' | 'bash'
        code = spec.get("code")
        if not code:
            return AdapterResult(outcome="failed", error_code="missing_code", error_summary="target_spec.code required")
        # W28K-1404g: canonical code-runner endpoint is /api/execute (not /run).
        # Honour an explicit `spec.endpoint` override for forward compatibility.
        url = f"{base.rstrip('/')}{spec.get('endpoint') or '/api/execute'}"
        headers = {"x-correlation-id": ctx.correlation_id, "Content-Type": "application/json"}
        if ctx.api_key:
            # W28K-1404g: code-runner's auth middleware rejects the
            # Authorization: Bearer header with "Bearer authentication unavailable"
            # — that scheme is wired off in the running container — so we send
            # x-api-key ONLY for the code_runner target. (MCP siblings need
            # Bearer for tools/call but that's a registered_tool concern.)
            headers["x-api-key"] = ctx.api_key
        body = {"runtime": runtime, "code": code, "correlation_id": ctx.correlation_id}
        t0 = time.perf_counter()
        try:
            if _CLIENT is not None:
                r = await _CLIENT.post(url, json=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                    r = await client.post(url, json=body, headers=headers)
        except httpx.RequestError as e:
            return AdapterResult(
                outcome="failed",
                error_code="target_unreachable",
                error_summary=f"{type(e).__name__}: {e}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        duration = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            return AdapterResult(
                outcome="failed",
                error_code=f"target_http_{r.status_code}",
                error_summary=r.text[:500],
                duration_ms=duration,
            )
        # W28K-1404g — capture the response body into result_ref so the §5.1.5
        # rung-(b) sentinel-echo assertion can find the injected UUID. The
        # body is truncated to 4 KiB to keep run rows bounded; sentinels are
        # short UUIDs so this is ample.
        response_body = r.text[:4096]
        return AdapterResult(
            outcome="succeeded",
            result_ref=f"code_runner:{runtime}:{ctx.correlation_id[:8]}:body={response_body}",
            duration_ms=duration,
        )
