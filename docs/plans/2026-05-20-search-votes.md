### Search-result upvotes / downvotes ("Acclaim" / "Disavow")

**Branch:** `feat/search-votes` → PR TBD
**Status:** plan-only
**Issue:** [#318](https://github.com/A-U-Supply/a-u.supply/issues/318)

## Goal

Per-user upvote/downvote on every media item in the Stacks search, surfaced as `▲ N ▼ M` chips with hover-revealed voter lists across grid / list / feed / detail. Votes are filterable and sortable. UI label is **Acclaim** (up) / **Disavow** (down); internal identifiers stay descriptive (`vote`, `vote_score`, `up_count`, `down_count`).

## Approach (the load-bearing decisions)

1. **Per-user, ±1, mutable.** One row per `(user, media_item)`; value ∈ `{-1, +1}`. Re-vote the same way → retract. Re-vote the other way → switch. No anonymous counters.
2. **Source of truth is SQLite.** Meilisearch carries denormalized aggregates only — it is a read cache, never the authority.
3. **Partial-update Meili per vote, debounced ≈500ms per `media_item`.** The vote endpoint writes the DB synchronously, then schedules a debounced Meili partial-update on `up_count` / `down_count` / `vote_score` / `upvoter_user_ids` / `downvoter_user_ids` / `upvoters` / `downvoters`. Does **not** trigger `sync_media_item()` (which rebuilds the entire doc including color analysis and source ingestion). The voting user sees their own vote instantly via optimistic local state; other admins see it within ~1s.
4. **Voters are denormalized into the search doc.** The admin team is bounded (~5–30 users); the array stays small. Tooltips are instant and survive offline.
5. **Two parallel filterable arrays (`upvoter_user_ids`, `downvoter_user_ids`) power the "my votes" filter.** No server-side intersect or two-round-trip dance.
6. **Independent of `total_reaction_count`.** Slack reactions are a passive upstream signal; votes are an active editorial signal. Both stay sortable; nothing about reactions changes.
7. **Default sort stays `Newest`.** `Acclaim` (vote_score desc) is added as a sort option. Switching the default is a separate UX call.
8. **Vote events go through `queue_batched` to Slack `#supply-side` and the activity feed.** Single event type `media_vote`; payload identifies media item + voter + direction. Coalesced naturally by `queue_batched`'s rollups so we don't drown the channel.
9. **Self-votes allowed, no restriction.** You can acclaim or disavow your own uploads. Curation-as-self-signal.
10. **No auto-side-effects in v1.** Score does not auto-midden, auto-feature, or reweight Hecatomb. We add the data; auto-actions are future plans.

## Data model

New table `media_votes`:

```sql
CREATE TABLE media_votes (
    media_item_id  TEXT    NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL REFERENCES users(id)       ON DELETE CASCADE,
    value          INTEGER NOT NULL,         -- -1 or +1, CHECK enforced
    created_at     DATETIME NOT NULL,
    updated_at     DATETIME NOT NULL,
    PRIMARY KEY (media_item_id, user_id)
);
CREATE INDEX ix_media_votes_user ON media_votes (user_id);
CREATE INDEX ix_media_votes_media ON media_votes (media_item_id);
```

SQLAlchemy model `MediaVote` in `server/models.py`, with `CheckConstraint("value IN (-1, 1)")` and a `value_label` hybrid property (`'up'` / `'down'`) for display.

No backfill required (table starts empty). Migration is additive.

## Meilisearch document delta

Add to every doc built in `server/search_client.py:_build_document`:

```jsonc
{
  "up_count":            12,
  "down_count":          3,
  "vote_score":          9,                                            // up - down
  "upvoter_user_ids":    [1, 4, 7, 12, ...],                           // filterable
  "downvoter_user_ids":  [3, 9],                                       // filterable
  "upvoters":            [{ "user_id": 1, "name": "alice" }, ...],     // display only
  "downvoters":          [{ "user_id": 3, "name": "carol" }, ...]
}
```

Filterable additions:

- `up_count`, `down_count`, `vote_score` (range filters → min/max sliders)
- `upvoter_user_ids`, `downvoter_user_ids` (equality filters → "my votes")

Sortable additions:

- `vote_score` (primary "Acclaim" sort)
- `up_count` (raw popularity, secondary)

`upvoters` / `downvoters` are **neither filterable nor sortable** — only embedded for hover-tooltip rendering. Two parallel ID arrays plus name objects is the cheapest way to satisfy both filtering and display without forcing the frontend to lookup names.

## API surface

| Method | Path                            | Purpose                                                                                                                                                |
| ------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POST` | `/api/search/{media_id}/vote`   | `{value: -1 \| 0 \| 1}`. `0` retracts. Idempotent on (user, media_id). Returns the post-state aggregate `{up_count, down_count, vote_score, my_vote}`. |
| `GET`  | `/api/search/{media_id}/voters` | Fallback voter list (used only if denormalized doc data is missing). `{upvoters: [...], downvoters: [...]}`.                                           |
| `GET`  | `/api/search/votes/mine`        | Returns `{up: [media_id, ...], down: [media_id, ...]}` for the current user. Caller seeds local state for chip highlight after a hard reload.          |

The vote endpoint:

1. Upserts the `media_votes` row (or deletes if `value=0`).
2. Recomputes aggregates from the table (single `GROUP BY` query, cheap).
3. Schedules a debounced Meili partial-update keyed on `media_item_id` (see Sync section).
4. Enqueues a `media_vote` activity event via `queue_batched`.
5. Returns the fresh aggregates to the caller for optimistic-confirm.

No bulk endpoint in v1. If we ever need "vote on N selected items," it's an obvious follow-up.

## Sync strategy

A per-process debounce table `{media_item_id: (last_request_at, asyncio.Task)}`:

- On vote, cancel any pending task for this item, schedule a new one for `now + 500ms`.
- Pending task executes `meili.index(...).update_documents([{...partial fields...}])` with just the vote fields. Meilisearch supports partial updates — fields not present in the update payload are left untouched.
- Aggregates are pulled fresh from SQLite inside the debounced task, so the last write wins regardless of vote order.
- Worst case (process crash): the DB has the truth, Meili is stale. Recovery is a single `manage.py resync-votes` call (also added — see Operations).

500ms is the right tradeoff: rapid clicks coalesce; cross-admin visibility stays under "feels live."

## Frontend

### Component

New `src/components/VoteChip.svelte` (Svelte 5 island) — already inside our "one framework" rule (Svelte islands for interactive bits, Astro for everything else).

Props: `mediaId`, `upCount`, `downCount`, `myVote`, `upvoters`, `downvoters`, `currentUserId`, `size?: 'sm' | 'md'`.

States:

- Default: `▲ 12  3 ▼` — counts dim when zero.
- My-upvote: up arrow highlighted, count bold.
- My-downvote: down arrow highlighted, count bold.
- Loading: optimistic update, no spinner; revert + toast on failure.
- Hover (any chip): floating tooltip `Upvoted by: alice, bob, … (12) — Downvoted by: carol, dave (3)`.

The chip emits a `vote` `CustomEvent` (`{detail: {mediaId, value, aggregates}}`) so the parent page can update its in-memory result list without a re-fetch. Matches the `data-* + CustomEvents` pattern from the UI-kit memory.

### Where the chip lives

- **Grid tile** (`.grid-item__meta`): right of the existing reaction chip, compact.
- **List (table) view**: new column **Acclaim** between `Reactions` and `Tags`.
- **Feed view** (`.feed-item__meta`): inline with the discussion chip + reply row.
- **Detail page** (`src/pages/admin/search/detail.astro`): large vertical Reddit-style stack (`▲ / score / ▼`) next to the title.

### Filter bar additions (`src/pages/admin/search/index.astro`)

Add to the existing filter sidebar (after the Reactions Min input):

- **Acclaim min** (net score, integer input)
- **Upvotes min** (integer input) — power-user, collapsed under an "Advanced" disclosure
- **Downvotes max** (integer input) — same
- **My votes** (select: `Any` / `Mine: upvoted` / `Mine: downvoted` / `Mine: any` / `No votes yet`)

These map cleanly onto the filterable Meili attributes above.

### Sort menu addition

Add `<option value="acclaim">Acclaim</option>` to `#sort-by`; map to `vote_score:desc` in `sortMap`. Default stays `newest`.

## Activity log + Slack

`queue_batched("media_vote", user, media_item_id, value, filename)`:

- Slack rollup window already exists in `slack_notifier.queue_batched` — voting events join the same per-user rollup as uploads / tags. Single user voting on 12 items in a minute produces one Slack line, not twelve.
- Activity feed entry text: `acclaimed <filename>` / `disavowed <filename>` / `retracted vote on <filename>`.
- No notifications to the uploader of an item when someone else votes (v1). Could be a follow-up; the social texture of admin-only voting probably doesn't need this.

## Operations

New `manage.py` subcommand: `resync-votes [--media-id ID]`. Recomputes aggregates from `media_votes` and writes partial updates into Meilisearch for the named item or all items. Matches the AGENTS rule against inline-Python-through-dokku — recovery is `ssh dokku run au-supply -- .venv/bin/python manage.py resync-votes`.

## Out of scope (v1)

- Bulk vote endpoint (`POST /api/search/vote/bulk`).
- Auto-midden on score ≤ −3 (or any threshold-driven side-effect).
- Default-sort switch to Acclaim.
- Hecatomb random-input weighting by vote score.
- Notifications to original uploaders.
- Vote history on a user profile page.
- Vote-decay or time-windowed "hot" rankings.
- Voting on threads or comments (this is media-item voting only; thread voting happens natively in Lemmy).

## Open questions

1. **Confirm `media_vote` qualifies for `queue_batched` rollups vs being silenced entirely.** A power-user could acclaim 200 items in a triage session; even one Slack line per session might be noisy. Default in plan: route through `queue_batched`. Easy to flip to silent if it turns out annoying.
2. **Should detail-page voters list expand into a clickable "filter by this user's votes" affordance?** Cheap to add (the filter exists), but might encourage social comparison. Default: not in v1.
3. **Chip placement on grid tiles when the tile is small (mobile 128px).** The reactions chip + acclaim chip + discussion chip starts to crowd the meta row. Plan: hide the acclaim chip on tiles below 160px and require hover-to-reveal — but worth eyeballing in the browser before shipping.
