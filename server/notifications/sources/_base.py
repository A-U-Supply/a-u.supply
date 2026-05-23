"""Shared helpers used by every source materializer.

Each source.materialize() does roughly:

    watermark = get_or_seed_watermark(db, user.id, SOURCE)
    window_end = utcnow()
    for row in <query events with source_ts >= watermark AND <= window_end>:
        upsert_notification(db, user.id, SOURCE, ref, …, created_at=row.ts)
    advance_watermark(db, user.id, SOURCE, window_end)

The `>= watermark AND <= window_end` window plus the unique constraint on
(user_id, source, source_ref) makes the pass idempotent: boundary events
get re-fetched on the next poll, hit the conflict, and skip silently.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from server.models import Notification, NotificationHighWater


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(ts: datetime) -> datetime:
    """SQLite stores naive datetimes; reattach UTC for clean comparisons."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def get_or_seed_watermark(db: Session, user_id: int, source: str) -> datetime:
    """Fetch the per-(user, source) watermark, seeding to NOW on first call.

    First-poll users do not get a historical backfill — the seed says
    "as of now, you've seen everything."
    """
    row = (
        db.query(NotificationHighWater)
        .filter_by(user_id=user_id, source=source)
        .one_or_none()
    )
    if row is None:
        now = utcnow()
        db.add(NotificationHighWater(user_id=user_id, source=source, last_seen_at=now))
        db.flush()
        return now
    return _as_utc(row.last_seen_at)


def advance_watermark(db: Session, user_id: int, source: str, to: datetime) -> None:
    """Move the per-(user, source) watermark forward to `to`.

    Strictly monotonic: a smaller `to` is a no-op (defensive against
    concurrent poll races).
    """
    row = (
        db.query(NotificationHighWater)
        .filter_by(user_id=user_id, source=source)
        .one_or_none()
    )
    if row is None:
        db.add(NotificationHighWater(user_id=user_id, source=source, last_seen_at=to))
    else:
        existing = _as_utc(row.last_seen_at)
        if to > existing:
            row.last_seen_at = to
    db.flush()


def upsert_notification(
    db: Session,
    *,
    user_id: int,
    source: str,
    source_ref: str,
    title: str,
    snippet: str | None,
    url: str,
    created_at: datetime,
    actor: str | None = None,
    community: str | None = None,
) -> bool:
    """Insert a Notification row if (user_id, source, source_ref) is new.

    Returns True if a new row was created, False if a duplicate was
    skipped. Idempotent — safe to call repeatedly for the same event.
    """
    existing = (
        db.query(Notification.id)
        .filter_by(user_id=user_id, source=source, source_ref=source_ref)
        .first()
    )
    if existing is not None:
        return False
    db.add(
        Notification(
            user_id=user_id,
            source=source,
            source_ref=source_ref,
            title=title,
            snippet=snippet,
            url=url,
            actor=actor,
            community=community,
            created_at=created_at,
        )
    )
    return True
