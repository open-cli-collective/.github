#!/usr/bin/env bash
# Pure all-or-none decision for macos-codesign-setup. Reads the four cert inputs and the
# binary from the environment (P12/PASSWORD/CN/LEAF_SHA/BINARY) and prints "enabled" or
# "disabled". Exits non-zero on a partial config (1-3 of 4 set) or a missing binary when
# enabled. Kept separate from action.yml so the load-bearing invariant — signing and DR
# enforcement are atomic (both on or both off) — is unit-tested on Linux.
set -euo pipefail
n=0
[ -n "${P12:-}" ] && n=$((n+1)) || true
[ -n "${PASSWORD:-}" ] && n=$((n+1)) || true
[ -n "${CN:-}" ] && n=$((n+1)) || true
[ -n "${LEAF_SHA:-}" ] && n=$((n+1)) || true
if [ "$n" -eq 0 ]; then echo disabled; exit 0; fi
if [ "$n" -ne 4 ]; then
  echo "::error::partial macOS signing config — pass all four cert-p12/cert-password/cert-cn/cert-leaf-sha, or none" >&2
  exit 1
fi
[ -n "${BINARY:-}" ] || { echo "::error::binary input is required when signing is enabled" >&2; exit 1; }
echo enabled
