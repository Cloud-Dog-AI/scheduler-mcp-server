"""Chain compiler + step executors — W28K-1417 + W28K-1418.

Compiles a chain definition (JSON DAG) into an ordered execution plan,
detecting cycles and validating step types. Step executors are dispatched
by `type` — http / mcp / a2a / wait / sub_chain. Phase 2 ships:

  - cycle detection + topological order (Kahn)
  - step type whitelist + per-step schema
  - in-process execute() that runs the DAG against a step registry
    (the registry is injected so tests can substitute fakes; real wiring
    lands at the API layer in Phase 4)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

ALLOWED_STEP_TYPES = frozenset({"http", "mcp", "a2a", "wait", "sub_chain"})


class ChainCompileError(ValueError):
    """Chain JSON is invalid (missing fields, bad type, or cyclic)."""


@dataclass
class ChainStep:
    step_id: str
    type: str
    config: dict
    needs: list[str] = field(default_factory=list)


@dataclass
class CompiledChain:
    chain_id: str
    version: int
    steps: dict[str, ChainStep]
    execution_order: list[str]


def compile_chain(chain_id: str, version: int, definition: dict) -> CompiledChain:
    """Compile a chain definition; raises ChainCompileError on invalid input.

    W28K-1407 NF-1407-1 — every compile failure increments the
    ``chain_compile_errors_total`` metric (single point covering API + A2A +
    worker chain-adapter callers), then re-raises.
    """
    try:
        return _compile_chain_impl(chain_id, version, definition)
    except ChainCompileError:
        try:
            from scheduler_mcp.metrics import inc_chain_compile_error

            inc_chain_compile_error()
        except Exception:  # noqa: BLE001 — metrics must never mask the compile error
            pass
        raise


def _compile_chain_impl(chain_id: str, version: int, definition: dict) -> CompiledChain:
    raw_steps = definition.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ChainCompileError("definition.steps must be a non-empty list")

    steps: dict[str, ChainStep] = {}
    for s in raw_steps:
        if not isinstance(s, dict):
            raise ChainCompileError(f"step must be an object, got {type(s).__name__}")
        sid = s.get("step_id") or s.get("id")
        if not sid or not isinstance(sid, str):
            raise ChainCompileError(f"step missing string step_id: {s!r}")
        if sid in steps:
            raise ChainCompileError(f"duplicate step_id: {sid!r}")
        stype = s.get("type")
        if stype not in ALLOWED_STEP_TYPES:
            raise ChainCompileError(f"unknown step type {stype!r} (allowed: {sorted(ALLOWED_STEP_TYPES)})")
        needs_raw = s.get("needs") or []
        if not isinstance(needs_raw, list) or any(not isinstance(n, str) for n in needs_raw):
            raise ChainCompileError(f"step {sid!r}: needs must be a list[str]")
        steps[sid] = ChainStep(step_id=sid, type=stype, config=s.get("config", {}) or {}, needs=list(needs_raw))

    # Validate needs refer to known steps + topological sort (Kahn's algorithm)
    for sid, st in steps.items():
        for n in st.needs:
            if n not in steps:
                raise ChainCompileError(f"step {sid!r}: needs unknown step {n!r}")
            if n == sid:
                raise ChainCompileError(f"step {sid!r}: self-dependency")

    indeg: dict[str, int] = {sid: 0 for sid in steps}
    for sid, st in steps.items():
        for _ in st.needs:
            indeg[sid] += 1
    ready = sorted([sid for sid, d in indeg.items() if d == 0])
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        # find dependents whose indegree drops to 0
        next_ready: list[str] = []
        for sid, st in steps.items():
            if cur in st.needs:
                indeg[sid] -= 1
                if indeg[sid] == 0:
                    next_ready.append(sid)
        ready.extend(sorted(next_ready))
    if len(order) != len(steps):
        raise ChainCompileError("chain has a cycle (topological sort incomplete)")

    return CompiledChain(chain_id=chain_id, version=version, steps=steps, execution_order=order)


# ---------- Step executors ----------
# Each executor takes (step, context) -> result dict. The dispatcher composes
# results into the chain context so subsequent steps see {step_id: result}.

StepExecutor = Callable[[ChainStep, dict[str, Any]], dict[str, Any]]


def _exec_wait(step: ChainStep, _ctx: dict[str, Any]) -> dict[str, Any]:
    """Trivial deterministic wait — Phase 2 doesn't actually sleep; it
    records the requested delay so the executor (Phase 4) can choose
    sync sleep or job-queue requeue.
    """
    return {"type": "wait", "seconds": int(step.config.get("seconds", 0))}


def _exec_http(step: ChainStep, _ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = step.config
    url = cfg.get("url")
    method = (cfg.get("method") or "GET").upper()
    if not url:
        raise ChainCompileError(f"http step {step.step_id!r}: url required")
    # Phase 2 does NOT actually call out — Phase 4 wires httpx-via-platform.
    # We compile the call descriptor so executor evidence is reproducible.
    return {"type": "http", "method": method, "url": url}


def _exec_mcp(step: ChainStep, _ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = step.config
    tool = cfg.get("tool")
    server = cfg.get("server")
    if not tool or not server:
        raise ChainCompileError(f"mcp step {step.step_id!r}: tool + server required")
    return {"type": "mcp", "server": server, "tool": tool, "params": cfg.get("params", {})}


def _exec_a2a(step: ChainStep, _ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = step.config
    target = cfg.get("agent") or cfg.get("target")
    skill = cfg.get("skill")
    if not target or not skill:
        raise ChainCompileError(f"a2a step {step.step_id!r}: agent + skill required")
    return {"type": "a2a", "agent": target, "skill": skill, "input": cfg.get("input", {})}


def _exec_sub_chain(step: ChainStep, _ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = step.config
    sub = cfg.get("chain_id")
    if not sub:
        raise ChainCompileError(f"sub_chain step {step.step_id!r}: chain_id required")
    return {"type": "sub_chain", "chain_id": sub}


_DEFAULT_EXECUTORS: dict[str, StepExecutor] = {
    "wait": _exec_wait,
    "http": _exec_http,
    "mcp": _exec_mcp,
    "a2a": _exec_a2a,
    "sub_chain": _exec_sub_chain,
}


def execute(
    compiled: CompiledChain,
    *,
    executors: dict[str, StepExecutor] | None = None,
    initial_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the compiled chain in topological order. Returns a result
    dict {step_id: result}. Tests may inject custom executors.
    """
    exes = dict(_DEFAULT_EXECUTORS)
    if executors:
        exes.update(executors)
    ctx: dict[str, Any] = dict(initial_context or {})
    results: dict[str, Any] = {}
    for sid in compiled.execution_order:
        st = compiled.steps[sid]
        ex = exes.get(st.type)
        if ex is None:
            raise ChainCompileError(f"no executor registered for step type {st.type!r}")
        out = ex(st, ctx)
        results[sid] = out
        ctx[sid] = out
    return results


def step_ids(compiled: CompiledChain) -> Iterable[str]:
    return iter(compiled.execution_order)
