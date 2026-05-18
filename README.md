# a-u.supply

Web catalog and admin platform for [A-U.Supply](https://a-u.supply) — Audio Units Division. Astro frontend + FastAPI backend, deployed via Dokku.

## Stack

| Layer      | Technology |
|------------|------------|
| Frontend   | Astro 5 (static), Svelte 5 islands |
| Styling    | Tailwind 4 + bits-ui, brutalist tokens |
| Backend    | FastAPI (Python 3.12+), SQLAlchemy, SQLite |
| Search     | Meilisearch (4 indices: images, audio, video, emulsion) |
| Discussion | Lemmy (private fold instance, proxied through FastAPI) |
| Auth       | JWT cookies + API key Bearer tokens |
| Deploy     | Docker → Dokku, GitHub Actions |

## Quick start

```bash
# Prerequisites: Python 3.12+, Node 22+, uv
git clone git@github.com:A-U-Supply/a-u.supply.git
cd a-u.supply
npm install
uv sync

# Two terminals:
npm run dev:api        # FastAPI on :5000
npm run dev            # Astro on :4321 (proxies /api → :5000)
```

Browse to <http://localhost:4321>. Full setup in [`docs/development.md`](docs/development.md).

## Contributing

Everyone contributes through AI coding agents — describe what you want, the agent does the coding. **Never commit directly to `master`**; every change goes through a PR (a pre-commit hook enforces this).

Read [`AGENTS.md`](AGENTS.md) first — it's the canonical guide for any AI coding agent working in this repo (Claude Code, Cursor, Codex CLI, Aider, etc.).

## Documentation

The full index lives in [`docs/README.md`](docs/README.md). Quick links:

**Start here**
- [`AGENTS.md`](AGENTS.md) — rules, workflow, hard gotchas, doc index for AI agents
- [`docs/glossary.md`](docs/glossary.md) — admin nomenclature (Auspices, Stacks, Hecatomb, …) decoded

**Build and ship**
- [`docs/development.md`](docs/development.md) — local dev, testing, formatting, worktree workflow
- [`docs/architecture.md`](docs/architecture.md) — stack, directory layout, data flow
- [`docs/frontend.md`](docs/frontend.md) — UI kit (Tailwind 4 + bits-ui + brutalist tokens), Svelte components, event bus
- [`docs/api.md`](docs/api.md) — REST API authentication, scopes, endpoint groups
- [`docs/deployment.md`](docs/deployment.md) — Dokku auto-deploy, SSL, legacy routing
- [`docs/operations.md`](docs/operations.md) — `manage.py` subcommands, SSH gotchas

**Section-specific**
- [`docs/atelier.md`](docs/atelier.md) — the Atelier section (Punctum, Photism)
- [`docs/bots.md`](docs/bots.md) — App Runner / TOML manifests / how to add a bot
- [`docs/player.md`](docs/player.md) — persistent audio player events
- [`docs/agents.md`](docs/agents.md) — agent-side config (`.claude/`, hooks, worktrees, skills)

**History**
- [`docs/plans/`](docs/plans/) — per-feature design plans
- [`docs/history/`](docs/history/) — frozen pre-implementation design docs

**Interactive API docs:** [`/docs`](https://a-u.supply/docs) (Swagger) and [`/redoc`](https://a-u.supply/redoc).

## License

All rights reserved.
