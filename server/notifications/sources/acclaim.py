"""Acclaim materializer.

Surfaces +1 votes ("acclaim") on media items you uploaded, by users
other than yourself. Downvotes and self-votes are excluded.

Uploader-of-record is read from MediaSource.uploader_id — a media item
can have multiple sources, and any source with your uploader_id makes
the item yours for acclaim purposes.
"""

from sqlalchemy import select

from server.models import MediaItem, MediaSource, MediaVote, User

from ._base import (
    advance_watermark,
    get_or_seed_watermark,
    upsert_notification,
    utcnow,
)

SOURCE = "acclaim"


def materialize(user: User, db) -> int:
    watermark = get_or_seed_watermark(db, user.id, SOURCE)
    window_end = utcnow()

    my_items_select = (
        select(MediaSource.media_item_id)
        .where(MediaSource.uploader_id == user.id)
        .distinct()
    )

    rows = (
        db.query(MediaVote, MediaItem, User)
        .join(MediaItem, MediaItem.id == MediaVote.media_item_id)
        .join(User, User.id == MediaVote.user_id)
        .filter(
            MediaVote.value == 1,
            MediaVote.user_id != user.id,
            MediaVote.media_item_id.in_(my_items_select),
            MediaVote.created_at >= watermark,
            MediaVote.created_at <= window_end,
        )
        .order_by(MediaVote.created_at.asc())
        .all()
    )

    inserted = 0
    for vote, media, voter in rows:
        ref = f"vote:{vote.media_item_id}:{vote.user_id}"
        url = f"/admin/search/detail?id={vote.media_item_id}"
        title = f"{voter.name} acclaimed your upload"
        snippet = media.filename or media.id
        if upsert_notification(
            db,
            user_id=user.id,
            source=SOURCE,
            source_ref=ref,
            title=title,
            snippet=snippet,
            url=url,
            created_at=vote.created_at,
        ):
            inserted += 1

    advance_watermark(db, user.id, SOURCE, window_end)
    return inserted
