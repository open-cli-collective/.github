#!/usr/bin/env bash
# Exercise the same release asset resolver used by release.yml.
set -uo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

cat > "$tmp/metadata.json" <<'JSON'
{"version":"1.3.97-SNAPSHOT-abc1234"}
JSON

# This is the cfl 1.3.97 GoReleaser shape: the manifest name is cfl, the
# archive owner ID is default, and Binaries contains cfl.exe.
cat > "$tmp/artifacts.json" <<'JSON'
[
  {"type":"Archive","goos":"windows","goarch":"amd64","name":"cfl_1.3.97-SNAPSHOT-abc1234_windows_amd64.zip","extra":{"Binaries":["cfl.exe"],"ID":"default"}},
  {"type":"Archive","goos":"windows","goarch":"arm64","name":"cfl_1.3.97-SNAPSHOT-abc1234_windows_arm64.zip","extra":{"Binaries":["cfl.exe"],"ID":"default"}}
]
JSON

output="$tmp/output"
GITHUB_OUTPUT="$output" \
ARTIFACTS_PATH="$tmp/artifacts.json" \
METADATA_PATH="$tmp/metadata.json" \
CHOCOLATEY='[{"name":"cfl","id":"confluence-cli","dir":"packaging/chocolatey"}]' \
WINGET='[]' \
VERSION='1.3.97' \
bash resolve.sh >/dev/null

matrix=$(sed -n 's/^chocolatey-matrix=//p' "$output")
printf '%s' "$matrix" | jq -e '
  length == 1 and
  .[0].name == "cfl" and
  .[0].x64_asset == "cfl_1.3.97_windows_amd64.zip" and
  .[0].arm64_asset == "cfl_1.3.97_windows_arm64.zip"
' >/dev/null \
  && ok "cfl .exe artifacts resolve to exact per-arch assets" \
  || bad "cfl .exe artifacts resolve to exact per-arch assets"

jq 'map(select(.goarch != "amd64"))' "$tmp/artifacts.json" > "$tmp/missing-amd64.json"
if GITHUB_OUTPUT="$tmp/missing-output" \
  ARTIFACTS_PATH="$tmp/missing-amd64.json" \
  METADATA_PATH="$tmp/metadata.json" \
  CHOCOLATEY='[{"name":"cfl","id":"confluence-cli","dir":"packaging/chocolatey"}]' \
  WINGET='[]' \
  VERSION='1.3.97' \
  bash resolve.sh > /dev/null 2>"$tmp/missing-error"; then
  bad "missing amd64 artifact fails"
elif grep -Fq 'expected exactly one windows/amd64 archive for cfl; got 0' "$tmp/missing-error"; then
  ok "missing amd64 artifact fails loudly"
else
  bad "missing amd64 artifact reports the expected error"
fi

echo "----"
if [ "$fails" -eq 0 ]; then echo "all release asset tests passed"; else echo "$fails failed"; exit 1; fi
