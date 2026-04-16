#!/usr/bin/env bash
# PreToolUse hook: block `git commit` when on master/main.
# Reads PreToolUse JSON from stdin, inspects the Bash tool's command.
# Exit 2 = block with stderr message shown to Claude.

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')

# Match `git commit` but not `git commit-tree` etc.
if printf '%s' "$cmd" | grep -qE '(^|[^A-Za-z0-9_])git[[:space:]]+(-[^[:space:]]+[[:space:]]+)*commit([[:space:]]|$)'; then
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$branch" = "master" ] || [ "$branch" = "main" ]; then
    echo "BLOCKED: Do not commit directly to $branch. Create a feature branch (or worktree) first." >&2
    exit 2
  fi
fi
exit 0
