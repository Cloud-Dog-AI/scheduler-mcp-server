"""Project-local Python runtime contract (W28R-3019).

The scheduler-mcp-server sanctioned runtime is Python 3.13 (remediated from 3.11
to clear the fixable CPython High/Critical supply-chain rows). This preflight
enforces the contract at import time so the service, its tests, and its tooling
all refuse to run under an earlier interpreter — a Dockerfile-only base-image
bump is not sufficient (RULES / AGENT-LESSONS §6.172; W28R-3007 PY313 send-back).

Binds requirement NF-009 (runtime contract) and test T-UT-RUNTIME-CONTRACT.
"""

from __future__ import annotations

import sys

# The minimum sanctioned interpreter. Keep aligned with:
#   - .python-version               (3.13)
#   - pyproject.toml requires-python (>=3.13)
#   - [tool.ruff] target-version    (py313)
#   - [tool.mypy] python_version    (3.13)
#   - Dockerfile FROM python:3.13-slim
MINIMUM_PYTHON: tuple[int, int] = (3, 13)


def enforce_runtime() -> None:
    """Raise ``RuntimeError`` when running under an interpreter older than the
    sanctioned Python 3.13 runtime.

    Called at package import (``scheduler_mcp.__init__``) so every entry point —
    server, worker, CLI, tests and lint tooling — fails closed on an unsupported
    interpreter rather than silently running on a vulnerable runtime.
    """
    if tuple(sys.version_info) < MINIMUM_PYTHON:
        # Index access (not .major/.minor) so the contract is testable with any
        # version-tuple stand-in and does not depend on the namedtuple attributes.
        vi = tuple(sys.version_info)
        found = ".".join(str(p) for p in vi[:3])
        want = f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}"
        raise RuntimeError(
            "scheduler-mcp-server requires Python >= "
            f"{want}; found Python {found}. "
            "Create and use a Python 3.13 virtual environment "
            "(see docs/BUILD.md and docs/TESTS.md)."
        )
