#!/usr/bin/env bash
# Sign one darwin Mach-O with the family's stable code-signing identity so the macOS
# Keychain "Always Allow" grant survives `brew upgrade` (cli-common distribution.md §2A).
# Invoked from each repo's goreleaser per-build darwin hooks.post, after the binary is
# linked and before it is archived. actions/macos-codesign-setup stages this script onto
# the runner and exports its absolute path as $CODESIGN_DARWIN_SCRIPT. No-op in local
# builds (SIGN_IDENTITY unset), so `goreleaser build` off-CI still works.
set -euo pipefail
BIN="$1"; OS="$2"
[ "$OS" = darwin ] || exit 0   # only Mach-O is signable
if [ -z "${SIGN_IDENTITY:-}" ]; then
  [ "${REQUIRE_SIGNING:-0}" = 1 ] && { echo "ERROR: REQUIRE_SIGNING set but SIGN_IDENTITY missing" >&2; exit 1; }
  echo "no SIGN_IDENTITY — skipping codesign (local build)"; exit 0
fi
codesign --force --sign "$SIGN_IDENTITY" --identifier "${TOOL_IDENTIFIER:?TOOL_IDENTIFIER required}" "$BIN"
codesign --verify --strict "$BIN"
# Fail loudly if the requirement still pins a per-build cdhash (ad-hoc). Parse ONLY the
# `designated =>` requirement line — a valid signature still prints `CDHash=` metadata,
# which is not the requirement keyword `cdhash`.
req="$(codesign -d -r- "$BIN" 2>&1 | sed -n 's/^designated => //p')"
case "$req" in *cdhash*) echo "ERROR: designated requirement still pins cdhash; grant will not persist" >&2; exit 1 ;; esac
echo "signed $BIN ($SIGN_IDENTITY / $TOOL_IDENTIFIER)"
