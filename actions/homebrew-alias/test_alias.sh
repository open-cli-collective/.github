#!/usr/bin/env bash
# Portable unit tests for alias.sh render.
set -uo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d); fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails+1)); }

cat > "$tmp/jtk.rb" <<'RB'
cask "jtk" do
  version "1.2.34"
  sha256 arm: "aaa", intel: "bbb"
  url "https://github.com/open-cli-collective/jira-ticket-cli/releases/download/jtk-v1.2.34/jtk_1.2.34_darwin_#{arch}.tar.gz"
  name "jtk"
  desc "Jira ticket CLI"
  binary "jtk"
end
RB

# --- happy path: only the cask stanza token is renamed ---
out=$(bash alias.sh render "$tmp/jtk.rb" jtk jira-ticket-cli)
echo "$out" | grep -q '^cask "jira-ticket-cli" do$' && ok "stanza renamed" || bad "stanza renamed"
echo "$out" | grep -q '^  binary "jtk"$'            && ok "binary preserved" || bad "binary preserved"
echo "$out" | grep -q '^  name "jtk"$'              && ok "name preserved" || bad "name preserved"
echo "$out" | grep -q 'jtk-v1.2.34/jtk_1.2.34'      && ok "url preserved" || bad "url preserved"
[ "$(echo "$out" | grep -c '^cask ')" = 1 ]         && ok "single cask stanza" || bad "single cask stanza"

# --- missing stanza must fail (don't ship a mislabeled duplicate) ---
cat > "$tmp/other.rb" <<'RB'
cask "something-else" do
  binary "jtk"
end
RB
bash alias.sh render "$tmp/other.rb" jtk jira-ticket-cli >/dev/null 2>&1 \
  && bad "missing stanza should fail" || ok "missing stanza fails"

# --- missing file must fail ---
bash alias.sh render "$tmp/nope.rb" jtk x >/dev/null 2>&1 \
  && bad "missing file should fail" || ok "missing file fails"

echo "----"
if [ "$fails" -eq 0 ]; then echo "all alias tests passed"; else echo "$fails failed"; exit 1; fi
