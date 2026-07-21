"""chain adapter — delegates to scheduler_mcp.chain (W28K-1404a).

The actual chain compilation + execution logic lives in
`scheduler_mcp.chain` (W28K-1417 + W28K-1418). This adapter resolves the
Chain row from the DB by `target_ref`, compiles it, and executes the DAG
in order. Sub-step results are placed in the chain context so subsequent
steps see {step_id: result}.
"""

from __future__ import annotations

import time

from sqlalchemy import select

from scheduler_mcp.adapters.base import AdapterBase, AdapterContext, AdapterResult
from scheduler_mcp.chain import ChainCompileError, compile_chain
from scheduler_mcp.chain import execute as execute_chain
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.chain import Chain


class ChainAdapter(AdapterBase):
    target_type = "chain"

    async def execute(self, ctx: AdapterContext) -> AdapterResult:
        target_ref = ctx.target_ref
        if not target_ref:
            return AdapterResult(
                outcome="failed", error_code="missing_target_ref", error_summary="target_ref (chain_id) required"
            )
        sm = get_session_manager()
        t0 = time.perf_counter()
        with sm.session() as session:
            chain_row = (
                session.execute(select(Chain).where((Chain.chain_id == target_ref) | (Chain.name == target_ref)))
                .scalars()
                .first()
            )
            if chain_row is None:
                return AdapterResult(
                    outcome="failed",
                    error_code="chain_not_found",
                    error_summary=f"no chain with id/name {target_ref!r}",
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )
            try:
                compiled = compile_chain(chain_row.chain_id, chain_row.version, chain_row.definition or {})
            except ChainCompileError as e:
                return AdapterResult(
                    outcome="failed",
                    error_code="chain_compile",
                    error_summary=str(e),
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )
            try:
                results = execute_chain(compiled)
            except ChainCompileError as e:
                return AdapterResult(
                    outcome="failed",
                    error_code="chain_step",
                    error_summary=str(e),
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )
        return AdapterResult(
            outcome="succeeded",
            result_ref=f"chain:{chain_row.chain_id}:steps={len(results)}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
