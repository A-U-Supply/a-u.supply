#!/usr/bin/env bash
# Provision (or update) an ephemeral Dokku app for a pull request.
# Usage: dokku-pr-provision.sh <pr-number> <image-ref>
#
# image-ref is a fully-qualified container image (e.g. ghcr.io/.../foo:pr-42-abc).
# The image is built and pushed to a registry by the GH Action *before* this
# runs; Dokku just pulls it. This avoids spending RAM on a build on the prod
# host (the original incident was a build OOMing prod).
#
# Idempotent: re-running with the same PR number just deploys the new image.
# DB is snapshotted from prod only on first creation, so login state persists
# across pushes to the same PR.

set -euo pipefail

PR_NUMBER="${1:?pr number required}"
IMAGE="${2:?image ref required}"

APP="au-supply-pr-${PR_NUMBER}"
HOST="pr-${PR_NUMBER}.dev.a-u.supply"
PROD_APP="au-supply"
APP_STORAGE="/var/lib/dokku/data/storage/${APP}-data"
MODEL_CACHE="/var/lib/dokku/data/storage/au-supply-model-cache"

ssh_dokku() { ssh dokku "$@"; }
# Binary-safe: -T disables pseudo-tty so byte streams don't get mangled.
ssh_dokku_bin() { ssh -T dokku "$@"; }

app_exists() { ssh_dokku apps:exists "${APP}" >/dev/null 2>&1; }

NEW_APP=0
if ! app_exists; then
  echo "==> Creating ${APP}"
  ssh_dokku apps:create "${APP}"
  ssh_dokku domains:set "${APP}" "${HOST}"

  ssh_dokku storage:ensure-directory "${APP}-data"
  ssh_dokku storage:mount "${APP}" "${APP_STORAGE}:/app/data"
  # Share the prod model cache read-only so PR envs don't re-download models.
  ssh_dokku storage:mount "${APP}" "${MODEL_CACHE}:${MODEL_CACHE}:ro"

  echo "==> Copying config from prod"
  PROD_CONFIG="$(ssh_dokku config:export "${PROD_APP}" --format=exports)"
  ssh_dokku config:set --no-restart "${APP}" \
    SECRET_KEY="$(openssl rand -hex 32)" \
    MODEL_CACHE_DIR="${MODEL_CACHE}" \
    STAGING="1" \
    PR_NUMBER="${PR_NUMBER}"
  AU_API_KEY=$(echo "${PROD_CONFIG}" | grep -E '^export AU_API_KEY=' | sed -E "s/^export AU_API_KEY='?([^']*)'?$/\1/" || true)
  if [ -n "${AU_API_KEY}" ]; then
    ssh_dokku config:set --no-restart "${APP}" AU_API_KEY="${AU_API_KEY}"
  fi

  # PR envs only need the web process. Skip the worker.
  ssh_dokku ps:scale --skip-deploy "${APP}" worker=0 web=1

  NEW_APP=1
fi

echo "==> Deploying ${IMAGE} to ${APP}"
ssh_dokku git:from-image "${APP}" "${IMAGE}"

# Snapshot prod's au.db into the new app on first create only. Done after the
# deploy so the destination container exists. The file is root-owned on the
# host so we go through `dokku run`, which mounts the same volume. Best-effort:
# failure leaves the preview with an empty DB rather than killing the workflow.
if [ "${NEW_APP}" = "1" ]; then
  echo "==> Snapshotting prod DB into ${APP}"
  SNAP=/tmp/au.snapshot.${PR_NUMBER}.db
  if ssh_dokku_bin run "${PROD_APP}" cat /app/data/au.db > "${SNAP}" 2>/dev/null && [ -s "${SNAP}" ]; then
    if ssh_dokku_bin run "${APP}" tee /app/data/au.db < "${SNAP}" >/dev/null; then
      ssh_dokku ps:restart "${APP}"
      echo "    snapshot installed ($(stat -c%s "${SNAP}") bytes)"
    else
      echo "    WARN: snapshot write failed; preview will use the empty DB the app initialised"
    fi
  else
    echo "    WARN: snapshot read failed or empty; preview will use an empty DB"
  fi
  rm -f "${SNAP}"
fi

echo "==> Enabling Let's Encrypt"
ssh_dokku letsencrypt:enable "${APP}" || \
  echo "letsencrypt:enable failed; check that ${HOST} resolves and the global LE email is set"

echo "==> Done. URL: https://${HOST}"
