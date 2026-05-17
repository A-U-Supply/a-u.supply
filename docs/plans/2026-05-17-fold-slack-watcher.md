# Fold → `#supply-side` Slack notifier

**Branch:** `feat/fold-slack-watcher`
**Status:** in progress

## Goal

`#supply-side` already hears about Latents-originated communities and threads (via `latent.created` / `latent.thread_created`). It hears **nothing** about activity that originates natively on fold.a-u.supply.com — someone logging into Lemmy directly and creating a community or making a post.

This plan closes that gap so the Slack channel sees every new local community and every new local post on fold, regardless of where it was created.

## Approach: poll the Lemmy API from the existing FastAPI app

The cleanest seam is to add a background task inside the a-u.supply backend (alongside `rollup_loop` in `main.py`'s lifespan). Reasons:

- Reuses `slack_notifier.notify_immediate` for transport + persistence to `activity_log`.
- Reuses `LEMMY_URL` / `LEMMY_ADMIN_TOKEN` already wired in `lemmy_client.py`.
- Same deploy unit — no second container, no extra cron infra.
- Dedup against Latents-originated communities/posts uses the same DB we already query.

Polling cadence: 5 min. Two API calls per tick (community list, post list). Negligible load.

## Components

### `server/fold_watcher.py` (new)

Async background task:

```
async def watcher_loop():
    await asyncio.sleep(60)             # settle
    while True:
        try:
            await tick()
        except Exception:
            logger.exception("fold_watcher tick failed")
        await asyncio.sleep(FOLD_WATCHER_INTERVAL)
```

`tick()`:

1. Read state row → `last_community_id`, `last_post_id`.
2. `GET /api/v3/community/list?type_=Local&sort=New&limit=20` (admin token).
3. `GET /api/v3/post/list?type_=Local&sort=New&limit=50` (admin token).
4. For each community with `id > last_community_id`:
   - Skip if a `Project` row has that `lemmy_community_id` (already announced as `latent.created`).
   - Skip if it's the `stacks` sentinel community (it gets created once on bootstrap).
   - Else `notify_immediate("fold.community_created", user=resolve(creator), name=, title=, …)`.
5. For each post with `id > last_post_id`:
   - Skip if a `Thread` row has that `lemmy_post_id` (already announced as `latent.thread_created`).
   - Else `notify_immediate("fold.post_created", user=resolve(creator), title=, community_name=, post_url=, …)`.
6. Update high-water marks to the max IDs seen, commit.

User resolution: look up `User` by `users.lemmy_user_id == creator_id`. If found, pass the User (so the formatter uses `User.name`, matching the rest of the channel). If not (fold-native user), pass `user=None` and stash `lemmy_username` + `lemmy_display_name` in the payload — the formatter falls back to those.

### State storage: `fold_watcher_state` table

Tiny single-row KV:

```python
class FoldWatcherState(Base):
    __tablename__ = "fold_watcher_state"
    key = Column(String, primary_key=True)
    value = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
```

Keys: `last_community_id`, `last_post_id`.

**Bootstrap:** on first run with no state row, write the current max IDs and announce nothing. Otherwise the first deploy backflood-spams every historical post into Slack. Subsequent ticks announce anything `> stored`.

### `slack_notifier.py` formatters

Two new formatters:

- `_format_fold_community_created(user_name, d)` — emoji `🪺`, links to `https://fold.a-u.supply.com/c/<name>`.
- `_format_fold_post_created(user_name, d)` — emoji `📝`, links to `https://fold.a-u.supply.com/post/<id>`, surfaces community name and title.

Both honor `d.get("lemmy_display_name") or d.get("lemmy_username")` if `user_name == "someone"` (i.e. no a-u.supply user is linked).

Registered in `_IMMEDIATE_FORMATTERS`.

## Config

| Env var | Default | Purpose |
|---|---|---|
| `FOLD_WATCHER_ENABLED` | `true` | Off switch without unsetting LEMMY_URL |
| `FOLD_WATCHER_INTERVAL` | `300` | Seconds between ticks |
| `FOLD_BASE_URL` | derived from `LEMMY_URL` | Public host used for Slack links (fold.a-u.supply.com) |

`LEMMY_URL` and `LEMMY_ADMIN_TOKEN` are reused from existing config; no new secrets.

## De-duplication, in detail

Two failure modes to avoid:

1. **Double-announcing Latents activity** — opening a latent already fires `latent.created`; calling `ensure_project_community` later creates the Lemmy community. The watcher must not re-announce it as `fold.community_created`. Skip when `SELECT 1 FROM projects WHERE lemmy_community_id = :id`.
2. **Double-announcing Latents threads** — `latent.thread_created` already fires when a thread is opened via Latents UI. The watcher must not re-announce the underlying Lemmy post. Skip when `SELECT 1 FROM threads WHERE lemmy_post_id = :id`.

Both checks are single indexed lookups; cheap.

## Failure handling

- Lemmy unreachable → log + skip the tick. State unchanged → next tick retries.
- Slack unreachable → `_post_slack` already swallows errors. Advance state anyway (matches existing behavior; one missed notification is OK, repeated retries spam the channel).
- DB errors → log + skip the tick.

## Verification

Local (dry-run, `SLACK_BOT_TOKEN` unset):

- Bootstrap with empty state → no `[slack-dry-run]` lines on first tick.
- Open a new community in fold → next tick emits one `[slack-dry-run] fold.community_created` line.
- Make a native post in fold → next tick emits one `[slack-dry-run] fold.post_created` line.
- Open a Latent (which creates a community + thread) → `latent.created` + `latent.thread_created` lines, no duplicate `fold.*` lines.

Prod:

- Watch `#supply-side` after the deploy. Bootstrap is silent. First native fold action should produce a notification within 5 min.

## Out of scope

- Auto-subscribing all users to all communities (covered in a separate conversation; deferred).
- Comment notifications (noisy; can add later if wanted).
- Federated (remote) community/post announcements (filtered out by `type_=Local`).
- Modlog events (community deletion, ban, etc.).
