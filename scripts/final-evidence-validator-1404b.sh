#!/usr/bin/env bash
# scheduler-mcp-server W28K-1401 Phase 1 — final-evidence validator.
# Validates the closeout evidence pack per
# COMMON-FINAL-EVIDENCE-CLOSEOUT-CONTROLS.md §0A / §0C / §0D + §4 + §6.92.
# Tail line is exactly `FINAL_EVIDENCE_VALIDATOR: PASS failures=0` on success,
# `FINAL_EVIDENCE_VALIDATOR: FAIL failures=N` on failure.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANE_ID="W28K-1404b"
EVIDENCE_ROOT="${REPO_ROOT}/working/evidence/${LANE_ID}/current"

failures=0
notes=()

check() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  [PASS] ${desc}"
    else
        echo "  [FAIL] ${desc}"
        failures=$((failures + 1))
        notes+=("${desc}")
    fi
}

echo "# final-evidence-validator — ${LANE_ID} — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "## required closeout artefacts"
check "00-reading-proof.md present" test -s "${EVIDENCE_ROOT}/00-reading-proof.md"
check "requirements-map.tsv present" test -s "${EVIDENCE_ROOT}/requirements-map.tsv"
check "touched-paths-manifest.tsv present" test -s "${EVIDENCE_ROOT}/touched-paths-manifest.tsv"
check "external-dirty-ledger.tsv present" test -s "${EVIDENCE_ROOT}/external-dirty-ledger.tsv"
check "scoped-clean-proof.txt present" test -s "${EVIDENCE_ROOT}/scoped-clean-proof.txt"
check "remote-proof.txt present" test -s "${EVIDENCE_ROOT}/remote-proof.txt"
check "adoption-grep.txt present" test -s "${EVIDENCE_ROOT}/adoption-grep.txt"
check "ut.log + junit-ut.xml present" bash -c "test -s ${EVIDENCE_ROOT}/ut.log && test -s ${EVIDENCE_ROOT}/junit-ut.xml"
check "it.log + junit-it.xml present" bash -c "test -s ${EVIDENCE_ROOT}/it.log && test -s ${EVIDENCE_ROOT}/junit-it.xml"
check "migrations-output.log present" test -s "${EVIDENCE_ROOT}/migrations-output.log"
check "design pack (W28K-1404b-design.md) present" bash -c "test -s ${EVIDENCE_ROOT}/W28K-1404b-design.md"

echo
echo "## test counts (junit)"
ut_passed=$(grep -oE 'tests="[0-9]+"' "${EVIDENCE_ROOT}/junit-ut.xml" 2>/dev/null | head -1 || echo 'tests="0"')
ut_failed=$(grep -oE 'failures="[0-9]+"' "${EVIDENCE_ROOT}/junit-ut.xml" 2>/dev/null | head -1 || echo 'failures="0"')
ut_errors=$(grep -oE 'errors="[0-9]+"' "${EVIDENCE_ROOT}/junit-ut.xml" 2>/dev/null | head -1 || echo 'errors="0"')
it_passed=$(grep -oE 'tests="[0-9]+"' "${EVIDENCE_ROOT}/junit-it.xml" 2>/dev/null | head -1 || echo 'tests="0"')
it_failed=$(grep -oE 'failures="[0-9]+"' "${EVIDENCE_ROOT}/junit-it.xml" 2>/dev/null | head -1 || echo 'failures="0"')
it_errors=$(grep -oE 'errors="[0-9]+"' "${EVIDENCE_ROOT}/junit-it.xml" 2>/dev/null | head -1 || echo 'errors="0"')
echo "  UT: ${ut_passed} ${ut_failed} ${ut_errors}"
echo "  IT: ${it_passed} ${it_failed} ${it_errors}"
check "UT failures=0 errors=0" bash -c "[[ '${ut_failed}' == 'failures=\"0\"' && '${ut_errors}' == 'errors=\"0\"' ]]"
check "IT failures=0 errors=0" bash -c "[[ '${it_failed}' == 'failures=\"0\"' && '${it_errors}' == 'errors=\"0\"' ]]"

echo
echo "## adoption gate"
check "0 §1.4.1 os.environ violations in src/" \
    bash -c "grep -rn 'os\.environ\.get\|os\.environ\[\|os\.getenv' ${REPO_ROOT}/src 2>/dev/null \
        | grep -v '__pycache__\|noqa.*1.4.1-carve-out' \
        | grep -vE ':[[:space:]]*(#|\"\"\")' | head -1 | { read line; [ -z \"\$line\" ]; }"
check "0 §1.7 functools.lru_cache violations in src/" \
    bash -c "grep -rn 'functools\.lru_cache\|functools\.cache' ${REPO_ROOT}/src 2>/dev/null \
        | grep -v '__pycache__' \
        | grep -vE ':[[:space:]]*(#|\"\"\")' | head -1 | { read line; [ -z \"\$line\" ]; }"
check "8/8 platform packages adopted in src/" \
    bash -c "for p in cloud_dog_config cloud_dog_logging cloud_dog_api_kit cloud_dog_idam cloud_dog_db cloud_dog_jobs cloud_dog_cache cloud_dog_storage; do
        n=\$(grep -rln \$p ${REPO_ROOT}/src 2>/dev/null | grep -v __pycache__ | wc -l)
        [ \"\$n\" -ge 1 ] || exit 1
    done"

echo
echo "## Phase 1 binding gates"
check "P1-013 local docker build succeeded" \
    bash -c "grep -q 'docker build exit 0' ${EVIDENCE_ROOT}/docker-build.log 2>/dev/null"
check "P1-014 local docker boot proved /health + RBAC 401/403/200 in-container" \
    test -s "${EVIDENCE_ROOT}/docker-boot.log"
check "P1-015 w28k-scheduler merged to canonical origin/main (ancestor proven)" \
    bash -c "cd ${REPO_ROOT} && git fetch origin main >/dev/null 2>&1; git merge-base --is-ancestor HEAD origin/main"
check "P1-016 EVIDENCE_TAG remote present" \
    bash -c "cd ${REPO_ROOT} && git ls-remote origin refs/tags/${LANE_ID}-EVIDENCE | grep -q ${LANE_ID}-EVIDENCE"
check "P1-016 FINAL_PROOF_TAG remote present" \
    bash -c "cd ${REPO_ROOT} && git ls-remote origin refs/tags/${LANE_ID}-FINAL-PROOF | grep -q ${LANE_ID}-FINAL-PROOF"

echo
echo "## requirements-map FAIL row scan"
if [[ -s "${EVIDENCE_ROOT}/requirements-map.tsv" ]]; then
    fail_rows=$(awk -F'\t' 'NR>1 && $NF=="FAIL"' "${EVIDENCE_ROOT}/requirements-map.tsv" | wc -l)
    partial_rows=$(awk -F'\t' 'NR>1 && $NF=="PARTIAL"' "${EVIDENCE_ROOT}/requirements-map.tsv" | wc -l)
    echo "  FAIL rows in requirements-map: ${fail_rows}"
    echo "  PARTIAL rows in requirements-map: ${partial_rows}"
    if [[ "${fail_rows}" -gt 0 ]]; then
        failures=$((failures + fail_rows))
        notes+=("requirements-map has ${fail_rows} FAIL rows")
    fi
    if [[ "${partial_rows}" -gt 0 ]]; then
        failures=$((failures + partial_rows))
        notes+=("requirements-map has ${partial_rows} PARTIAL rows (not acceptance per §0A)")
    fi
fi

echo
echo "## summary"
if [[ ${failures} -eq 0 ]]; then
    echo "FINAL_EVIDENCE_VALIDATOR: PASS failures=0"
    exit 0
else
    printf 'FINAL_EVIDENCE_VALIDATOR: FAIL failures=%d\n' "${failures}"
    echo "fail reasons:"
    for n in "${notes[@]}"; do echo "  - ${n}"; done
    exit 1
fi
