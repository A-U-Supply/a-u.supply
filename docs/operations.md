# Operations

Server-side commands and gotchas for running the live a-u.supply instance. For deploy mechanics (auto-deploy, Docker, SSL), see [`deployment.md`](deployment.md).

## SSH access

```bash
# Interactive shell on the running web container
ssh -t dokku enter au-supply web bash

# One-off command in a fresh container (clean slate, exits when done)
ssh dokku run au-supply .venv/bin/python manage.py <subcommand>

# Command inside the currently running web container
ssh dokku enter au-supply web .venv/bin/python manage.py <subcommand>
```

`ssh dokku` is an SSH alias on the local machine. Never use `ssh dokku@<ip>`.

## The Dokku quoting trap

**Never pass inline Python or multiline strings through `ssh dokku run` / `ssh dokku enter`.** Dokku's argument parser mangles quotes, backslashes, and other special characters — strings arrive at the container malformed. Symptoms include silent truncation, escaped quotes turning into literal `\` characters, and JSON payloads failing to parse.

The fix: add a `manage.py` subcommand (or a small Python helper) and invoke that. If a subcommand needs to receive structured data, hex-encode the JSON on the caller side and decode it inside the subcommand — that's the only reliable way to pipe a complex object through `ssh dokku run`.

## `manage.py` subcommands

`manage.py` is the single dispatcher for server-side management operations. Add new ops here rather than crafting one-liners.

```bash
# From your laptop
ssh dokku run au-supply .venv/bin/python manage.py <subcommand> [args...]
```

| Subcommand | Args | What it does |
|------------|------|--------------|
| `create-user` | `<email> <password> <name> [role]` | Create a user. Role defaults to `member`; pass `admin` for full access. |
| `set-role` | `<email> <role>` | Promote / demote a user. |
| `list-users` | — | Print all users. |
| `make-apikey` | `<email> <label> <scope>` | Generate an API key (`au_…`). Scope is `read` / `write` / `admin`. |
| `revoke-apikey` | `<key-prefix>` | Revoke a key by its prefix. |
| `reindex` | — | Wipe + rebuild the Meilisearch indices from SQLite. Safe; SQLite is the source of truth. |
| `migrate-index` | `<old-index> <new-index>` | Move `MediaItem` rows from one `output_index` to another and reindex. |
| `resync-votes` | `[<media_id>]` | Recompute Acclaim/Disavow aggregates + voter lists from `media_votes` and push partial updates into Meilisearch. Recovery for sync drift after a crash or Meili outage. Targets a single item when an id is given, otherwise every item. |
| `refresh-app` | `<name>` | Re-sync a single `AppDefinition` row from its TOML manifest. Run after editing an `apps/*.toml` file. |
| `refresh-all-apps` | — | Same for every manifest. |
| `check-meta` | — | Audit metadata extraction coverage. |
| `color-histogram` | — | Backfill per-image colour histograms. |
| `color-overlap` | — | Diagnostic for the colour-similarity feature. |
| `source-audit` | — | Audit `media_source` rows for orphans / inconsistencies. |
| `backfill-posters` | — | Backfill Slack poster identities on existing media items. |
| `add-slack-mapping` | `<slack-user-id> <user-email>` | Manually link a Slack user to an au-supply account. |
| `seed-slack-mapping` | `[--dry-run]` | Auto-map Slack users to accounts by email. Use `--dry-run` first. |
| `backfill-slack-uploader-id` | `[--dry-run]` | Populate `uploader_id` on legacy slack-source rows. Requires `seed-slack-mapping` + `backfill-posters` to have run first. |
| `backfill-text` | — | Backfill `slack_message_text` on slack-source rows. |
| `backfill-transcripts` | — | Generate audio transcripts via faster-whisper. |
| `backfill-ocr` | `[--include-empty] [--all] [--restart]` | OCR-extract text from images via EasyOCR. Checkpointed — interrupted runs resume. |
| `test-ocr` | `<media_id> [--write]` | Spot-check OCR on a single item. |
| `backfill-ai-descriptions` | `[--all] [--restart]` | Generate AI vision descriptions, tags, and structured attributes via the vision model. Requires `VISION_API_KEY`. Checkpointed. See [`ai-image-descriptions.md`](ai-image-descriptions.md). |
| `test-ai-description` | `<media_id> [--write]` | Spot-check the vision pipeline on a single item. |
| `backfill-thumbnails` | — | Generate sm / md / lg thumbnails for image media items missing any of them. |
| `backfill-video-thumbnails` | — | Grab a poster frame for any video missing one. The image command above skips videos entirely, so anything whose extraction never ran has stayed frameless and renders as a placeholder. Runs ffprobe first when there's no `MediaVideoMeta` row yet. |

Run `ssh dokku run au-supply .venv/bin/python manage.py` (no subcommand) to print the live usage banner.

## API admin tokens

Programmatic admin access goes through API keys, not interactive SSH:

```bash
# Generate
ssh dokku run au-supply .venv/bin/python manage.py make-apikey you@example.com "laptop" admin

# Use
curl https://a-u.supply/api/admin/stats -H 'Authorization: Bearer au_xxxxx'
```

Token scopes: `read` (browse) < `write` (mutate + manage own keys) < `admin` (everything, plus user management and scrapes). See [`api.md`](api.md).

The `AU_API_KEY` env var in `.env` (local) holds a long-lived admin Bearer token for local scripts and the legacy webhook receiver. If you rotate it, also update the matching `APIKey` row in the DB.

## Data persistence

SQLite, release media, search media, and the legacy site each sit on their own Dokku-mounted volume that survives container rebuilds. Merging a PR never drops data.

The worker additionally mounts a **model cache** at `/var/lib/dokku/data/storage/au-supply-model-cache` (host) → same path inside the worker. PyTorch / HuggingFace weights live here (`TORCH_HOME`, `HF_HOME`) so dream / neural bot jobs download models once. The path is exposed to the worker container via the `MODEL_CACHE_DIR` env var, and the worker passes the same host path through to child bot containers as `-v` mounts.

If the app is ever rebuilt from scratch, re-run `.github/workflows/setup-storage.yml` to re-establish the mount and env var. Likewise `setup-legacy.yml` re-establishes the legacy-site volume.

## Session bundles (Latents)

Multi-part DAW bundle uploads (`.logicx` etc.) stage under `{SEARCH_MEDIA_DIR}/.bundles/{uuid}/` and are moved into `session/YYYY-MM/` on completion. Abandoned staging dirs older than **24h** are reaped on app startup. Relevant env vars:

| Var | Default | Purpose |
|---|---|---|
| `MAX_UPLOAD_PART_BYTES` | `2147483648` (2 GiB); **20 GiB in prod** | Per-file size cap for bundle parts (413 above) |
| `BUNDLE_STALE_HOURS` | `24` | Staging TTL before the startup reaper deletes it |
| `WHISPER_MAX_SECONDS` | `900` | Skip speech transcription for media longer than this (0 = transcribe everything) |
| `SESSION_LOGIC_PARSE` | unset (off) | `1` enables the experimental Logic `ProjectData` marker parser (best-effort, log-only — see `server/session_extract/logic_markers.py`) |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Endpoint for audio AI text tagging. In prod this points at SiliconFlow (`https://api.siliconflow.com/v1`) with `DEEPSEEK_MODEL=deepseek-ai/DeepSeek-V4-Flash`, because the direct DeepSeek key is invalid (audio tagging silently no-ops on 401s — check this first if tags stop appearing) |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | Model id for audio text tagging (namespaced when using SiliconFlow) |

Session bundles are stored **unpacked** (directory tree + `manifest.json`); downloads are zipped on the fly by `GET /api/media/{id}/file`. Bundle parts stream through nginx, so `client_max_body_size` must cover the largest single file inside a bundle (see [`deployment.md`](deployment.md#nginx)).

Disk sizing note: a 5 GB bundle stays 5 GB on disk (extraction hard-links children out of the bundle when possible), but the *parts* of an upload count against the volume while staging. Watch the search-media volume on big upload days.

## fold.a-u.supply (Lemmy)

The private Lemmy instance that backs Latents discussion and the `stacks` global community. Runs on a separate Dokku app. Local-only — no federation.

### The Let's Encrypt gotcha

fold's nginx config requires a manual `^~` override on `fold-api-proxy.conf` for the `/.well-known/acme-challenge/` path. Without it, Let's Encrypt cert renewal silently fails because the API proxy block matches first. If fold's cert is expiring and renewal isn't working, this is the first thing to check.

### Lemmy version notes

We're on Lemmy 0.19.x. 0.19 doesn't have native post tags / flair — the 1.0 alpha adds backend support but the UI hasn't shipped. Revisit "tag a fold post" once 1.0 stable lands.

### Read-side linkage (notifications / inbox)

The Inbox feature in a-u.supply reads Fold's Postgres directly (raw SQL via a
second SQLAlchemy engine — see `server/fold_db.py`). To enable it on Dokku:

```
dokku postgres:link <fold-postgres-svc> a-u-supply --alias FOLD_DATABASE
```

This exports `FOLD_DATABASE_URL` into the app container. The `--alias` keeps
it from clobbering `DATABASE_URL` (a-u.supply uses SQLite, but other apps may
not). If `FOLD_DATABASE_URL` is unset, the Fold notification sources
(community / thread / inbox) gracefully no-op and the Inbox page only shows
internal sources (fallen, midden, acclaim).

## GitHub Actions (overview)

See [`deployment.md`](deployment.md) for the full deploy flow. Quick reference:

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `deploy.yml` | Push to `master` | Push to Dokku, restart the web container |
| `create-user.yml` | Manual | Run `cli.py create-user` on the server |
| `seed-catalog.yml` | Manual | One-shot `seed_catalog.py` invocation |
| `setup-storage.yml` | Manual | (Re-)establish the model-cache volume + env var |
| `setup-legacy.yml` | Manual | (Re-)establish the legacy-site volume + clone |

Workflows reference entry-point script names (`cli.py`, `seed_catalog.py`, `main:app`) directly. If you rename or relocate any of those, update the workflows in the same PR.

## Related

- [`deployment.md`](deployment.md) — Dokku auto-deploy, SSL, legacy routing
- [`api.md`](api.md) — API authentication and scopes
- [`agents.md`](agents.md) — agent-side config
- Memory: there are user preferences around Dokku commands that the agent loads automatically each session. If you're a non-Claude agent, the gist is: prefer `dokku run` over `dokku enter`, never one-line inline Python.
