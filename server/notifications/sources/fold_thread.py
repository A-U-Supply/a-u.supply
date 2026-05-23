"""Fold thread materializer.

For each FoldThreadSubscription (a user watching a specific Lemmy post),
surface new comments on that post since the last poll. Self-comments
are excluded.
"""

from sqlalchemy import bindparam, text

from server import fold_db
from server.models import FoldThreadSubscription, User

from ._base import (
    advance_watermark,
    get_or_seed_watermark,
    upsert_notification,
    utcnow,
)

SOURCE = "fold_thread"

_FOLD_BASE = "https://fold.a-u.supply"

_COMMENTS_SQL = text(
    """
    SELECT cm.id AS comment_id,
           cm.content AS content,
           cm.published AS published,
           cm.post_id AS post_id,
           p.name AS post_title,
           pe.name AS author_name
      FROM comment cm
      JOIN post p    ON p.id = cm.post_id
      JOIN person pe ON pe.id = cm.creator_id
     WHERE cm.post_id IN :post_ids
       AND cm.published >= :watermark
       AND cm.published <= :window_end
       AND cm.deleted = false
       AND cm.removed = false
       AND cm.creator_id != :my_id
     ORDER BY cm.published ASC
     LIMIT 200
    """
).bindparams(bindparam("post_ids", expanding=True))


def materialize(user: User, db) -> int:
    if not fold_db.is_configured():
        return 0

    subs = (
        db.query(FoldThreadSubscription)
        .filter_by(user_id=user.id)
        .all()
    )
    if not subs:
        return 0

    watermark = get_or_seed_watermark(db, user.id, SOURCE)
    window_end = utcnow()
    post_ids = [s.lemmy_post_id for s in subs]
    my_id = user.lemmy_user_id if user.lemmy_user_id else -1

    inserted = 0
    with fold_db.fold_connection() as conn:
        rows = conn.execute(
            _COMMENTS_SQL,
            {
                "post_ids": post_ids,
                "watermark": watermark,
                "window_end": window_end,
                "my_id": my_id,
            },
        ).mappings()
        for row in rows:
            ref = f"fold_comment:{row['comment_id']}"
            url = f"{_FOLD_BASE}/comment/{row['comment_id']}"
            title = f"new comment on: {(row['post_title'] or '')[:100]}"
            content = (row["content"] or "").strip()
            snippet = f"{row['author_name']}: {content[:140]}" if content else row["author_name"]
            if upsert_notification(
                db,
                user_id=user.id,
                source=SOURCE,
                source_ref=ref,
                title=title,
                snippet=snippet,
                url=url,
                actor=row["author_name"],
                community=row["community_name"],
                created_at=row["published"],
            ):
                inserted += 1

    advance_watermark(db, user.id, SOURCE, window_end)
    return inserted
