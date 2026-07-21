"""external_http adapter — direct HTTP call (W28K-1404a)."""

from __future__ import annotations

import time

import httpx

from scheduler_mcp.adapters.base import AdapterBase, AdapterContext, AdapterResult

_CLIENT: httpx.AsyncClient | None = None  # UT seam


def _shared_client() -> httpx.AsyncClient:
    """See registered_tool._shared_client — UT override preserved."""
    if _CLIENT is not None:
        return _CLIENT
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0))


class ExternalHttpAdapter(AdapterBase):
    target_type = "external_http"

    async def execute(self, ctx: AdapterContext) -> AdapterResult:
        spec = ctx.target_spec or {}
        url = spec.get("url")
        method = (spec.get("method") or "GET").upper()
        if not url:
            return AdapterResult(outcome="failed", error_code="missing_url", error_summary="target_spec.url required")
        headers = dict(spec.get("headers") or {})
        headers["x-correlation-id"] = ctx.correlation_id
        # NF-1407-2 — trace id propagation (== correlation_id / schedule_run_id).
        headers["x-trace-id"] = ctx.correlation_id
        body = spec.get("body")
        t0 = time.perf_counter()
        try:
            if _CLIENT is not None:
                r = await _CLIENT.request(method, url, json=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                    r = await client.request(method, url, json=body, headers=headers)
        except httpx.RequestError as e:
            return AdapterResult(
                outcome="failed",
                error_code="target_unreachable",
                error_summary=f"{type(e).__name__}: {e}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        duration = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 500:
            return AdapterResult(
                outcome="failed",
                error_code=f"target_http_{r.status_code}",
                error_summary=r.text[:500],
                duration_ms=duration,
            )
        if r.status_code >= 400:
            return AdapterResult(
                outcome="failed",
                error_code=f"target_http_{r.status_code}",
                error_summary=r.text[:500],
                duration_ms=duration,
            )
        # W28K-1404g — capture response body for §5.1.5 rung-(b) sentinel echo.
        body_capture = r.text[:4096]
        return AdapterResult(
            outcome="succeeded",
            result_ref=f"http:{method} {url} -> {r.status_code}:body={body_capture}",
            duration_ms=duration,
        )
