#!/usr/bin/env bash
# Tear down an ephemeral PR Dokku app.
# Usage: dokku-pr-destroy.sh <pr-number>

set -euo pipefail

PR_NUMBER="${1:?pr number required}"
APP="au-supply-pr-${PR_NUMBER}"

ssh_dokku() { ssh dokku "$@"; }

if ! ssh_dokku apps:exists "${APP}" >/dev/null 2>&1; then
  echo "==> ${APP} does not exist; nothing to do"
  exit 0
fi

echo "==> Destroying ${APP}"
ssh_dokku apps:destroy "${APP}" --force

# Host-side docker image cache for the PR is reclaimed by the daily root cron
# `docker image prune -af --filter "until=24h"` on the dokku host (max 24h
# lag). Raw `docker` calls can't be made via the dokku SSH user (forced
# command), and the registry-side image is already deleted by the GHCR
# cleanup step in the destroy workflow job.

echo "==> Done"
