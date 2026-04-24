# Architecture

High-level tour of how the pieces fit together. For setup and workflow, see [`development.md`](development.md). For deploy mechanics, see [`deployment.md`](deployment.md).

## Stack

| Layer    | Technology                                                              | Role |
|----------|-------------------------------------------------------------------------|------|
| Frontend | [Astro](https://astro.build/) 5.x (static output), Svelte 5 island     | All pages are `.astro`; the audio player is a persistent Svelte island |
| Backend  | [FastAPI](https://fastapi.tiangolo.com/) (Python 3.12+), SQLAlchemy    | REST API, auth, webhooks, static-file serving |
| Database | SQLite (WAL mode)                                                       | Primary store; persists on a Dokku mounted volume |
| Search   | [Meilisearch](https://www.meilisearch.com/)                             | Full-text, typo-tolerant media search |
| Workers  | `worker.py` + Docker bot images                                         | Pulls Docker images to process media jobs |
| Auth     | JWT (httpOnly cookies) + API key Bearer tokens                          | Browser sessions + programmatic access |
| Deploy   | Docker → Dokku, GitHub Actions                                          | Auto-deploy on merge to `master` |

## Runtime shape

```
                Browser
                   │
                   ▼
          Astro dev server (4321)        ← only in local dev
                   │   (proxies /api → 5000)
                   ▼
              FastAPI (main.py, port 5000)
                   │
        ┌──────────┼───────────────┐
        ▼          ▼               ▼
     SQLite   Meilisearch     File storage
       │         (search       (media, covers,
       │          index)        thumbnails)
       │
       ▼
   worker.py  ──▶  Docker bot images (apps/*.toml manifests)
                   mount /work/input/ + /work/output/
```

In production, FastAPI serves the built Astro `dist/` directly — there's no separate frontend server.

## Directory layout

```
src/                      — Astro frontend source
  components/Player.svelte  — persistent audio player (Svelte 5 island)
  layouts/
    Base.astro              — public layout (ViewTransitions + Player)
    Admin.astro             — authenticated admin layout (sidebar + Player)
  pages/                    — every .astro here becomes a URL
    index.astro             — homepage
    login.astro
    catalog/                — public catalog grid + release detail
    admin/                  — dashboard, catalog mgmt, search, settings, jobs
  lib/                      — shared TS helpers (encrypt, viewers, workspace)
  styles/
    global.css              — fluid typography, custom properties, reset
    admin.css               — admin layout, sidebar, login form

*.py  (at repo root)        — FastAPI app + modules (see "Python files" below)
apps/*.toml                 — bot manifests (not bot code; code lives in separate repos)
data/                       — SQLite DB + media (not committed; mounted volume in prod)
public/                     — static assets copied to dist/ at build time
tests/                      — pytest suite (uv run pytest)
docs/                       — these guides
```

### Python files at root

Entry points (referenced by `Dockerfile`, `Procfile`, `.github/workflows/`):

- `main.py` — FastAPI app, auth routes, webhooks, static-file serving
- `worker.py` — Job queue worker (polls pending jobs, runs Docker containers)
- `cli.py` — User management CLI
- `manage.py` — Management CLI (user/API key/Slack sync ops)
- `seed_catalog.py` — Seed the release catalog
- `reset_db.py` — Reset users table

Modules (imported by the entry points):

- `auth.py` — JWT auth, API key auth, scope hierarchy
- `models.py` — SQLAlchemy models (User, Release, Track, MediaItem, Job, …)
- `admin_api.py`, `bookmarks_api.py`, `catalog.py`, `jobs_api.py`, `search_api.py` — FastAPI routers
- `extraction.py` — Async metadata extraction (images, audio, video)
- `search_client.py` — Meilisearch wrapper
- `slack_scraper.py`, `slack_notifier.py` — Slack ingest + notifications

> **Note:** A future PR will move these modules into a `server/` package for cleanliness. Entry points will stay at the root (the Dokku buildpack + workflow files reference them by name).

## Auth model

- **Session cookie** — `POST /api/login` with email/password. Sets an httpOnly JWT cookie. Used by the browser UI.
- **API key** — `POST /api/keys` to generate a Bearer token. Send as `Authorization: Bearer au_xxxxx`. Used for scripts and programmatic access.
- **Passwords** — bcrypt via passlib.
- **JWT** — stored in httpOnly cookies (`secure` in prod, `sameSite=lax`); 1-year expiry.
- **CSRF** — tokens required for state-changing cookie-based requests.
- **Rate limiting** — 5 login attempts / minute / IP.
- **Roles** — `admin` (full access, can manage users) and `member` (read/write, no admin operations). Session cookie scope is derived from the role: `admin` → `admin`, `member` → `write`.

Full endpoint auth details are in [`api.md`](api.md).

## CSS architecture

Vanilla CSS with custom properties. No framework.

- **Fluid typography** — `clamp()`-based scale (`--text-sm` through `--text-2xl`)
- **Spacing scale** — `clamp()`-based (`--space-xs` through `--space-xl`)
- **Breakpoints** — mobile-first: 640px, 1024px, 1440px
- **Custom properties** — colors, fonts, spacing in `:root`

Global styles live in `src/styles/global.css`. Admin-specific styles in `src/styles/admin.css`. Page-specific styles use Astro's scoped `<style>` blocks.
