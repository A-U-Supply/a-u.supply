"""Inbox / notifications HTTP API.

Endpoints
---------
GET    /api/admin/notifications/count       → {unread}
GET    /api/admin/notifications             → {items: [...]}
POST   /api/admin/notifications/dismiss-all → {dismissed: N}
POST   /api/admin/notifications/{id}/dismiss → 204

Every read endpoint runs the materialization fan-out first so the
sidebar badge and Inbox page are always fresh as of "right now."
Mutations don't materialize — there's no reason for a dismiss to also
poll.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server import notifications as notif
from server.auth import get_db, require_admin
from server.models import User


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/admin/notifications", tags=["Notifications"])


def _serialize(n) -> dict:
    return {
        "id": n.id,
        "source": n.source,
        "source_ref": n.source_ref,
        "title": n.title,
        "snippet": n.snippet,
        "url": n.url,
        "actor": n.actor,
        "community": n.community,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "dismissed_at": n.dismissed_at.isoformat() if n.dismissed_at else None,
    }


@router.get("/count", summary="Unread notification count for the calling user")
def count(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    notif.materialize_for_user(user, db)
    return {"unread": notif.unread_count(user, db)}


@router.get("", summary="List notifications (newest first)")
def list_notifications(
    include_dismissed: bool = False,
    limit: int = 200,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    notif.materialize_for_user(user, db)
    limit = max(1, min(limit, 500))
    items = notif.list_for_user(user, db, include_dismissed=include_dismissed, limit=limit)
    return {"items": [_serialize(n) for n in items]}


@router.post("/dismiss-all", summary="Mark every unread notification dismissed")
def dismiss_all(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"dismissed": notif.dismiss_all(user, db)}


@router.post(
    "/{notification_id}/dismiss",
    status_code=204,
    summary="Mark a single notification dismissed",
)
def dismiss(
    notification_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not notif.dismiss(notification_id, user, db):
        raise HTTPException(status_code=404, detail="Notification not found")
    return None
