#!/usr/bin/env bash
# Render an alias Homebrew cask from the goreleaser-rendered canonical cask.
#
#   alias.sh render <canonical-cask-file> <canonical-token> <alias-token>
#       Print the alias cask to stdout: the canonical cask verbatim (same url,
#       version, sha256, binary) with only the `cask "<token>" do` stanza
#       identifier renamed. Everything else — including the installed binary
#       name — is intentionally preserved, so the alias is just a second name
#       that installs the same artifact (distribution.md §8.2).
#
# Pure/portable; unit-tested with a fixture cask. The action.yml around it does
# the clone/commit/push atomically.
set -euo pipefail

render() {
  local file="$1" canon="$2" alias="$3"
  [ -f "$file" ] || { echo "::error::canonical cask not found: $file" >&2; return 1; }
  [ -n "$canon" ] && [ -n "$alias" ] || { echo "::error::render needs <file> <canonical-token> <alias-token>" >&2; return 1; }

  # Rename ONLY the first `cask "<canon>" do` declaration. Anchored to the cask
  # keyword so a `name`/`binary`/`desc` line that happens to contain the token
  # is left untouched. Fail if the stanza isn't found — a silent no-op would
  # ship a duplicate of the canonical cask under the alias filename.
  awk -v canon="$canon" -v alias="$alias" '
    !done && $0 ~ "^[[:space:]]*cask[[:space:]]+\"" canon "\"" {
      sub("\"" canon "\"", "\"" alias "\"")
      done = 1
    }
    { print }
    END { if (!done) { print "::error::no `cask \"" canon "\"` stanza in canonical cask" > "/dev/stderr"; exit 1 } }
  ' "$file"
}

case "${1:-}" in
  render) shift; render "$@" ;;
  *) echo "usage: alias.sh render <canonical-cask-file> <canonical-token> <alias-token>" >&2; exit 2 ;;
esac
