# Python package reorg — move modules into `server/`

## Goal

Clean up the root directory by moving the 11 FastAPI-router and support modules into a `server/` Python package. Entry points stay at the root because `Dockerfile`, `Procfile`, and `.github/workflows/` reference them by name.

## Motivation

Today the repo root has 16 `.py` files. To contributors (many of whom are non-technical and contribute via Claude Code), this reads as clutter. A single `server/` package makes the shape of the app obvious and keeps the root reserved for deploy-anchored entry points.

Paired with PR #228 (docs reorg), this is the second half of the "make the repo feel organized" cleanup.

## Approach

**Stay at repo root** (referenced by deploy config — do NOT move):

- `main.py` — Dockerfile `CMD [".venv/bin/uvicorn", "main:app", ...]`, Procfile `web`, `npm run dev:api`
- `worker.py` — Procfile `worker: .venv/bin/python worker.py`
- `cli.py` — `.github/workflows/create-user.yml` runs `cli.py create-user ...`
- `seed_catalog.py` — `.github/workflows/seed-catalog.yml`, `setup-storage.yml`
- `manage.py` — invoked ad-hoc via SSH on the Dokku host
- `reset_db.py` — one-off utility

**Move into `server/`**:

- `admin_api.py`, `auth.py`, `bookmarks_api.py`, `catalog.py`, `jobs_api.py`, `search_api.py` — FastAPI routers
- `models.py` — SQLAlchemy models
- `extraction.py` — async metadata extraction
- `search_client.py` — Meilisearch wrapper
- `slack_notifier.py`, `slack_scraper.py` — Slack integration

**Package initializer**: `server/__init__.py` — empty file.

## Import-rewrite shape

Across 243 import sites in 28 files:

| Current | After |
|---------|-------|
| `from models import X` | `from server.models import X` |
| `from auth import X` | `from server.auth import X` |
| `import jobs_api` (5 sites, in tests only, used for `monkeypatch.setattr(jobs_api, ...)`) | `from server import jobs_api` |

All 243 sites are bare-module imports (no `sys.path` manipulation, no `__init__.py` previously existed, no dynamic imports via `importlib`). A `sed`-based rewrite is viable, but I'll do it with Python-aware edits to avoid false positives (e.g. strings containing `auth`).

## Deferred imports (inside function bodies)

Previously flagged:

- `catalog.py` — imports `search_api` inside `_load_content_type()`
- `extraction.py` — deferred imports inside functions around line 500+
- `jobs_api.py` — deferred imports (breaks circular cycle with `search_api`)
- `main.py` — dynamic `jobs_api` import
- `manage.py` — deferred imports of `extraction`, `search_client`, `slack_scraper`
- `search_api.py` — deferred imports of `jobs_api`, `extraction`, etc.

The grep catches these too since `^\s*(from|import)` matches indented imports inside `def`s.

## Tests

- `tests/conftest.py` — 6 bare-module imports (models, auth, main)
- 11 test files with ~140 total import sites

`main` stays at root, so `from main import app` in conftest is unchanged.

## What does NOT change

- `Dockerfile`, `Procfile`, `package.json`, `app.json`
- `.github/workflows/*.yml`
- `astro.config.mjs`, `tsconfig.json`, `pyproject.toml` (except possibly `testpaths` remains `["tests"]` — fine)
- Any Astro/frontend file
- Any runtime behavior — this is purely code organization

## Verification

1. `uv run pytest` — expect same result as master: 173 passed + 4 pre-existing failures (confirmed during PR #228 verification)
2. `npm run build` — passes (unaffected by Python changes)
3. `npm run dev:api` — starts without import errors (spot check)
4. `grep -rE "^\s*(from|import)\s+(admin_api|auth|bookmarks_api|catalog|extraction|jobs_api|models|search_api|search_client|slack_notifier|slack_scraper)(\s|\.|$)" --include="*.py" .` — zero hits after rewrite (all converted to `server.X`)

## Open questions

None. The deploy-config / entry-point split is well-understood from PR #228's exploration. The import rewrite is mechanical and grep-checkable.

## Lifecycle note

Per `docs/plans/README.md`, the ideal lifecycle would be plan PR → review → implementation PR. In this case the user already approved the approach verbally during the PR #228 plan-mode conversation, so the plan file ships in the same PR as the implementation. Cross-reference from the implementation PR description.
