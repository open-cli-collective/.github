#!/usr/bin/env bash
# Canonical conventional-commit grammar for the Open CLI Collective.
# Single source of truth (cli-common/docs/release.md §1.1), shared by the
# pr-title CI check and the auto-release commit gate so the two never desync.
#
# Usage: check.sh <mode> <message>
#   title         accept the full conventional-commit type set
#   release-gate  accept feat|fix, skip valid non-release types, reject malformed subjects
set -euo pipefail

mode="${1:-}"
message="${2:-}"

if [ -z "$mode" ] || [ -z "$message" ]; then
  echo "usage: check.sh <title|release-gate> <message>" >&2
  exit 2
fi

title_pattern='^(feat|fix|refactor|test|docs|ci|chore|build|perf|style)(\([^)]+\))?!?: .+'

case "$mode" in
  title)
    pattern="$title_pattern"
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

# release-gate exit 1 is reserved for a valid conventional commit that is not
# release-worthy. A malformed landed subject must fail the workflow instead of
# being mistaken for an intentional skip.
if [ "$mode" = "release-gate" ] && printf '%s' "$message" | grep -Eq "$title_pattern"; then
  exit 1
fi

if [ "$mode" = "release-gate" ]; then
  echo "::error::invalid landed commit subject; refusing to skip auto-release: $message" >&2
  echo "expected pattern: $title_pattern" >&2
  exit 2
fi

echo "::error::not a conventional commit (mode=$mode): $message"
echo "expected pattern: $pattern" >&2
exit 1
