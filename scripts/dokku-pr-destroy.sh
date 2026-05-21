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
ssh_dokku --force apps:destroy "${APP}"
echo "==> Done"
