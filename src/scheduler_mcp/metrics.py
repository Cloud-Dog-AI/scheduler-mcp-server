"""Prometheus metrics — W28K-1407 NF-1407-1 (PS-40 §5).

GET /metrics (no auth — Prometheus scrape) exposes:
  - the three scheduler counters, named EXACTLY as the contract requires:
      schedule_runs_total          (every dispatched run, terminal or blocked)
      schedule_runs_failed         (runs that ended failed)
      chain_compile_errors_total   (chain definitions that failed to compile)
  - the default platform process/runtime metrics (process_*, python_*) via
    prometheus_client's ProcessCollector + PlatformCollector.

The custom counters are rendered directly in Prometheus text format so the
metric names match the contract verbatim (prometheus_client's Counter appends
its own ``_total`` suffix, which would mangle ``schedule_runs_failed``). The
default platform metrics use prometheus_client when available; if the package
is absent the endpoint still serves the three custom counters (never 500s).
"""

from __future__ import annotations

_CUSTOM: tuple[tuple[str, str], ...] = (
    ("schedule_runs_total", "Total schedule runs dispatched (terminal or blocked)"),
    ("schedule_runs_failed", "Total schedule runs that ended in a failed state"),
    ("chain_compile_errors_total", "Total chain definitions that failed to compile"),
)

_counts: dict[str, int] = {name: 0 for name, _ in _CUSTOM}


def observe_dispatch(status: str) -> None:
    """Count one dispatched run; also count a failure when terminal status is failed."""
    _counts["schedule_runs_total"] += 1
    if status == "failed":
        _counts["schedule_runs_failed"] += 1


def inc_chain_compile_error() -> None:
    _counts["chain_compile_errors_total"] += 1


def _render_custom() -> str:
    lines: list[str] = []
    for name, help_ in _CUSTOM:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {_counts[name]}")
    return "\n".join(lines) + "\n"


# Default platform process/runtime metrics via prometheus_client (optional).
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        PlatformCollector,
        ProcessCollector,
        generate_latest,
    )

    _PLATFORM_REGISTRY = CollectorRegistry()
    try:
        ProcessCollector(registry=_PLATFORM_REGISTRY)
        PlatformCollector(registry=_PLATFORM_REGISTRY)
    except Exception:  # noqa: BLE001 — collectors are best-effort
        pass

    def _render_platform() -> str:
        try:
            return generate_latest(_PLATFORM_REGISTRY).decode("utf-8")
        except Exception:  # noqa: BLE001
            return ""

    _CONTENT_TYPE = CONTENT_TYPE_LATEST
except Exception:  # noqa: BLE001 — prometheus_client unavailable

    def _render_platform() -> str:
        return ""

    _CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def render() -> tuple[bytes, str]:
    body = _render_custom() + _render_platform()
    return body.encode("utf-8"), _CONTENT_TYPE
