#!/usr/bin/env bash
# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# Licensed under the Apache License, Version 2.0
#
# W28K-1427 — Build the monorepo scheduler-mcp app and sync its dist into
# scheduler-mcp-server/ui/dist with a checksum manifest.
#
# Usage:
#   bash scripts/sync-ui-dist.sh                       # build + sync
#   SCHEDULER_MCP_MONOREPO=/path bash scripts/...      # override monorepo root
#
# RULES §3.2: this script never reaches out to a remote daemon.
# AGENT-LESSONS §2.29: monorepo build → service ui/dist is the only canonical path.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MONOREPO="${SCHEDULER_MCP_MONOREPO:-../cloud-dog-ai-ui-monorepo}"
APP_DIR="${MONOREPO}/apps/scheduler-mcp"
DIST_SRC="${APP_DIR}/dist"
DIST_DST="${REPO_ROOT}/ui/dist"

if [[ ! -d "${APP_DIR}" ]]; then
  echo "ERROR: monorepo app directory not found: ${APP_DIR}" >&2
  exit 2
fi

echo "[sync-ui-dist] Building ${APP_DIR} ..."
(
  cd "${MONOREPO}"
  # Clean per-app stale shared-package copies per AGENT-LESSONS §6.29
  for pkg in ui shell auth config tokens api-client testing idam; do
    for app in apps/*/node_modules/@cloud-dog/${pkg}; do
      # W28K-1411: use `if` (not `[[ ]] && …`) so a false test does not leave the
      # subshell's last command non-zero and trip `set -e` before the build runs.
      if [[ -d "${app}" && ! -L "${app}" ]]; then
        rm -rf "${app}" && echo "  removed stale: ${app}"
      fi
    done
  done
)
# W28K-1411 root-cause fix: build with CWD = the app directory.
# Tailwind resolves its `content` globs relative to process.cwd(); the scheduler
# tailwind.config.ts uses CWD-relative globs (./index.html, ./src/**,
# ../../packages/{ui,shell,auth}/src/**). The prior invocation ran `vite build`
# from the MONOREPO ROOT (cd "${MONOREPO}"; vite build --config ... apps/scheduler-mcp),
# so those globs resolved to <monorepo>/src and <parent>/packages — matching NO
# source files. Tailwind then generated ONLY the base/preflight layer and PURGED
# every utility class, shipping a 5,829-byte base-only stylesheet, so the live
# scheduler WebUI rendered effectively unstyled (W28K-1411). Building from the app
# directory makes the globs resolve correctly and emits the full (~34 KB) utility
# CSS, matching every sibling app (expert-agent/file-mcp/db-mcp ~34-36 KB) and the
# canonical sibling build pattern (expert-agent build-ui-dist.sh runs
# `npm run build --workspace ...`, i.e. CWD = the app directory).
(
  cd "${APP_DIR}"
  "${MONOREPO}/node_modules/.bin/tsc" -p tsconfig.json --noEmit
  "${MONOREPO}/node_modules/.bin/vite" build
)

if [[ ! -d "${DIST_SRC}" || ! -f "${DIST_SRC}/index.html" ]]; then
  echo "ERROR: build did not produce ${DIST_SRC}/index.html" >&2
  exit 3
fi

echo "[sync-ui-dist] Syncing dist → ${DIST_DST} ..."
rm -rf "${DIST_DST}"
mkdir -p "$(dirname "${DIST_DST}")"
cp -r "${DIST_SRC}" "${DIST_DST}"

echo "[sync-ui-dist] Generating MANIFEST.sha256 ..."
(
  cd "${DIST_DST}"
  find . -type f \( -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.svg' \
                     -o -name '*.ico' -o -name '*.png' -o -name '*.json' \) -print0 \
    | sort -z \
    | xargs -0 sha256sum > MANIFEST.sha256
)

echo "[sync-ui-dist] Verifying replay from MANIFEST.sha256 ..."
(
  cd "${DIST_DST}"
  sha256sum -c MANIFEST.sha256 >/dev/null
)

echo "[sync-ui-dist] OK"
echo "  dist size:   $(du -sh "${DIST_DST}" | cut -f1)"
echo "  index.html:  $(sha256sum "${DIST_DST}/index.html" | cut -c1-12)"
echo "  manifest:    $(wc -l < "${DIST_DST}/MANIFEST.sha256") files tracked"
