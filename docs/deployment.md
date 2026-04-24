# Deployment

How code ships to production and how to operate the live instance.

## Auto-deploy

Push to `master` → GitHub Actions pushes to Dokku → Docker image rebuilds → container restarts → live at <https://a-u.supply>.

Never commit directly to `master`. Every change goes through a PR (see [`../CLAUDE.md`](../CLAUDE.md)). A git hook blocks direct commits to master locally.

## Docker build

Multi-stage build:

1. **Node stage** — installs npm deps, runs `astro build`, produces `dist/`.
2. **Python stage** — installs Python deps via uv, copies app code + `dist/` from the Node stage, includes `git` (for legacy-site webhook pulls) and `ffmpeg` (for audio processing).

The `Dockerfile` and `Procfile` must stay at the repo root — Dokku's buildpack expects them there.

## Data persistence

SQLite DB, release media, and search media are stored in persistent volumes that survive container rebuilds. The legacy site lives in a separate persisted volume. **Merging a PR does not lose data.**

## SSL

Auto-managed via Let's Encrypt. No manual renewal needed.

## SSH access

Always use the `ssh dokku` alias. Do not SSH with an explicit user or IP.

```bash
# Interactive shell on the web container
ssh -t dokku enter au-supply web bash
```

### Running a command on the server

Pick the right flavor:

```bash
# One-off command inside a fresh container (short-lived, clean slate)
ssh dokku run au-supply .venv/bin/python manage.py <subcommand>

# Command inside the currently running web container
ssh dokku enter au-supply web .venv/bin/python manage.py <subcommand>
```

Use `manage.py` subcommands or dedicated helpers rather than passing inline Python.

### Dokku CLI pitfall

**Never pipe inline Python or multi-line strings through `ssh dokku run` or `ssh dokku enter`.** Dokku's argument parser mangles quotes, backslashes, and other special characters — strings arrive at the container malformed. Add the operation as a `manage.py` subcommand (or a small helper script) and invoke *that* instead.

## GitHub Actions

Workflows in `.github/workflows/`:

- `deploy.yml` — pushes to Dokku on merge to master
- `create-user.yml` — runs `cli.py create-user` on the server (manual trigger)
- `seed-catalog.yml` / `setup-storage.yml` — one-shot helpers that invoke `seed_catalog.py`

These reference entry-point script names directly (`cli.py`, `seed_catalog.py`, `main:app`). If you ever rename or relocate those files, update the workflows in the same PR.

## Dual-repo routing

Two GitHub repos serve the same domain:

| Repo | Purpose | Served from |
|------|---------|-------------|
| `A-U-Supply/a-u.supply` | Astro + FastAPI app | `/` (primary) |
| `A-U-Supply/ausupply.github.io` | Legacy static site | Fallback for unmatched paths |

### Routing priority

FastAPI resolves URLs in this order:

1. **API routes** — `/api/*` (explicit handlers)
2. **Explicit pages** — `/` (Astro homepage), `/the-expenditure` (legacy homepage alias)
3. **Webhook endpoints** — `/hooks/legacy`, `/hooks/deploy`
4. **Astro static files** — anything in `dist/`
5. **Legacy fallback** — if nothing above matched, try the legacy site directory

If both Astro and legacy have a file at the same path, Astro wins.

### The `/the-expenditure` alias

The legacy site's `index.html` is NOT served at `/` — the Astro homepage owns that. Instead it's aliased to `/the-expenditure`. All other legacy paths work unchanged. Relative asset paths in legacy HTML resolve correctly because legacy files are served from the root.

### Legacy repo auto-deploy

Push to `ausupply.github.io` → GitHub webhook hits `POST /hooks/legacy` → `git pull` in the legacy site directory. No container restart needed.
