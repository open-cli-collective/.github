#!/usr/bin/env bash
# Path-gate and tag-compute helpers for the reusable auto-release workflow.
# Kept as a standalone script so the logic is unit-testable without minting tags.
#
#   gate.sh validate-version <MAJOR.MINOR>
#   gate.sh compute-tag <prefix> <MAJOR.MINOR> <run>
#   gate.sh match-paths <release-csv> <tool-csv> <version-file>   < changed-files
#       exit 0 if any changed file is release-worthy, 1 otherwise
set -euo pipefail

# Split a comma-separated list into lines, dropping empties.
_split_csv() { local IFS=','; for x in $1; do [ -n "$x" ] && printf '%s\n' "$x"; done; }

# Translate a path glob into a bash `case` pattern: ** -> * (case patterns
# already match across '/', so a single * suffices for "any depth").
# NOTE: in a bash `case`, a single `*` also matches '/', so `*.go` matches
# `internal/foo.go` — broader than POSIX pathname globbing. Callers passing
# single-`*` patterns get this any-depth behavior intentionally.
_glob_to_pat() { printf '%s' "${1//\*\*/*}"; }

validate_version() {
  if ! printf '%s' "$1" | grep -Eq '^[0-9]+\.[0-9]+$'; then
    echo "::error::version file must be exactly MAJOR.MINOR (got '$1')" >&2
    return 1
  fi
}

compute_tag() {
  validate_version "$2" || return 1
  printf '%s%s.%s\n' "$1" "$2" "$3"
}

match_paths() {
  local release_csv="$1" tool_csv="$2" version_file="$3"
  local -a rels=() tools=()
  local line
  while IFS= read -r line; do rels+=("$line"); done < <(_split_csv "$release_csv")
  [ -n "$version_file" ] && rels+=("$version_file")   # always release on a version bump
  while IFS= read -r line; do tools+=("$line"); done < <(_split_csv "$tool_csv")

  local f tg pat prefix rel r rp
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ "${#tools[@]}" -gt 0 ]; then
      for tg in "${tools[@]}"; do
        pat="$(_glob_to_pat "$tg")"
        case "$f" in
          $pat)
            prefix="${tg%%\**}"          # tool root, e.g. tools/cfl/
            rel="${f#"$prefix"}"          # path relative to the tool root
            for r in "${rels[@]}"; do
              rp="$(_glob_to_pat "$r")"
              case "$f"   in $rp) return 0;; esac   # full-path match
              case "$rel" in $rp) return 0;; esac   # tool-relative match
            done
            ;;
        esac
      done
    else
      for r in "${rels[@]}"; do
        rp="$(_glob_to_pat "$r")"
        case "$f" in $rp) return 0;; esac
      done
    fi
  done
  return 1
}

case "${1:-}" in
  validate-version) validate_version "$2" ;;
  compute-tag)      compute_tag "$2" "$3" "$4" ;;
  match-paths)      shift; match_paths "$@" ;;
  *) echo "usage: gate.sh {validate-version|compute-tag|match-paths} ..." >&2; exit 2 ;;
esac
