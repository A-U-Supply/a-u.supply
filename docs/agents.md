# Agents

How AI coding agents are wired into this repo. Read [`../AGENTS.md`](../AGENTS.md) first — that's the canonical rule sheet. This page covers the mechanics.

## `.claude/` — repo-local agent config

```
.claude/
  settings.json        — committed; hooks declaration
  settings.local.json  — gitignored; per-machine Bash allowlist
  hooks/
    block-master-commit.sh  — PreToolUse hook (see below)
  worktrees/             — isolated checkouts for parallel branch work
```

### `settings.json` (committed)

Declares the project-wide `PreToolUse` hook that blocks committing to `master` or `main`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/block-master-commit.sh" }]
      }
    ]
  }
}
```

### `settings.local.json` (gitignored)

Per-developer Bash allowlist that suppresses permission prompts for routine commands (`git push`, `gh pr`, `ssh dokku`, etc.). Not shared — each contributor's machine builds their own. Use `/permissions` in Claude Code, or the `fewer-permission-prompts` skill, to extend it.

### Hook: `block-master-commit.sh`

PreToolUse hook fired before every `Bash` call. Reads the proposed command, checks for `git ... commit`, and if the current branch is `master` or `main`, exits with status 2 (block) and a stderr message. This is the technical enforcement behind the "all work via PRs" rule.

Implication: an agent must always be on a feature branch before committing. Hence the worktree workflow below.

## Worktree workflow

The user's standing rule (also documented in [`../AGENTS.md`](../AGENTS.md)) is: **never switch branches in the primary checkout**. Always make a worktree.

```bash
# Always fetch first — origin/master may be ahead of your local
git fetch origin master

# Create a worktree on a new branch off origin/master
git worktree add .claude/worktrees/<slug> -b <slug> origin/master

# Work inside the worktree
cd .claude/worktrees/<slug>
…edit files…
git add …
git commit -m "…"
git push -u origin <slug>
gh pr create --title "…" --body "…"

# When the PR is merged, clean up
cd /home/tube/github/a-u.supply   # back to primary
git worktree remove .claude/worktrees/<slug>
git branch -D <slug>              # local branch cleanup
```

### Why worktrees, not branch switching

- The primary checkout stays on `master` and reflects what's deployed.
- Parallel work doesn't stomp on each other — each worktree has its own file tree, its own dev server, its own dirty state.
- The pre-commit hook still applies (it checks the current branch, which is the worktree's branch).
- You can have many worktrees open at once. The existing ones live alongside `.claude/worktrees/` already.

### Naming

Use a short kebab-case slug that matches the branch name. Examples already in the tree: `add-sparagmos-app`, `latents-fix`, `slack+fold-thread-notifs`. There's no enforced prefix; the existing convention is "describe the change."

## Slash commands and skills

Skills are the Claude Code mechanism for invoking specialized prompts (e.g. `/review`, `/security-review`, `/init`). They're not committed to the repo — they live in the Claude Code installation and user settings.

Useful skills for working in this repo:

| Skill | When |
|-------|------|
| `/review` | After implementing a non-trivial change in a worktree, before opening a PR |
| `/security-review` | Anything touching auth, file upload, request handling |
| `/ultrareview` | User-invoked only — multi-agent cloud review of a branch or PR. Cannot be invoked by an agent. |
| `fewer-permission-prompts` | Cuts noise by adding routine Bash patterns to the local allowlist |
| `update-config` | Edits `.claude/settings.json` for hooks / env vars / permissions |

Plus the `/loop` and `/schedule` skills for recurring tasks, and the project-specific `init` skill for bootstrapping a `CLAUDE.md` (which in this repo is a 1-line pointer to `AGENTS.md`).

## Memory

Claude Code maintains a per-project memory at `~/.claude/projects/<project>/memory/` containing user preferences, feedback, project context, and reference pointers. Across sessions, this is what lets the agent remember things like "use worktrees," "default to production," "redact MCA."

Other agent tools (Cursor, Codex CLI, Aider) don't share this memory. They read `AGENTS.md` and whatever else they're configured to read, but they won't know the per-user history. If you're switching tools and notice the agent lacks context, surface the relevant memory by referencing the doc in [`../AGENTS.md`](../AGENTS.md) or `docs/`.

## Plans

Non-trivial changes start with a plan file at `docs/plans/YYYY-MM-DD-<slug>.md`. The lifecycle is spelled out in [`plans/README.md`](plans/README.md). Plans that have shipped carry a `Status: shipped — PR #N` line near the top.

## What lives outside `.claude/`

- **`AGENTS.md`** (root) — the rule sheet every agent reads
- **`CLAUDE.md`** (root) — 1-line pointer to `AGENTS.md` (Claude Code still reads it specifically)
- **`docs/`** — the deep documentation tree, indexed from `AGENTS.md` and `docs/README.md`
- **GitHub Actions** (`.github/workflows/`) — CI, deploy, one-shot helpers. Not agent-facing.

## Related

- [`../AGENTS.md`](../AGENTS.md) — canonical rules
- [`development.md`](development.md) — local dev mechanics (the human side)
- [`plans/README.md`](plans/README.md) — plan lifecycle
- [`operations.md`](operations.md) — server-side `manage.py` ops
