#!/usr/bin/env bash
# scheduler-mcp-server — healthcheck.sh
# Docker HEALTHCHECK; dynamic port resolution per AGENT-LESSONS §4.11
# (fall back to CLOUD_DOG__SERVER__API__PORT → 8080).
#
# W28A-SEC-R18 hardening: probe with the always-present Python stdlib (urllib) so
# the runtime image needs no `curl`/`libcurl` (removed to drop their base-OS CVEs).
set -euo pipefail

PORT="${SCHEDULER_HEALTH_PORT:-${CLOUD_DOG__SERVER__API__PORT:-8080}}"
URL="http://127.0.0.1:${PORT}/health"

exec python -c "import urllib.request,sys; r=urllib.request.urlopen('${URL}', timeout=4); sys.exit(0 if r.status==200 else 1)"
