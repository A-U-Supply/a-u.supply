# A-U.SUPPLY — Project Guide

## How We Work

This repo is the web app for [a-u.supply](https://a-u.supply). Everyone contributes through **Claude Code** — describe what you want, Claude does the coding.

### All Work Via PRs

Never commit directly to master. Every change follows this flow:

1. Claude creates a feature branch (using a worktree)
2. Claude opens a PR on GitHub
3. Tube reviews and merges
4. Merging to master auto-deploys to production

A hook will block any commit directly to master.

### What Belongs Here vs. a New Repo

This repo is the **web app only** — pages, API, catalog, player, admin UI.

**Does NOT belong here:**
- New bots / audio tools / CLI apps — these get their own repo + Docker image
- Search engine internals — talk to Tube first
- New services or standalone tools

If you're trying to add new functionality that isn't a page, API endpoint, or UI feature for the existing web app: **TUBE IS WATCHING. WWTD??** Ask Claude to help you create a new repo instead. Bot code lives in its own repo and connects via a TOML manifest in `apps/`.

## Stack

- **Frontend**: Astro 5.x (static pages). ALL pages are `.astro` files — no exceptions
- **Player**: Svelte 5 island (`src/components/Player.svelte`)
- **Backend**: FastAPI (Python 3.12+), SQLAlchemy, SQLite
- **Auth**: JWT cookies, roles: admin / member
- **Deploy**: Docker → Dokku, auto-deploys on merge to master

## Local Dev Setup

### Prerequisites (Mac)

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install what you need
brew install node uv
```

### First-Time Setup

```bash
git clone git@github.com:A-U-Supply/a-u.supply.git
cd a-u.supply
npm install
uv sync
```

### Running Locally

Two terminals (or ask Claude to start them for you):

```bash
# Terminal 1: Backend API (port 5000)
npm run dev:api

# Terminal 2: Frontend (port 4321, proxies API calls to backend)
npm run dev
```

Open http://localhost:4321.

**Frontend only?** If you're just editing pages/styles, `npm run dev` alone works — API calls will fail but pages render fine.

## Rules

- **One framework**: All pages use Astro. Never introduce another template engine.
- **Use uv for Python**: `uv sync`, `uv run`, `uv lock` — never pip.
- **Product codes have special characters** (`#`, spaces, dots). URL-encode them: `encodeURIComponent()` in JS, `quote(code, safe='')` in Python.
- **Format before committing**: Run `npm run format` to auto-format code.

## File Layout

```
src/
  components/Player.svelte    — persistent audio player (Svelte 5)
  layouts/Base.astro          — public layout (ViewTransitions + Player)
  layouts/Admin.astro         — admin layout (sidebar + auth + Player)
  pages/                      — every .astro file here becomes a URL
    index.astro               — homepage
    catalog/                  — public catalog
    admin/                    — admin pages (dashboard, catalog, settings, jobs)

main.py                       — FastAPI app, auth, static file serving
catalog.py                    — release catalog API
search_api.py                 — media search API
jobs_api.py                   — workspace & job queue API
models.py                     — database models
auth.py                       — JWT auth helpers

apps/*.toml                   — bot manifests (pointers to Docker images, not bot code)
data/                         — SQLite DB + media (not committed, lives on server)
```

## Player Integration

Queue tracks from any page:

```js
document.dispatchEvent(new CustomEvent('player:queue', {
  detail: {
    tracks: [{ track_id, title, release_title, release_code, stream_url, cover_url, duration, entity_name }],
    startIndex: 0
  }
}));
```

## App Runner (Bots)

Bots are Docker images that process media. They live in **their own repos** — this repo only has TOML manifests pointing to them in `apps/`.

**To add a new bot:** Create a new repo with a Dockerfile, then add a manifest at `apps/<bot-name>.toml`. See existing manifests for the format.

**How it works:** Users select media into a workspace → pick an app → submit a job. The worker pulls the Docker image, mounts input files at `/work/input/`, runs the container, collects outputs from `/work/output/`.

## Deployment

- **Auto-deploy**: Merge to master → GitHub Actions → Dokku → live at a-u.supply
- **Run commands on server**: `ssh dokku enter au-supply web <command>`
- **Dokku CLI pitfall**: Dokku's argument parser mangles quotes and special characters. Never pass inline Python or multiline strings through `ssh dokku run`. Use `manage.py` commands or the API instead.
- **Data persists** across deploys (DB + media in mounted volumes)
- **SSL**: Auto-managed via Let's Encrypt

## API Docs

Interactive API docs at [a-u.supply/docs](https://a-u.supply/docs).
