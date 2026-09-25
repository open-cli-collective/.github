#!/usr/bin/env bash
set -euo pipefail

: "${ARTIFACTS_PATH:?ARTIFACTS_PATH is required}"
: "${METADATA_PATH:?METADATA_PATH is required}"
: "${CHOCOLATEY:?CHOCOLATEY is required}"
: "${WINGET:?WINGET is required}"
: "${VERSION:?VERSION is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

[ -f "$ARTIFACTS_PATH" ] || { echo "::error::artifacts.json not found: $ARTIFACTS_PATH" >&2; exit 1; }
[ -f "$METADATA_PATH" ] || { echo "::error::metadata.json not found: $METADATA_PATH" >&2; exit 1; }

# The snapshot build names its archives with the snapshot version (for example,
# 1.2.1-SNAPSHOT-abc1234); the published release uses the final version, so
# rewrite that substring before handing names to the Windows channels.
snapshot_version="$(jq -r '.version // empty' "$METADATA_PATH")"
[ -n "$snapshot_version" ] || {
  echo "::error::$METADATA_PATH has no version; the snapshot goreleaser step must write it" >&2
  exit 1
}

enrich() {
  printf '%s' "$1" | jq -c --slurpfile artifacts "$ARTIFACTS_PATH" --arg snap "$snapshot_version" --arg final "$VERSION" '
    # GoReleaser records Windows executables with their .exe suffix while
    # identity manifests use the canonical binary name (for example cfl).
    def canonical($value):
      ($value // "" | ascii_downcase | sub("\\.exe$"; ""));
    def owns($name):
      (canonical(.extra.ID) == canonical($name)) or
      ((.extra.Binaries // []) | map(canonical(.)) | index(canonical($name)) != null);
    def finalize: split($snap) | join($final);
    def asset($name; $arch):
      [$artifacts[0][] | select(.type == "Archive" and .goos == "windows" and .goarch == $arch and owns($name)) | .name | finalize]
      | if length == 1 then .[0] else error("expected exactly one windows/" + $arch + " archive for " + $name + "; got " + (length|tostring)) end;
    map(. + {x64_asset: asset(.name; "amd64"), arm64_asset: asset(.name; "arm64")})'
}

chocolatey_matrix="$(enrich "$CHOCOLATEY")"
winget_matrix="$(enrich "$WINGET")"
printf 'chocolatey-matrix=%s\n' "$chocolatey_matrix" >> "$GITHUB_OUTPUT"
printf 'winget-matrix=%s\n' "$winget_matrix" >> "$GITHUB_OUTPUT"
