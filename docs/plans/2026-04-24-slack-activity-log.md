# Slack activity log — `#supply-side`

**Branch:** `feature/slack-activity-log` → PRs #223, #224
**Status:** shipped (initial) + follow-up URL / framing fixes in flight

## Goal

Post site activity to Slack so humans in `#supply-side` can see what's happening without watching the admin UI.

## Approach: two tiers

| Tier | Events | Post cadence |
|---|---|---|
| Immediate | release create / update / publish / unpublish / delete, job submit, batch submit, app register / update | Fire-and-forget on commit |
| Batched | tag add / remove, output indexed, midden discard | 30-min rollup, skipped when queue is empty |

Tiering chosen because the noisy events (tagging, indexing, midden) happen in bursts and would drown out the high-signal events. Rollup skips empty intervals so the channel never gets a heartbeat with nothing in it.

## Transport

- `chat.postMessage` via `httpx.AsyncClient`, fire-and-forget on the running loop — Slack outage can't block a release edit or job submit.
- When `SLACK_BOT_TOKEN` is unset (local dev), posts log to stdout with a `[slack-dry-run]` prefix — forgetting to set the env var isn't a crash.

## Storage: `activity_log` table

```
id, event_type, tier ('immediate' | 'batched'), user_id, payload (JSON), created_at, posted_at
```

Doubles as a lightweight audit trail. Immediate events get `posted_at=now` on insert. Batched events start with `posted_at IS NULL`; the rollup drains them, posts one summary, marks them all posted in one commit.

Migration is inline in `main.py` (matches existing `ALTER TABLE` pattern).

## Config

Dokku env vars:

| Var | Default | Purpose |
|---|---|---|
| `SLACK_BOT_TOKEN` | _unset_ → dry-run | Bot OAuth token (xoxb-…) |
| `SLACK_LOG_CHANNEL` | `C0AUNJ6BMJT` | `#supply-side` channel ID |
| `SLACK_LOG_ENABLED` | `true` | Off switch without unsetting the token |
| `SLACK_ROLLUP_INTERVAL` | `1800` | Seconds between rollup ticks |

## Message style

- Always lead with the user's display name in **bold**.
- Always link to the most relevant next page (release, job, batch slop bucket, midden, search filtered by tag/index/app).
- Include entity (artist) names on release events.
- Attach cover art for published releases (drafts 404 publicly, so Slack can't fetch).
- Emoji per event family, not per message — consistent visual tagging.
- Batch submit is framed as "ran Hecatomb on _App_ — N jobs" because that's how users talk about it; `/api/jobs/batch` is the Hecatomb dispatch endpoint.

## Instrumentation points

- `catalog.py` — 5 release endpoints
- `jobs_api.py` — single + batch job submit, app register / update, index output (×3 paths), midden discard (×3 paths)
- `search_api.py` — tag add / remove (also fixed these to extract the user, previously threw away attribution)

## Open questions that got resolved

- **Noisy vs. quiet channel?** One channel, two tiers. Not two channels.
- **Cadence?** 30 min. Skip empty rollups.
- **Batch cap per rollup post?** No cap — user preference.
- **Bookmark events?** Dropped — personal action, not activity.
- **Hecatomb as app or tool?** Tool. Real apps are `bullethole`, `sparagmos`, `rottengenizdat`; Hecatomb is the UI that fires batches. Batch-submit message now names Hecatomb explicitly.
- **Display-name sensitivity (MCA redaction)?** Slack channel is private, uses `User.name` verbatim — acceptable.

## Follow-up bugs hit after shipping #223

- Hardcoded `/dashboard/*` URLs that don't exist on this site → fixed in #224 with real routes (`/catalog/release?code=`, `/admin/catalog/edit?code=`, `/admin/jobs/detail?id=`, `/admin/search/midden`, `/admin/search?tags=`, `?output_index=`, `?app=`).
- Search page dropdowns (`#filter-output-index`, `#filter-job-app`) silently dropped URL values not in their hardcoded option list → fixed by dynamically appending the URL-provided value as an option before `.value = ...`.
- Batch submit messaging conflated Hecatomb (the dispatch UI) with apps → fixed.

## Verification

- Every URL pattern hit with curl for HTTP 200 (proves route exists, not that filter applies).
- Every formatter smoke-tested end-to-end (dry-run + live `chat.postMessage`).
- Search page URL-filter preservation: only statically verified (client-rendered behind auth — couldn't browser-test without a logged-in session). Needs real-world check after merge.
