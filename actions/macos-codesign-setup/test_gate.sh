#!/usr/bin/env bash
# Unit tests for gate.sh — the macos-codesign-setup all-or-none decision (exit code:
# 0 enabled / 10 disabled / 1 invalid). This is the load-bearing invariant: signing and
# DR enforcement must be atomic, so a partial config must fail (1) rather than enable or
# silently skip.
set -uo pipefail
cd "$(dirname "$0")"
fails=0
ok()  { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }
rc_of() { "$@" >/dev/null 2>&1; echo $?; }

# no secrets → disabled (10) — the unsigned / opt-out path
[ "$(rc_of env P12= PASSWORD= CN= LEAF_SHA= BINARY= bash gate.sh)" = 10 ] \
  && ok "none set → disabled (10)" || bad "none set → disabled (10)"

# all four + binary → enabled (0)
[ "$(rc_of env P12=a PASSWORD=b CN=c LEAF_SHA=d BINARY=slck bash gate.sh)" = 0 ] \
  && ok "all four → enabled (0)" || bad "all four → enabled (0)"

# partial: 3 of 4 (missing leaf-sha — the exact gap the gate must catch) → invalid (1)
[ "$(rc_of env P12=a PASSWORD=b CN=c LEAF_SHA= BINARY=slck bash gate.sh)" = 1 ] \
  && ok "partial (missing leaf) → invalid (1)" || bad "partial (missing leaf) → invalid (1)"

# partial: 1 of 4 → invalid (1)
[ "$(rc_of env P12=a PASSWORD= CN= LEAF_SHA= BINARY=slck bash gate.sh)" = 1 ] \
  && ok "partial (1 of 4) → invalid (1)" || bad "partial (1 of 4) → invalid (1)"

# all four secrets but no binary → invalid (1) (identifier can't be formed)
[ "$(rc_of env P12=a PASSWORD=b CN=c LEAF_SHA=d BINARY= bash gate.sh)" = 1 ] \
  && ok "enabled without binary → invalid (1)" || bad "enabled without binary → invalid (1)"

echo "----"
if [ "$fails" -eq 0 ]; then echo "all gate tests passed"; else echo "$fails failed"; exit 1; fi
