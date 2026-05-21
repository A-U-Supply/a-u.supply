#!/usr/bin/env bash
# Provision (or update) an ephemeral Dokku app for a pull request from a
# pre-built container image.
#
# Usage: dokku-pr-provision.sh <pr-number> <image-ref>
#
# image-ref is a fully-qualified container image (e.g.
# ghcr.io/.../au-supply-preview:pr-42-abc). The image is built and pushed
# by the GH Action *before* this runs; Dokku just pulls it. This keeps the
# heavy build off the prod host (an in-host build was the cause of the
# original OOM incident).
#
# Idempotent: re-running with the same PR number just redeploys the image.

set -euo pipefail

PR_NUMBER="${1:?pr number required}"
IMAGE="${2:?image ref required}"

APP="au-supply-pr-${PR_NUMBER}"
HOST="pr-${PR_NUMBER}.dev.a-u.supply"
PROD_APP="au-supply"
APP_STORAGE="/var/lib/dokku/data/storage/${APP}-data"
MODEL_CACHE="/var/lib/dokku/data/storage/au-supply-model-cache"

ssh_dokku() { ssh dokku "$@"; }

app_exists() { ssh_dokku apps:exists "${APP}" >/dev/null 2>&1; }

if ! app_exists; then
  echo "==> Creating ${APP}"
  ssh_dokku apps:create "${APP}"
  ssh_dokku domains:set "${APP}" "${HOST}"

  ssh_dokku storage:ensure-directory "${APP}-data"
  ssh_dokku storage:mount "${APP}" "${APP_STORAGE}:/app/data"
  # Share prod's model cache read-only so previews don't re-download models.
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

  # Previews only need the web process.
  ssh_dokku ps:scale --skip-deploy "${APP}" worker=0 web=1
fi

echo "==> Deploying ${IMAGE} to ${APP}"
# git:from-image's healthcheck can flap on first deploy because uvicorn races
# itself initialising a fresh SQLite DB before the bind mount has settled.
# Docker's on-failure restart policy recovers, so we don't fail the workflow
# on that initial healthcheck timeout -- we wait for the container to come up.
ssh_dokku git:from-image "${APP}" "${IMAGE}" || true

echo "==> Waiting for ${APP} web to be running"
DEPLOYED=0
# Up to 5 minutes. The container's restart-on-failure:10 policy needs time to
# recover from transient startup issues; we don't want to declare failure
# before Docker has stopped retrying.
for i in $(seq 1 60); do
  if ssh_dokku ps:report "${APP}" 2>/dev/null | grep -qE 'Status web 1:[[:space:]]+running'; then
    DEPLOYED=1
    echo "    web is running (after ${i} checks)"
    break
  fi
  sleep 5
done

if [ "${DEPLOYED}" -ne 1 ]; then
  echo "ERROR: ${APP} web did not reach running state within 300s"
  ssh_dokku logs "${APP}" --tail 80 || true
  exit 1
fi

echo "==> Enabling Let's Encrypt"
ssh_dokku letsencrypt:enable "${APP}" || \
  echo "letsencrypt:enable failed; check that ${HOST} resolves and the global LE email is set"

echo "==> Done. URL: https://${HOST}"
