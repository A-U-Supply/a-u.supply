# a-u.supply

Web catalog and admin platform for [A-U.Supply](https://a-u.supply) — Audio Units Division.

## Stack

| Layer    | Technology                                    |
|----------|-----------------------------------------------|
| Frontend | [Astro](https://astro.build/) (static output) |
| Backend  | [FastAPI](https://fastapi.tiangolo.com/)       |
| Database | SQLite (WAL mode)                             |
| Search   | [Meilisearch](https://www.meilisearch.com/) (full-text, typo-tolerant) |
| Auth     | JWT in httpOnly cookies + API key Bearer tokens |
| Deploy   | Docker, Dokku, GitHub Actions                 |

## Project structure

```
a-u.supply/
├── src/                    # Astro frontend source
│   ├── components/
│   │   └── Player.svelte   # Persistent audio player (Svelte 5 island)
│   ├── layouts/
│   │   ├── Base.astro      # Public page layout (ViewTransitions + Player)
│   │   └── Admin.astro     # Authenticated admin layout (sidebar nav)
│   ├── pages/
│   │   ├── index.astro     # Homepage
│   │   ├── login.astro     # Login form
│   │   ├── catalog/        # Public catalog grid + release detail pages
│   │   └── admin/          # Admin pages (dashboard, catalog mgmt, search, settings)
│   └── styles/
│       ├── global.css      # Fluid typography, custom properties, reset
│       └── admin.css       # Admin layout, sidebar, login form
├── public/                 # Static assets (copied to dist/ at build)
├── main.py                 # FastAPI application, auth routes, webhooks
├── auth.py                 # JWT auth, API key auth, scope hierarchy
├── catalog.py              # Release catalog API (entities, releases, tracks, covers)
├── search_api.py           # Media search API (search, media CRUD, tags, Slack sync, API keys)
├── models.py               # SQLAlchemy models (User, Release, Track, MediaItem, etc.)
├── search_client.py        # Meilisearch integration
├── slack_scraper.py        # Slack channel scraper + yt-dlp integration
├── extraction.py           # Async metadata extraction (images, audio, video)
├── cli.py                  # User management CLI
├── astro.config.mjs        # Astro config (dev proxy to FastAPI)
├── Dockerfile              # Multi-stage build (Node + Python)
├── Procfile                # Dokku process definition
├── pyproject.toml          # Python dependencies (uv)
└── package.json            # Node dependencies (Astro)
```

## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 22+

### Setup

```bash
# Install dependencies
uv sync
npm install

# Create your first admin user
python cli.py create-user --email you@example.com --name "Your Name" --password changeme --role admin
```

### Running locally

Two processes — run them in separate terminals:

```bash
# Terminal 1: FastAPI backend (port 5000)
npm run dev:api

# Terminal 2: Astro dev server (port 4321, proxies /api to FastAPI)
npm run dev
```

Browse to `http://localhost:4321`. The Astro dev server proxies all `/api/*` requests to FastAPI on port 5000.

### Formatting

```bash
npm run format        # Auto-format JS, Astro, Svelte, CSS files
npm run format:check  # Check without writing
```

Uses Prettier with Astro and Svelte plugins.

### Building

```bash
npm run build    # Outputs to dist/
```

FastAPI serves the built files from `dist/` in production.

## User management

No public signup. Users are created via CLI or by an admin through the settings page.

### CLI

```bash
# Create a user
python cli.py create-user --email user@example.com --name "User Name" --password secret --role member

# List all users
python cli.py list-users

# Delete a user
python cli.py delete-user --email user@example.com
```

Roles: `admin` (full access, can manage users), `member` (read/write access, no admin operations).

### Admin UI

Admins can invite and delete users at `/admin/settings`.

## API documentation

**Interactive API docs are available at [`/docs`](https://a-u.supply/docs)** (Swagger UI) and [`/redoc`](https://a-u.supply/redoc) (ReDoc). These are the primary API reference — every endpoint has detailed descriptions, parameter documentation, and request/response schemas.

### Authentication

Two methods:

1. **Session cookie** — `POST /api/login` with email/password. Sets an httpOnly JWT cookie. Used by the browser UI.
2. **API key** — `POST /api/keys` to generate a Bearer token. Send as `Authorization: Bearer au_xxxxx`. Used for scripts and programmatic access.

### Scopes

| Scope | Access level |
|-------|-------------|
| `read` | Search, view, stream, download |
| `write` | Read + upload, tag, edit, manage API keys |
| `admin` | Write + delete, manage users, trigger scrapes |

Session cookie scope is derived from role: `admin` → admin, `member` → write.

### API overview

| Group | Endpoints | Description |
|-------|-----------|-------------|
| **Authentication** | `GET /api/csrf`, `POST /api/login`, `POST /api/logout` | Session management, CSRF tokens |
| **User Profile** | `GET /api/me`, `POST /api/me/password` | View/edit your own account |
| **User Admin** | `GET/POST/DELETE /api/admin/users` | Manage user accounts (admin only) |
| **Entities** | `GET/POST/PUT/DELETE /api/entities` | Artist/manufacturer management |
| **Releases** | `GET/POST/PUT/DELETE /api/releases`, publish/unpublish | Release catalog CRUD and lifecycle |
| **Tracks** | `POST/DELETE /api/releases/{code}/tracks`, reorder, stream | Audio upload, management, and streaming |
| **Cover Art** | `POST/GET /api/releases/{code}/cover` | Cover art upload and serving (auto-thumbnails) |
| **Media Search** | `POST /api/search`, `GET /api/search/facets` | Full-text search with filters and facets |
| **Media Items** | `GET/POST/PUT/DELETE /api/media` | Media CRUD, upload, file download, thumbnails |
| **Tagging** | `POST/DELETE /api/media/{id}/tags`, `GET /api/tags` | Tag management and autocomplete |
| **Batch Ops** | `POST /api/media/batch/*` | Bulk tag, delete, re-extract, ZIP export |
| **Slack Sync** | `POST /api/ingest/slack/*` | Scrape, sync, dry-run, reaction refresh |
| **API Keys** | `GET/POST/DELETE /api/keys` | Generate and revoke API keys |
| **Extraction** | `GET /api/extraction-failures`, retry, resolve | Manage metadata extraction failures |

**For full endpoint documentation, see [`/docs`](https://a-u.supply/docs).**

### Special characters in product codes

Product codes can contain `#`, spaces, dots, etc. Always URL-encode them in paths:

```javascript
// JavaScript
fetch(`/api/releases/${encodeURIComponent(code)}`)
```

```python
# Python
from urllib.parse import quote
requests.get(f"/api/releases/{quote(code, safe='')}")
```

## Auth details

- Passwords hashed with bcrypt via passlib
- JWT tokens stored in httpOnly cookies (`secure` flag enabled in production, `sameSite=lax`)
- 1-year token expiry
- CSRF tokens for state-changing cookie-based requests
- API keys: `Authorization: Bearer au_xxxxx`, bcrypt-hashed, with scope hierarchy (`read` < `write` < `admin`)
- Rate limiting: 5 login attempts per minute per IP

## Deployment architecture

Two GitHub repos serve the same domain:

| Repo | Purpose | Served from |
|------|---------|-------------|
| `A-U-Supply/a-u.supply` | Astro + FastAPI app | `/` (primary) |
| `A-U-Supply/ausupply.github.io` | Legacy static site | Fallback for unmatched paths |

### Routing priority

FastAPI resolves URLs in this order:

1. **API routes** — `/api/*` (explicit route handlers)
2. **Explicit pages** — `/` (Astro homepage), `/the-expenditure` (legacy homepage alias)
3. **Webhook endpoints** — `/hooks/legacy`, `/hooks/deploy`
4. **Astro static files** — anything in `dist/` (built Astro output)
5. **Legacy fallback** — if nothing above matched, try the legacy site directory

If both Astro and legacy have a file at the same path, Astro wins.

#### The `/the-expenditure` alias

The legacy site's `index.html` is NOT served at `/` — the Astro homepage owns that. Instead it's aliased to `/the-expenditure`. All other legacy paths work unchanged. Relative asset paths in legacy HTML resolve correctly because legacy files are served from the root.

### Auto-deploy: main repo

Push to `master` → GitHub Actions pushes to Dokku → Docker image rebuilds → container restarts.

### Auto-deploy: legacy repo

Push to `ausupply.github.io` → GitHub webhook hits `POST /hooks/legacy` → `git pull` in the legacy site directory. No container restart needed.

### Docker build

The Dockerfile uses a multi-stage build:

1. **Node stage** — installs npm deps, runs `astro build`, produces `dist/`
2. **Python stage** — installs Python deps via uv, copies app code + `dist/` from Node stage, includes `git` for webhook pulls and `ffmpeg` for audio processing

### Data persistence

SQLite database, release media, and search media are stored in persistent volumes that survive container rebuilds. The legacy site is also persisted in a separate volume.

## CSS architecture

Vanilla CSS with custom properties. No framework.

- **Fluid typography** — `clamp()`-based scale (`--text-sm` through `--text-2xl`)
- **Spacing scale** — `clamp()`-based (`--space-xs` through `--space-xl`)
- **Breakpoints** — mobile-first: 640px, 1024px, 1440px
- **Custom properties** — colors, fonts, spacing in `:root`

Global styles in `src/styles/global.css`. Admin-specific styles in `src/styles/admin.css`. Page-specific styles use Astro's scoped `<style>` blocks.

## License

All rights reserved.
