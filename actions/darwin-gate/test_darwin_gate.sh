#!/usr/bin/env bash
# Portable unit tests for darwin-gate.sh (check-artifacts + probe). check-macho
# needs the macOS toolchain and is exercised on the release runner.
set -uo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d); fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

# --- check-artifacts ---
cat > "$tmp/good.json" <<'JSON'
[
 {"type":"Binary","goos":"darwin","goarch":"arm64","path":"a"},
 {"type":"Binary","goos":"darwin","goarch":"amd64","path":"b"},
 {"type":"Archive","goos":"darwin","goarch":"arm64","name":"x_darwin_arm64.tar.gz"},
 {"type":"Archive","goos":"darwin","goarch":"amd64","name":"x_darwin_amd64.tar.gz"}
]
JSON
bash darwin-gate.sh check-artifacts "$tmp/good.json" >/dev/null 2>&1 && ok "artifacts good" || bad "artifacts good"

cat > "$tmp/dup.json" <<'JSON'
[
 {"type":"Binary","goos":"darwin","goarch":"arm64","path":"a"},
 {"type":"Binary","goos":"darwin","goarch":"amd64","path":"b"},
 {"type":"Archive","goos":"darwin","goarch":"arm64","name":"dup.tar.gz"},
 {"type":"Archive","goos":"darwin","goarch":"amd64","name":"dup.tar.gz"}
]
JSON
bash darwin-gate.sh check-artifacts "$tmp/dup.json" >/dev/null 2>&1 && bad "artifacts dup should fail" || ok "artifacts dup fails"

cat > "$tmp/missing.json" <<'JSON'
[ {"type":"Binary","goos":"darwin","goarch":"arm64","path":"a"} ]
JSON
bash darwin-gate.sh check-artifacts "$tmp/missing.json" >/dev/null 2>&1 && bad "artifacts missing should fail" || ok "artifacts missing fails"

# --- probe (json) with a stub binary ---
cat > "$tmp/stub-good" <<'SH'
#!/usr/bin/env bash
echo '{"backend":"keychain","backend_source":"auto","credential_ref":"x/default"}'
SH
chmod +x "$tmp/stub-good"
SPEC='{"env_unset":["X_KEYRING_BACKEND"],"seed_config":{"path":"x/config.yml","content":"credential_ref: x/default\n"},"command":["--output","json","config","show"],"output":"json","assertions":{".backend":"keychain",".backend_source":"auto",".credential_ref":"x/default"}}'
bash darwin-gate.sh probe "$SPEC" "$tmp/stub-good" >/dev/null 2>&1 && ok "probe json pass" || bad "probe json pass"

cat > "$tmp/stub-bad" <<'SH'
#!/usr/bin/env bash
echo '{"backend":"file","backend_source":"config","credential_ref":"x/default"}'
SH
chmod +x "$tmp/stub-bad"
bash darwin-gate.sh probe "$SPEC" "$tmp/stub-bad" >/dev/null 2>&1 && bad "probe wrong backend should fail" || ok "probe wrong backend fails"

# --- probe (text) ---
cat > "$tmp/stub-text" <<'SH'
#!/usr/bin/env bash
echo "backend: keychain"
echo "source: auto"
SH
chmod +x "$tmp/stub-text"
TSPEC='{"command":["config","show"],"output":"text","match":["backend:\\s*keychain","source:\\s*auto"]}'
bash darwin-gate.sh probe "$TSPEC" "$tmp/stub-text" >/dev/null 2>&1 && ok "probe text pass" || bad "probe text pass"

echo "----"
if [ "$fails" -eq 0 ]; then echo "all darwin-gate tests passed"; else echo "$fails failed"; exit 1; fi
