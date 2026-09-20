#!/usr/bin/env bash
# Portable unit tests for modernize.sh render.
set -uo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d); fails=0
trap 'rm -rf "$tmp"' EXIT
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

cat > "$tmp/legacy.rb" <<'RB'
cask "example" do
  preflight do
    system_command "prepare"
  end
  postflight do
    system_command "/usr/bin/xattr", args: ["-dr", "com.apple.quarantine", "#{staged_path}/example"]
  end
  uninstall_preflight do
    system_command "unprepare"
  end
  uninstall_postflight do
    system_command "unfinish"
  end
  # postflight do must not change inside a comment
end
RB

out=$(bash modernize.sh render "$tmp/legacy.rb")
for stanza in preflight_steps postflight_steps uninstall_preflight_steps uninstall_postflight_steps; do
  echo "$out" | grep -q "^  ${stanza} do$" && ok "$stanza rendered" || bad "$stanza rendered"
done
echo "$out" | grep -q '^  # postflight do must not change inside a comment$' \
  && ok "comment preserved" || bad "comment preserved"
echo "$out" | grep -q 'run "/usr/bin/xattr", args: \["-dr", "com.apple.quarantine", "example"\], base: :staged_path' \
  && ok "command converted to install-step DSL" || bad "command converted to install-step DSL"
echo "$out" | grep -q 'system_command' \
  && bad "legacy command should be absent" || ok "legacy command absent"

printf '%s\n' "$out" > "$tmp/modern.rb"
rerendered=$(bash modernize.sh render "$tmp/modern.rb")
[ "$out" = "$rerendered" ] && ok "render is idempotent" || bad "render is idempotent"

bash modernize.sh render "$tmp/missing.rb" >/dev/null 2>&1 \
  && bad "missing file should fail" || ok "missing file fails"

echo "----"
if [ "$fails" -eq 0 ]; then echo "all modernization tests passed"; else echo "$fails failed"; exit 1; fi
