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

## Deployment

Deploys automatically on push to `master` via GitHub Actions. The workflow pushes to a Dokku instance which builds the Docker image.

### Docker build

The Dockerfile uses a multi-stage build:

1. **Node stage** — installs npm deps, runs `astro build`, produces `dist/`
2. **Python stage** — installs Python deps via uv, copies app code + `dist/` from Node stage

### Environment variables

| Variable          | Default                  | Description                          |
|-------------------|--------------------------|--------------------------------------|
| `SECRET_KEY`      | `change-me-in-production`| JWT signing key                      |
| `PRODUCTION`      | (unset)                  | Set to `1` for secure cookies        |
| `ALLOWED_ORIGINS` | `http://localhost:4321`  | CORS origins (comma-separated)       |

### Data persistence

SQLite database lives at `data/au.db`. In Dokku, the `data/` directory should be mounted as persistent storage so it survives container restarts.

## CSS architecture

Vanilla CSS with custom properties. No framework.

- **Fluid typography** — `clamp()`-based scale (`--text-sm` through `--text-2xl`)
- **Spacing scale** — `clamp()`-based (`--space-xs` through `--space-xl`)
- **Breakpoints** — mobile-first: 640px, 1024px, 1440px
- **Custom properties** — colors, fonts, spacing in `:root`

Global styles in `src/styles/global.css`. Admin-specific styles in `src/styles/admin.css`. Page-specific styles use Astro's scoped `<style>` blocks.

## License

All rights reserved.
