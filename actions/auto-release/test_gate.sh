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

# --- regression (.github#25): the gate must not depend on its CWD. In CI the
# gate runs from the repo *root*, where tool-paths like `tools/cfl/**` name real
# directories. An unquoted glob in _split_csv pathname-expanded them into
# directory entries, so EVERY change to a monorepo tool was skipped. Re-run the
# monorepo cases from inside such a tree to lock the behavior. ---
GATE="$(pwd)/gate.sh"
fixture="$(mktemp -d)"
mkdir -p "$fixture/tools/cfl/internal" "$fixture/tools/cfl/cmd/cfl" "$fixture/shared"
: > "$fixture/tools/cfl/version.txt"; : > "$fixture/tools/cfl/go.mod"
mp_pass_at() { # label cwd release tool vfile files
  if ( cd "$2" && printf '%s\n' "$6" | bash "$GATE" match-paths "$3" "$4" "$5" ); then ok "$1"; else bad "$1"; fi
}
mp_fail_at() {
  if ( cd "$2" && printf '%s\n' "$6" | bash "$GATE" match-paths "$3" "$4" "$5" ); then bad "$1"; else ok "$1"; fi
}
mp_pass_at "from root: cfl version bump" "$fixture" "$REL" "$TOOL" version.txt "tools/cfl/version.txt"
mp_pass_at "from root: cfl go file"      "$fixture" "$REL" "$TOOL" version.txt "tools/cfl/internal/foo.go"
mp_pass_at "from root: cfl go.mod"       "$fixture" "$REL" "$TOOL" version.txt "tools/cfl/go.mod"
mp_fail_at "from root: other tool"       "$fixture" "$REL" "$TOOL" version.txt "tools/jtk/main.go"
mp_fail_at "from root: tool doc only"    "$fixture" "$REL" "$TOOL" version.txt "tools/cfl/README.md"
rm -rf "$fixture"

# --- version validation ---
bash gate.sh validate-version "3.1"   >/dev/null 2>&1 && ok "ver 3.1"        || bad "ver 3.1"
bash gate.sh validate-version "v3.1"  >/dev/null 2>&1 && bad "reject v3.1"   || ok "reject v3.1"
bash gate.sh validate-version "3.1.5" >/dev/null 2>&1 && bad "reject 3.1.5"  || ok "reject 3.1.5"
bash gate.sh validate-version "3"     >/dev/null 2>&1 && bad "reject 3"      || ok "reject 3"

# --- tag compute ---
[ "$(bash gate.sh compute-tag v 3.1 150)" = "v3.1.150" ]        && ok "tag v"     || bad "tag v"
[ "$(bash gate.sh compute-tag jtk-v 1.0 42)" = "jtk-v1.0.42" ]  && ok "tag jtk-v" || bad "tag jtk-v"

# --- release commit gate: release, intentional skip, and fail-loud invalid subject ---
CCHECK="../conventional-commit/check.sh"
check_rc() {
  local mode="$1" message="$2" output rc
  if output="$(bash "$CCHECK" "$mode" "$message" 2>&1)"; then rc=0; else rc=$?; fi
  printf '%s\n' "$rc|$output"
}
expect_rc() {
  local label="$1" expected="$2" result rc
  result="$(check_rc "$3" "$4")"
  rc="${result%%|*}"
  if [ "$rc" -eq "$expected" ]; then ok "$label"; else bad "$label (rc=$rc)"; fi
}
expect_rc "feat release" 0 release-gate "feat: ship it"
expect_rc "fix release" 0 release-gate "fix(scope)!: stop the bug"
expect_rc "docs skip" 1 release-gate "docs: update the guide"
expect_rc "refactor skip" 1 release-gate "refactor(core): simplify the path"
expect_rc "ci skip" 1 release-gate "ci: update automation"
invalid="$(check_rc release-gate "Fix scoped reviewer workspace path validation (#533)")"
invalid_rc="${invalid%%|*}"
invalid_output="${invalid#*|}"
case "$invalid_output" in
  *"invalid landed commit subject; refusing to skip auto-release"*)
    [ "$invalid_rc" -eq 2 ] && ok "invalid landed subject fails loudly" || bad "invalid landed subject rc=$invalid_rc" ;;
  *) bad "invalid landed subject message" ;;
esac

echo "----"
if [ "$fails" -eq 0 ]; then echo "all gate.sh tests passed"; else echo "$fails failed"; exit 1; fi
