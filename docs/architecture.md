# Architecture

High-level tour of how the pieces fit together. For setup and workflow, see [`development.md`](development.md). For deploy mechanics, see [`deployment.md`](deployment.md). For the frontend specifically (UI kit, components), see [`frontend.md`](frontend.md).

## Stack

| Layer    | Technology                                                              | Role |
|----------|-------------------------------------------------------------------------|------|
| Frontend | [Astro](https://astro.build/) 5.x (static output), Svelte 5 islands     | All pages are `.astro`; the audio player + Latents UI + filters are Svelte islands. See [`frontend.md`](frontend.md). |
| Styling  | [Tailwind 4](https://tailwindcss.com/) + [bits-ui](https://bits-ui.com/) + brutalist tokens | Tailwind utilities, headless primitives, and a single `.brutalist-control` class — no rounded corners, hard shadows |
| Backend  | [FastAPI](https://fastapi.tiangolo.com/) (Python 3.12+), SQLAlchemy    | REST API, auth, webhooks, static-file serving |
| Database | SQLite (WAL mode)                                                       | Primary store; persists on a Dokku mounted volume |
| Search   | [Meilisearch](https://www.meilisearch.com/)                             | Indices: `images`, `audio`, `video`, `emulsion` (user-uploaded WIP) |
| Discussion | [Lemmy](https://join-lemmy.org/) instance at `fold.a-u.supply`        | Backs Latents and Stacks threads. Local-only, federation disabled. Proxied through FastAPI; the browser never talks to fold directly. |
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
        ┌──────────┬──────────┬──────────┬──────────┐
        ▼          ▼          ▼          ▼          ▼
     SQLite   Meilisearch  File       Lemmy     worker.py
       │     (4 indices)  storage    (fold,        │
       │                  (media,    proxied)      ▼
       │                   covers,            Docker bot
       │                   thumbs)            images
       │                                      (apps/*.toml)
       │
       └─ source of truth; Meilisearch can be rebuilt from it
```

In production, FastAPI serves the built Astro `dist/` directly — there's no separate frontend server.

## Directory layout

```
src/                      — Astro frontend source
  components/             — Svelte 5 islands (Player, Threads, Uploader, Latent*, IndexFilter, …)
  layouts/
    Base.astro              — public layout (ViewTransitions + Player)
    Admin.astro             — admin layout (sidebar + auth gate + Player + page-bg painter)
  pages/                    — every .astro here becomes a URL
    index.astro             — homepage
    login.astro
    catalog/                — public catalog grid + release detail
    admin/                  — see "Admin sidebar" below; or glossary.md
  lib/                      — shared TS helpers (encrypt, viewers, workspace)
  styles/
    global.css              — fluid typography, colour tokens, reset
    admin.css               — admin layout, sidebar, login form
    tailwind.css            — Tailwind 4 entry + brutalist @theme + .brutalist-control

server/                   — FastAPI Python package
  __init__.py
  admin_api.py            — admin dashboard, action queue, activity feed, Altar of the Day
  auth.py                 — JWT auth, API key auth, scope hierarchy
  bookmarks_api.py        — per-user bookmarks
  catalog.py              — release catalog CRUD
  jobs_api.py             — workspaces, jobs, app registration, batch (Hecatomb)
  latents_api.py          — Latents + slots + items + documents
  threads_api.py          — generic Lemmy-backed threads
  lemmy_client.py         — Lemmy HTTP client + admin operations
  search_api.py           — media items, tags, search, uploads, midden, slop
  search_client.py        — Meilisearch wrapper
  extraction.py           — async metadata extraction (image / audio / video / session)
  models.py               — SQLAlchemy models
  slack_notifier.py       — outbound Slack posts (immediate + batched)
  slack_scraper.py        — Slack ingest + user mapping

main.py                   — FastAPI app entry: wires routers, serves dist/, webhooks, legacy fallback
worker.py                 — Job-queue worker (polls pending jobs, runs Docker containers)
cli.py                    — User management CLI (used by .github/workflows/create-user.yml)
manage.py                 — Server-side management CLI (run via ssh dokku — see operations.md)
seed_catalog.py           — Seed the release catalog
reset_db.py               — Reset users table (one-off utility)

apps/*.toml               — bot manifests (not bot code; code lives in separate repos)
data/                     — SQLite DB + media (not committed; mounted volume in prod)
public/                   — static assets copied to dist/ at build time
tests/                    — pytest suite (uv run pytest)
docs/                     — these guides
.claude/                  — agent config (see agents.md)
.github/workflows/        — CI / deploy
```

### Entry points stay at the root

`main.py`, `worker.py`, `cli.py`, `manage.py`, `seed_catalog.py`, and `reset_db.py` are referenced by `Dockerfile`, `Procfile`, and `.github/workflows/*.yml` by name. Modules they import live in `server/`. If you ever rename or relocate an entry-point file, update those references in the same PR.

## Admin sidebar

Every admin page sits under `/admin/*` and is grouped into themed sections in the sidebar. The vocabulary is deliberately ritualistic — Auspices, Stacks, Hecatomb, Midden, Atelier, Sacristy, etc. See [`glossary.md`](glossary.md) for the complete map from sidebar label → URL → function.

Notable groups:

- **The Larder** — media library backed by Meilisearch
- **The Winnowing** — failed extractions, slop bucket, midden (soft-deleted)
- **The Altar** — workspace, Hecatomb (bulk dispatch), the job queue
- **The Darkroom** — Latents (private pre-release workspace)
- **The Atelier** — browser-side generative image tools ([`atelier.md`](atelier.md))

## Auth model

- **Session cookie** — `POST /api/login` with email/password. Sets an httpOnly JWT cookie. Used by the browser UI.
- **API key** — `POST /api/keys` to generate a Bearer token. Send as `Authorization: Bearer au_xxxxx`. Used for scripts and programmatic access.
- **Passwords** — bcrypt via passlib.
- **JWT** — stored in httpOnly cookies (`secure` in prod, `sameSite=lax`); 1-year expiry.
- **CSRF** — tokens required for state-changing cookie-based requests.
- **Rate limiting** — 5 login attempts / minute / IP.
- **Roles** — `admin` (full access, can manage users) and `member` (read/write, no admin operations). Session-cookie scope is derived from the role: `admin` → `admin`, `member` → `write`.

Full endpoint auth details are in [`api.md`](api.md).

## Search indices (Meilisearch)

| Index | Source | Notes |
|-------|--------|-------|
| `images` | Slack scrapes + yt-dlp + release media | Public-ish media (still admin-gated, but not WIP) |
| `audio` | Same | Includes release track audio |
| `video` | Same | |
| `emulsion` | User uploads via Tribute (`/admin/search/upload`) | The pre-release WIP pool. Holds image / audio / video / session (DAW & NLE project files). |

SQLite is the source of truth. The Meilisearch indices can be rebuilt at any time via `manage.py reindex` ([`operations.md`](operations.md)).

Each indexed document carries denormalized vote aggregates (`up_count`, `down_count`, `vote_score`) plus parallel voter-id arrays (`upvoter_user_ids`, `downvoter_user_ids`) and inline voter objects (`upvoters`, `downvoters`) so the Acclaim/Disavow chip can render counts, my-vote highlight, and hover tooltips without a second round-trip. Per-vote sync is a debounced (~500ms) partial update via `server/vote_sync.py` — never a full doc rebuild. See [`plans/2026-05-20-search-votes.md`](plans/2026-05-20-search-votes.md).

## Latents and discussion

[Latents](plans/2026-05-15-latents.md) is an admin-only pre-release workspace. Each Latent has ordered slots, loose files, named markdown documents, and threaded discussion. Discussion runs through the fold Lemmy instance:

- One private Lemmy community per Latent (auto-provisioned on creation).
- One global `stacks` community for media-item-anchored threads across search.
- All Lemmy reads/writes go through FastAPI `/api/threads` — the browser never sees Lemmy.

See the [latents plan](plans/2026-05-15-latents.md) for the data model, API surface, and Lemmy auto-provisioning sequence. See [`operations.md`](operations.md) for the fold Let's Encrypt gotcha and version notes.

## Tests

`tests/` is the pytest suite — auth, models, search API, extraction, dedup, midden, tags, thumbnails, and Slack scraper coverage. Run with `uv run pytest`. `tests/conftest.py` sets up an in-memory DB and patches the search client. See [`development.md`](development.md) for the working-directory expectations.

## Media extraction pipeline

Every newly-ingested image runs through `server/extraction._run_image_extraction`,
which performs (in order):

1. **Dimensions / format** via PIL.
2. **Dominant colors** — PIL palette quantization → 12 hex codes (deterministic).
3. **Thumbnails** — `_thumb_sm.webp` (128), `_thumb.webp` (400), `_thumb_lg.webp` (1600).
4. **OCR text** — EasyOCR on the original, upscaled when small. Stored on `media_image_meta.caption`.
5. **AI vision enrichment** — DeepSeek-VL2 (via SiliconFlow by default) on the
   `_thumb_lg.webp`, given the OCR caption as a hint. Returns description, tags,
   color temperature/character, vibe, and 9 content boolean flags. Skipped if
   `VISION_API_KEY` (or legacy `DEEPSEEK_API_KEY`) is not set. See
   [`ai-image-descriptions.md`](ai-image-descriptions.md).

Each step is independent — a failure logs to `extraction_failures` and the
others still run. Retries hit `/api/extraction-failures/:id/retry`.

## Related deep dives

- [`frontend.md`](frontend.md) — UI kit, Svelte components, event-bus pattern
- [`atelier.md`](atelier.md) — the Atelier section
- [`bots.md`](bots.md) — App Runner / TOML manifests / bot repos
- [`player.md`](player.md) — persistent audio player events
- [`api.md`](api.md) — API surface
- [`ai-image-descriptions.md`](ai-image-descriptions.md) — vision-model enrichment design
- [`operations.md`](operations.md) — `manage.py` and server-side commands
- [`deployment.md`](deployment.md) — Dokku, SSL, legacy routing
- [`glossary.md`](glossary.md) — admin nomenclature
