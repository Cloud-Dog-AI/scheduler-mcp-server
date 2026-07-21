#!/usr/bin/env bash
# scheduler-mcp-server W28K-1407 — final-evidence validator (implementation lane).
# Checks the §0A pack + the W28K-1407 implementation artefacts (UT/IT/AT junit,
# docker build/boot, migration replay, metrics) + CHECKSUMS coverage + tags +
# requirements-map PASS coverage. Run from a CLEAN CLONE for the authoritative result.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANE_ID="W28K-1407"
EV="${REPO_ROOT}/working/evidence/${LANE_ID}/current"

failures=0
notes=()
check() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then echo "  [PASS] ${desc}"; else
        echo "  [FAIL] ${desc}"; failures=$((failures + 1)); notes+=("${desc}"); fi
}

# junit assertion: failures=0 errors=0 skipped=0 AND tests>=min
junit_clean() {
    local f="$1"; local min="$2"
    [[ -s "$f" ]] || return 1
    local tests fails errs skips
    tests=$(grep -oE 'tests="[0-9]+"' "$f" | head -1 | grep -oE '[0-9]+')
    fails=$(grep -oE 'failures="[0-9]+"' "$f" | head -1 | grep -oE '[0-9]+')
    errs=$(grep -oE 'errors="[0-9]+"' "$f" | head -1 | grep -oE '[0-9]+')
    skips=$(grep -oE 'skipped="[0-9]+"' "$f" | head -1 | grep -oE '[0-9]+')
    [[ "${fails:-1}" == "0" && "${errs:-1}" == "0" && "${skips:-1}" == "0" && "${tests:-0}" -ge "$min" ]]
}

echo "# final-evidence-validator-1407 — ${LANE_ID} — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
echo "## §0A platform-validator-compliant pack"
for f in 00-reading-proof.md requirements-map.tsv touched-paths-manifest.tsv \
         external-dirty-ledger.tsv scoped-clean-proof.txt remote-proof.txt \
         FINAL-TAG-VERIFICATION.txt CLOSE-GATE.md CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md \
         CHECKSUMS.sha256 CHECKSUMS.verify.txt final-evidence-validator.txt FINAL-RETURN.md; do
    check "${f} present" test -s "${EV}/${f}"
done

echo
echo "## W28K-1407 implementation artefacts"
for f in requirements-traceability.tsv junit-ut.xml ut.log junit-it.xml it.log \
         junit-at.xml at.log docker-build.log docker-boot.log \
         idam-sql-migration-replay.log metrics-probe.log; do
    check "${f} present" test -s "${EV}/${f}"
done
check "docs/W28K-1407-DESIGN.md present" test -s "${REPO_ROOT}/docs/W28K-1407-DESIGN.md"

echo
echo "## test tier results (zero fail/error/skip; UT>=120 IT>=70 AT>=18)"
check "junit-ut.xml clean (>=120)" junit_clean "${EV}/junit-ut.xml" 120
check "junit-it.xml clean (>=70)"  junit_clean "${EV}/junit-it.xml" 70
check "junit-at.xml clean (==18)"  junit_clean "${EV}/junit-at.xml" 18
check "docker-build.log exit 0"    bash -c "grep -q 'Build OK' '${EV}/docker-build.log'"
check "migration replay OK"        bash -c "grep -q 'REPLAY_OK: idam tables present = True' '${EV}/idam-sql-migration-replay.log'"
check "metrics probe has 3 custom counters" \
    bash -c "grep -q '^schedule_runs_total ' '${EV}/metrics-probe.log' && grep -q '^schedule_runs_failed ' '${EV}/metrics-probe.log' && grep -q '^chain_compile_errors_total ' '${EV}/metrics-probe.log'"

echo
echo "## CHECKSUMS coverage (§6.92) + replay"
check "CHECKSUMS lists final-evidence-validator.txt" bash -c "grep -F 'final-evidence-validator.txt' '${EV}/CHECKSUMS.sha256'"
check "CHECKSUMS lists CLOSE-GATE.md"                bash -c "grep -F 'CLOSE-GATE.md' '${EV}/CHECKSUMS.sha256'"
check "CHECKSUMS lists CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md" bash -c "grep -F 'CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md' '${EV}/CHECKSUMS.sha256'"
check "CHECKSUMS.verify.txt OK + no FAILED" \
    bash -c "grep -q ': OK' '${EV}/CHECKSUMS.verify.txt' && ! grep -E ': FAILED|FAILED open|FAILED read' '${EV}/CHECKSUMS.verify.txt'"

echo
echo "## release gate (tags + ancestor) — INFORMATIONAL here; the PLATFORM validator"
echo "## (cloud-dog-ai-platform-standards/scripts/final-evidence-validator.sh) is the"
echo "## authoritative tag/ancestry checker run live from the clean clone."
if git -C "${REPO_ROOT}" ls-remote origin refs/tags/${LANE_ID}-EVIDENCE 2>/dev/null | grep -q ${LANE_ID}-EVIDENCE; then
    echo "  [INFO] EVIDENCE tag present on remote"; else echo "  [INFO] EVIDENCE tag not yet on remote (pre-push generation)"; fi
if git -C "${REPO_ROOT}" ls-remote origin refs/tags/${LANE_ID}-FINAL-PROOF 2>/dev/null | grep -q ${LANE_ID}-FINAL-PROOF; then
    echo "  [INFO] FINAL-PROOF tag present on remote"; else echo "  [INFO] FINAL-PROOF tag not yet on remote (pre-push generation)"; fi

echo
echo "## manifest + requirements-map scope scans"
silent_external=$(awk -F'\t' 'NR>1 && $4!="yes"' "${EV}/touched-paths-manifest.tsv" | wc -l)
check "touched-paths-manifest created_or_modified_by_lane all yes" bash -c "[ '${silent_external}' = '0' ]"
if [[ -s "${EV}/requirements-map.tsv" ]]; then
    nonpass=$(awk -F'\t' 'NR>1 && $NF!="PASS"' "${EV}/requirements-map.tsv" | wc -l)
    echo "  requirements-map non-PASS rows: ${nonpass}"
    if [[ "${nonpass}" -gt 0 ]]; then failures=$((failures + nonpass)); notes+=("requirements-map has ${nonpass} non-PASS rows"); fi
fi

echo
echo "## summary"
if [[ ${failures} -eq 0 ]]; then
    echo "FINAL_EVIDENCE_VALIDATOR: PASS failures=0"; exit 0
else
    printf 'FINAL_EVIDENCE_VALIDATOR: FAIL failures=%d\n' "${failures}"
    for n in "${notes[@]}"; do echo "  - ${n}"; done
    exit 1
fi
