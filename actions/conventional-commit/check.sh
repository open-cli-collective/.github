#!/usr/bin/env bash
# Canonical conventional-commit grammar for the Open CLI Collective.
# Single source of truth (cli-common/docs/release.md §1.1), shared by the
# pr-title CI check and the auto-release commit gate so the two never desync.
#
# Usage: check.sh <mode> <message>
#   title         accept the full conventional-commit type set
#   release-gate  accept only feat|fix (the release-cutting subset)
set -euo pipefail

mode="${1:-}"
message="${2:-}"

if [ -z "$mode" ] || [ -z "$message" ]; then
  echo "usage: check.sh <title|release-gate> <message>" >&2
  exit 2
fi

case "$mode" in
  title)
    pattern='^(feat|fix|refactor|test|docs|ci|chore|build|perf|style)(\([^)]+\))?!?: .+'
    ;;
  release-gate)
    pattern='^(feat|fix)(\([^)]+\))?!?: .+'
    ;;
  *)
    echo "unknown mode: $mode (expected 'title' or 'release-gate')" >&2
    exit 2
    ;;
esac

if printf '%s' "$message" | grep -Eq "$pattern"; then
  exit 0
fi

echo "::error::not a conventional commit (mode=$mode): $message"
echo "expected pattern: $pattern" >&2
exit 1
