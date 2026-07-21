#!/usr/bin/env bash
# scheduler-mcp-server — server_control.sh (RULES §3.1)
# The ONLY way to start/stop/check the scheduler-mcp-server outside Docker.
# Always pass --env <env-file>.
#
# Usage:
#   ./server_control.sh --env tests/env-IT start all
#   ./server_control.sh --env tests/env-IT status
#   ./server_control.sh --env tests/env-IT stop all
set -euo pipefail

usage() {
    echo "Usage: $0 --env <env-file> <start|stop|status> [all|api]"
    exit 1
}

ENV_FILE=""
ACTION=""
ROLE="all"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)
            ENV_FILE="$2"
            shift 2
            ;;
        start|stop|status|restart)
            ACTION="$1"
            shift
            ;;
        all|api|mcp|a2a)
            ROLE="$1"
            shift
            ;;
        *)
            usage
            ;;
    esac
done

[[ -z "${ENV_FILE}" || -z "${ACTION}" ]] && usage

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="${REPO_ROOT}/.pids"
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${PID_DIR}" "${LOG_DIR}"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

ENV_HASH="$(sha256sum "${ENV_FILE}" 2>/dev/null | awk '{print substr($1,1,12)}' || echo nohash)"
PID_FILE="${PID_DIR}/api-${ENV_HASH}.pid"

# Use the platform `cloud_dog_config` env file chaining mechanism
export CLOUD_DOG_ENV_FILES="${ENV_FILE}"

start_api() {
    if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
        echo "API already running (pid=$(cat "${PID_FILE}"))"
        return 0
    fi
    echo "Starting scheduler-mcp API (env=${ENV_FILE}, hash=${ENV_HASH})"
    local python_bin="${GUARD_PYTHON:-python}"
    if command -v setsid >/dev/null 2>&1; then
        PYTHONUNBUFFERED=1 PYTHONFAULTHANDLER=1 setsid "${python_bin}" -m scheduler_mcp.server > "${LOG_DIR}/api.log" 2>&1 < /dev/null &
    else
        PYTHONUNBUFFERED=1 PYTHONFAULTHANDLER=1 nohup "${python_bin}" -m scheduler_mcp.server > "${LOG_DIR}/api.log" 2>&1 < /dev/null &
    fi
    echo $! > "${PID_FILE}"
    disown "$(cat "${PID_FILE}")" 2>/dev/null || true
    sleep 2
    if kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
        echo "API started, pid=$(cat "${PID_FILE}")"
    else
        echo "API failed to start. Last log lines:"
        tail -20 "${LOG_DIR}/api.log" 2>/dev/null || true
        return 1
    fi
}

stop_api() {
    if [[ -f "${PID_FILE}" ]]; then
        local pid; pid="$(cat "${PID_FILE}")"
        if kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}" || true
            sleep 1
            kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" || true
            echo "API stopped"
        fi
        rm -f "${PID_FILE}"
    else
        echo "API not running (no pid file)"
    fi
}

status_api() {
    if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
        echo "API running, pid=$(cat "${PID_FILE}")"
    else
        echo "API not running"
    fi
}

case "${ACTION}" in
    start)   start_api ;;
    stop)    stop_api ;;
    status)  status_api ;;
    restart) stop_api; start_api ;;
esac
