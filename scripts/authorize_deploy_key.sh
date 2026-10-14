#!/usr/bin/env bash
# Run on the VM, as the account that owns the checkout and runs `docker compose`. See docs/auto-deploy.md.
#
# Lets the CI deploy key log in, but only to run scripts/redeploy.sh: with a forced command, sshd runs
# that script whatever command the client asks for, and `restrict` turns off port, agent and X11
# forwarding and the pty. Re-running it replaces the previous deploy key, which is how the key is rotated.
#
# Usage: scripts/authorize_deploy_key.sh 'ssh-ed25519 AAAA... racb-github-deploy'
# The last line it prints is "HOST_KEY: <type> <key>", the value of the VM_SSH_HOST_KEY variable.
set -euo pipefail

readonly KEY_COMMENT=racb-github-deploy
readonly HOST_KEY_FILE=/etc/ssh/ssh_host_ed25519_key.pub

die() {
  echo "authorize_deploy_key: $*" >&2
  exit 1
}

public_key="${1:-}"
public_key_pattern="^ssh-ed25519 [A-Za-z0-9+/=]+ $KEY_COMMENT\$"
[[ $public_key =~ $public_key_pattern ]] \
  || die "usage: $0 'ssh-ed25519 AAAA... $KEY_COMMENT'"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
redeploy_script="$repo_dir/scripts/redeploy.sh"
[[ -x $redeploy_script ]] || die "$redeploy_script is missing or not executable; pull master first"
docker info >/dev/null 2>&1 \
  || die "$(id -un) cannot use Docker; run this as the account that runs docker compose"

authorized_keys="$HOME/.ssh/authorized_keys"
install -d -m 700 "$HOME/.ssh"
touch "$authorized_keys"
(
  umask 077
  # Keep every other key, including the ones the Google guest agent manages, and drop the old deploy key
  grep -v -F " $KEY_COMMENT" "$authorized_keys" >"$authorized_keys.new" || true
  printf 'command="%s",restrict %s\n' "$redeploy_script" "$public_key" >>"$authorized_keys.new"
)
mv "$authorized_keys.new" "$authorized_keys"

echo "Authorized the deploy key for $(id -un), restricted to $redeploy_script"
echo "HOST_KEY: $(cut -d ' ' -f 1,2 "$HOST_KEY_FILE")"
