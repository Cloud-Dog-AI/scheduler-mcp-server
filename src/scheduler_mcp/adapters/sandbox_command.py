"""sandbox_command adapter — isolated subprocess execution (W28K-1404a).

The sandbox dir is provisioned via cloud_dog_storage (no per-service sandbox
implementation). Subprocess runs with restricted cwd; never invokes the
host shell with PATH-elevated commands.
"""

from __future__ import annotations

import asyncio
import shlex
import tempfile
import time
from pathlib import Path

from scheduler_mcp import config
from scheduler_mcp.adapters.base import AdapterBase, AdapterContext, AdapterResult


def _sandbox_root() -> Path:
    """Sandbox root: cloud_dog_config override → /app/data/sandbox if writable
    → tempdir. Honours `adapters.sandbox_command.root` config key."""
    configured = config.get("adapters.sandbox_command.root", None)
    if configured:
        return Path(str(configured))
    canonical = Path("/app/data/sandbox")
    try:
        canonical.mkdir(parents=True, exist_ok=True)
        return canonical
    except (PermissionError, FileNotFoundError, OSError):
        return Path(tempfile.gettempdir()) / "scheduler-sandbox"


class SandboxCommandAdapter(AdapterBase):
    target_type = "sandbox_command"

    async def execute(self, ctx: AdapterContext) -> AdapterResult:
        spec = ctx.target_spec or {}
        command = spec.get("command")
        if not command:
            return AdapterResult(
                outcome="failed", error_code="missing_command", error_summary="target_spec.command required"
            )
        cwd_default = _sandbox_root() / ctx.correlation_id[:16]
        cwd = Path(spec.get("cwd") or cwd_default)
        cwd.mkdir(parents=True, exist_ok=True)

        argv = command if isinstance(command, list) else shlex.split(str(command))
        timeout = float(ctx.timeout_seconds or spec.get("timeout_seconds") or 30)
        t0 = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return AdapterResult(
                outcome="failed",
                error_code="timeout",
                error_summary=f"sandbox command exceeded {timeout}s",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        except FileNotFoundError as e:
            return AdapterResult(
                outcome="failed",
                error_code="command_not_found",
                error_summary=str(e),
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        duration = int((time.perf_counter() - t0) * 1000)
        if proc.returncode != 0:
            return AdapterResult(
                outcome="failed",
                error_code=f"exit_{proc.returncode}",
                error_summary=(stderr or b"").decode("utf-8", "replace")[:500],
                duration_ms=duration,
            )
        # W28K-1404g — capture stdout into result_ref for §5.1.5 rung-(b) sentinel echo.
        stdout_text = (stdout or b"").decode("utf-8", "replace")[:4096]
        return AdapterResult(
            outcome="succeeded",
            result_ref=f"sandbox:{ctx.correlation_id[:8]}:rc=0:stdout={stdout_text}",
            duration_ms=duration,
        )
