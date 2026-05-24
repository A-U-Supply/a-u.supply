"""Inbox — per-user notifications aggregated from heterogeneous sources.

Public surface used by server.notifications_api:

    materialize_for_user(user, db) -> int
        Fan out across every source materializer. Returns the total
        number of new Notification rows inserted across all sources.
        Safe to call on every request — each source self-gates on
        watermarks + subscriptions, and a failure in one source does
        not break the others.

    unread_count(user, db) -> int
    list_for_user(user, db, *, include_dismissed=False, limit=200) -> list[Notification]
    dismiss(notification_id, user, db) -> bool
    dismiss_all(user, db) -> int
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from server.models import Notification, User

from .sources import ALL as SOURCE_MODULES

logger = logging.getLogger(__name__)


def materialize_for_user(user: User, db: Session) -> int:
    """Run every source materializer for this user.

    Each source is wrapped in its own try/except so an exception in one
    (e.g. Fold DB unreachable mid-poll) does not prevent the others
    from running. The materializer commits its own writes via the
    shared session.
    """
    total = 0
    _seed_default_muted(user, db)
    muted = _parse_muted(user.muted_sources)
    for module in SOURCE_MODULES:
        source_name = getattr(module, "SOURCE", module.__name__)
        if source_name in muted:
            continue
        try:
            total += module.materialize(user, db)
        except Exception:
            logger.exception(
                "Notification source %s materializer failed for user %s",
                source_name,
                user.id,
            )
            try:
                db.rollback()
            except Exception:
                pass
    db.commit()
    return total


def unread_count(user: User, db: Session) -> int:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.dismissed_at.is_(None))
        .count()
    )


def list_for_user(
    user: User,
    db: Session,
    *,
    include_dismissed: bool = False,
    limit: int = 200,
) -> list[Notification]:
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if not include_dismissed:
        q = q.filter(Notification.dismissed_at.is_(None))
    return q.order_by(Notification.created_at.desc()).limit(limit).all()


def dismiss(notification_id: int, user: User, db: Session) -> bool:
    """Mark a single notification dismissed. Returns False if it's not
    yours or doesn't exist (404-ish at the API layer)."""
    row = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == user.id)
        .one_or_none()
    )
    if row is None:
        return False
    if row.dismissed_at is None:
        row.dismissed_at = datetime.now(timezone.utc)
        db.commit()
    return True


def dismiss_all(user: User, db: Session) -> int:
    """Mark every undismissed notification for this user dismissed.
    Returns the number of rows affected."""
    now = datetime.now(timezone.utc)
    rows = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.dismissed_at.is_(None))
        .update({Notification.dismissed_at: now}, synchronize_session=False)
    )
    db.commit()
    return int(rows)


def _parse_muted(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return set()


def _seed_default_muted(user: User, db):
    """Seed muted_sources with ['midden'] on first materialize for new users."""
    if user.muted_sources is not None:
        return
    user.muted_sources = json.dumps(["midden"])
    db.flush()
