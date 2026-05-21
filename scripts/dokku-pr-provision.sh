#!/usr/bin/env bash
# Provision (or update) an ephemeral Dokku app for a pull request.
# Usage: dokku-pr-provision.sh <pr-number> <git-ref>
#
# Idempotent: re-running with the same PR number just pushes the new commit.
# DB is snapshotted from prod only on first creation, so login state persists
# across pushes to the same PR.

set -euo pipefail

PR_NUMBER="${1:?pr number required}"
GIT_REF="${2:?git ref required}"

APP="au-supply-pr-${PR_NUMBER}"
HOST="pr-${PR_NUMBER}.dev.a-u.supply"
PROD_APP="au-supply"
PROD_STORAGE="/var/lib/dokku/data/storage/${PROD_APP}"
APP_STORAGE="/var/lib/dokku/data/storage/${APP}"

ssh_dokku() { ssh dokku "$@"; }

app_exists() {
  ssh_dokku apps:exists "${APP}" >/dev/null 2>&1
}

if ! app_exists; then
  echo "==> Creating ${APP}"
  ssh_dokku apps:create "${APP}"
  ssh_dokku domains:set "${APP}" "${HOST}"

  echo "==> Ensuring storage and snapshotting prod DB"
  ssh_dokku storage:ensure-directory "${APP}"
  # Snapshot prod DB into the new app's storage. WAL is checkpointed first
  # so the .db file is a complete, self-contained copy.
  ssh_dokku -- "
    set -e
    sqlite3 ${PROD_STORAGE}/au.db 'PRAGMA wal_checkpoint(TRUNCATE);' >/dev/null 2>&1 || true
    cp ${PROD_STORAGE}/au.db ${APP_STORAGE}/au.db
  "
  ssh_dokku storage:mount "${APP}" "${APP_STORAGE}:/app/data"

  # Share the prod model cache read-only so PR envs don't re-download models.
  ssh_dokku storage:mount "${APP}" \
    "/var/lib/dokku/data/storage/au-supply-model-cache:/var/lib/dokku/data/storage/au-supply-model-cache:ro"

  echo "==> Copying config from prod"
  # Copy non-secret env from prod, then override per-env values.
  PROD_CONFIG="$(ssh_dokku config:export "${PROD_APP}" --format=exports)"
  # shellcheck disable=SC2086
  ssh_dokku config:set --no-restart "${APP}" \
    SECRET_KEY="$(openssl rand -hex 32)" \
    MODEL_CACHE_DIR="/var/lib/dokku/data/storage/au-supply-model-cache" \
    STAGING="1" \
    PR_NUMBER="${PR_NUMBER}"
  # Re-apply AU_API_KEY from prod so admin endpoints work.
  AU_API_KEY=$(echo "${PROD_CONFIG}" | grep -E '^export AU_API_KEY=' | sed -E "s/^export AU_API_KEY='?([^']*)'?$/\1/" || true)
  if [ -n "${AU_API_KEY}" ]; then
    ssh_dokku config:set --no-restart "${APP}" AU_API_KEY="${AU_API_KEY}"
  fi

  # PR envs only need the web process. Skip the worker.
  ssh_dokku ps:scale --skip-deploy "${APP}" worker=0 web=1
fi

echo "==> Pushing ${GIT_REF} to ${APP}"
git push "dokku@204.168.201.89:${APP}" "${GIT_REF}:refs/heads/master" --force

echo "==> Enabling Let's Encrypt"
# Use the same email as prod (whatever's configured).
ssh_dokku letsencrypt:enable "${APP}" || \
  echo "letsencrypt:enable failed; check 'ssh dokku letsencrypt:set ${APP} email <addr>'"

echo "==> Done. URL: https://${HOST}"
