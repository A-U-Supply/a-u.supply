# Development

Local setup and day-to-day dev workflow. For the big picture, see [`architecture.md`](architecture.md).

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — Python package manager
- Node.js 22+

### Mac install

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install what you need
brew install node uv
```

## First-time setup

```bash
git clone git@github.com:A-U-Supply/a-u.supply.git
cd a-u.supply
npm install
uv sync
```

### Create your first admin user

```bash
uv run python cli.py create-user --email you@example.com --name "Your Name" --password changeme --role admin
```

## Running locally

Two processes — run them in separate terminals (or ask Claude to start them for you):

```bash
# Terminal 1: FastAPI backend on port 5000
npm run dev:api

# Terminal 2: Astro dev server on port 4321 (proxies /api to FastAPI)
npm run dev
```

Browse to <http://localhost:4321>.

**Frontend only?** If you're only editing pages or styles, `npm run dev` alone works — API calls will fail but pages still render.

## Building

```bash
npm run build    # Outputs to dist/
```

FastAPI serves the built files from `dist/` in production.

## Formatting

Always format before committing — the repo uses Prettier with Astro and Svelte plugins.

```bash
npm run format        # Auto-format JS, Astro, Svelte, CSS
npm run format:check  # Check without writing
```

## Tests

```bash
uv run pytest
```

Tests live in `tests/` and assume the repo root as the working directory.

## User management

No public signup. Users are created via CLI or by an admin through the settings page.

### CLI

```bash
# Create a user
uv run python cli.py create-user --email user@example.com --name "User Name" --password secret --role member

# List all users
uv run python cli.py list-users

# Delete a user
uv run python cli.py delete-user --email user@example.com
```

Roles: `admin` (full access, can manage users), `member` (read/write access, no admin operations).

### Admin UI

Admins can invite and delete users at `/admin/settings`.

## Special characters in product codes

Product codes can contain `#`, spaces, dots, and other characters that need URL encoding. Always encode them in paths:

```javascript
// JavaScript
fetch(`/api/releases/${encodeURIComponent(code)}`)
```

```python
# Python
from urllib.parse import quote
requests.get(f"/api/releases/{quote(code, safe='')}")
```

## Planning before code

Non-trivial changes start with a plan doc at `docs/plans/YYYY-MM-DD-<slug>.md`. See [`plans/README.md`](plans/README.md) for the format and lifecycle.

## Branches and worktrees

Never commit to `master` — a pre-commit hook blocks it. Always work in a feature branch, and per the team convention, create that branch as a worktree rather than switching the primary checkout:

```bash
git fetch origin master
git worktree add .claude/worktrees/<slug> -b <slug> origin/master
cd .claude/worktrees/<slug>
```

Open a PR from there. Worktree mechanics and rationale: [`agents.md`](agents.md#worktree-workflow).

## Related

- [`architecture.md`](architecture.md) — directory layout, what each module does
- [`frontend.md`](frontend.md) — UI kit and Svelte components
- [`operations.md`](operations.md) — `manage.py` for server-side ops
- [`agents.md`](agents.md) — hooks, worktrees, agent setup
- [`../AGENTS.md`](../AGENTS.md) — the canonical rule sheet
