"""Per-media-item debounced push of vote fields into Meilisearch.

The vote endpoint writes SQLite synchronously (source of truth), then asks
this module to make sure Meilisearch eventually matches. Rapid clicks on the
same item coalesce into a single partial update so we don't hammer the
indexer.

Design:

- One pending `asyncio.Task` per `media_item_id`, scheduled to fire after a
  short debounce window. New requests within the window cancel the prior
  task and schedule a fresh one.
- When the task fires, it reads the current aggregates straight from the DB
  (so the last DB write wins, regardless of vote arrival order).
- All work is fire-and-forget. The HTTP response to the voter already carries
  the fresh aggregates from the DB; Meilisearch sync only matters for OTHER
  admins seeing the change in their next search.
- If no event loop is running (sync tests, CLI tools), we fall back to a
  synchronous push — slower but never silently no-ops.

Manual recovery from drift (process crash, Meili outage): run
`uv run python manage.py resync-votes`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from server.models import MediaItem, SessionLocal
from server.search_client import update_vote_fields

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = float(os.environ.get("VOTE_SYNC_DEBOUNCE_SECONDS", "0.5"))

_pending: dict[str, asyncio.Task] = {}


def schedule(media_item_id: str) -> None:
    """Schedule a debounced Meili partial update for this media item.

    Cancels any pending task for the same id. Safe to call from a request
    handler — never raises.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop (e.g. CLI). Push immediately, synchronously.
        _flush_sync(media_item_id)
        return

    prior = _pending.get(media_item_id)
    if prior is not None and not prior.done():
        prior.cancel()

    task = loop.create_task(_debounced_flush(media_item_id))
    _pending[media_item_id] = task


async def _debounced_flush(media_item_id: str) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _flush_sync, media_item_id)
    finally:
        # Don't leak entries for items that aren't being voted on anymore.
        if _pending.get(media_item_id) is asyncio.current_task():
            _pending.pop(media_item_id, None)


def _flush_sync(media_item_id: str) -> None:
    session = SessionLocal()
    try:
        media_item: Optional[MediaItem] = (
            session.query(MediaItem).filter(MediaItem.id == media_item_id).first()
        )
        if media_item is None:
            return
        update_vote_fields(session, media_item)
    except Exception:
        logger.exception("vote_sync flush failed for %s", media_item_id)
    finally:
        session.close()
