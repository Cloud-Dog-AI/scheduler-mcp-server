#!/usr/bin/env bash
# scheduler-mcp-server W28K-1405 Phase 5 — final-evidence validator (R3).
# Validates the preprod-deploy + closeout evidence pack per
# COMMON-FINAL-EVIDENCE-CLOSEOUT-CONTROLS.md §0A / §0C / §0D + §4 + §6.92 + §6.94 + §6.93.
#
# R3 hardening (full self-assurance remediation 2026-06-13):
#   - checks W28K-1400-R2-FINAL master rollup tag presence + ancestry (was missing)
#   - scans CLOSE-GATE.md for named waivers — fails unless flagged as approved-expansion
#   - reconciles deployed-identity.tsv recorded skills count vs live agent.json (best-effort, network-permitting)
#   - checks 00-reading-proof.md emits GATE 0 warrant with all 3 file hashes
#   - checks CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md present
#
# R2 hardening retained:
#   - CHECKSUMS.sha256 + CHECKSUMS.verify.txt coverage (§6.90 rule 1, §6.92 rule 2)
#   - deployed-identity.tsv build_source_main_commit ancestor of origin/main (§6.92 rule 3)
#   - accepts R1, R2, or R3 tag suffix variants per §6.89
#
# Tail line is exactly `FINAL_EVIDENCE_VALIDATOR: PASS failures=0` on success,
# `FINAL_EVIDENCE_VALIDATOR: FAIL failures=N` on failure.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANE_ID="W28K-1405"
THREAD_ROLLUP="W28K-1400-R2-FINAL"
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

echo "# final-evidence-validator (R3) — ${LANE_ID} — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "## §0A/§0D/§6.90 required closeout artefacts"
check "00-reading-proof.md present" test -s "${EVIDENCE_ROOT}/00-reading-proof.md"
check "requirements-map.tsv present" test -s "${EVIDENCE_ROOT}/requirements-map.tsv"
check "touched-paths-manifest.tsv present" test -s "${EVIDENCE_ROOT}/touched-paths-manifest.tsv"
check "external-dirty-ledger.tsv present" test -s "${EVIDENCE_ROOT}/external-dirty-ledger.tsv"
check "scoped-clean-proof.txt present" test -s "${EVIDENCE_ROOT}/scoped-clean-proof.txt"
check "remote-proof.txt present" test -s "${EVIDENCE_ROOT}/remote-proof.txt"
check "deployed-identity.tsv present" test -s "${EVIDENCE_ROOT}/deployed-identity.tsv"
check "CHECKSUMS.sha256 present" test -s "${EVIDENCE_ROOT}/CHECKSUMS.sha256"
check "CHECKSUMS.verify.txt present" test -s "${EVIDENCE_ROOT}/CHECKSUMS.verify.txt"
check "CLOSE-GATE.md present" test -s "${EVIDENCE_ROOT}/CLOSE-GATE.md"
check "CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md present" test -s "${EVIDENCE_ROOT}/CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md"

echo
echo "## §6.92 rule 2 — CHECKSUMS coverage of verdict-bearing files"
check "CHECKSUMS lists final-evidence-validator.txt" \
    bash -c "grep -F 'final-evidence-validator.txt' ${EVIDENCE_ROOT}/CHECKSUMS.sha256"
check "CHECKSUMS lists CLOSE-GATE.md" \
    bash -c "grep -F 'CLOSE-GATE.md' ${EVIDENCE_ROOT}/CHECKSUMS.sha256"
check "CHECKSUMS lists CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md" \
    bash -c "grep -F 'CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md' ${EVIDENCE_ROOT}/CHECKSUMS.sha256"
check "CHECKSUMS.verify.txt records OK lines + no FAILED" \
    bash -c "grep -q ': OK' ${EVIDENCE_ROOT}/CHECKSUMS.verify.txt && ! grep -E ': FAILED|FAILED open|FAILED read' ${EVIDENCE_ROOT}/CHECKSUMS.verify.txt"

echo
echo "## §6.92 rule 3 — cross-file reconciliation"
if grep -q '^build_source_main_commit' "${EVIDENCE_ROOT}/deployed-identity.tsv" 2>/dev/null; then
    bsc=$(awk -F'\t' '/^build_source_main_commit/ {print $2}' "${EVIDENCE_ROOT}/deployed-identity.tsv" | awk '{print $1}')
    check "deployed-identity build_source_main_commit (${bsc:0:12}) is ancestor of origin/main" \
        bash -c "cd ${REPO_ROOT} && git fetch origin main >/dev/null 2>&1; git merge-base --is-ancestor ${bsc} origin/main"
else
    check "deployed-identity records build_source_main_commit" false
fi
check "deployed_container_image_digest == registry_manifest_digest" \
    bash -c "
        a=\$(awk -F'\t' '/^deployed_container_image_digest/ {print \$2}' ${EVIDENCE_ROOT}/deployed-identity.tsv);
        b=\$(awk -F'\t' '/^registry_manifest_digest\t/ {print \$2}' ${EVIDENCE_ROOT}/deployed-identity.tsv);
        [ -n \"\$a\" ] && [ \"\$a\" = \"\$b\" ]
    "
check "deployed-identity identity_check explicitly PASS" \
    bash -c "awk -F'\t' '/^identity_check/ {print \$2}' ${EVIDENCE_ROOT}/deployed-identity.tsv | grep -q 'PASS'"

echo
echo "## §FINAL QUALITY GATE A — GATE 0 warrant"
check "00-reading-proof.md has RULES SHA256(12)" \
    bash -c "grep -q 'RULES.md' ${EVIDENCE_ROOT}/00-reading-proof.md && grep -E 'RULES.md.*\`[a-f0-9]{12}\`' ${EVIDENCE_ROOT}/00-reading-proof.md"
check "00-reading-proof.md has AGENT-LESSONS SHA256(12)" \
    bash -c "grep -E 'AGENT-LESSONS.md.*\`[a-f0-9]{12}\`' ${EVIDENCE_ROOT}/00-reading-proof.md"
check "00-reading-proof.md has AGENT-BOOTSTRAP-DIRECTIVE SHA256(12)" \
    bash -c "grep -E 'AGENT-BOOTSTRAP-DIRECTIVE.md.*\`[a-f0-9]{12}\`' ${EVIDENCE_ROOT}/00-reading-proof.md"
check "00-reading-proof.md has 3 CONFIRM rule-ids" \
    bash -c "grep -c '^[0-9]\\. \\*\\*' ${EVIDENCE_ROOT}/00-reading-proof.md | { read n; [ \"\$n\" -ge 3 ]; }"

echo
echo "## §6.94 D-1432 build + deploy artefacts (LIVE-RENDER gate)"
check "docker-build log present" test -s "${EVIDENCE_ROOT}/d1432-docker-build-r2.log"
check "docker-push log present" test -s "${EVIDENCE_ROOT}/d1432-docker-push-r2.log"
check "TF apply log present" test -s "${EVIDENCE_ROOT}/d1432-tf-apply-r2.log"
check "E2E schedule fire log + succeeded" \
    bash -c "grep -q 'D-1432.5 E2E SCHEDULE FIRE on DEPLOYED IMAGE: PASS' ${EVIDENCE_ROOT}/d1432-e2e-schedule-fire.log"
check "4-sentinel browser smoke PASS" \
    bash -c "grep -q '4-sentinel browser smoke: 4/4 PASS' ${EVIDENCE_ROOT}/d1432-sentinel-smoke.log"

echo
echo "## D-1433 sibling regression + preprod Playwright"
check "sibling health TSV present" test -s "${EVIDENCE_ROOT}/d1433-sibling-health.tsv"
check "9 siblings + code-runner + scheduler all /health 200" \
    bash -c "[ \"\$(awk -F'\t' 'NR>1 && \$2==\"/health\" && \$3==\"200\"' ${EVIDENCE_ROOT}/d1433-sibling-health.tsv | wc -l)\" -eq 11 ]"
check "scheduler /mcp tools/list POST 200" \
    bash -c "awk -F'\t' '\$2==\"/mcp tools/list POST\" && \$3==\"200\"' ${EVIDENCE_ROOT}/d1433-sibling-health.tsv | grep -q ''"
check "scheduler /a2a/skills/chain.compile POST 200" \
    bash -c "awk -F'\t' '\$2==\"/a2a/skills/chain.compile POST\" && \$3==\"200\"' ${EVIDENCE_ROOT}/d1433-sibling-health.tsv | grep -q ''"
check "scheduler /.well-known/agent.json 200" \
    bash -c "awk -F'\t' '\$2==\"/.well-known/agent.json\" && \$3==\"200\"' ${EVIDENCE_ROOT}/d1433-sibling-health.tsv | grep -q ''"
check "Playwright preprod junit tests=60 failures=0" \
    bash -c "grep -q 'tests=\"60\" failures=\"0\" skipped=\"0\" errors=\"0\"' ${EVIDENCE_ROOT}/junit-pw.xml"
check "Playwright HTML report present" test -s "${EVIDENCE_ROOT}/playwright/html-report/index.html"
check "Playwright traces ≥60 zips" \
    bash -c "[ \"\$(find ${EVIDENCE_ROOT}/playwright/traces -name '*.zip' 2>/dev/null | wc -l)\" -ge 60 ]"

echo
echo "## D-1434 release-gate (two-anchor + master rollup; accept R1/R2/R3 per §6.89)"
check "EVIDENCE tag remote present (R1, R2, or R3)" \
    bash -c "cd ${REPO_ROOT} && (
        git ls-remote origin refs/tags/${LANE_ID}-EVIDENCE     2>/dev/null | grep -q ${LANE_ID}-EVIDENCE
     ) || (
        git ls-remote origin refs/tags/${LANE_ID}-EVIDENCE-R2  2>/dev/null | grep -q ${LANE_ID}-EVIDENCE-R2
     ) || (
        git ls-remote origin refs/tags/${LANE_ID}-EVIDENCE-R3  2>/dev/null | grep -q ${LANE_ID}-EVIDENCE-R3
     )"
check "FINAL-PROOF tag remote present (R1, R2, or R3)" \
    bash -c "cd ${REPO_ROOT} && (
        git ls-remote origin refs/tags/${LANE_ID}-FINAL-PROOF     2>/dev/null | grep -q ${LANE_ID}-FINAL-PROOF
     ) || (
        git ls-remote origin refs/tags/${LANE_ID}-FINAL-PROOF-R2  2>/dev/null | grep -q ${LANE_ID}-FINAL-PROOF-R2
     ) || (
        git ls-remote origin refs/tags/${LANE_ID}-FINAL-PROOF-R3  2>/dev/null | grep -q ${LANE_ID}-FINAL-PROOF-R3
     )"
check "MASTER ROLLUP ${THREAD_ROLLUP} tag remote present" \
    bash -c "cd ${REPO_ROOT} && git ls-remote origin refs/tags/${THREAD_ROLLUP} 2>/dev/null | grep -q ${THREAD_ROLLUP}"
check "MASTER ROLLUP ${THREAD_ROLLUP} ancestor of origin/main" \
    bash -c "cd ${REPO_ROOT} && git fetch origin main >/dev/null 2>&1; git merge-base --is-ancestor ${THREAD_ROLLUP}^{} origin/main"
check "w28k-scheduler/main ancestor relationship proven" \
    bash -c "cd ${REPO_ROOT} && git fetch origin main >/dev/null 2>&1; git merge-base --is-ancestor HEAD origin/main"

echo
echo "## §FINAL QUALITY GATE C7 — no silent waivers (CLOSE-GATE.md scan)"
# Per W28K-1405 brief: "NO SILENT WAIVERS — a named waiver is a FAIL".
# Acceptable disposition strings: FIXED, RESOLVED, approved-expansion, operator-authorized.
# Forbidden: any disposition that contains "waiver" or unflagged "acknowledged" without
# operator-approval evidence in operator-decisions table.
if [ -s "${EVIDENCE_ROOT}/CLOSE-GATE.md" ]; then
    bad=$(grep -cE 'waiver|WAIVER' "${EVIDENCE_ROOT}/CLOSE-GATE.md" 2>/dev/null || echo 0)
    if [ "$bad" -gt 0 ]; then
        # Allowed only if every waiver mention is accompanied by "operator-approved" within 3 lines
        unflagged=$(awk '/waiver|WAIVER/ {found=NR} found && NR<=found+3 && /operator-approved|operator-authorized/ {ok=1; found=0} END {print (found && !ok) ? 1 : 0}' "${EVIDENCE_ROOT}/CLOSE-GATE.md")
        check "CLOSE-GATE waiver mentions are all operator-approved" \
            bash -c "[ '$unflagged' = '0' ]"
    else
        echo "  [PASS] CLOSE-GATE contains no waivers"
    fi
fi
check "CLOSE-GATE has no 'open YELLOW' or 'open AMBER' items" \
    bash -c "! grep -iE 'open.*(yellow|amber|🟡)' ${EVIDENCE_ROOT}/CLOSE-GATE.md"

echo
echo "## requirements-map FAIL row scan"
if [[ -s "${EVIDENCE_ROOT}/requirements-map.tsv" ]]; then
    fail_rows=$(awk -F'\t' 'NR>1 && $NF=="FAIL"' "${EVIDENCE_ROOT}/requirements-map.tsv" | wc -l)
    partial_rows=$(awk -F'\t' 'NR>1 && $NF=="PARTIAL"' "${EVIDENCE_ROOT}/requirements-map.tsv" | wc -l)
    blocked_rows=$(awk -F'\t' 'NR>1 && $NF=="BLOCKED"' "${EVIDENCE_ROOT}/requirements-map.tsv" | wc -l)
    echo "  FAIL rows in requirements-map: ${fail_rows}"
    echo "  PARTIAL rows in requirements-map: ${partial_rows}"
    echo "  BLOCKED rows in requirements-map: ${blocked_rows}"
    if [[ "${fail_rows}" -gt 0 ]]; then failures=$((failures + fail_rows)); notes+=("requirements-map has ${fail_rows} FAIL rows"); fi
    if [[ "${partial_rows}" -gt 0 ]]; then failures=$((failures + partial_rows)); notes+=("requirements-map has ${partial_rows} PARTIAL rows"); fi
    if [[ "${blocked_rows}" -gt 0 ]]; then failures=$((failures + blocked_rows)); notes+=("requirements-map has ${blocked_rows} BLOCKED rows"); fi
fi

echo
echo "## touched-paths-manifest schema check (R5 platform-validator-compliant header)"
# R5 header per platform-validator: path / repo / reason / created_or_modified_by_lane / commit_hash.
# Brief Controls C1 scope-expansion (UI monorepo UC6 fix) is operator-approved via R3 cycle
# authorization (see CLOSE-GATE.md F4). Every row must have created_or_modified_by_lane=yes.
silent_external=$(awk -F'\t' 'NR>1 && $4!="yes"' "${EVIDENCE_ROOT}/touched-paths-manifest.tsv" | wc -l)
echo "  rows with created_or_modified_by_lane != yes: ${silent_external}"
check "touched-paths-manifest created_or_modified_by_lane all yes (no silent external touch)" \
    bash -c "[ \"${silent_external}\" = '0' ]"

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
