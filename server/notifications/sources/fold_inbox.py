"""Fold inbox materializer.

Surfaces replies and @-mentions addressed to the user's linked
lemmy_user_id, using Lemmy's native inbox tables (comment_reply +
person_mention). No subscription picker is needed — every user with a
linked Fold account gets this automatically.

When the user hasn't linked a Fold account (`lemmy_user_id IS NULL`),
this materializer no-ops cleanly.
"""

from sqlalchemy import text

from server import fold_db
from server.models import User

from ._base import (
    advance_watermark,
    get_or_seed_watermark,
    upsert_notification,
    utcnow,
)

SOURCE = "fold_inbox"

_FOLD_BASE = "https://fold.a-u.supply"

_REPLIES_SQL = text(
    """
    SELECT cr.id AS reply_id,
           cm.id AS comment_id,
           cm.content AS content,
           cm.published AS published,
           cm.post_id AS post_id,
           p.name AS post_title,
           pe.name AS author_name
      FROM comment_reply cr
      JOIN comment cm ON cm.id = cr.comment_id
      JOIN post p     ON p.id = cm.post_id
      JOIN person pe  ON pe.id = cm.creator_id
     WHERE cr.recipient_id = :my_id
       AND cm.published >= :watermark
       AND cm.published <= :window_end
       AND cm.deleted = false
       AND cm.removed = false
       AND cm.creator_id != :my_id
     ORDER BY cm.published ASC
     LIMIT 200
    """
)

_MENTIONS_SQL = text(
    """
    SELECT pm.id AS mention_id,
           cm.id AS comment_id,
           cm.content AS content,
           cm.published AS published,
           cm.post_id AS post_id,
           p.name AS post_title,
           pe.name AS author_name
      FROM person_mention pm
      JOIN comment cm ON cm.id = pm.comment_id
      JOIN post p     ON p.id = cm.post_id
      JOIN person pe  ON pe.id = cm.creator_id
     WHERE pm.recipient_id = :my_id
       AND cm.published >= :watermark
       AND cm.published <= :window_end
       AND cm.deleted = false
       AND cm.removed = false
       AND cm.creator_id != :my_id
     ORDER BY cm.published ASC
     LIMIT 200
    """
)


def materialize(user: User, db) -> int:
    if not fold_db.is_configured():
        return 0
    if not user.lemmy_user_id:
        return 0

    watermark = get_or_seed_watermark(db, user.id, SOURCE)
    window_end = utcnow()
    params = {
        "my_id": user.lemmy_user_id,
        "watermark": watermark,
        "window_end": window_end,
    }

    inserted = 0
    with fold_db.fold_connection() as conn:
        for row in conn.execute(_REPLIES_SQL, params).mappings():
            ref = f"fold_reply:{row['reply_id']}"
            url = f"{_FOLD_BASE}/comment/{row['comment_id']}"
            content = (row["content"] or "").strip()
            title = f"reply from {row['author_name']}"
            snippet = content[:160] if content else (row["post_title"] or "")[:160]
            if upsert_notification(
                db,
                user_id=user.id,
                source=SOURCE,
                source_ref=ref,
                title=title,
                snippet=snippet,
                url=url,
                created_at=row["published"],
            ):
                inserted += 1

        for row in conn.execute(_MENTIONS_SQL, params).mappings():
            ref = f"fold_mention:{row['mention_id']}"
            url = f"{_FOLD_BASE}/comment/{row['comment_id']}"
            content = (row["content"] or "").strip()
            title = f"{row['author_name']} mentioned you"
            snippet = content[:160] if content else (row["post_title"] or "")[:160]
            if upsert_notification(
                db,
                user_id=user.id,
                source=SOURCE,
                source_ref=ref,
                title=title,
                snippet=snippet,
                url=url,
                created_at=row["published"],
            ):
                inserted += 1

    advance_watermark(db, user.id, SOURCE, window_end)
    return inserted
