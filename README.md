# a-u.supply

Web catalog and admin platform for [A-U.Supply](https://a-u.supply) — Audio Units Division.

## Stack

| Layer    | Technology                                    |
|----------|-----------------------------------------------|
| Frontend | [Astro](https://astro.build/) (static output) |
| Backend  | [FastAPI](https://fastapi.tiangolo.com/)       |
| Database | SQLite (WAL mode)                             |
| Auth     | JWT in httpOnly cookies, bcrypt passwords     |
| Deploy   | Docker, Dokku, GitHub Actions                 |

## Project structure

```
a-u.supply/
├── src/                    # Astro frontend source
│   ├── layouts/
│   │   ├── Base.astro      # Public page layout
│   │   └── Admin.astro     # Authenticated admin layout (sidebar nav)
│   ├── pages/
│   │   ├── index.astro     # Homepage (cover page)
│   │   ├── login.astro     # Login form
│   │   └── admin/
│   │       ├── dashboard.astro
│   │       ├── files.astro
│   │       └── settings.astro
│   └── styles/
│       ├── global.css      # Fluid typography, custom properties, reset
│       └── admin.css       # Admin layout, sidebar, login form
├── public/                 # Static assets (copied to dist/ at build)
│   ├── assets/             # Images (logo, product art)
│   └── favicon.jpg
├── main.py                 # FastAPI application
├── auth.py                 # JWT, password hashing, auth dependencies
├── models.py               # SQLAlchemy models (User)
├── cli.py                  # User management CLI
├── astro.config.mjs        # Astro config (dev proxy to FastAPI)
├── Dockerfile              # Multi-stage build (Node + Python)
├── Procfile                # Dokku process definition
├── pyproject.toml           # Python dependencies (uv)
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

Roles: `admin` (can manage users), `member` (access to everything else).

### Admin UI

Admins can invite and delete users at `/admin/settings`.

## API routes

All API endpoints are under the `/api` prefix.

| Method   | Endpoint              | Auth     | Description           |
|----------|-----------------------|----------|-----------------------|
| `GET`    | `/api/csrf`           | No       | Get CSRF token        |
| `POST`   | `/api/login`          | No       | Login (sets cookie)   |
| `POST`   | `/api/logout`         | No       | Logout (clears cookie)|
| `GET`    | `/api/me`             | Session  | Current user info     |
| `GET`    | `/api/admin/users`    | Admin    | List all users        |
| `POST`   | `/api/admin/users`    | Admin    | Create a user         |
| `DELETE` | `/api/admin/users/:id`| Admin    | Delete a user         |

Login is rate-limited to 5 requests per minute per IP.

## Auth

- Passwords hashed with bcrypt via passlib
- JWT tokens stored in httpOnly cookies (`secure` flag enabled in production, `sameSite=lax`)
- 24-hour token expiry
- CSRF tokens for state-changing requests
- No bearer tokens — cookie-only flow

## Deployment architecture

Two GitHub repos serve the same domain:

| Repo | Purpose | Served from |
|------|---------|-------------|
| `A-U-Supply/a-u.supply` | New Astro + FastAPI app | `/` (primary) |
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

The legacy site's `index.html` is NOT served at `/` — the Astro homepage owns that. Instead it's aliased to `/the-expenditure`. All other legacy paths work unchanged (e.g. `/puke-box.html`, `/mire.html`, `/audex.html`). Relative asset paths in legacy HTML resolve correctly because legacy files are served from the root.

### File locations on the server

| Path | Contents | Persistence |
|------|----------|-------------|
| `/app/dist/` | Built Astro output | Rebuilt on deploy |
| `/app/data/` | SQLite database | Dokku persistent storage |
| `/srv/legacy-site/` | Clone of `ausupply.github.io` | Dokku persistent storage |

### Auto-deploy: main repo

Push to `master` → GitHub Actions pushes to Dokku → Docker image rebuilds → container restarts.

The workflow is in `.github/workflows/deploy.yml`. Dokku handles the full build (Astro + Python) via the multi-stage Dockerfile.

### Auto-deploy: legacy repo

Push to `ausupply.github.io` → GitHub webhook hits `POST /hooks/legacy` → `git pull` in the legacy site directory. No container restart needed — files are served directly from the volume.

To set up the webhook on GitHub:
1. Go to `A-U-Supply/ausupply.github.io` → Settings → Webhooks → Add webhook
2. Payload URL: `https://a-u.supply/hooks/legacy`
3. Content type: `application/json`
4. Secret: same value as `WEBHOOK_SECRET` env var on the server
5. Events: Just the push event

### Manual deploy

If a webhook fails or you need to deploy manually:

```bash
# Legacy site — SSH into the server and pull
dokku enter au-supply web bash -c "git -C /srv/legacy-site pull --ff-only"

# Main app — re-push to Dokku
git push dokku master:main --force
```

### Docker build

The Dockerfile uses a multi-stage build:

1. **Node stage** — installs npm deps, runs `astro build`, produces `dist/`
2. **Python stage** — installs Python deps via uv, copies app code + `dist/` from Node stage, includes `git` for webhook pulls

### Environment variables

| Variable          | Default                  | Description                          |
|-------------------|--------------------------|--------------------------------------|
| `SECRET_KEY`      | `change-me-in-production`| JWT signing key                      |
| `PRODUCTION`      | (unset)                  | Set to `1` for secure cookies        |
| `ALLOWED_ORIGINS` | `http://localhost:4321`  | CORS origins (comma-separated)       |
| `LEGACY_SITE_DIR` | `/srv/legacy-site`       | Path to legacy site clone            |
| `WEBHOOK_SECRET`  | (unset)                  | GitHub webhook HMAC secret           |
| `DEPLOY_SCRIPT`   | (unset)                  | Optional script to run on deploy webhook |

### Data persistence

SQLite database lives at `data/au.db`. Legacy site lives at `/srv/legacy-site`. Both directories should be mounted as Dokku persistent storage:

```bash
# Database (existing)
dokku storage:mount au-supply /var/lib/dokku/data/storage/au-supply:/app/data

# Legacy site
dokku storage:ensure-directory au-supply-legacy
dokku storage:mount au-supply /var/lib/dokku/data/storage/au-supply-legacy:/srv/legacy-site

# Clone the legacy repo into the storage directory (one-time setup)
git clone https://github.com/A-U-Supply/ausupply.github.io.git /var/lib/dokku/data/storage/au-supply-legacy
```

## CSS architecture

Vanilla CSS with custom properties. No framework.

- **Fluid typography** — `clamp()`-based scale (`--text-sm` through `--text-2xl`)
- **Spacing scale** — `clamp()`-based (`--space-xs` through `--space-xl`)
- **Breakpoints** — mobile-first: 640px, 1024px, 1440px
- **Custom properties** — colors, fonts, spacing in `:root`

Global styles in `src/styles/global.css`. Admin-specific styles in `src/styles/admin.css`. Page-specific styles use Astro's scoped `<style>` blocks.

## License

All rights reserved.
