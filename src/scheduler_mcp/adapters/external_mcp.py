"""external_mcp adapter — MCP-shaped target for NON-registered services (W28K-1407 F-1407-9).

Unlike ``registered_tool`` (which derives the MCP URL from a registry
``<service>.cloud-dog.net`` convention and a ``<service>.<tool>`` target_ref),
``external_mcp`` takes the MCP endpoint explicitly from
``target_spec.mcp_url``/``base_url`` and uses ``target_ref`` directly as the
tool name. It reuses the JSON-RPC 2.0 + ``jsonrpc_id`` sentinel-echo pattern so
AT-tier rung-(b) proof works verbatim.
"""

from __future__ import annotations

import time

import httpx

from scheduler_mcp.adapters.base import AdapterBase, AdapterContext, AdapterResult

_MCP_ACCEPT = "application/json, text/event-stream"

_CLIENT: httpx.AsyncClient | None = None  # UT seam (test_external_mcp_adapter._patch_shared_client)


class ExternalMcpAdapter(AdapterBase):
    target_type = "external_mcp"

    async def execute(self, ctx: AdapterContext) -> AdapterResult:
        spec = ctx.target_spec or {}
        # External MCP services are NOT in the registry — the endpoint is
        # explicit. No <service>.cloud-dog.net default.
        mcp_url = spec.get("mcp_url") or spec.get("base_url")
        if not mcp_url:
            return AdapterResult(
                outcome="failed",
                error_code="missing_mcp_url",
                error_summary="external_mcp requires target_spec.mcp_url or target_spec.base_url",
            )
        url = str(mcp_url).rstrip("/")
        if not url.endswith("/mcp"):
            url = f"{url}/mcp"

        tool_name = ctx.target_ref or ""
        method = spec.get("method") or ("tools/call" if tool_name else "tools/list")
        if method == "tools/call":
            params = {"name": tool_name, "arguments": spec.get("params") or {}}
        else:
            params = spec.get("params") or {}

        # jsonrpc_id override -> universal sentinel echo (JSON-RPC 2.0 echoes id).
        jsonrpc_id = spec.get("jsonrpc_id") or ctx.correlation_id
        body = {"jsonrpc": "2.0", "id": jsonrpc_id, "method": method, "params": params}

        headers = {"Accept": _MCP_ACCEPT, "Content-Type": "application/json"}
        if ctx.api_key:
            headers["x-api-key"] = ctx.api_key
            headers["Authorization"] = f"Bearer {ctx.api_key}"
        # NF-1407-2 — propagate the correlation/trace id so the external MCP can
        # correlate the inbound call with this scheduler run.
        headers["x-correlation-id"] = ctx.correlation_id
        headers["x-trace-id"] = ctx.correlation_id

        client_timeout = float(spec.get("timeout_seconds") or 30.0)
        t0 = time.perf_counter()
        try:
            if _CLIENT is not None:
                r = await _CLIENT.post(url, json=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(client_timeout)) as client:
                    r = await client.post(url, json=body, headers=headers)
        except httpx.RequestError as e:
            return AdapterResult(
                outcome="failed",
                error_code="target_unreachable",
                error_summary=f"{type(e).__name__}: {e}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        if r.status_code != 200:
            return AdapterResult(
                outcome="failed",
                error_code=f"target_http_{r.status_code}",
                error_summary=r.text[:500],
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        resp_body = r.text[:4096]
        result_ref = f"external_mcp:{url}:{str(jsonrpc_id)[:8]}:body={resp_body}"
        return AdapterResult(
            outcome="succeeded",
            result_ref=result_ref,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
