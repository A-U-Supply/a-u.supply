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

# Immediately reclaim the disk for this app's image + layers. apps:destroy
# removes the tagged image, this sweeps the orphan layers left behind without
# waiting for the daily cron.
echo "==> Pruning dangling images"
ssh_dokku -- docker image prune -f >/dev/null 2>&1 || true

echo "==> Done"
