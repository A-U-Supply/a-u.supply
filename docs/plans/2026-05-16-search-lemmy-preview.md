# Search-page Lemmy preview + thread sanity pass

## Goal

Make the Lemmy-backed discussion surface on the search page sensible: one canonical thread per media item, clear "where does this live on Lemmy" affordances everywhere a thread shows, a restrained preview that doesn't crowd the dense grid/list views, and inline reply from feed view so the most common action ("drop a thought on this") doesn't require leaving the page.

## Problems today

- `enrichDiscussionCounts()` in `src/pages/admin/search/index.astro:1930` injects a `💬 N` chip into grid + feed tiles, but it's **non-clickable** — you see a count and have to guess what to do with it.
- List (table) view shows nothing at all.
- `Threads.svelte` lets you create unlimited threads per `media_item`. For "the discussion about this video," multiple threads fragments the conversation for no benefit.
- Nothing on the site links out to Lemmy — not to the post, not to the `stacks` community. Users have no idea their post landed in a real local forum or where to find it natively.
- Users don't know what community a thread goes into; it's silently `stacks` (hardcoded at `server/lemmy_client.py:36`).

## Approach

### 1. Constrain `media_item` to one thread (backend + detail UI)

- `POST /api/threads` with `anchor_type=media_item` becomes upsert-shaped: if a thread already exists, return the existing one with `200` instead of creating a duplicate (don't 409 — clients shouldn't need to handle that branch).
- Leave `project` and `slot` alone — those legitimately host multiple threads for different sub-topics.
- **Auto-thread title**: `"Discussion: <filename>"`. Editable post-create via the existing `PATCH /api/threads/{id}` (already implemented) through a small pencil icon on the thread header — visible only to the thread creator.
- **Detail page** (`Threads.svelte` with `compact` for media_item): render the single thread **inline, always-open** — title, body, full comment tree, reply box — no expand/collapse. Discussion becomes part of the page like Sources or Tags.
- **Empty state on detail page**: same inline layout, but the thread body slot is replaced by a single comment composer ("Start the discussion") that auto-creates the thread on submit using the title format above.
- Existing duplicate `media_item` threads in prod: **leave them**. The list UI continues to handle N — only new creates are coalesced. Plan does not include a one-time cleanup migration.

### 2. Surface community + Lemmy links on every thread surface

Backend (`server/threads_api.py`):

- New endpoint `GET /api/threads/info` → `{lemmy_base_url, configured: bool}`. Cheap, no Lemmy round-trip.
- `_thread_summary()` adds: `lemmy_url` (`{LEMMY_URL}/post/{lemmy_post_id}`), `community_name`, `community_url` (`{LEMMY_URL}/c/{name}`).
- `community_name` lookup: `media_item` → `"stacks"`, `project`/`slot` → `project.slug` (the slug **is** the community name; confirmed against `ensure_project_community` in `server/lemmy_client.py:211-238`). **No schema change needed.**

Frontend (`src/components/Threads.svelte`):

- Section header subline: `in c/stacks ↗` linking to the community URL.
- Per-thread row: small `↗ Lemmy` link to `lemmy_url` (`target="_blank" rel="noopener"`).
- Composer help text (persistent, no banner — audience is admin-only on a `LocalOnly` community, so light-touch is enough): `Posts as you in c/stacks.`

### 3. Search-page preview — restrained, view-aware

**Pushed back on body previews in grid/list.** Grid tiles are 160–220px (128px on mobile); list is a scannable table. Body excerpts would either truncate to noise or break the layout rhythm. Title + reply count is enough signal.

- **Grid view:** keep the `💬 N · 💭 M` chip in `.grid-item__meta`. Make it a **link**.
  - Count == 1 → links direct to `lemmy_url` (new tab).
  - Count > 1 (legacy duplicates only) → links to `/admin/search/detail?id=<id>#threads`.
- **List (table) view:** new compact column "Discussion" with the same chip. Currently has nothing.
- **Feed view:** add a single-line preview row between meta and actions:
  ```
  💬 "Discussion: <filename>" · 12 replies · c/stacks ↗     [Reply ▾]
  ```
  - Title truncates ~60 chars; title links to `lemmy_url`.
  - `[Reply ▾]` is a separate affordance from the chip — lazy-rendered inline composer (see item 4).
  - Click-target audit: the preview row sits **above** `.feed-item__actions`, so existing `data-play-id`/`data-ws-id` delegated handlers aren't affected. The row uses its own delegated handler keyed off `data-reply-id` / `data-thread-link`.

### 4. Inline reply from feed view

- `[Reply ▾]` toggle on each feed row expands a small textarea + Post button in place.
- **Lazy-render the composer.** Do not render N hidden textareas eagerly — render on first toggle, keep mounted thereafter so drafts survive scroll.
- **Auto-create the thread on first comment if none exists.** If `count == 0`, the inline-reply submit first calls `POST /api/threads` (which is now upsert-shaped per item 1) with `title = "Discussion: <filename>"`, `body = null`, then `POST /api/threads/{id}/comments` with the comment body. From the user's POV, they just left a comment; the thread materializes silently.
- **No threading depth in feed view.** Replies from feed are always top-level. Replying to a specific comment requires opening the detail page or the Lemmy link.
- **Post-success feedback:** chip count bumps (`💬 N → N+1`, replies +1), textarea clears, small "Posted ↗" toast that links to the Lemmy post for 4s. No full re-render.

### 5. Counts endpoint enrichment (enables 1, 3, 4)

Extend `GET /api/threads/counts` from `{anchor_id: int}` to:

```json
{
  "counts": {
    "<anchor_id>": {
      "count": 1,
      "thread_id": "uuid",
      "lemmy_post_id": 42,
      "lemmy_url": "https://fold.../post/42",
      "title": "Discussion: <filename>",
      "comment_count": 12,
      "community_name": "stacks",
      "community_url": "https://fold.../c/stacks"
    }
  }
}
```

- For `count > 1` (legacy), return the **most recent** thread's metadata.
- **Comment count is included in v1.** Cost: one Lemmy `/api/v3/post/list` (or `/api/v3/community` with `include_posts`) bulk call per `counts` request, filtered to the threads in the page (~50 items max). Measured against acceptable: ~200ms added to chip enrichment, which is `async` after first paint, so the user sees results regardless.
  - Implementation: collect `lemmy_post_id`s for the page → single Lemmy bulk call → merge into the response. If Lemmy is unavailable, return counts without `comment_count` (chips degrade to `💬 N`); never block the page.
- Thread title comes from a new `Thread.title_cache` column (written on create, refreshed lazily when a single-thread endpoint is hit). This keeps the counts endpoint at a single DB query for the count + the one Lemmy bulk call for replies.

## Data model

- `Thread.title_cache: str | None` — denormalized post title, written on create and refreshed lazily when a thread is fetched individually. Source of truth stays Lemmy. Migration adds the column nullable; no backfill required (`null` means "show '(thread)' or fall back to a fetch on detail page").

No other schema changes.

## API surface (delta)

| Endpoint                       | Change                                                                                |
| ------------------------------ | ------------------------------------------------------------------------------------- |
| `POST /api/threads`            | For `media_item`: return existing thread (200) instead of creating duplicate.         |
| `GET /api/threads/counts`      | Response shape: object per anchor (count + post metadata + comment_count) — breaking. |
| `GET /api/threads/info`        | **New.** `{lemmy_base_url, configured}`. No auth round-trip.                          |
| `GET /api/threads?…`           | `_thread_summary` includes `lemmy_url`, `community_name`, `community_url`.            |
| `GET /api/threads/{id}`        | Same enrichment as above.                                                             |
| `PATCH /api/threads/{id}`      | Unchanged — already supports title/body edits. UI adds a pencil affordance.           |

Counts response is a breaking change to one caller (`enrichDiscussionCounts` in the search page). Update the caller in the same PR.

## Implementation notes

- **Where Reply ▾ lives in feed row:** new preview row directly under `.feed-item__meta`, before `.feed-item__actions`. Reply ▾ is right-aligned within the preview row so the chip-and-title visually own the left side.
- **Chip styling:** keep the current `.discuss-chip` look but add `cursor: pointer`, link underline on hover. Use `<a>` not `<button>` so middle-click / cmd-click works.
- **Edit title affordance:** small pencil icon next to the title in `Threads.svelte`, visible only to `thread.created_by === currentUser.id`. Click → swap title text for an input with Save/Cancel; PATCH on Save.
- **Toast:** reuse whatever toast component the site already has — don't introduce a new one. If none exists, a minimal absolutely-positioned `<div>` is fine; check `src/components/` first.

## Out of scope (v1)

- One-time merge of legacy duplicate `media_item` threads.
- Inline-rendered body previews on search-results listings.
- Notifications when someone replies on Lemmy to your post.
- Reply-to-specific-comment from feed view (always top-level).
- Edit affordance for the thread body (only title is editable in v1).
