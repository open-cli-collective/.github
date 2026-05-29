#!/usr/bin/env bash
# CGO-darwin pre-publish gate (distribution.md §2). Proves the darwin binaries
# actually carry the Keychain backend before anything is published.
#
#   darwin-gate.sh check-artifacts <artifacts.json>
#       darwin arm64+amd64 binaries present; exactly one darwin archive per arch.
#   darwin-gate.sh check-macho <arm64-bin> <amd64-bin>      (macOS only)
#       Mach-O arch sanity + amd64 linked against Security.framework.
#   darwin-gate.sh probe <keychain-probe-json> <arm64-bin>
#       manifest-driven functional probe: seed config, run, assert backend.
#
# check-artifacts and probe are pure/portable (unit-tested on Linux with
# fixtures and a stub binary); check-macho needs the macOS toolchain and runs
# on the release runner.
set -euo pipefail

check_artifacts() {
  local art="$1"
  command -v jq >/dev/null || { echo "::error::jq required"; exit 2; }
  local arm amd tot uniq
  arm=$(jq -r '[.[]|select(.type=="Binary" and .goos=="darwin" and .goarch=="arm64")]|length' "$art")
  amd=$(jq -r '[.[]|select(.type=="Binary" and .goos=="darwin" and .goarch=="amd64")]|length' "$art")
  [ "$arm" -ge 1 ] && [ "$amd" -ge 1 ] || { echo "::error::missing a darwin binary (arm64=$arm amd64=$amd)"; return 1; }
  tot=$(jq '[.[]|select(.type=="Archive" and .goos=="darwin")|.name]|length' "$art")
  uniq=$(jq '[.[]|select(.type=="Archive" and .goos=="darwin")|.name]|unique|length' "$art")
  [ "$tot" = "$uniq" ] || { echo "::error::duplicate darwin archive names"; return 1; }
  for a in arm64 amd64; do
    local n
    n=$(jq "[.[]|select(.type==\"Archive\" and .goos==\"darwin\" and .goarch==\"$a\")]|length" "$art")
    [ "$n" = 1 ] || { echo "::error::expected exactly one darwin/$a archive (got $n)"; return 1; }
  done
  echo "check-artifacts OK"
}

check_macho() {
  local arm="$1" amd="$2"
  file "$arm" | grep -q 'arm64'  || { echo "::error::arm64 binary is not arm64 Mach-O"; return 1; }
  file "$amd" | grep -q 'x86_64' || { echo "::error::amd64 binary is not x86_64 Mach-O"; return 1; }
  lipo -archs "$arm" | grep -qw arm64  || { echo "::error::lipo: arm64 slice missing"; return 1; }
  lipo -archs "$amd" | grep -qw x86_64 || { echo "::error::lipo: x86_64 slice missing"; return 1; }
  # amd64 can't run on the arm64 runner: Security.framework linkage is a sound
  # necessary cgo signal (CGO_ENABLED=0 omits it entirely).
  otool -L "$amd" | grep -q '/System/Library/Frameworks/Security.framework' \
    || { echo "::error::amd64 binary not linked against Security.framework (cgo missing)"; return 1; }
  echo "check-macho OK"
}

probe() {
  local spec="$1" bin="$2"
  command -v jq >/dev/null || { echo "::error::jq required"; exit 2; }
  local tmp; tmp=$(mktemp -d)
  local xdg="$tmp/xdg"; mkdir -p "$xdg"

  # seed a hermetic config so auto-detect (not an override) selects the backend
  local seed_path
  seed_path=$(printf '%s' "$spec" | jq -r '.seed_config.path // empty')
  if [ -n "$seed_path" ]; then
    mkdir -p "$xdg/$(dirname "$seed_path")"
    printf '%s' "$spec" | jq -r '.seed_config.content // ""' > "$xdg/$seed_path"
  fi

  # build the env -u list + the command argv
  local -a unset_args=() cmd=()
  local k
  while IFS= read -r k; do [ -n "$k" ] && unset_args+=( -u "$k" ); done < <(printf '%s' "$spec" | jq -r '.env_unset // [] | .[]')
  while IFS= read -r k; do [ -n "$k" ] && cmd+=( "$k" ); done < <(printf '%s' "$spec" | jq -r '.command // [] | .[]')

  local out
  # guard empty arrays for set -u on older bash
  out=$(env ${unset_args[@]+"${unset_args[@]}"} HOME="$tmp" XDG_CONFIG_HOME="$xdg" "$bin" ${cmd[@]+"${cmd[@]}"})
  echo "$out"

  local mode; mode=$(printf '%s' "$spec" | jq -r '.output // "json"')
  if [ "$mode" = "json" ]; then
    local key want got
    while IFS= read -r key; do
      [ -n "$key" ] || continue
      want=$(printf '%s' "$spec" | jq -r --arg k "$key" '.assertions[$k]')
      got=$(printf '%s' "$out" | jq -r "$key")
      [ "$got" = "$want" ] || { echo "::error::probe assertion $key: got '$got' want '$want'"; return 1; }
    done < <(printf '%s' "$spec" | jq -r '.assertions // {} | keys[]')
  else
    local re
    while IFS= read -r re; do
      [ -n "$re" ] || continue
      printf '%s' "$out" | grep -Eq "$re" || { echo "::error::probe text match failed: /$re/"; return 1; }
    done < <(printf '%s' "$spec" | jq -r '.match // [] | .[]')
  fi
  echo "probe OK"
}

case "${1:-}" in
  check-artifacts) check_artifacts "$2" ;;
  check-macho)     check_macho "$2" "$3" ;;
  probe)           probe "$2" "$3" ;;
  *) echo "usage: darwin-gate.sh {check-artifacts|check-macho|probe} ..." >&2; exit 2 ;;
esac
