"""Target adapter package — W28K-1404a.

Each adapter module implements the W28K-1404a adapter contract for one
target_type. The module's `execute(ctx) -> AdapterResult` is the real
async implementation; the shim functions in `scheduler_mcp.worker._REGISTRY`
remain in place to keep W28K-1430 functional-matrix tests stable, and
delegate to the per-module `execute` when the caller uses the new path.

Routing:
    target_type=="registered_tool"  -> adapters.registered_tool
    target_type=="external_http"    -> adapters.external_http
    target_type=="code_runner"      -> adapters.code_runner
    target_type=="sandbox_command"  -> adapters.sandbox_command
    target_type=="chain"            -> adapters.chain
"""

from __future__ import annotations

from scheduler_mcp.adapters import (
    chain,
    code_runner,
    external_http,
    external_mcp,
    registered_tool,
    sandbox_command,
)
from scheduler_mcp.adapters.base import AdapterBase, AdapterContext, AdapterResult

ASYNC_ADAPTERS: dict[str, AdapterBase] = {
    "registered_tool": registered_tool.RegisteredToolAdapter(),
    "external_http": external_http.ExternalHttpAdapter(),
    "code_runner": code_runner.CodeRunnerAdapter(),
    "sandbox_command": sandbox_command.SandboxCommandAdapter(),
    "chain": chain.ChainAdapter(),
    # W28K-1407 F-1407-9 — generic MCP target for non-registered services.
    "external_mcp": external_mcp.ExternalMcpAdapter(),
}


def get_async_adapter(target_type: str) -> AdapterBase | None:
    return ASYNC_ADAPTERS.get(target_type)


__all__ = [
    "AdapterBase",
    "AdapterContext",
    "AdapterResult",
    "ASYNC_ADAPTERS",
    "get_async_adapter",
]
