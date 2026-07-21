"""registered_tool adapter — MCP tools/call against a registry project (W28K-1404a)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from scheduler_mcp import config
from scheduler_mcp.adapters.base import AdapterBase, AdapterContext, AdapterResult

_MCP_ACCEPT = "application/json, text/event-stream"


_CLIENT: httpx.AsyncClient | None = None  # UT seam (test_adapters._patch_shared_client)


def _shared_client() -> httpx.AsyncClient:
    """W28K-1404g — return the UT-monkeypatched _CLIENT if set, else a fresh
    AsyncClient. dispatch_run uses asyncio.run() which creates and then
    CLOSES a new event loop each invocation; a module-level cached client
    binds to the first loop and then raises 'Event loop is closed' on every
    subsequent invocation. So the production default is per-call.

    The UT path (tests/unit/test_adapters.py) sets `_CLIENT` to a
    MockTransport-backed client via monkeypatch — that override path is
    preserved verbatim here.
    """
    if _CLIENT is not None:
        return _CLIENT
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0))


def _new_client() -> httpx.AsyncClient:
    """Alias for callers that want the production path explicitly."""
    return _shared_client()


def _json_response_payload(response: httpx.Response) -> Any:
    """Return JSON from normal or SSE-framed MCP responses."""
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        payload: Any = None
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
        return payload


def _mcp_failure(value: Any) -> str:
    """Classify MCP/JSON-RPC error envelopes without inspecting domain result rows."""
    if not isinstance(value, dict):
        return ""
    if "error" in value:
        error = value["error"]
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "mcp_error")
        return str(error).strip() or "mcp_tool_returned_error"
    if value.get("isError") is True:
        return "mcp_tool_returned_error"
    if value.get("ok") is False:
        return str(value.get("reason") or value.get("message") or "mcp_tool_returned_ok_false")

    for key in ("result", "structuredContent", "data"):
        failure = _mcp_failure(value.get(key))
        if failure:
            return failure
    content = value.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("isError") is True:
                return "mcp_tool_returned_error"
            text = item.get("text")
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            failure = _mcp_failure(parsed)
            if failure:
                return failure
    return ""


def _job_envelope(value: Any) -> dict[str, Any] | None:
    """Return a sibling's async-job envelope, if this payload is one.

    W28R-3019 R4 — some siblings cannot finish long work inside their own
    synchronous budget and instead answer with a job handle, e.g. sql-agent's
    NL->SQL returns::

        {"success": false, "error": {...sync budget of 480 seconds...},
         "job_id": "JOB-004060", "status": "running"|"pending",
         "poll_url": "/jobs/JOB-004060", "result_url": "/jobs/JOB-004060/result"}

    That is NOT a tool failure -- it is the platform's async/job lifecycle
    (AGENT-LESSONS CDP-ARCH-006 / CDP-TEST-006: long LLM, indexing, build and
    retrieval work uses the common async/job contract with bounded polling).
    Treating it as an error made a scheduled long query permanently impossible.
    """
    if not isinstance(value, dict):
        return None
    job_id = value.get("job_id")
    status = str(value.get("status") or "").lower()
    if not job_id or status not in ("running", "pending", "queued", "submitted"):
        return None
    return {
        "job_id": str(job_id),
        "poll_url": str(value.get("poll_url") or f"/api/jobs/{job_id}"),
        "result_url": str(value.get("result_url") or f"/api/jobs/{job_id}/result"),
    }


def _find_job_envelope(value: Any) -> dict[str, Any] | None:
    """Locate a job envelope anywhere in an MCP tools/call response body."""
    if not isinstance(value, dict):
        return None
    for key in ("result", "structuredContent", "data"):
        env = _job_envelope(value.get(key))
        if env:
            return env
    inner = value.get("result") if isinstance(value.get("result"), dict) else value
    content = (inner or {}).get("content") if isinstance(inner, dict) else None
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            env = _job_envelope(item)
            if env:
                return env
            text = item.get("text")
            if isinstance(text, str):
                try:
                    env = _job_envelope(json.loads(text))
                except json.JSONDecodeError:
                    env = None
                if env:
                    return env
    return None


def _job_url_candidates(
    root: str,
    advertised: str,
    job_id: str,
    *,
    kind: str,
) -> list[str]:
    """Return supported sibling async-job URLs, most-specific first.

    API-kit MCP transports expose opaque ``job-...`` handles at
    ``/mcp/jobs/<id>`` while database-backed services commonly expose their
    handles at ``/api/jobs/<id>``.  A wait=false acknowledgement is permitted
    to omit ``poll_url``, so the adapter must support both canonical surfaces
    instead of assuming that every job handle belongs to the REST Jobs API.
    """
    out: list[str] = []

    def add(url: str) -> None:
        if url and url not in out:
            out.append(url)

    if advertised:
        add(advertised if advertised.startswith("http") else f"{root}{advertised}")
        if not advertised.startswith("http") and not advertised.startswith("/api/"):
            add(f"{root}/api{advertised}")

    suffix = "/result" if kind == "result" else ""
    add(f"{root}/api/jobs/{job_id}{suffix}")
    add(f"{root}/mcp/jobs/{job_id}{suffix}")
    return out


async def _await_sibling_job(
    base_url: str,
    env: dict[str, Any],
    headers: dict[str, str],
    *,
    deadline_s: float,
    interval_s: float,
) -> tuple[str, Any]:
    """Poll a sibling job to a terminal state. Returns (state, payload).

    Bounded polling only -- never an unbounded wait, never a bespoke sleep loop
    outside the caller's budget (CDP-TEST-006).
    """
    root = base_url[: -len("/mcp")] if base_url.endswith("/mcp") else base_url

    poll_urls = _job_url_candidates(
        root, env["poll_url"], env["job_id"], kind="poll"
    )
    result_urls = _job_url_candidates(
        root, env["result_url"], env["job_id"], kind="result"
    )
    # The job handle is served by the sibling's REST API, not its MCP route. Carry the
    # api-key + correlation headers, but DROP Content-Type and Authorization: the MCP call
    # sends both x-api-key and Bearer for sibling compatibility, while the REST surface
    # rejects the Bearer form outright ("Bearer token verification not configured" -> 401).
    poll_headers = {k: v for k, v in headers.items() if k.lower() not in ("content-type", "authorization", "accept")}
    started = time.perf_counter()

    async def _get_json(client: httpx.AsyncClient, urls: list[str]) -> tuple[Any | None, str]:
        last = ""
        for u in urls:
            try:
                rr = await client.get(u, headers=poll_headers)
            except httpx.RequestError as e:
                last = f"{type(e).__name__}: {e}"
                continue
            if rr.status_code != 200:
                last = f"HTTP {rr.status_code} at {u}: {rr.text[:120]}"
                continue
            try:
                return rr.json(), ""
            except (json.JSONDecodeError, ValueError):
                last = f"non-JSON at {u} (SPA-shadowed route?): {rr.text[:80]}"
        return None, last

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        while time.perf_counter() - started < deadline_s:
            doc, err = await _get_json(client, poll_urls)
            if doc is None:
                return "poll_bad_payload", err
            state = str(doc.get("status") or "").lower()
            if state in ("succeeded", "completed", "success"):
                res_doc, _ = await _get_json(client, result_urls)
                return "succeeded", (res_doc if res_doc is not None else doc)
            if state in ("failed", "error", "cancelled", "canceled", "dead_lettered"):
                return "failed", doc
            await asyncio.sleep(interval_s)
    return "timeout", {"job_id": env["job_id"], "waited_seconds": round(time.perf_counter() - started)}


class RegisteredToolAdapter(AdapterBase):
    target_type = "registered_tool"

    async def execute(self, ctx: AdapterContext) -> AdapterResult:
        spec = ctx.target_spec or {}
        target_ref = ctx.target_ref or ""
        # target_ref is canonically "<service>.<tool_name>", e.g. file-mcp.list_files
        if "." not in target_ref:
            return AdapterResult(
                outcome="failed",
                error_code="bad_target_ref",
                error_summary=f"target_ref {target_ref!r} not in <service>.<tool> form",
            )
        service, tool_name = target_ref.split(".", 1)
        base_url = spec.get("base_url") or spec.get("mcp_url")
        if not base_url:
            config_key = spec.get("base_url_config_key") or spec.get("mcp_url_config_key")
            if config_key:
                base_url = config.get(str(config_key))
        if not base_url:
            base_url = f"https://{service}.cloud-dog.net"
        base_url = str(base_url).rstrip("/")
        # Vault's canonical ``mcp_url`` values already include the transport
        # suffix.  Accept both service roots and full MCP URLs without producing
        # the invalid ``/mcp/mcp`` path.
        url = base_url if base_url.endswith("/mcp") else f"{base_url}/mcp"
        params = spec.get("params") or {}
        headers = {"Accept": _MCP_ACCEPT, "Content-Type": "application/json"}
        if ctx.api_key:
            # Send BOTH x-api-key and Authorization: Bearer. file-mcp's
            # MCP `tools/call` route (W28C-1702) requires Bearer; some other
            # MCP siblings accept x-api-key for the same path. Including both
            # keeps the adapter compatible with the full PS-95 sibling set.
            headers["x-api-key"] = ctx.api_key
            headers["Authorization"] = f"Bearer {ctx.api_key}"
        headers["x-correlation-id"] = ctx.correlation_id
        # NF-1407-2 — propagate the trace id (== correlation_id / schedule_run_id)
        # so the downstream sibling can correlate the inbound tools/call.
        headers["x-trace-id"] = ctx.correlation_id

        # W28K-1404g — `target_spec.jsonrpc_id` overrides the JSON-RPC id so
        # AT-tier rung-(b) sentinel proof works universally: every JSON-RPC 2.0
        # response echoes the request `id` verbatim, so the captured body always
        # contains the sentinel. Falls back to correlation_id for production.
        jsonrpc_id = spec.get("jsonrpc_id") or ctx.correlation_id
        body = {
            "jsonrpc": "2.0",
            "id": jsonrpc_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": params},
        }

        t0 = time.perf_counter()
        # W28K-1404g — honor target_spec.timeout_seconds for the HTTP client so
        # LLM-class siblings (sql-agent / expert-agent) get the AGENT-LESSONS
        # §3.17 480s budget instead of the default 30s read deadline.
        client_timeout = float(spec.get("timeout_seconds") or 30.0)
        try:
            if _CLIENT is not None:
                # UT path — caller-owned client, do NOT close it
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
        payload = _json_response_payload(r)

        # W28R-3019 R4 — sibling async/job lifecycle (CDP-ARCH-006 / CDP-TEST-006).
        # If the sibling answered with a job handle instead of a result (it could not
        # finish inside its own sync budget), await that job within THIS target's
        # timeout_seconds rather than mis-reporting it as a tool error. Opt out with
        # target_spec.await_job = false.
        if spec.get("await_job", True):
            job = _find_job_envelope(payload)
            if job:
                remaining = max(0.0, client_timeout - (time.perf_counter() - t0))
                state, job_payload = await _await_sibling_job(
                    url,
                    job,
                    headers,
                    deadline_s=remaining,
                    interval_s=float(spec.get("job_poll_interval_seconds") or 5.0),
                )
                duration_ms = int((time.perf_counter() - t0) * 1000)
                if state == "succeeded":
                    return AdapterResult(
                        outcome="succeeded",
                        result_ref=json.dumps(job_payload)[:20000],
                        duration_ms=duration_ms,
                    )
                if state == "timeout":
                    return AdapterResult(
                        outcome="failed",
                        error_code="timeout",
                        error_summary=f"sibling job {job['job_id']} not terminal within {client_timeout:.0f}s",
                        duration_ms=duration_ms,
                    )
                return AdapterResult(
                    outcome="failed",
                    error_code="target_tool_error",
                    error_summary=f"sibling job {job['job_id']} {state}: {str(job_payload)[:300]}",
                    duration_ms=duration_ms,
                )

        failure = _mcp_failure(payload)
        if failure:
            return AdapterResult(
                outcome="failed",
                error_code="target_tool_error",
                error_summary=failure[:500],
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        # W28K-1404g — capture the response body into result_ref so the §5.1.5
        # rung-(b) sentinel-echo assertion can find the injected UUID. SSE
        # responses come through r.text as-is; truncate to 4 KiB.
        response_body = r.text[:4096]
        result_ref = f"mcp:{service}:{tool_name}:{ctx.correlation_id[:8]}:body={response_body}"
        return AdapterResult(
            outcome="succeeded",
            result_ref=result_ref,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
