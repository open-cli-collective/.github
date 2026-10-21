#!/usr/bin/env bash
# Rewrite GoReleaser's legacy Homebrew install-step stanzas to the current DSL.
#
# Usage:
#   modernize.sh render <cask-file>
#       Print the cask with only deprecated install-step block declarations
#       renamed. Already-modern casks pass through unchanged. Fails when a
#       system_command call is left that the rewrite could not convert.
set -euo pipefail

render() {
  local file="$1"
  [ -f "$file" ] || { echo "::error::cask not found: $file" >&2; return 1; }

  local out
  out=$(mktemp)
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
    # base: resolves only the command path, never args, so a staged path in
    # args has to stay absolute through the install-step template token.
    /#\{staged_path\}/ {
      gsub(/#\{staged_path\}/, "{{staged_path}}")
    }
    { print }
  ' "$file" > "$out"

  # The install-step DSL has no system_command, so one left in place (an
  # assignment such as `result = system_command ...`, which the line-start
  # rewrite above skips) makes Homebrew reject the whole cask as invalid.
  if grep -nE '^[^#]*\bsystem_command\b' "$out" >&2; then
    echo "::error::$file: system_command calls above cannot be converted to install steps; make each a bare system_command line" >&2
    rm -f "$out"
    return 1
  fi
  cat "$out"
  rm -f "$out"
}

case "${1:-}" in
  render) render "${2:-}" ;;
  *) echo "usage: modernize.sh render <cask-file>" >&2; exit 2 ;;
esac
