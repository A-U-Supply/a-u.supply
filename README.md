# a-u.supply

Web catalog and admin platform for [A-U.Supply](https://a-u.supply) — Audio Units Division. Astro frontend + FastAPI backend, deployed via Dokku.

## Stack

| Layer    | Technology |
|----------|------------|
| Frontend | Astro 5 (static), Svelte 5 island for the audio player |
| Backend  | FastAPI (Python 3.12+), SQLAlchemy, SQLite |
| Search   | Meilisearch |
| Auth     | JWT cookies + API key Bearer tokens |
| Deploy   | Docker → Dokku, GitHub Actions |

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

Everyone contributes through **Claude Code** — describe what you want, Claude does the coding. Never commit directly to `master`; every change goes through a PR. Read [`CLAUDE.md`](CLAUDE.md) first — it's the primary contributor guide.

## Documentation

- [`CLAUDE.md`](CLAUDE.md) — contributor rules, workflow, everyday gotchas
- [`docs/development.md`](docs/development.md) — local dev, testing, formatting, user management
- [`docs/architecture.md`](docs/architecture.md) — stack, directory layout, data flow
- [`docs/deployment.md`](docs/deployment.md) — Dokku auto-deploy, SSH, data persistence
- [`docs/api.md`](docs/api.md) — API authentication, scopes, endpoint overview
- [`docs/bots.md`](docs/bots.md) — TOML manifests, how to add a bot
- [`docs/player.md`](docs/player.md) — audio player events + queue pattern
- [`docs/plans/`](docs/plans/) — per-feature design plans
- [`docs/history/`](docs/history/) — frozen pre-implementation design docs

**Interactive API docs:** [`/docs`](https://a-u.supply/docs) (Swagger) and [`/redoc`](https://a-u.supply/redoc).

## License

All rights reserved.
