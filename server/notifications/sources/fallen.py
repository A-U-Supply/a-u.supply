"""Fallen-items materializer.

Surfaces new (unresolved) ExtractionFailure rows — including the
consolidated meilisearch_sync class — as notifications. The source_ref
is keyed on failure.id, so re-attempts of the same failure (which bump
attempts/last_attempt_at on the same row) do not re-notify.
"""

from server.models import ExtractionFailure, MediaItem, User

from ._base import (
    advance_watermark,
    get_or_seed_watermark,
    upsert_notification,
    utcnow,
)

SOURCE = "fallen"


def materialize(user: User, db) -> int:
    watermark = get_or_seed_watermark(db, user.id, SOURCE)
    window_end = utcnow()

    rows = (
        db.query(ExtractionFailure, MediaItem)
        .outerjoin(MediaItem, MediaItem.id == ExtractionFailure.media_item_id)
        .filter(
            ExtractionFailure.resolved == False,  # noqa: E712
            ExtractionFailure.last_attempt_at >= watermark,
            ExtractionFailure.last_attempt_at <= window_end,
        )
        .order_by(ExtractionFailure.last_attempt_at.asc())
        .all()
    )

    inserted = 0
    for failure, media in rows:
        ref = f"failure:{failure.id}"
        media_id = failure.media_item_id
        url = f"/admin/search/failures?focus={failure.id}"
        title = f"{failure.extraction_type} failed"
        snippet_bits: list[str] = []
        if media is not None and media.filename:
            snippet_bits.append(media.filename)
        if failure.error_message:
            snippet_bits.append((failure.error_message or "")[:160])
        snippet = " · ".join(snippet_bits) if snippet_bits else None
        if upsert_notification(
            db,
            user_id=user.id,
            source=SOURCE,
            source_ref=ref,
            title=title,
            snippet=snippet,
            url=url,
            created_at=failure.last_attempt_at,
        ):
            inserted += 1

    advance_watermark(db, user.id, SOURCE, window_end)
    return inserted
