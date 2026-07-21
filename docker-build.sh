#!/usr/bin/env bash
# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# Licensed under the Apache License, Version 2.0
#
# scheduler-mcp-server — Docker Build Script (PS-91 / PS-97 v1.1 §1.1.3)
# Canonical pattern copied verbatim from file-mcp-server/docker-build.sh
# (the reference) per AGENT-LESSONS §4.7 + §6.21 + RULES §3.2.
# Only CONTAINER name changed.
#
# Uses BuildKit secret mount for PyPI auth — credentials never enter image layers.
# Uses --network=host so the build container inherits daemon-host DNS/routing
# (the missing piece from the prior W28K-1401 PARTIAL: build sandbox had no DNS).
#
# Variant selector (PS-97 v1.1 §1.1.3):
#   --variant public  (default) builds Dockerfile.public for publication.
#   --variant dev     builds the internal Dockerfile (Gitea/internal package
#                      index default) for developer/preprod checkouts.
#
# Usage:
#   bash docker-build.sh [VERSION] [--variant dev|public]
set -euo pipefail

# ── Argument parsing ────────────────────────────────────────────
VARIANT="${PUBLICATION_BUILD_VARIANT:-public}"
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --variant)
      VARIANT="${2:-dev}"
      shift 2
      ;;
    --variant=*)
      VARIANT="${1#*=}"
      shift
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done
set -- "${POSITIONAL[@]}"

case "${VARIANT}" in
  dev)
    DOCKERFILE="Dockerfile"
    ;;
  public)
    DOCKERFILE="Dockerfile.public"
    ;;
  *)
    echo "ERROR: --variant must be 'dev' or 'public' (got: ${VARIANT})" >&2
    exit 2
    ;;
esac
if [[ ! -f "${DOCKERFILE}" ]]; then
  echo "ERROR: ${DOCKERFILE} not found (variant=${VARIANT})" >&2
  exit 2
fi

VERSION="${1:-latest}"
CONTAINER="scheduler-mcp-server"
FOLDER="cloud-dog"
REGISTRY="${REGISTRY:-}"
PIP_CONF=".pip.conf.build"
CA_BUNDLE_FILE=".ca-bundle.build"
cleanup_build_secrets() {
  rm -f "${PIP_CONF}" "${CA_BUNDLE_FILE}"
}
trap cleanup_build_secrets EXIT

PUBLICATION_TAG_SUFFIX="${PUBLICATION_TAG_SUFFIX:-}"
if [[ -n "${PUBLICATION_TAG_SUFFIX}" ]]; then
  if [[ ! "${PUBLICATION_TAG_SUFFIX}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
    echo "ERROR: PUBLICATION_TAG_SUFFIX must match ^[a-z0-9]([a-z0-9-]*[a-z0-9])?\$ (got: '${PUBLICATION_TAG_SUFFIX}')" >&2
    exit 2
  fi
  case "${PUBLICATION_TAG_SUFFIX}" in
    latest|dev|prod|release|stable)
      echo "ERROR: PUBLICATION_TAG_SUFFIX '${PUBLICATION_TAG_SUFFIX}' is reserved" >&2
      exit 2
      ;;
  esac
  EFFECTIVE_TAG="${VERSION}-${PUBLICATION_TAG_SUFFIX}"
  echo "Publication test build: tag suffix '-${PUBLICATION_TAG_SUFFIX}' (registry tag will be skipped)."
else
  EFFECTIVE_TAG="${VERSION}"
fi

CUSTOM_CA_CERT="${CUSTOM_CA_CERT:-}"
CORPORATE_CA_CERT="${CORPORATE_CA_CERT:-/usr/local/share/ca-certificates/cloud-dog.net.ca.crt}"

echo "=========================================="
echo "Docker Build: ${FOLDER}/${CONTAINER}:${EFFECTIVE_TAG} (variant=${VARIANT}, dockerfile=${DOCKERFILE})"
echo "=========================================="

# ── PyPI Configuration ───────────────────────────────────────────
if [[ -n "${PYPI_URL:-}" ]]; then
  : # honour caller override
elif [[ "${VARIANT}" == "public" ]]; then
  PYPI_URL="https://pypi.org/simple"
else
  # W28R-3019 Cloud-Dog-only boundary: dev builds resolve packages from the
  # internal Cloud-Dog PyPI (Vault-backed dev.repository.pypi), never a Gitea
  # package index (RULES §1.7 / §3.2.0; AGENT-LESSONS §6.129 / §6.147). The
  # internal index host is supplied by the build environment via the PYPI_URL
  # env/ARG and is NOT committed here (public-source leakage gate); absent an
  # override this falls back to the public index.
  PYPI_URL="${PYPI_URL:-https://pypi.org/simple/}"
fi
PYPI_USERNAME="${PYPI_USERNAME:-}"
PYPI_PASSWORD="${PYPI_PASSWORD:-}"

# Only the BuildKit secret may contain URL userinfo. Build arguments and image
# history receive a credential-free equivalent even when PYPI_URL itself embeds
# credentials supplied by the sanctioned package-index configuration.
readarray -t _PYPI_SAFE_PARTS < <(python3 - "${PYPI_URL}" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

parsed = urlsplit(sys.argv[1])
host = parsed.hostname or "pypi.org"
netloc = f"[{host}]" if ":" in host else host
if parsed.port is not None:
    netloc = f"{netloc}:{parsed.port}"
print(host)
print(urlunsplit((parsed.scheme or "https", netloc, parsed.path, parsed.query, parsed.fragment)))
PY
)
PYPI_HOST="${_PYPI_SAFE_PARTS[0]}"
PYPI_SAFE_URL="${_PYPI_SAFE_PARTS[1]}"

if [[ "${VARIANT}" == "public" ]]; then
  if [[ -n "${PYPI_USERNAME}" ]] && [[ -n "${PYPI_PASSWORD}" ]]; then
    cat > "${PIP_CONF}" << EOF
[global]
index-url = https://${PYPI_USERNAME}:${PYPI_PASSWORD}@${PYPI_URL#https://}
trusted-host = ${PYPI_HOST}
EOF
    echo "pip.conf: public variant, authenticated single-index access (${PYPI_HOST})."
  else
    cat > "${PIP_CONF}" << EOF
[global]
index-url = ${PYPI_URL}
trusted-host = ${PYPI_HOST}
EOF
    echo "pip.conf: public variant, anonymous single-index access (${PYPI_HOST})."
  fi
else
  if [[ -n "${PYPI_USERNAME}" ]] && [[ -n "${PYPI_PASSWORD}" ]]; then
    cat > "${PIP_CONF}" << EOF
[global]
index-url = https://${PYPI_USERNAME}:${PYPI_PASSWORD}@${PYPI_URL#https://}
trusted-host = ${PYPI_HOST}
EOF
    echo "pip.conf: dev variant, authenticated Cloud-Dog-only index (${PYPI_HOST})."
  else
    cat > "${PIP_CONF}" << EOF
[global]
index-url = ${PYPI_URL}
trusted-host = ${PYPI_HOST}
EOF
    echo "pip.conf: dev variant, anonymous mirror access (${PYPI_HOST})."
  fi
fi
chmod 600 "${PIP_CONF}"

# ── CA Certificate ───────────────────────────────────────────────
rm -f "${CA_BUNDLE_FILE}"
touch "${CA_BUNDLE_FILE}"
for cert in "${CUSTOM_CA_CERT}" "${CORPORATE_CA_CERT}"; do
  if [[ -n "${cert}" && -f "${cert}" ]]; then
    cat "${cert}" >> "${CA_BUNDLE_FILE}"
    echo "" >> "${CA_BUNDLE_FILE}"
  fi
done
chmod 600 "${CA_BUNDLE_FILE}"

# ── Build ────────────────────────────────────────────────────────
# ── W28C-1719 publish-before-pin guard + build-provenance revision label (fail-closed) ──
_PBP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# W28A-SEC-R18 public payload: the W28C-1719 publish-before-pin guard is INTERNAL CI
# build-guard tooling (it resolves the internal cloud-dog-* pins against the internal
# package index) and is intentionally excluded from the public mirror. A public
# external-actor build resolves every dependency from the single public index, so run
# the guard only when it is present; its absence must not fail the public build.
if [[ -x "${_PBP_DIR}/scripts/publish-before-pin-guard.sh" ]]; then
  "${_PBP_DIR}/scripts/publish-before-pin-guard.sh" "${_PBP_DIR}" || exit $?
fi
_PBP_REV="$(git -C "${_PBP_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
# W28E-1863 fix-wave-c (WSC-014): propagate build identity to the image so the
# Dockerfile can stamp OCI labels + runtime ENV for _build_identity(). SOURCE_COMMIT
# reuses _PBP_REV so the runtime /version source_commit == the OCI revision label.
SOURCE_COMMIT="${_PBP_REV}"
SOURCE_BRANCH="$(git -C "${_PBP_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Use the fully qualified internal tag as the BuildKit output whenever REGISTRY
# is configured. This prevents local-name normalization from expanding the
# primary image to docker.io in build provenance or diagnostics.
if [[ -n "${REGISTRY}" && -z "${PUBLICATION_TAG_SUFFIX}" ]]; then
  BUILDX_PRIMARY_TAG="${REGISTRY}/${FOLDER}/${CONTAINER}:${EFFECTIVE_TAG}"
else
  BUILDX_PRIMARY_TAG="${FOLDER}/${CONTAINER}:${EFFECTIVE_TAG}"
fi

DOCKER_BUILDKIT=1 docker buildx build \
  --label "org.opencontainers.image.revision=${_PBP_REV}" \
  --progress=plain \
  --network=host \
  --load \
  -f "${DOCKERFILE}" \
  --secret id=pip_conf,src="${PIP_CONF}" \
  --secret id=ca_bundle,src="${CA_BUNDLE_FILE}" \
  --build-arg PYPI_INDEX_URL="${PYPI_SAFE_URL}" \
  --build-arg PYPI_URL="${PYPI_SAFE_URL}" \
  --build-arg HTTP_PROXY="${HTTP_PROXY:-}" \
  --build-arg HTTPS_PROXY="${HTTPS_PROXY:-}" \
  --build-arg NO_PROXY="${NO_PROXY:-}" \
  --build-arg http_proxy="${http_proxy:-}" \
  --build-arg https_proxy="${https_proxy:-}" \
  --build-arg no_proxy="${no_proxy:-}" \
  --build-arg SOURCE_COMMIT="${SOURCE_COMMIT}" \
  --build-arg SOURCE_BRANCH="${SOURCE_BRANCH}" \
  --build-arg BUILD_DATE="${BUILD_DATE}" \
  -t "${BUILDX_PRIMARY_TAG}" \
  . 2>&1 | tee docker-build.log

BUILD_STATUS=${PIPESTATUS[0]}

if [[ ${BUILD_STATUS} -eq 0 ]]; then
  echo "Build OK: ${BUILDX_PRIMARY_TAG} (variant=${VARIANT})"
  if [[ "${VARIANT}" == "dev" && -n "${REGISTRY}" && -z "${PUBLICATION_TAG_SUFFIX}" ]]; then
    docker tag "${BUILDX_PRIMARY_TAG}" "${FOLDER}/${CONTAINER}:${EFFECTIVE_TAG}"
    echo "Tagged: ${REGISTRY}/${FOLDER}/${CONTAINER}:${EFFECTIVE_TAG}"
  elif [[ -n "${PUBLICATION_TAG_SUFFIX}" ]]; then
    echo "Registry tag skipped for publication suffix '${PUBLICATION_TAG_SUFFIX}'."
  else
    echo "Registry tag skipped (public variant or no REGISTRY set; PS-97 §1.1.3 closed-loop)."
  fi
else
  echo "Build FAILED — see docker-build.log"
fi

exit ${BUILD_STATUS}
