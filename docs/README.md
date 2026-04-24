# Docs

Detailed guides for working on [a-u.supply](https://a-u.supply). The root [`README.md`](../README.md) is the quick landing page and [`CLAUDE.md`](../CLAUDE.md) is the primary contributor guide.

## Guides

- [`development.md`](development.md) — local setup, running the dev servers, tests, formatting, user management
- [`architecture.md`](architecture.md) — stack, directory layout, data flow, auth model, CSS
- [`deployment.md`](deployment.md) — Dokku auto-deploy, SSH access, data persistence, legacy site routing
- [`api.md`](api.md) — API authentication, scopes, endpoint overview
- [`bots.md`](bots.md) — TOML manifests, job queue flow, how to add a new bot
- [`player.md`](player.md) — persistent audio player events and queue pattern

## Subdirectories

- [`plans/`](plans/) — per-feature design plans (write one here before non-trivial work; see [`plans/README.md`](plans/README.md))
- [`history/`](history/) — frozen pre-implementation design docs, kept for context. Code is the source of truth.
