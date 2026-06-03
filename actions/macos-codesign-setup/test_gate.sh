#!/usr/bin/env bash
# Unit tests for gate.sh — the macos-codesign-setup all-or-none decision. This is the
# load-bearing invariant: signing and DR enforcement must be atomic, so a partial config
# must fail rather than sign-without-enforcing (or skip silently).
set -uo pipefail
cd "$(dirname "$0")"
fails=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

# no secrets → disabled (the unsigned / opt-out path)
out=$(env P12= PASSWORD= CN= LEAF_SHA= BINARY= bash gate.sh 2>/dev/null) \
  && [ "$out" = disabled ] && ok "none set → disabled" || bad "none set → disabled"

# all four + binary → enabled
out=$(env P12=a PASSWORD=b CN=c LEAF_SHA=d BINARY=slck bash gate.sh 2>/dev/null) \
  && [ "$out" = enabled ] && ok "all four → enabled" || bad "all four → enabled"

# partial: 3 of 4 (missing leaf-sha — the exact gap the gate must catch) → error
env P12=a PASSWORD=b CN=c LEAF_SHA= BINARY=slck bash gate.sh >/dev/null 2>&1 \
  && bad "partial (missing leaf) should fail" || ok "partial (missing leaf) fails"

# partial: 1 of 4 → error
env P12=a PASSWORD= CN= LEAF_SHA= BINARY=slck bash gate.sh >/dev/null 2>&1 \
  && bad "partial (1 of 4) should fail" || ok "partial (1 of 4) fails"

# all four secrets but no binary → error (identifier can't be formed)
env P12=a PASSWORD=b CN=c LEAF_SHA=d BINARY= bash gate.sh >/dev/null 2>&1 \
  && bad "enabled without binary should fail" || ok "enabled without binary fails"

echo "----"
if [ "$fails" -eq 0 ]; then echo "all gate tests passed"; else echo "$fails failed"; exit 1; fi
