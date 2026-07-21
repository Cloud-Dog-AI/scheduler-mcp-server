#!/usr/bin/env bash
# scheduler-mcp-server W28K-1406 — final-evidence validator (R1).
# AUDIT-ONLY lane validator: checks 13 brief-listed artefacts + §0A platform pack +
# 3 follow-on dispatch instructions + CHECKSUMS replay.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANE_ID="W28K-1406"
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

echo "# final-evidence-validator (R1) — ${LANE_ID} — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "## §0A platform-validator-compliant pack"
check "00-reading-proof.md present" test -s "${EVIDENCE_ROOT}/00-reading-proof.md"
check "requirements-map.tsv present" test -s "${EVIDENCE_ROOT}/requirements-map.tsv"
check "touched-paths-manifest.tsv present" test -s "${EVIDENCE_ROOT}/touched-paths-manifest.tsv"
check "external-dirty-ledger.tsv present" test -s "${EVIDENCE_ROOT}/external-dirty-ledger.tsv"
check "scoped-clean-proof.txt present" test -s "${EVIDENCE_ROOT}/scoped-clean-proof.txt"
check "remote-proof.txt present" test -s "${EVIDENCE_ROOT}/remote-proof.txt"
check "FINAL-TAG-VERIFICATION.txt present" test -s "${EVIDENCE_ROOT}/FINAL-TAG-VERIFICATION.txt"
check "CLOSE-GATE.md present" test -s "${EVIDENCE_ROOT}/CLOSE-GATE.md"
check "CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md present" test -s "${EVIDENCE_ROOT}/CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md"
check "CHECKSUMS.sha256 present" test -s "${EVIDENCE_ROOT}/CHECKSUMS.sha256"
check "CHECKSUMS.verify.txt present" test -s "${EVIDENCE_ROOT}/CHECKSUMS.verify.txt"
check "FINAL-RETURN.md present" test -s "${EVIDENCE_ROOT}/FINAL-RETURN.md"

echo
echo "## W28K-1406 brief-listed audit artefacts (13 required)"
check "source-repo-baseline.tsv present" test -s "${EVIDENCE_ROOT}/source-repo-baseline.tsv"
check "accepted-lane-baseline.tsv present" test -s "${EVIDENCE_ROOT}/accepted-lane-baseline.tsv"
check "completion-gap-register.tsv present" test -s "${EVIDENCE_ROOT}/completion-gap-register.tsv"
check "requirements-usecase-test-trace-coverage.tsv present" test -s "${EVIDENCE_ROOT}/requirements-usecase-test-trace-coverage.tsv"
check "api-mcp-a2a-webui-surface-map.tsv present" test -s "${EVIDENCE_ROOT}/api-mcp-a2a-webui-surface-map.tsv"
check "platform-integration-gap-map.tsv present" test -s "${EVIDENCE_ROOT}/platform-integration-gap-map.tsv"
check "deploy-accessibility-gap-map.tsv present" test -s "${EVIDENCE_ROOT}/deploy-accessibility-gap-map.tsv"
check "followon-lane-index.md present" test -s "${EVIDENCE_ROOT}/followon-lane-index.md"
check "assurance-plan.md present" test -s "${EVIDENCE_ROOT}/assurance-plan.md"

echo
echo "## Three follow-on dispatch instructions (W28K-1407/1408/1409)"
check "W28K-1407 dispatch file present" \
    bash -c "ls ${EVIDENCE_ROOT}/W28K-1407-*.md 2>/dev/null | head -1 | grep -q ''"
check "W28K-1408 dispatch file present" \
    bash -c "ls ${EVIDENCE_ROOT}/W28K-1408-*.md 2>/dev/null | head -1 | grep -q ''"
check "W28K-1409 dispatch file present" \
    bash -c "ls ${EVIDENCE_ROOT}/W28K-1409-*.md 2>/dev/null | head -1 | grep -q ''"
check "W28K-1407 dispatch has Scope section" \
    bash -c "grep -q '^## Scope' ${EVIDENCE_ROOT}/W28K-1407-*.md"
check "W28K-1407 dispatch has Out-of-scope section" \
    bash -c "grep -qiE '^## Out-of-scope' ${EVIDENCE_ROOT}/W28K-1407-*.md"
check "W28K-1407 dispatch has Final validator command section" \
    bash -c "grep -q '^## Final validator command' ${EVIDENCE_ROOT}/W28K-1407-*.md"
check "W28K-1408 dispatch has Scope section" \
    bash -c "grep -q '^## Scope' ${EVIDENCE_ROOT}/W28K-1408-*.md"
check "W28K-1408 dispatch has Out-of-scope section" \
    bash -c "grep -qiE '^## Out-of-scope' ${EVIDENCE_ROOT}/W28K-1408-*.md"
check "W28K-1408 dispatch has Final validator command section" \
    bash -c "grep -q '^## Final validator command' ${EVIDENCE_ROOT}/W28K-1408-*.md"
check "W28K-1409 dispatch has Scope section" \
    bash -c "grep -q '^## Scope' ${EVIDENCE_ROOT}/W28K-1409-*.md"
check "W28K-1409 dispatch has Out-of-scope section" \
    bash -c "grep -qiE '^## Out-of-scope' ${EVIDENCE_ROOT}/W28K-1409-*.md"
check "W28K-1409 dispatch has Final validator command section" \
    bash -c "grep -q '^## Final validator command' ${EVIDENCE_ROOT}/W28K-1409-*.md"

echo
echo "## §6.92 rule 2 — CHECKSUMS coverage of verdict-bearing files"
check "CHECKSUMS lists final-evidence-validator.txt" \
    bash -c "grep -F 'final-evidence-validator.txt' ${EVIDENCE_ROOT}/CHECKSUMS.sha256"
check "CHECKSUMS lists CLOSE-GATE.md" \
    bash -c "grep -F 'CLOSE-GATE.md' ${EVIDENCE_ROOT}/CHECKSUMS.sha256"
check "CHECKSUMS lists CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md" \
    bash -c "grep -F 'CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md' ${EVIDENCE_ROOT}/CHECKSUMS.sha256"
check "CHECKSUMS lists all 3 dispatch files" \
    bash -c "[ \"\$(grep -cE 'W28K-140[789]-.*\\.md' ${EVIDENCE_ROOT}/CHECKSUMS.sha256)\" -ge 3 ]"
check "CHECKSUMS.verify.txt records OK lines + no FAILED" \
    bash -c "grep -q ': OK' ${EVIDENCE_ROOT}/CHECKSUMS.verify.txt && ! grep -E ': FAILED|FAILED open|FAILED read' ${EVIDENCE_ROOT}/CHECKSUMS.verify.txt"

echo
echo "## release gate (tags + ancestor proof)"
check "EVIDENCE tag remote present" \
    bash -c "cd ${REPO_ROOT} && git ls-remote origin refs/tags/${LANE_ID}-EVIDENCE 2>/dev/null | grep -q ${LANE_ID}-EVIDENCE"
check "FINAL-PROOF tag remote present" \
    bash -c "cd ${REPO_ROOT} && git ls-remote origin refs/tags/${LANE_ID}-FINAL-PROOF 2>/dev/null | grep -q ${LANE_ID}-FINAL-PROOF"
check "w28k-scheduler/main ancestor relationship proven" \
    bash -c "cd ${REPO_ROOT} && git fetch origin main >/dev/null 2>&1; git merge-base --is-ancestor HEAD origin/main"

echo
echo "## requirements-map FAIL row scan"
if [[ -s "${EVIDENCE_ROOT}/requirements-map.tsv" ]]; then
    fail_rows=$(awk -F'\t' 'NR>1 && $NF=="FAIL"' "${EVIDENCE_ROOT}/requirements-map.tsv" | wc -l)
    partial_rows=$(awk -F'\t' 'NR>1 && $NF=="PARTIAL"' "${EVIDENCE_ROOT}/requirements-map.tsv" | wc -l)
    blocked_rows=$(awk -F'\t' 'NR>1 && $NF=="BLOCKED"' "${EVIDENCE_ROOT}/requirements-map.tsv" | wc -l)
    echo "  FAIL rows: ${fail_rows}; PARTIAL rows: ${partial_rows}; BLOCKED rows: ${blocked_rows}"
    if [[ "${fail_rows}" -gt 0 ]]; then failures=$((failures + fail_rows)); notes+=("requirements-map has ${fail_rows} FAIL rows"); fi
    if [[ "${partial_rows}" -gt 0 ]]; then failures=$((failures + partial_rows)); notes+=("requirements-map has ${partial_rows} PARTIAL rows"); fi
    if [[ "${blocked_rows}" -gt 0 ]]; then failures=$((failures + blocked_rows)); notes+=("requirements-map has ${blocked_rows} BLOCKED rows"); fi
fi

echo
echo "## touched-paths-manifest scope scan (audit-only lane)"
silent_external=$(awk -F'\t' 'NR>1 && $4!="yes"' "${EVIDENCE_ROOT}/touched-paths-manifest.tsv" | wc -l)
check "touched-paths-manifest created_or_modified_by_lane all yes" \
    bash -c "[ \"${silent_external}\" = '0' ]"

echo
echo "## audit-only lane scope check"
check "No source code edits (src/ clean against origin/main)" \
    bash -c "cd ${REPO_ROOT} && git diff --name-only origin/main -- src/ 2>/dev/null | { read line; [ -z \"\$line\" ]; }"

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
