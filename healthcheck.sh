#!/usr/bin/env bash
# scheduler-mcp-server — healthcheck.sh
# Docker HEALTHCHECK; dynamic port resolution per AGENT-LESSONS §4.11
# (fall back to CLOUD_DOG__SERVER__API__PORT → SERVICE_HEALTH_PORT → 8080).
set -euo pipefail

PORT="${SCHEDULER_HEALTH_PORT:-${CLOUD_DOG__SERVER__API__PORT:-8080}}"
URL="http://127.0.0.1:${PORT}/health"

# Use ss (preferred) → netstat → fallback (AGENT-LESSONS §4.3)
if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 4 "${URL}" >/dev/null
    exit $?
else
    python -c "import urllib.request,sys; r=urllib.request.urlopen('${URL}', timeout=4); sys.exit(0 if r.status==200 else 1)"
    exit $?
fi
