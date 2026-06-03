#!/usr/bin/env bash
# Pure all-or-none decision for macos-codesign-setup. Reads the four cert inputs and the
# binary from the environment (P12/PASSWORD/CN/LEAF_SHA/BINARY) and signals via exit code
# (so any ::error:: annotation can go to stdout, where the Actions runner renders it — a
# captured stdout value would hide it):
#   0  = enabled  (all four cert secrets + binary present)
#   10 = disabled (no secrets — the unsigned / opt-out path)
#   1  = invalid  (1-3 of 4 set, or enabled without a binary; prints a ::error:: annotation)
# Kept separate from action.yml so the load-bearing invariant — signing and DR enforcement
# are atomic (both on or both off) — is unit-tested on Linux.
set -euo pipefail
n=0
[ -n "${P12:-}" ] && n=$((n+1)) || true
[ -n "${PASSWORD:-}" ] && n=$((n+1)) || true
[ -n "${CN:-}" ] && n=$((n+1)) || true
[ -n "${LEAF_SHA:-}" ] && n=$((n+1)) || true
[ "$n" -eq 0 ] && exit 10
if [ "$n" -ne 4 ]; then
  echo "::error::partial macOS signing config — pass all four cert secrets (p12/password/cn/leaf-sha), or none"
  exit 1
fi
if [ -z "${BINARY:-}" ]; then
  echo "::error::binary input is required when macOS signing is enabled"
  exit 1
fi
exit 0
