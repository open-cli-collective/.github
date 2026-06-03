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
if grep -Fxq "credential_ref: x/default" "$XDG_CONFIG_HOME/x/config.yml" 2>/dev/null; then
  echo '{"backend":"keychain","backend_source":"auto","credential_ref":"x/default"}'
else
  echo '{"backend":"file","backend_source":"missing-seed","credential_ref":"x/default"}'
fi
SH
chmod +x "$tmp/stub-good"
SPEC='{"env_unset":["X_KEYRING_BACKEND"],"seed_config":{"path":"x/config.yml","content":"credential_ref: x/default\n"},"command":["--output","json","config","show"],"output":"json","assertions":{".backend":"keychain",".backend_source":"auto",".credential_ref":"x/default"}}'
bash darwin-gate.sh probe "$SPEC" "$tmp/stub-good" >/dev/null 2>&1 && ok "probe json pass" || bad "probe json pass"

cat > "$tmp/stub-native-seed" <<'SH'
#!/usr/bin/env bash
if grep -Fxq "credential_ref: x/default" "$HOME/Library/Application Support/x/config.yml" 2>/dev/null; then
  echo '{"backend":"keychain","backend_source":"auto","credential_ref":"x/default"}'
else
  echo '{"backend":"file","backend_source":"missing-seed","credential_ref":"x/default"}'
fi
SH
chmod +x "$tmp/stub-native-seed"
NSPEC='{"env_unset":["X_KEYRING_BACKEND"],"seed_config":{"base":"native_user_config","path":"x/config.yml","content":"credential_ref: x/default\n"},"command":["config","show","--json"],"output":"json","assertions":{".backend":"keychain",".backend_source":"auto",".credential_ref":"x/default"}}'
bash darwin-gate.sh probe "$NSPEC" "$tmp/stub-native-seed" >/dev/null 2>&1 && ok "probe native user config seed pass" || bad "probe native user config seed pass"

TRAVERSAL_SPEC='{"seed_config":{"path":"../Library/Application Support/x/config.yml","content":"x\n"},"command":["config","show","--json"],"output":"json","assertions":{".backend":"keychain"}}'
bash darwin-gate.sh probe "$TRAVERSAL_SPEC" "$tmp/stub-good" >/dev/null 2>&1 && bad "probe traversal should fail" || ok "probe traversal fails"

NESTED_TRAVERSAL_SPEC='{"seed_config":{"path":"x/../config.yml","content":"x\n"},"command":["config","show","--json"],"output":"json","assertions":{".backend":"keychain"}}'
bash darwin-gate.sh probe "$NESTED_TRAVERSAL_SPEC" "$tmp/stub-good" >/dev/null 2>&1 && bad "probe nested traversal should fail" || ok "probe nested traversal fails"

ABSOLUTE_SPEC='{"seed_config":{"path":"/tmp/x/config.yml","content":"x\n"},"command":["config","show","--json"],"output":"json","assertions":{".backend":"keychain"}}'
bash darwin-gate.sh probe "$ABSOLUTE_SPEC" "$tmp/stub-good" >/dev/null 2>&1 && bad "probe absolute path should fail" || ok "probe absolute path fails"

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

# --- assert-dr (pure DR assertion; codesign -d -r- output on stdin) ---
LEAF=42e1afd02aae8666c09c15f171e1639550f301c2
IDENT=org.open-cli-collective.slck

# valid: requirement pins our leaf + identifier; the CDHash= metadata line must be ignored
cat <<DR | bash darwin-gate.sh assert-dr "$LEAF" "$IDENT" >/dev/null 2>&1 && ok "assert-dr valid (ignores CDHash= metadata)" || bad "assert-dr valid"
Executable=/x/slck
Identifier=org.open-cli-collective.slck
CodeDirectory v=20400 size=245 flags=0x0(none) hashes=2+2 location=embedded
CDHash=deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
Authority=Open CLI Code Signing
designated => identifier "org.open-cli-collective.slck" and certificate leaf = H"$LEAF"
DR

# uppercase leaf in the requirement vs lowercase expected → case-insensitive match
printf 'designated => identifier "%s" and certificate leaf = H"%s"\n' "$IDENT" "42E1AFD02AAE8666C09C15F171E1639550F301C2" \
  | bash darwin-gate.sh assert-dr "$LEAF" "$IDENT" >/dev/null 2>&1 && ok "assert-dr case-insensitive leaf" || bad "assert-dr case-insensitive leaf"

# ad-hoc (requirement pins cdhash) → must fail
printf 'designated => cdhash H"%s"\n' "$LEAF" \
  | bash darwin-gate.sh assert-dr "$LEAF" "$IDENT" >/dev/null 2>&1 && bad "assert-dr ad-hoc cdhash should fail" || ok "assert-dr ad-hoc cdhash fails"

# wrong leaf hash → must fail
printf 'designated => identifier "%s" and certificate leaf = H"%s"\n' "$IDENT" "ffffffffffffffffffffffffffffffffffffffff" \
  | bash darwin-gate.sh assert-dr "$LEAF" "$IDENT" >/dev/null 2>&1 && bad "assert-dr wrong leaf should fail" || ok "assert-dr wrong leaf fails"

# wrong identifier → must fail
printf 'designated => identifier "%s" and certificate leaf = H"%s"\n' "org.open-cli-collective.evil" "$LEAF" \
  | bash darwin-gate.sh assert-dr "$LEAF" "$IDENT" >/dev/null 2>&1 && bad "assert-dr wrong identifier should fail" || ok "assert-dr wrong identifier fails"

# no designated => line → must fail
printf 'Identifier=org.open-cli-collective.slck\nAuthority=Open CLI Code Signing\n' \
  | bash darwin-gate.sh assert-dr "$LEAF" "$IDENT" >/dev/null 2>&1 && bad "assert-dr missing requirement should fail" || ok "assert-dr missing requirement fails"

echo "----"
if [ "$fails" -eq 0 ]; then echo "all darwin-gate tests passed"; else echo "$fails failed"; exit 1; fi
