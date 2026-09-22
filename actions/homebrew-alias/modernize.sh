#!/usr/bin/env bash
# Rewrite GoReleaser's legacy Homebrew install-step stanzas to the current DSL.
#
# Usage:
#   modernize.sh render <cask-file>
#       Print the cask with only deprecated install-step block declarations
#       renamed. Already-modern casks pass through unchanged.
set -euo pipefail

render() {
  local file="$1"
  [ -f "$file" ] || { echo "::error::cask not found: $file" >&2; return 1; }

  awk '
    /^[[:space:]]*preflight[[:space:]]+do[[:space:]]*$/ {
      sub(/preflight[[:space:]]+do[[:space:]]*$/, "preflight_steps do")
    }
    /^[[:space:]]*postflight[[:space:]]+do[[:space:]]*$/ {
      sub(/postflight[[:space:]]+do[[:space:]]*$/, "postflight_steps do")
    }
    /^[[:space:]]*uninstall_preflight[[:space:]]+do[[:space:]]*$/ {
      sub(/uninstall_preflight[[:space:]]+do[[:space:]]*$/, "uninstall_preflight_steps do")
    }
    /^[[:space:]]*uninstall_postflight[[:space:]]+do[[:space:]]*$/ {
      sub(/uninstall_postflight[[:space:]]+do[[:space:]]*$/, "uninstall_postflight_steps do")
    }
    /^[[:space:]]*system_command[[:space:]]+/ {
      sub(/system_command[[:space:]]+/, "run ")
    }
    /"#\{staged_path\}\// {
      sub(/"#\{staged_path\}\//, "\"")
      sub(/\][[:space:]]*$/, "], base: :staged_path")
    }
    { print }
  ' "$file"
}

case "${1:-}" in
  render) render "${2:-}" ;;
  *) echo "usage: modernize.sh render <cask-file>" >&2; exit 2 ;;
esac
