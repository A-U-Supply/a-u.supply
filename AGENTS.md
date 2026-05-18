# AGENTS.md — A-U.SUPPLY

Canonical guide for any AI coding agent working in this repo (Claude Code, Cursor, Codex CLI, Aider, etc.). Humans should read [`README.md`](README.md) first.

This file is intentionally lean. Everything deeper lives in [`docs/`](docs/) — see the index at the bottom.

## What this repo is

The web app for [a-u.supply](https://a-u.supply) — pages, API, catalog, player, admin UI. Astro frontend + FastAPI backend, deployed to Dokku.

It is **not**: bot/tool code, audio CLIs, search-engine internals, or anything else that runs as its own service. Those live in their own repos and connect via a TOML manifest in `apps/` (see [`docs/bots.md`](docs/bots.md)).

## Hard rules

1. **All work goes through PRs.** Never commit to `master`. A pre-commit hook blocks it locally.
2. **Always use a worktree** for implementation work — `git worktree add .claude/worktrees/<slug> -b <slug> origin/master`. Don't switch branches in the main checkout.
3. **One framework.** All pages are `.astro`. The player is the one Svelte 5 island. Don't introduce a new template engine.
4. **Use `uv` for Python.** Never `pip`. `uv sync`, `uv run python ...`, `uv lock`.
5. **Plan before non-trivial work.** Drop a plan at `docs/plans/YYYY-MM-DD-<slug>.md` first. Open a plan-only PR if the change is big enough to warrant approach review. See [`docs/plans/README.md`](docs/plans/README.md).
6. **Format before committing.** `npm run format`.
7. **URL-encode product codes.** They contain `#`, spaces, dots. `encodeURIComponent()` in JS, `quote(code, safe='')` in Python.
8. **Never pass inline Python through `ssh dokku run` / `ssh dokku enter`.** Dokku mangles quotes — add a `manage.py` subcommand instead. See [`docs/operations.md`](docs/operations.md).
9. **Stop and ask before destructive ops.** Force-push, `reset --hard`, dropping branches, deleting files you didn't create — confirm first.
10. **`ssh dokku`** is an SSH alias. Never `ssh dokku@<ip>`.

## Where to find things

| You want… | Read |
|-----------|------|
| Local dev setup | [`docs/development.md`](docs/development.md) |
| Stack, directory layout, data flow | [`docs/architecture.md`](docs/architecture.md) |
| Frontend: UI kit, design tokens, components, naming | [`docs/frontend.md`](docs/frontend.md) |
| What "Auspices", "Stacks", "Hecatomb", etc. mean | [`docs/glossary.md`](docs/glossary.md) |
| The Atelier section (Punctum, Photism) | [`docs/atelier.md`](docs/atelier.md) |
| API auth + endpoint groups | [`docs/api.md`](docs/api.md) |
| Bots / App Runner / TOML manifests | [`docs/bots.md`](docs/bots.md) |
| Persistent audio player events | [`docs/player.md`](docs/player.md) |
| Latents (pre-release workspace) + fold/Lemmy threads | [`docs/plans/2026-05-15-latents.md`](docs/plans/2026-05-15-latents.md) |
| Deploy mechanics, Dokku, SSH, legacy routing | [`docs/deployment.md`](docs/deployment.md) |
| `manage.py` subcommands + server-side ops | [`docs/operations.md`](docs/operations.md) |
| Hooks, worktrees, slash commands, agent setup | [`docs/agents.md`](docs/agents.md) |
| Per-feature plans (current + historical) | [`docs/plans/`](docs/plans/) |
| Pre-implementation design archaeology | [`docs/history/`](docs/history/) |

## Workflow at a glance

```bash
# 1. Worktree off the freshest origin/master
git fetch origin master
git worktree add .claude/worktrees/<slug> -b <slug> origin/master

# 2. Work in the worktree. Format. Test.
npm run format
uv run pytest

# 3. Open a PR — never push to master directly.
gh pr create --title "..." --body "..."
```

Merging to `master` auto-deploys to production via GitHub Actions → Dokku.

## What lives where (top-level)

```
src/                  — Astro frontend (pages, layouts, components, styles, lib)
server/               — FastAPI routers + models + integrations (Python package)
main.py worker.py     — entry points (kept at root; deploy configs reference them)
cli.py manage.py      — CLIs (kept at root)
apps/*.toml           — bot manifests (pointers to Docker images in other repos)
tests/                — pytest suite
data/                 — SQLite DB + media (not committed; mounted in prod)
docs/                 — these guides
.claude/              — agent config (hooks, worktrees, settings)
.github/workflows/    — CI / deploy
```

See [`docs/architecture.md`](docs/architecture.md) for the full layout and what each module does.

## Memory note

Project-specific feedback the user has given lives in agent memory across sessions. If you're a Claude Code agent, you have it loaded. If you're not — that history isn't visible to you; you'll need to ask the user about preferences explicitly the first time they come up.
