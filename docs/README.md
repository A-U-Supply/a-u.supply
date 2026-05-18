# Docs

The deep documentation for [a-u.supply](https://a-u.supply). The root [`README.md`](../README.md) is the quick landing page, and [`AGENTS.md`](../AGENTS.md) is the canonical guide for AI coding agents (with [`CLAUDE.md`](../CLAUDE.md) as a 1-line pointer to it).

## Index

### Orientation

- [`glossary.md`](glossary.md) — admin nomenclature (Auspices, Stacks, Hecatomb, …) → plain English. **Start here if a sidebar label or term doesn't tell you what something does.**
- [`architecture.md`](architecture.md) — stack, directory layout, data flow, auth, search indices, Latents

### Building it

- [`development.md`](development.md) — local setup, dev servers, tests, formatting, user management, worktree workflow
- [`frontend.md`](frontend.md) — UI kit (Tailwind 4, bits-ui, brutalist tokens), Svelte components, event-bus pattern, `<Admin>` layout conventions
- [`api.md`](api.md) — REST API auth, scopes, endpoint groups, special-character encoding
- [`player.md`](player.md) — persistent audio player and `player:queue` event payload

### Section-specific

- [`atelier.md`](atelier.md) — the Atelier admin section (Punctum, Photism, Spectralize). What it is, what's in it, conventions.
- [`bots.md`](bots.md) — App Runner: TOML manifests, the bot-per-repo model, the job-queue flow

### Running it

- [`deployment.md`](deployment.md) — Dokku auto-deploy, Docker build, SSL, dual-repo legacy routing, data persistence
- [`operations.md`](operations.md) — `manage.py` subcommand reference, SSH gotchas, API tokens, the fold (Lemmy) Let's Encrypt gotcha

### Working as an agent

- [`agents.md`](agents.md) — `.claude/` config, hooks, worktrees, slash commands, skills, memory
- [`../AGENTS.md`](../AGENTS.md) — the canonical rule sheet (worktree workflow, PR-only commits, hard gotchas)

### Per-feature plans and history

- [`plans/`](plans/) — pre-implementation plans, one per non-trivial feature. Plans that have shipped carry a `Status: shipped — PR #N` line near the top. See [`plans/README.md`](plans/README.md) for the lifecycle.
- [`history/`](history/) — frozen pre-implementation design docs from before this directory existed. Kept for context. Code is the source of truth.

## Conventions

- Cross-link liberally between docs. The "Related" section at the bottom of each guide is your friend.
- When you ship a non-trivial change, either (a) note the change in the relevant guide, or (b) update the plan in `plans/` so it stays in sync with reality.
- Don't create new top-level guides without an obvious home in the index above. If a topic is small enough to live as a subsection of an existing guide, put it there.
