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

# Reclaim disk immediately:
#  1. Untag and remove the GHCR image we pulled for this PR (the preview's
#     pulled image tag, e.g. ghcr.io/.../au-supply-preview:pr-42-<sha>).
#  2. Sweep dangling layers left behind by the destroy.
# Both are best-effort: a failure here doesn't fail the workflow.
echo "==> Removing PR images from the host"
ssh_dokku -- "docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'au-supply-preview:pr-${PR_NUMBER}(-|\$)' | xargs -r docker rmi -f" >/dev/null 2>&1 || true

echo "==> Pruning dangling layers"
ssh_dokku -- docker image prune -f >/dev/null 2>&1 || true

echo "==> Done"
