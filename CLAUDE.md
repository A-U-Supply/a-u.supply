# A-U.SUPPLY — Project Guide

## Stack

- **Frontend**: Astro 5.x (static SSG output). ALL pages are Astro pages — no exceptions.
- **Player**: Svelte 5 island (`src/components/Player.svelte`) mounted via `@astrojs/svelte` in Base.astro with `transition:persist`
- **Backend**: FastAPI (Python 3.12+), SQLAlchemy ORM, SQLite with WAL
- **Auth**: JWT in httpOnly cookies, role-based (admin/member)
- **Deployment**: Docker multi-stage build, pushed to Dokku via GitHub Actions

## Rules

- **One framework**: All pages use Astro. Never use Jinja2, Mako, or any other template engine for pages. FastAPI serves the Astro `dist/` output and the API.
- **All work via PRs**: Never commit directly to master. Create a feature branch, open a PR, merge it. Deploys are triggered by merging to master.
- **Use uv**: Python dependency management uses `uv`, not pip. Run `uv sync`, `uv run`, `uv lock`.
- **Product codes can contain special characters** (`#`, spaces, dots). Always URL-encode with `encodeURIComponent()` in JS and `quote(code, safe='')` in Python when building URLs.

## Deployment (Dokku)

- **Server**: 204.168.201.89
- **App name**: au-supply
- **Deploy method**: `git push dokku master:main` via GitHub Actions (`.github/workflows/deploy.yml`)
- **SSH key**: `DOKKU_SSH_KEY` GitHub secret, connects as `dokku@204.168.201.89`
- **Persistent storage**: `/var/lib/dokku/data/storage/au-supply-data:/app/data` (SQLite DB + media files survive deploys)
- **Legacy site storage**: `/var/lib/dokku/data/storage/au-supply-legacy:/srv/legacy-site`
- **Run commands on server**: `ssh dokku@204.168.201.89 enter au-supply web <command>` (use `enter`, not `run` — `run` creates a disposable container)
- **ffmpeg** is installed in the Docker image for audio duration extraction

## File Layout

```
src/
  components/Player.svelte    — persistent audio player (Svelte 5)
  layouts/Base.astro          — public layout (ViewTransitions + Player)
  layouts/Admin.astro         — admin layout (sidebar + auth + Player)
  pages/
    index.astro               — homepage
    login.astro
    catalog/
      index.astro             — public catalog grid
      release.astro           — release detail (?code=XXX)
    admin/
      catalog/
        index.astro           — release list
        new.astro             — create release
        edit.astro            — edit release (?code=XXX)
      dashboard.astro
      files.astro
      settings.astro

catalog.py                    — release catalog API (FastAPI router)
main.py                       — FastAPI app, auth routes, static file middleware
models.py                     — SQLAlchemy models (User, Entity, Release, Track, etc.)
auth.py                       — JWT auth helpers

data/                         — SQLite DB + media (persistent via Dokku storage)
  au.db
  media/releases/{code}/
    cover.{ext}
    cover_thumb.webp
    tracks/{nn}-{slug}.{ext}
```

## Player Integration

The Svelte player listens for `player:queue` events on `document`:

```js
document.dispatchEvent(new CustomEvent('player:queue', {
  detail: {
    tracks: [{ track_id, title, release_title, release_code, stream_url, cover_url, duration, entity_name }],
    startIndex: 0
  }
}));
```

## Dev

```sh
npm run dev          # Astro dev server (port 4321, proxies /api to 5000)
uv run uvicorn main:app --reload --port 5000   # FastAPI backend
```
