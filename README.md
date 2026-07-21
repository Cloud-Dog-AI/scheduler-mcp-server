---
template-id: T-RME
template-version: 1.0
applies-to: README.md
---

# Scheduler MCP Server

`scheduler-mcp-server` runs and manages scheduled jobs, schedule chains, and a
project registry, exposing them over REST, a Web UI, MCP, and A2A-compatible
endpoints. It authenticates callers with API keys (REST/MCP/A2A) and a
username/password cookie session (Web UI), and enforces scope-based RBAC on
every write.

## Development runtime — Python 3.13 (mandatory)

The sanctioned runtime is **Python 3.13**. Create and use a Python 3.13 virtual
environment for all local development and tests (see `BUILD.md` / `docs/BUILD.md`):

```bash
python3.13 -m venv .venv && source .venv/bin/activate
python --version                       # Python 3.13.x
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[dev]'
python -m pytest
```

The runtime is pinned in `.python-version`, `pyproject.toml`
(`requires-python = ">=3.13"`), the `Dockerfile` (`python:3.13-slim`), and
enforced at import by `scheduler_mcp._runtime.enforce_runtime()`
(NF-009 / `T-UT-RUNTIME-CONTRACT`).

## Local development

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install --index-url https://pypi.org/simple/ -e ".[dev]"
```

The cloud-dog platform packages must be resolvable from the active package index.
See [EXTERNAL-BUILD.md](EXTERNAL-BUILD.md) if they are not yet on your index.

## Publication quick start

Prerequisites:

- Docker 24 or newer with BuildKit enabled
- Python 3.13 if you run the package locally (container base is `python:3.13-slim`)
- Public package source: `https://pypi.org/simple/` (override with `PYPI_URL`)

Build the public image (see [EXTERNAL-BUILD.md](EXTERNAL-BUILD.md) for full guidance):

```bash
./docker-build.sh latest --variant public
```

Run the local smoke by executing the shell block in [PUBLICATION-SMOKE.md](PUBLICATION-SMOKE.md).
The smoke run uses [.env.example](.env.example) and probes the exposed ports:

- API / Web: `8080`
- MCP: `8082`
- A2A: `8083`

## Configuration

Configuration is layered by `cloud_dog_config` with the precedence
`os.environ > env files > config.yaml > defaults.yaml`. Committed defaults live in
[defaults.yaml](defaults.yaml); never store secrets there. Credentials and
environment-specific values are supplied via env files or the environment (for
example `CLOUD_DOG__SERVER__WEB__PASSWORD` for the Web UI admin login).

## Testing

```bash
python -m pytest tests/unit -q                        # unit tier
python -m pytest tests/integration -q                 # in-process integration tier
python -m pytest tests/application --env tests/env-AT # against a running container
```

## Endpoints

| Surface  | Path prefix | Auth                              |
|----------|-------------|-----------------------------------|
| REST API | `/v1`       | API key or session cookie         |
| Web UI   | `/`         | username/password cookie session  |
| MCP      | `/mcp`      | API key                           |
| A2A      | `/a2a`      | API key (agent card is public)    |

## Licence

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
