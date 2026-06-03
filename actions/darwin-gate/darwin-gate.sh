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
#   darwin-gate.sh assert-dr <expected-leaf> <identifier>   (codesign -d -r- on stdin)
#       pure: designated requirement pins our cert leaf + identifier, no cdhash.
#   darwin-gate.sh check-signature <arm64-bin> <amd64-bin> <expected-leaf> <identifier>  (macOS only)
#       per binary: codesign --verify --strict, then assert-dr on its requirement.
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
  # Security.framework linkage is a sound necessary cgo signal (CGO_ENABLED=0
  # omits it entirely). The arm64 probe already runs the binary, but assert
  # linkage on BOTH slices so check-macho stands on its own — a cgo-disabled
  # arm64 build shouldn't pass on the lipo arch check alone.
  for pair in "arm64:$arm" "amd64:$amd"; do
    arch="${pair%%:*}"; bin="${pair#*:}"
    otool -L "$bin" | grep -q '/System/Library/Frameworks/Security.framework' \
      || { echo "::error::$arch binary not linked against Security.framework (cgo missing)"; return 1; }
  done
  echo "check-macho OK"
}

probe() {
  local spec="$1" bin="$2"
  command -v jq >/dev/null || { echo "::error::jq required"; exit 2; }
  local tmp; tmp=$(mktemp -d)
  local xdg="$tmp/xdg"; mkdir -p "$xdg"

  # seed a hermetic config so auto-detect (not an override) selects the backend
  local seed_path seed_base seed_root
  seed_path=$(printf '%s' "$spec" | jq -r '.seed_config.path // empty')
  seed_base=$(printf '%s' "$spec" | jq -r '.seed_config.base // "xdg_config"')
  if [ -n "$seed_path" ]; then
    case "$seed_base" in
      xdg_config) seed_root="$xdg" ;;
      native_user_config) seed_root="$tmp/Library/Application Support" ;;
      *) echo "::error::unsupported seed_config.base '$seed_base'"; return 1 ;;
    esac
    case "$seed_path" in
      /*) echo "::error::seed_config.path must be relative: $seed_path"; return 1 ;;
    esac
    local -a seed_parts=()
    local part
    IFS='/' read -r -a seed_parts <<< "$seed_path"
    for part in "${seed_parts[@]}"; do
      [ "$part" != ".." ] || { echo "::error::seed_config.path must not contain '..': $seed_path"; return 1; }
    done
    mkdir -p "$seed_root/$(dirname "$seed_path")"
    printf '%s' "$spec" | jq -r '.seed_config.content // ""' > "$seed_root/$seed_path"
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

# assert-dr — pure/portable: read full `codesign -d -r-` output on stdin, extract the
# `designated => ` requirement line, and assert it pins our cert leaf + identifier and
# NOT a cdhash. Parsing ONLY the requirement line is deliberate — a valid signature's
# verbose output also prints `CDHash=` metadata, which is not the requirement keyword
# `cdhash`. Leaf match is case-insensitive (codesign may emit the hash in either case).
assert_dr() {
  local leaf="$1" ident="$2" req lreq lleaf
  req="$(cat | sed -n 's/^designated => //p' | head -1)"
  [ -n "$req" ] || { echo "::error::no 'designated =>' requirement in codesign output"; return 1; }
  case "$req" in *cdhash*) echo "::error::DR pins cdhash (ad-hoc) — Keychain grant will not persist"; return 1 ;; esac
  lreq="$(printf '%s' "$req" | tr 'A-Z' 'a-z')"; lleaf="$(printf '%s' "$leaf" | tr 'A-Z' 'a-z')"
  case "$lreq" in *"certificate leaf = h\"$lleaf\""*) : ;; *) echo "::error::DR leaf != expected $leaf; got: $req"; return 1 ;; esac
  case "$req"  in *"identifier \"$ident\""*) : ;; *) echo "::error::DR identifier != $ident; got: $req"; return 1 ;; esac
  echo "assert-dr OK"
}

# check-signature (macOS only) — per darwin binary: verify the seal, then assert the DR
# via assert-dr. The release.yml step passes the pinned leaf only when the cert secrets
# exist, so this runs solely for signed (opted-in) releases.
check_signature() {
  local arm="$1" amd="$2" leaf="$3" ident="$4" bin
  for bin in "$arm" "$amd"; do
    codesign --verify --strict "$bin" 2>/dev/null || { echo "::error::codesign --verify --strict failed: $bin"; return 1; }
    codesign -d -r- "$bin" 2>&1 | assert_dr "$leaf" "$ident" || { echo "::error::DR check failed: $bin"; return 1; }
  done
  echo "check-signature OK"
}

case "${1:-}" in
  check-artifacts) check_artifacts "$2" ;;
  check-macho)     check_macho "$2" "$3" ;;
  probe)           probe "$2" "$3" ;;
  assert-dr)       assert_dr "$2" "$3" ;;
  check-signature) check_signature "$2" "$3" "$4" "$5" ;;
  *) echo "usage: darwin-gate.sh {check-artifacts|check-macho|probe|assert-dr|check-signature} ..." >&2; exit 2 ;;
esac
