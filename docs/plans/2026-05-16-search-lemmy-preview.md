# Search-page Lemmy preview + thread sanity pass

## Goal

Make the Lemmy-backed discussion surface on the search page sensible: one canonical thread per media item, clear "where does this live on Lemmy" affordances everywhere a thread shows, and a restrained preview that doesn't crowd the dense grid/list views.

## Problems today

- `enrichDiscussionCounts()` in `src/pages/admin/search/index.astro:1930` injects a `💬 N` chip into grid + feed tiles, but it's **non-clickable** — you see a count and have to guess what to do with it.
- List (table) view shows nothing at all.
- `Threads.svelte` lets you create unlimited threads per `media_item`. For "the discussion about this video," multiple threads fragments the conversation for no benefit.
- Nothing on the site links out to Lemmy — not to the post, not to the `stacks` community. Users have no idea their post landed in a real federated forum or where to find it natively.
- Users don't know what community a thread goes into; it's silently `stacks` (hardcoded at `server/lemmy_client.py:36`).

## Approach

Three changes, smallest-blast-radius first.

### 1. Constrain `media_item` to one thread (backend + detail UI)

- `POST /api/threads` with `anchor_type=media_item` becomes upsert-shaped: if a thread already exists, return the existing one with `200` instead of creating a duplicate (don't 409 — clients shouldn't need to handle that branch).
- Leave `project` and `slot` alone — those legitimately host multiple threads for different sub-topics.
- `Threads.svelte` in `compact` mode for `media_item`: render the single thread inline (no list, no expand/collapse). If no thread exists, swap "+ New thread" for a primary "Start the discussion" CTA.
- Existing duplicate `media_item` threads in prod: leave them. The list UI continues to handle N — only new creates are coalesced.

### 2. Surface community + Lemmy links on every thread surface

Backend (`server/threads_api.py`):

- New endpoint `GET /api/threads/info` → `{lemmy_base_url, configured: bool}`. Cheap, no auth round-trip.
- `_thread_summary()` adds: `lemmy_url` (`{LEMMY_URL}/post/{lemmy_post_id}`), `community_name`, `community_url` (`{LEMMY_URL}/c/{name}`).
- `community_name` comes from a small lookup: `media_item` → `"stacks"`, `project`/`slot` → the project's community slug (already stored on `Project.lemmy_community_name`, verify).

Frontend (`src/components/Threads.svelte`):

- Section header subline: `in c/stacks ↗` linking to the community URL.
- Per-thread row: small `↗ Lemmy` link to `lemmy_url` (`target="_blank" rel="noopener"`).
- Composer help text: "Posts as you on Lemmy in `c/stacks` — visible to anyone browsing the community."

### 3. Search-page preview — restrained, view-aware (item 5: inline reply lives here)

**Push back on the user's instinct to add previews everywhere.** Grid tiles are 160–220px (128px on mobile); list view is a scannable table. Body excerpts in either would either truncate to noise or break the layout rhythm.

- **Grid + List:** keep the `💬 N` chip, make it a **link**.
  - Count == 1 → links direct to `lemmy_url` of the single thread (new tab).
  - Count > 1 → links to `/admin/search/detail?id=<id>#threads`.
  - List view gets the chip too (currently has nothing).
- **Feed view:** add a single-line preview row under the meta:
  ```
  💬 "<thread title>" — in c/stacks ↗     [Reply ▾]
  ```
  Title only, ~60 char truncate, title links to `lemmy_url`. No body excerpt. No comment count in v1 (would require a per-item Lemmy round-trip; defer).

  **Inline reply from feed view (item 5).** A `[Reply ▾]` toggle on each feed row expands a small textarea + Post button in place. Posts directly to the item's thread via `POST /api/threads/{id}/comments`. Constraints:
  - **Lazy-render the composer.** Do not render N hidden textareas eagerly — render on first toggle, keep mounted thereafter so the user can come back to a draft as they scroll.
  - **Auto-create the thread on first comment if none exists.** If `count == 0`, the inline-reply POST first creates a thread with `title = <filename>`, `body = null`, then posts the comment. From the user's POV, they just left a comment; the thread materializes silently. Backend supports this naturally because POST is idempotent per item 1.
  - **No threading depth in feed view.** Replies from feed are always top-level on the thread. Anyone wanting to reply to a specific comment opens the detail page (or the Lemmy link).
  - **Post-success feedback:** chip count bumps `💬 N → N+1`, textarea clears, small "Posted ↗" toast that links to the Lemmy post for 4s. No full re-render.

### 4. Counts endpoint enrichment (enables 1, 3)

Extend `GET /api/threads/counts` from `{anchor_id: int}` to:

```json
{
  "counts": {
    "<anchor_id>": {
      "count": 1,
      "lemmy_post_id": 42,
      "lemmy_url": "https://fold.../post/42",
      "title": "first thread title",
      "community_name": "stacks",
      "community_url": "https://fold.../c/stacks"
    }
  }
}
```

- For `count > 1`, return the **most recent** thread's metadata (title, post id, url).
- Title comes from `Thread`'s denormalized cache if we have one, else fall back to "(thread)" — **do not** fetch from Lemmy per item; this endpoint must stay one DB query.
- This means `Thread` model needs a `title_cache TEXT` column populated on create + on the next read after edit. Cheap addition; migration adds the column nullable.

## Data model

- `Thread.title_cache: str | None` — denormalized post title, written on create and refreshed lazily when a thread is fetched individually. Source of truth stays Lemmy.

No other schema changes.

## API surface (delta)

| Endpoint                       | Change                                                                                |
| ------------------------------ | ------------------------------------------------------------------------------------- |
| `POST /api/threads`            | For `media_item`: return existing thread (200) instead of creating duplicate.         |
| `GET /api/threads/counts`      | Response shape: object per anchor (count + post metadata) instead of bare int.        |
| `GET /api/threads/info`        | **New.** `{lemmy_base_url, configured}`. No auth round-trip.                          |
| `GET /api/threads?…`           | `_thread_summary` includes `lemmy_url`, `community_name`, `community_url`.            |
| `GET /api/threads/{id}`        | Same enrichment as above.                                                             |

Counts response is a breaking change to one caller (`enrichDiscussionCounts` in the search page). Update the caller in the same PR.

## Open questions

- **Project/slot communities — do we have the slug at hand?** Plan assumes `Project.lemmy_community_name` exists. If not, store it when the community is provisioned (`ensure_project_community`) — small backend change.
- **Should the existing duplicate `media_item` threads be merged?** Plan says no (leave them, only new creates collapse). If we want a one-time cleanup migration, that's an extra item.
- **Composer "post to Lemmy as you" disclosure** — first-time only (banner with dismiss), or persistent help text? Plan defaults to persistent help text — simpler, no dismiss-state to track. Push back if you want a banner.
- **Feed-view preview interaction with bookmarks/select** — the new row sits between meta and actions. Verify it doesn't intercept clicks that should hit `.bm-star` or the checkbox.

## Out of scope (v1)

- Comment count in the chip — requires per-item Lemmy fetches; revisit once we have a cache or a Lemmy batch endpoint.
- Inline-rendered body previews on the search page — fight the urge.
- Notifications when someone replies on Lemmy to your post — separate plan.
