#!/usr/bin/env bash
# Rebuilds and restarts the bot on the VM at a commit of master. See docs/auto-deploy.md.
#
# The CI `deploy` job runs this through a forced-command SSH key and passes the commit SHA, which sshd
# hands over in SSH_ORIGINAL_COMMAND. It also works by hand on the VM:
#   ./scripts/redeploy.sh              # deploy the latest origin/master
#   ./scripts/redeploy.sh <commit-sha> # deploy a specific commit of master, e.g. to roll back
#
# All the work happens inside main(), which bash reads in full before running it: the `git reset` below
# rewrites this very file, and bash would otherwise go on reading the new version mid-run.
set -euo pipefail

readonly HEALTH_CHECK_DELAY_SECONDS=30
readonly LOCK_WAIT_SECONDS=900
readonly LOCK_FILE=/tmp/racb-redeploy.lock

die() {
  echo "redeploy: $*" >&2
  exit 1
}

# Prints "<container id> <status> <restart count>" for the bot container, or nothing if there is none
bot_container_state() {
  local container_id
  container_id="$(docker compose ps --all --quiet bot)"
  if [[ -n $container_id ]]; then
    docker inspect --format '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$container_id"
  fi
}

main() {
  local requested_commit="${1:-${SSH_ORIGINAL_COMMAND:-}}"
  local repo_dir
  repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  cd "$repo_dir"

  # One deploy at a time, whether it came from CI or from a person on the VM
  exec 9>"$LOCK_FILE"
  flock --wait "$LOCK_WAIT_SECONDS" 9 || die "another deploy is still running"

  [[ -f .env ]] || die "$repo_dir/.env is missing"
  [[ "$(git symbolic-ref --quiet --short HEAD)" == master ]] \
    || die "the checkout in $repo_dir must be on branch master"
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] \
    || die "tracked files were edited on the VM; commit or discard them first (see git status)"
  # git reset replaces a file by deleting and recreating it, which needs write access to its directory.
  # Check up front, so a directory owned by someone else (e.g. after a `sudo git pull`) cannot leave
  # the checkout half-updated.
  local unwritable_dir
  unwritable_dir="$(find . -type d ! -writable -print -quit)"
  [[ -z $unwritable_dir ]] \
    || die "$(id -un) cannot write to $repo_dir/${unwritable_dir#./}; fix the ownership with:" \
      "sudo chown -R $(id -un): $repo_dir"

  git fetch --quiet origin master
  git merge-base --is-ancestor HEAD origin/master \
    || die "local master has commits that are not on origin/master"

  local target_commit
  if [[ -z $requested_commit ]]; then
    target_commit="$(git rev-parse origin/master)"
  elif [[ $requested_commit =~ ^[0-9a-f]{40}$ ]]; then
    target_commit="$requested_commit"
  else
    die "expected a full 40-character commit SHA, got '$requested_commit'"
  fi
  git merge-base --is-ancestor "$target_commit" origin/master \
    || die "$target_commit is not a commit of origin/master"

  echo "Deploying $(git log -1 --format='%h %s' "$target_commit")"
  # .env is git-ignored, so the reset leaves it alone
  git reset --quiet --hard "$target_commit"

  local state_before
  state_before="$(bot_container_state)"
  # Builds the new image first and only then replaces the container, so the bot is down for seconds
  docker compose up --detach --build

  echo "Waiting ${HEALTH_CHECK_DELAY_SECONDS}s to check that the bot stays up"
  sleep "$HEALTH_CHECK_DELAY_SECONDS"
  local state_after id_before restarts_before id_after status_after restarts_after
  state_after="$(bot_container_state)"
  read -r id_before _ restarts_before <<<"$state_before"
  read -r id_after status_after restarts_after <<<"$state_after"
  # Compose keeps the old container when nothing changed; then only new restarts count as a crash
  local allowed_restarts=0
  if [[ $id_after == "$id_before" ]]; then
    allowed_restarts="${restarts_before:-0}"
  fi
  if [[ $status_after != running || ${restarts_after:-0} -gt $allowed_restarts ]]; then
    docker compose logs --tail 50 bot
    die "the bot is not running cleanly (status '${status_after:-none}', restarts ${restarts_after:-0})"
  fi

  # Drop the image the previous deploy left behind, or the 30 GB disk slowly fills up
  docker image prune --force >/dev/null
  echo "Deployed $(git rev-parse --short HEAD); the bot is running."
}

main "$@"
exit
