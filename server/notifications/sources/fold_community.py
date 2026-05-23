"""Fold community materializer.

For each FoldCommunitySubscription, surface new posts and comments
within the subscribed community since the last poll. Excludes events
authored by the subscribing user themselves (no self-notifications).

Reads raw SQL from Fold's Postgres via server.fold_db. Skips silently
when FOLD_DATABASE_URL is unset.
"""

from sqlalchemy import bindparam, text

from server import fold_db
from server.models import FoldCommunitySubscription, User

from ._base import (
    advance_watermark,
    get_or_seed_watermark,
    upsert_notification,
    utcnow,
)

SOURCE = "fold_community"

_FOLD_BASE = "https://fold.a-u.supply"

_POSTS_SQL = text(
    """
    SELECT p.id AS post_id,
           p.name AS post_title,
           p.community_id,
           c.name AS community_name,
           p.published AS published,
           pe.name AS author_name
      FROM post p
      JOIN community c ON c.id = p.community_id
      JOIN person pe   ON pe.id = p.creator_id
     WHERE p.community_id IN :community_ids
       AND p.published >= :watermark
       AND p.published <= :window_end
       AND p.deleted = false
       AND p.removed = false
       AND p.creator_id != :my_id
     ORDER BY p.published ASC
     LIMIT 200
    """
).bindparams(bindparam("community_ids", expanding=True))

_COMMENTS_SQL = text(
    """
    SELECT cm.id AS comment_id,
           cm.content AS content,
           cm.published AS published,
           cm.post_id AS post_id,
           p.name AS post_title,
           p.community_id,
           c.name AS community_name,
           pe.name AS author_name
      FROM comment cm
      JOIN post p      ON p.id = cm.post_id
      JOIN community c ON c.id = p.community_id
      JOIN person pe   ON pe.id = cm.creator_id
     WHERE p.community_id IN :community_ids
       AND cm.published >= :watermark
       AND cm.published <= :window_end
       AND cm.deleted = false
       AND cm.removed = false
       AND cm.creator_id != :my_id
     ORDER BY cm.published ASC
     LIMIT 200
    """
).bindparams(bindparam("community_ids", expanding=True))


def materialize(user: User, db) -> int:
    if not fold_db.is_configured():
        return 0

    subs = (
        db.query(FoldCommunitySubscription)
        .filter_by(user_id=user.id)
        .all()
    )
    if not subs:
        return 0

    watermark = get_or_seed_watermark(db, user.id, SOURCE)
    window_end = utcnow()
    community_ids = [s.lemmy_community_id for s in subs]
    # -1 sentinel: when a user hasn't linked their Lemmy account, no row
    # ever matches creator_id != -1 (every real Lemmy person has id > 0).
    my_id = user.lemmy_user_id if user.lemmy_user_id else -1

    inserted = 0
    with fold_db.fold_connection() as conn:
        params = {
            "community_ids": community_ids,
            "watermark": watermark,
            "window_end": window_end,
            "my_id": my_id,
        }

        for row in conn.execute(_POSTS_SQL, params).mappings():
            ref = f"fold_post:{row['post_id']}"
            url = f"{_FOLD_BASE}/post/{row['post_id']}"
            title = f"new post in /c/{row['community_name']}"
            snippet = f"{row['author_name']} · {(row['post_title'] or '')[:140]}"
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

        for row in conn.execute(_COMMENTS_SQL, params).mappings():
            ref = f"fold_comment:{row['comment_id']}"
            url = f"{_FOLD_BASE}/comment/{row['comment_id']}"
            title = f"new comment in /c/{row['community_name']}"
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
