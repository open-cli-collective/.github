#!/usr/bin/env bash
# Unit tests for gate.sh — run locally and in CI. No tags are minted.
set -uo pipefail
cd "$(dirname "$0")"
fails=0
ok()   { echo "ok   $1"; }
bad()  { echo "FAIL $1"; fails=$((fails+1)); }

# expect match-paths to PASS (release-worthy)
mp_pass() { # label release tool vfile <<< files
  local label="$1" rel="$2" tool="$3" vf="$4" files="$5"
  if printf '%s\n' "$files" | bash gate.sh match-paths "$rel" "$tool" "$vf"; then ok "$label"; else bad "$label"; fi
}
mp_fail() {
  local label="$1" rel="$2" tool="$3" vf="$4" files="$5"
  if printf '%s\n' "$files" | bash gate.sh match-paths "$rel" "$tool" "$vf"; then bad "$label"; else ok "$label"; fi
}

REL='**.go,go.mod,go.sum,version.txt'

# --- single-repo path gate ---
mp_pass "go file"            "$REL" "" version.txt "internal/foo.go"
mp_pass "go.mod"             "$REL" "" version.txt "go.mod"
mp_pass "version.txt"        "$REL" "" version.txt "version.txt"
mp_pass "version via vfile"  "README.md"  "" version.txt "version.txt"   # unioned even if release-paths omits it
mp_pass "removed go file"    "$REL" "" version.txt "internal/old.go"
mp_fail "docs only"          "$REL" "" version.txt "README.md
docs/x.md"
mp_fail "workflow only"      "$REL" "" version.txt ".github/workflows/ci.yml"

# --- monorepo: tool-scoped + tool-relative ---
TOOL='tools/cfl/**,shared/**'
mp_pass "cfl go file"        "$REL" "$TOOL" version.txt "tools/cfl/main.go"
mp_pass "cfl version (rel)"  "$REL" "$TOOL" version.txt "tools/cfl/version.txt"   # matches version.txt tool-relative
mp_pass "shared go"          "$REL" "$TOOL" version.txt "shared/util.go"
mp_fail "other tool"         "$REL" "$TOOL" version.txt "tools/jtk/main.go"
mp_fail "root doc in mono"   "$REL" "$TOOL" version.txt "README.md"

# --- version validation ---
bash gate.sh validate-version "3.1"   >/dev/null 2>&1 && ok "ver 3.1"        || bad "ver 3.1"
bash gate.sh validate-version "v3.1"  >/dev/null 2>&1 && bad "reject v3.1"   || ok "reject v3.1"
bash gate.sh validate-version "3.1.5" >/dev/null 2>&1 && bad "reject 3.1.5"  || ok "reject 3.1.5"
bash gate.sh validate-version "3"     >/dev/null 2>&1 && bad "reject 3"      || ok "reject 3"

# --- tag compute ---
[ "$(bash gate.sh compute-tag v 3.1 150)" = "v3.1.150" ]        && ok "tag v"     || bad "tag v"
[ "$(bash gate.sh compute-tag jtk-v 1.0 42)" = "jtk-v1.0.42" ]  && ok "tag jtk-v" || bad "tag jtk-v"

echo "----"
if [ "$fails" -eq 0 ]; then echo "all gate.sh tests passed"; else echo "$fails failed"; exit 1; fi
