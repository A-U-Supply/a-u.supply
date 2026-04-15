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
- **Domain**: `a-u.supply` (DNS points to the server IP)
- **SSL**: Let's Encrypt via `dokku-letsencrypt` plugin, auto-renews via cron. HTTP redirects to HTTPS.
- **App name**: au-supply
- **Deploy method**: `git push dokku master:main` via GitHub Actions (`.github/workflows/deploy.yml`)
- **SSH key**: `DOKKU_SSH_KEY` GitHub secret, connects as `dokku@204.168.201.89`
- **Persistent storage**: `/var/lib/dokku/data/storage/au-supply-data:/app/data` (SQLite DB + media files survive deploys)
- **Legacy site storage**: `/var/lib/dokku/data/storage/au-supply-legacy:/srv/legacy-site`
- **Run commands on server**: `ssh dokku@204.168.201.89 enter au-supply web <command>` (use `enter`, not `run` — `run` creates a disposable container)
- **ffmpeg** is installed in the Docker image for audio duration extraction
- **No Caddy/external proxy**: Dokku's built-in nginx handles reverse proxying and SSL termination

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

## App Runner

The app runner processes media through containerized apps (bots). Users select media into workspaces, pick an app, configure parameters, and submit jobs. A separate worker process polls for pending jobs and runs them in Docker containers.

### Architecture

- **Manifests**: `apps/*.toml` — each file defines an app (Docker image, accepted inputs, parameter schema). These are pointers, not code. Bot code lives in its own repo.
- **API**: `jobs_api.py` — workspace CRUD, app registry, job queue, output management. All endpoints use `require_scope()` so they work with both cookies and API keys.
- **Worker**: `worker.py` — runs as a separate Dokku process type (`Procfile: worker`). Polls for pending jobs, pulls Docker images, mounts input/output dirs, runs containers.
- **Models**: Workspace, WorkspaceItem, AppDefinition, Job, JobOutput (in `models.py`)

### How jobs work

1. Worker picks a pending job from the `jobs` table (priority order)
2. Copies input media files from `/app/search-data/` into `/app/job-data/{job_id}/input/`
3. Writes `/app/job-data/{job_id}/job.json` with params and input file list
4. Runs `docker run` with the job dir mounted at `/work` inside the container
5. Bot reads from `/work/input/`, writes to `/work/output/`
6. Worker collects outputs, creates `job_output` rows
7. Admin reviews outputs, indexes good ones into the search engine or discards

### Bot container contract

Bots are Docker images. The worker mounts a job directory at `/work`:

- `/work/input/` — input media files
- `/work/job.json` — `{"job_id", "params", "input_files": [{filename, media_type, media_item_id}]}`
- `/work/output/` — bot writes results here

Exit codes: `0` = success, `1` = expected failure, `2` = config error.

Optional: write `/work/output/manifest.json` to describe outputs with media types and descriptions. If absent, types are inferred from extensions.

### Adding a new bot

1. Bot repo needs a `Dockerfile` and a GitHub Actions workflow that builds + pushes to GHCR
2. Create `apps/<bot-name>.toml` manifest in this repo (see `apps/rottengenizdat.toml` for example)
3. Register via `POST /api/apps` with the TOML as `manifest_toml` (requires admin API key)

### Dokku setup

- Docker socket: `dokku storage:mount au-supply /var/run/docker.sock:/var/run/docker.sock`
- Job data volume: `dokku storage:mount au-supply /var/lib/dokku/data/storage/au-supply-jobs:/app/job-data`
- Worker scaling: `dokku ps:scale au-supply worker=1`
- GHCR auth: set `GHCR_USER` and `GHCR_TOKEN` via `dokku config:set` (worker uses these to `docker login` before pulling)

### Key pages

- `/admin/search` — "+ Workspace" button in batch bar to add selected items
- `/admin/search/workspace` — workspace management, "Process with..." app selector
- `/admin/jobs` — job list with status, cancel, retry
- `/admin/jobs/detail?id=X` — job detail with logs, outputs, index/discard
- `/docs` — full API documentation including manifest format and container contract

## Dev

```sh
npm run dev          # Astro dev server (port 4321, proxies /api to 5000)
uv run uvicorn main:app --reload --port 5000   # FastAPI backend
```
