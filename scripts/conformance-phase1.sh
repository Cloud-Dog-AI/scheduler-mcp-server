#!/usr/bin/env bash
# scheduler-mcp-server W28K-1401 Phase 1 conformance.
# Adoption greps + UT + IT + alembic upgrade/downgrade replay.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_ROOT="${REPO_ROOT}/working/evidence/W28K-1401/current"
mkdir -p "${EVIDENCE_ROOT}"

failures=0
fail_reasons=()

run_step() {
    local name="$1"; shift
    echo "=== ${name} ==="
    if "$@"; then
        echo "[PASS] ${name}"
        return 0
    fi
    echo "[FAIL] ${name}"
    failures=$((failures + 1))
    fail_reasons+=("${name}")
    return 1
}

# Return only "real-code" lines from a grep result: drop lines whose first
# non-whitespace character is '#' or starts a docstring fence.
filter_code_lines() {
    grep -vE ':[[:space:]]*#'    \
        | grep -vE ':[[:space:]]*"""' \
        | grep -vE "':[[:space:]]*'''"
}

adoption_check() {
    local out="${EVIDENCE_ROOT}/adoption-grep.txt"
    local hard_fail=0
    {
        date -u +"# scheduler-mcp-server adoption greps %Y-%m-%dT%H:%M:%SZ"
        echo
        echo "## RULES 1.4.1 os.environ os.getenv os.environ[ MUST be 0"
        env_hits=$(grep -rn -E 'os\.environ\.get|os\.environ\[|os\.getenv' "${REPO_ROOT}/src" 2>/dev/null \
            | grep -v '__pycache__' \
            | grep -v 'noqa.*1.4.1-carve-out' \
            | filter_code_lines || true)
        if [[ -z "${env_hits}" ]]; then
            echo "(no matches)"
        else
            echo "${env_hits}"
            hard_fail=1
        fi
        echo
        echo "## RULES 1.7 functools.lru_cache functools.cache MUST be 0"
        lru_hits=$(grep -rn -E 'functools\.lru_cache|functools\.cache' "${REPO_ROOT}/src" 2>/dev/null \
            | grep -v '__pycache__' \
            | filter_code_lines || true)
        if [[ -z "${lru_hits}" ]]; then
            echo "(no matches)"
        else
            echo "${lru_hits}"
            hard_fail=1
        fi
        echo
        echo "## custom Cache class MUST be 0 outside cloud_dog_cache"
        cache_hits=$(grep -rn -E '^class [A-Z][A-Za-z]+Cache' "${REPO_ROOT}/src" 2>/dev/null \
            | grep -v '__pycache__' \
            | grep -v 'cloud_dog_cache' || true)
        if [[ -z "${cache_hits}" ]]; then
            echo "(no matches)"
        else
            echo "${cache_hits}"
            hard_fail=1
        fi
        echo
        echo "## bespoke auth def verify_api_key role==admin MUST be 0"
        auth_hits=$(grep -rn -E 'def verify_api_key|role *== *"admin"' "${REPO_ROOT}/src" 2>/dev/null \
            | grep -v '__pycache__' \
            | grep -v 'cloud_dog_idam' \
            | filter_code_lines || true)
        if [[ -z "${auth_hits}" ]]; then
            echo "(no matches)"
        else
            echo "${auth_hits}"
            hard_fail=1
        fi
        echo
        echo "## stdlib logging in src MUST be 0 outside alembic env.py boilerplate"
        log_hits=$(grep -rn -E '^import logging|^from logging|logging\.getLogger|logging\.basicConfig' "${REPO_ROOT}/src" 2>/dev/null \
            | grep -v '__pycache__' \
            | grep -v 'cloud_dog_logging' \
            | grep -v 'db/migrations/env.py' \
            | filter_code_lines || true)
        if [[ -z "${log_hits}" ]]; then
            echo "(no matches)"
        else
            echo "${log_hits}"
            hard_fail=1
        fi
        echo
        echo "## platform package adoption each MUST have at least 1 src reference"
        missing_pkgs=""
        for pkg in cloud_dog_config cloud_dog_logging cloud_dog_api_kit cloud_dog_idam cloud_dog_db cloud_dog_jobs cloud_dog_cache cloud_dog_storage; do
            count=$(grep -rln "${pkg}" "${REPO_ROOT}/src" 2>/dev/null | grep -v __pycache__ | wc -l)
            echo "  ${pkg}: ${count} file(s)"
            if [[ "${count}" -eq 0 ]]; then
                missing_pkgs="${missing_pkgs} ${pkg}"
            fi
        done
        if [[ -n "${missing_pkgs}" ]]; then
            echo
            echo "FAIL missing platform-package adoption:${missing_pkgs}"
            hard_fail=1
        fi
    } > "${out}"
    cat "${out}"
    return ${hard_fail}
}

run_ut() {
    local log="${EVIDENCE_ROOT}/ut.log"
    cd "${REPO_ROOT}"
    CLOUD_DOG_ENV_FILES="${REPO_ROOT}/tests/env-UT" \
        python3 -m pytest tests/unit -v --tb=short \
            --junitxml="${EVIDENCE_ROOT}/junit-ut.xml" \
            --env tests/env-UT 2>&1 | tee "${log}"
    return ${PIPESTATUS[0]}
}

run_it() {
    local log="${EVIDENCE_ROOT}/it.log"
    cd "${REPO_ROOT}"
    CLOUD_DOG_ENV_FILES="${REPO_ROOT}/tests/env-IT" \
        python3 -m pytest tests/integration -v --tb=short \
            --junitxml="${EVIDENCE_ROOT}/junit-it.xml" \
            --env tests/env-IT 2>&1 | tee "${log}"
    return ${PIPESTATUS[0]}
}

run_migrations() {
    local log="${EVIDENCE_ROOT}/migrations-output.log"
    local db_path="${EVIDENCE_ROOT}/.conformance.db"
    cd "${REPO_ROOT}"
    rm -f "${db_path}"
    local URL="sqlite:///${db_path}"
    {
        echo "# alembic upgrade head URL=${URL}"
        CLOUD_DOG_ENV_FILES="${REPO_ROOT}/tests/env-IT" \
        CLOUD_DOG__DB__URL="${URL}" \
        alembic -c alembic.ini -x "sqlalchemy.url=${URL}" upgrade head
        echo "# alembic downgrade base"
        CLOUD_DOG_ENV_FILES="${REPO_ROOT}/tests/env-IT" \
        CLOUD_DOG__DB__URL="${URL}" \
        alembic -c alembic.ini -x "sqlalchemy.url=${URL}" downgrade base
        echo "# alembic upgrade head replay"
        CLOUD_DOG_ENV_FILES="${REPO_ROOT}/tests/env-IT" \
        CLOUD_DOG__DB__URL="${URL}" \
        alembic -c alembic.ini -x "sqlalchemy.url=${URL}" upgrade head
    } 2>&1 | tee "${log}"
    local rc=${PIPESTATUS[0]}
    rm -f "${db_path}"
    return $rc
}

run_step adoption_check adoption_check
run_step unit_tests run_ut
run_step integration_tests run_it
run_step migrations_replay run_migrations

echo
if [[ ${failures} -eq 0 ]]; then
    echo "CONFORMANCE_PHASE1: PASS failures=0"
    exit 0
fi
echo "CONFORMANCE_PHASE1: FAIL failures=${failures} (${fail_reasons[*]})"
exit 1
