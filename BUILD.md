---
template-id: T-BLD
template-version: 1.0
applies-to: BUILD.md
project: scheduler-mcp-server
doc-last-updated: 2026-06-17T00:00:00Z
doc-git-branch: main
doc-age-policy: 90d
doc-conformance-stamp: 2026-06-17T00:00:00Z
---

# scheduler-mcp-server - Build

Build and local execution use the repository `pyproject.toml`, `Dockerfile`, and `docker-build.sh`.

## Runtime contract — Python 3.13 (mandatory)

The sanctioned runtime is **Python 3.13** (`.python-version` = `3.13`,
`pyproject.toml` `requires-python = ">=3.13"`, Ruff/mypy target `py313`/`3.13`,
`Dockerfile FROM python:3.13-slim`). The package enforces this at import time via
`scheduler_mcp._runtime.enforce_runtime()` — earlier interpreters fail closed
(NF-009 / `T-UT-RUNTIME-CONTRACT`). Create and use a Python 3.13 virtual
environment for all local development and tests.

## Commands

```bash
# 1. Create a Python 3.13 virtual environment (do NOT use 3.10/3.11/3.12)
python3.13 -m venv .venv
source .venv/bin/activate
python --version            # must print Python 3.13.x

# 2. Install the project + dev tooling from the internal Cloud-Dog PyPI
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[dev]'

# 3. Run the tests (see docs/TESTS.md for tiers/markers)
python -m pytest

# 4. Build the container (Python 3.13 base) via the sanctioned path
./docker-build.sh
```
