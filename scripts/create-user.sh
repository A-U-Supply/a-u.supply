#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "Usage: $0 <email> <password> <name> [role]"
  echo "  role defaults to 'member' (options: member, admin)"
  exit 1
fi

ssh dokku run au-supply .venv/bin/python manage.py create-user "$@"
