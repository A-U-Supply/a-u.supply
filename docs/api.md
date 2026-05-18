# API

**Interactive API docs live at [`/docs`](https://a-u.supply/docs) (Swagger UI) and [`/redoc`](https://a-u.supply/redoc).** Those are the primary reference — every endpoint has parameter docs and request/response schemas. This file is a short orientation.

## Authentication

Two methods:

1. **Session cookie** — `POST /api/login` with email/password. Sets an httpOnly JWT cookie. Used by the browser UI.
2. **API key** — `POST /api/keys` to generate a Bearer token. Send as `Authorization: Bearer au_xxxxx`. Used for scripts and programmatic access.

### Scopes

| Scope | Access level |
|-------|-------------|
| `read`  | Search, view, stream, download |
| `write` | Read + upload, tag, edit, manage API keys |
| `admin` | Write + delete, manage users, trigger scrapes |

Session-cookie scope is derived from role: `admin` → `admin`, `member` → `write`.

## Endpoint groups

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
| **Jobs** | `GET/POST /api/jobs`, workspace ops | Submit and track bot jobs (see [`bots.md`](bots.md)) |
| **Bookmarks** | `GET/POST/DELETE /api/bookmarks` | Per-user bookmarks |
| **Admin Dashboard** | `GET /api/admin/stats`, `action-queue`, `activity-feed`, `altar` | Auspices dashboard data |
| **Latents** | `GET/POST/PATCH/DELETE /api/projects[/...]` | Latents, slots, items, documents (see [`plans/2026-05-15-latents.md`](plans/2026-05-15-latents.md)) |
| **Threads** | `GET/POST/PATCH/DELETE /api/threads[/...]` | Lemmy-backed discussion threads anchored to projects / slots / media items |

For full endpoint documentation, use [`/docs`](https://a-u.supply/docs).

## Special characters in codes

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

## Related

- [`architecture.md`](architecture.md#auth-model) — auth model details
- [`operations.md`](operations.md) — generating API keys via `manage.py make-apikey`
- [`bots.md`](bots.md) — the jobs surface
