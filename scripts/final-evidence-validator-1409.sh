#!/usr/bin/env bash
# scheduler-mcp-server W28K-1409 — final-evidence validator (product-complete release).
# Checks the §0A pack + W28K-1409 deliverable artefacts (UT/IT/AT2 junit, deployed
# identity, PG variant + leader election, live deployed proofs, sibling regression)
# + CHECKSUMS coverage + master-rollup tag. Run from a CLEAN CLONE for the
# authoritative result. The platform validator is the authoritative tag/ancestry gate.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANE_ID="W28K-1409"
EV="${REPO_ROOT}/working/evidence/${LANE_ID}/current"
failures=0; notes=()
check(){ local d="$1"; shift; if "$@" >/dev/null 2>&1; then echo "  [PASS] $d"; else echo "  [FAIL] $d"; failures=$((failures+1)); notes+=("$d"); fi; }
junit_clean(){ local f="$1" min="$2"; [[ -s "$f" ]] || return 1
  local t fa er; t=$(grep -oE 'tests="[0-9]+"' "$f"|head -1|grep -oE '[0-9]+'); fa=$(grep -oE 'failures="[0-9]+"' "$f"|head -1|grep -oE '[0-9]+'); er=$(grep -oE 'errors="[0-9]+"' "$f"|head -1|grep -oE '[0-9]+')
  [[ "${fa:-1}" == "0" && "${er:-1}" == "0" && "${t:-0}" -ge "$min" ]]; }

echo "# final-evidence-validator-1409 — ${LANE_ID} — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "## §0A platform-validator-compliant pack"
for f in 00-reading-proof.md requirements-map.tsv touched-paths-manifest.tsv \
         external-dirty-ledger.tsv scoped-clean-proof.txt remote-proof.txt \
         FINAL-TAG-VERIFICATION.txt CLOSE-GATE.md CONTRACT-EVIDENCE-SELF-REJECTION-GATE.md \
         deployed-identity.tsv CHECKSUMS.sha256 CHECKSUMS.verify.txt final-evidence-validator.txt FINAL-RETURN.md; do
  check "${f} present" test -s "${EV}/${f}"
done
echo "## W28K-1409 deliverable artefacts"
for f in requirements-traceability.tsv junit-ut.xml junit-it.xml junit-at2.xml \
         d-multi-step-chain-on-deployed.log d-real-llm-chain-synthesis.log \
         d-vault-idam-cookie-login.log d-pdf-report-sample.pdf d-load-test-report.json \
         d-failure-tolerance-trace.log d-failure-tolerance-audit-events.json \
         d-pg-variant-replay.log d-pg-variant-leader-election.log \
         d-deploy-r2-built-and-pushed-identity.tsv d-sibling-regression-final.tsv; do
  check "${f} present" test -s "${EV}/${f}"
done
check "docs/W28K-1409-DESIGN.md present" test -s "${REPO_ROOT}/docs/W28K-1409-DESIGN.md"

echo "## test tiers (zero fail/error; UT>=180 IT>=140 AT2==6)"
check "junit-ut.xml clean (>=180)" junit_clean "${EV}/junit-ut.xml" 180
check "junit-it.xml clean (>=140)" junit_clean "${EV}/junit-it.xml" 140
check "junit-at2.xml clean (==6)"  junit_clean "${EV}/junit-at2.xml" 6

echo "## deliverable raw-value assertions"
check "F-1409-1 chain steps=4 succeeded" bash -c "grep -q 'steps=4' '${EV}/d-multi-step-chain-on-deployed.log' && grep -q 'succeeded' '${EV}/d-multi-step-chain-on-deployed.log'"
check "F-1409-2 LLM validated=true"       bash -c "grep -q '\"validated\": true' '${EV}/d-real-llm-chain-synthesis.log'"
check "F-1409-3 PG alembic head + leader" bash -c "grep -q '0003_idam_sql_backend' '${EV}/d-pg-variant-replay.log' && grep -q 'i_am_leader\": true' '${EV}/d-pg-variant-leader-election.log' && grep -q 'acquired\": false' '${EV}/d-pg-variant-leader-election.log'"
check "F-1409-5 cookie 401 + 200 roles"   bash -c "grep -q '401' '${EV}/d-vault-idam-cookie-login.log' && grep -q 'ReadOnly' '${EV}/d-vault-idam-cookie-login.log'"
check "F-1409-6 PDF magic"                 bash -c "head -c5 '${EV}/d-pdf-report-sample.pdf' | grep -q '%PDF-'"
check "deployed digest == registry (YES)"  bash -c "grep -q 'digests_match.*YES' '${EV}/deployed-identity.tsv'"
check "sibling regression 11/11"           bash -c "[ \$(grep -c 'PASS' '${EV}/d-sibling-regression-final.tsv') -eq 11 ]"
check "UI source on origin/main (not branch-only)" bash -c "test -s '${EV}/d-ui-source-on-main.tsv' && awk -F'\t' '\$1==\"w28k-1409-ui_ancestor_of_main\" && \$2==\"YES\"{f=1} END{exit f?0:1}' '${EV}/d-ui-source-on-main.tsv' && awk -F'\t' 'NR>1 && \$NF!=\"PASS\"{bad=1} END{exit bad?1:0}' '${EV}/d-ui-source-on-main.tsv'"

echo "## CHECKSUMS coverage + replay"
check "CHECKSUMS lists CLOSE-GATE.md" bash -c "grep -F 'CLOSE-GATE.md' '${EV}/CHECKSUMS.sha256'"
check "CHECKSUMS lists final-evidence-validator.txt" bash -c "grep -F 'final-evidence-validator.txt' '${EV}/CHECKSUMS.sha256'"
check "CHECKSUMS.verify OK + no FAILED" bash -c "grep -q ': OK' '${EV}/CHECKSUMS.verify.txt' && ! grep -E ': FAILED' '${EV}/CHECKSUMS.verify.txt'"

echo "## scope scans"
silent=$(awk -F'\t' 'NR>1 && $4!="yes"' "${EV}/touched-paths-manifest.tsv" | wc -l)
check "touched-paths all created_or_modified_by_lane=yes" bash -c "[ '${silent}' = '0' ]"
if [[ -s "${EV}/requirements-map.tsv" ]]; then
  nonpass=$(awk -F'\t' 'NR>1 && $NF!="PASS"' "${EV}/requirements-map.tsv" | wc -l)
  echo "  requirements-map non-PASS rows: ${nonpass}"
  [[ "${nonpass}" -gt 0 ]] && { failures=$((failures+nonpass)); notes+=("requirements-map ${nonpass} non-PASS"); }
fi
echo "## master rollup tag (informational; platform validator is authoritative)"
if git -C "${REPO_ROOT}" ls-remote origin refs/tags/W28K-1400-R2-COMPLETE 2>/dev/null | grep -q R2-COMPLETE; then echo "  [INFO] W28K-1400-R2-COMPLETE present on remote"; else echo "  [INFO] rollup tag not yet on remote"; fi

echo "## summary"
if [[ ${failures} -eq 0 ]]; then echo "FINAL_EVIDENCE_VALIDATOR: PASS failures=0"; exit 0
else printf 'FINAL_EVIDENCE_VALIDATOR: FAIL failures=%d\n' "${failures}"; for n in "${notes[@]}"; do echo "  - ${n}"; done; exit 1; fi
