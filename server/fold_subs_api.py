"""Fold subscriptions + Lemmy-lookup endpoints (Inbox > Sources tab).

Subscriptions
-------------
GET    /api/admin/fold/subscriptions                 → {communities:[...], threads:[...]}
POST   /api/admin/fold/subscriptions/community       → add (body: lemmy_community_id)
DELETE /api/admin/fold/subscriptions/community/{id}  → 204
POST   /api/admin/fold/subscriptions/thread          → add (body: lemmy_post_id)
DELETE /api/admin/fold/subscriptions/thread/{id}     → 204

Lemmy lookups (used by the picker UI)
-------------------------------------
GET    /api/admin/fold/communities                   → list communities from Lemmy DB
GET    /api/admin/fold/threads?q=...                 → search Lemmy posts by title

When FOLD_DATABASE_URL isn't set, the lookup endpoints return 503 so
the UI can show a "Fold linkage not yet configured" hint instead of
silently rendering empty.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from server import fold_db
from server.auth import get_db, require_admin
from server.models import (
    FoldCommunitySubscription,
    FoldThreadSubscription,
    User,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/admin/fold", tags=["Fold"])


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class CommunityBody(BaseModel):
    lemmy_community_id: int = Field(..., ge=1)


class ThreadBody(BaseModel):
    lemmy_post_id: int = Field(..., ge=1)


def _community_dict(s: FoldCommunitySubscription) -> dict:
    return {
        "lemmy_community_id": s.lemmy_community_id,
        "name": s.name_snapshot,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _thread_dict(s: FoldThreadSubscription) -> dict:
    return {
        "lemmy_post_id": s.lemmy_post_id,
        "title": s.title_snapshot,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.get("/subscriptions", summary="List the calling user's fold subscriptions")
def list_subscriptions(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    communities = (
        db.query(FoldCommunitySubscription)
        .filter_by(user_id=user.id)
        .order_by(FoldCommunitySubscription.name_snapshot.asc())
        .all()
    )
    threads = (
        db.query(FoldThreadSubscription)
        .filter_by(user_id=user.id)
        .order_by(FoldThreadSubscription.created_at.desc())
        .all()
    )
    # Parse muted community IDs for the frontend
    muted_ids = set()
    if user.muted_communities:
        try:
            muted_ids = set(json.loads(user.muted_communities))
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "communities": [
            {**_community_dict(c), "muted": c.lemmy_community_id in muted_ids}
            for c in communities
        ],
        "threads": [_thread_dict(t) for t in threads],
        "fold_configured": fold_db.is_configured(),
    }


class CommunityMuteBody(BaseModel):
    community_id: int
    muted: bool


@router.put("/communities/muted", summary="Mute or unmute a fold community")
def toggle_community_mute(
    body: CommunityMuteBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    muted_ids = set()
    if user.muted_communities:
        try:
            muted_ids = set(json.loads(user.muted_communities))
        except (json.JSONDecodeError, TypeError):
            pass
    if body.muted:
        muted_ids.add(body.community_id)
    else:
        muted_ids.discard(body.community_id)
    user.muted_communities = json.dumps(sorted(muted_ids))
    # Sync subscriptions: remove if muted, add if unmuted
    if body.muted:
        db.query(FoldCommunitySubscription).filter_by(
            user_id=user.id, lemmy_community_id=body.community_id
        ).delete()
    else:
        existing = db.query(FoldCommunitySubscription).filter_by(
            user_id=user.id, lemmy_community_id=body.community_id
        ).first()
        if not existing:
            name = _fetch_community_name(body.community_id)
            db.add(FoldCommunitySubscription(
                user_id=user.id,
                lemmy_community_id=body.community_id,
                name_snapshot=name,
            ))
    db.commit()
    return {"muted": sorted(muted_ids)}


def _fetch_community_name(community_id: int) -> str:
    """Return community.name (the slug-ish handle, not title) from Lemmy.

    Raises HTTPException(404) if no such community.
    """
    with fold_db.fold_connection() as conn:
        row = conn.execute(
            text("SELECT name FROM community WHERE id = :cid"),
            {"cid": community_id},
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Community not found in Fold")
    return str(row[0])


def _fetch_post_title(post_id: int) -> str:
    with fold_db.fold_connection() as conn:
        row = conn.execute(
            text("SELECT name FROM post WHERE id = :pid"),
            {"pid": post_id},
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Post not found in Fold")
    return str(row[0])


@router.post("/subscriptions/community", summary="Subscribe to a fold community")
def add_community(
    body: CommunityBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not fold_db.is_configured():
        raise HTTPException(status_code=503, detail="Fold linkage not configured")
    existing = (
        db.query(FoldCommunitySubscription)
        .filter_by(user_id=user.id, lemmy_community_id=body.lemmy_community_id)
        .one_or_none()
    )
    if existing is not None:
        return _community_dict(existing)
    name = _fetch_community_name(body.lemmy_community_id)
    sub = FoldCommunitySubscription(
        user_id=user.id,
        lemmy_community_id=body.lemmy_community_id,
        name_snapshot=name,
    )
    db.add(sub)
    db.commit()
    return _community_dict(sub)


@router.delete(
    "/subscriptions/community/{community_id}",
    status_code=204,
    summary="Unsubscribe from a fold community",
)
def remove_community(
    community_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = (
        db.query(FoldCommunitySubscription)
        .filter_by(user_id=user.id, lemmy_community_id=community_id)
        .one_or_none()
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return None


@router.post("/subscriptions/community/all", summary="Subscribe to every community on fold")
def subscribe_all(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Subscribe the calling user to every non-deleted community on Fold."""
    if not fold_db.is_configured():
        raise HTTPException(status_code=503, detail="Fold linkage not configured")
    existing = {s.lemmy_community_id for s in
                db.query(FoldCommunitySubscription).filter_by(user_id=user.id).all()}
    with fold_db.fold_connection() as conn:
        rows = conn.execute(
            text(
                """SELECT id, name FROM community
                    WHERE removed = false AND deleted = false
                    ORDER BY name ASC LIMIT 500"""
            )
        ).mappings().all()
    added = 0
    for r in rows:
        if r["id"] not in existing:
            db.add(FoldCommunitySubscription(
                user_id=user.id,
                lemmy_community_id=r["id"],
                name_snapshot=r["name"],
            ))
            added += 1
    db.commit()
    return {"subscribed": added}


@router.post("/subscriptions/thread", summary="Subscribe to a fold thread")
def add_thread(
    body: ThreadBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not fold_db.is_configured():
        raise HTTPException(status_code=503, detail="Fold linkage not configured")
    existing = (
        db.query(FoldThreadSubscription)
        .filter_by(user_id=user.id, lemmy_post_id=body.lemmy_post_id)
        .one_or_none()
    )
    if existing is not None:
        return _thread_dict(existing)
    title = _fetch_post_title(body.lemmy_post_id)
    sub = FoldThreadSubscription(
        user_id=user.id,
        lemmy_post_id=body.lemmy_post_id,
        title_snapshot=title,
    )
    db.add(sub)
    db.commit()
    return _thread_dict(sub)


@router.delete(
    "/subscriptions/thread/{post_id}",
    status_code=204,
    summary="Unsubscribe from a fold thread",
)
def remove_thread(
    post_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = (
        db.query(FoldThreadSubscription)
        .filter_by(user_id=user.id, lemmy_post_id=post_id)
        .one_or_none()
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return None


# ---------------------------------------------------------------------------
# Lemmy lookups for the subscription picker
# ---------------------------------------------------------------------------


@router.get("/communities", summary="List all communities on fold")
def list_communities(
    user: User = Depends(require_admin),
):
    """Returns every non-removed community from the Fold DB.

    Small set (a-u.supply's Fold is a local single-instance) so no
    pagination — the picker UI just receives the full list.
    """
    if not fold_db.is_configured():
        raise HTTPException(status_code=503, detail="Fold linkage not configured")
    with fold_db.fold_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, name, title
                  FROM community
                 WHERE removed = false
                   AND deleted = false
                 ORDER BY name ASC
                 LIMIT 500
                """
            )
        ).mappings().all()
    return {
        "communities": [
            {"id": r["id"], "name": r["name"], "title": r["title"]}
            for r in rows
        ]
    }


@router.get("/threads", summary="Search fold posts by title")
def search_threads(
    q: str = "",
    user: User = Depends(require_admin),
):
    """Title-substring search across fold posts (case-insensitive).

    Returns up to 50 results, ordered newest first. Empty query returns
    the most recent posts — useful when the user just wants to browse.
    """
    if not fold_db.is_configured():
        raise HTTPException(status_code=503, detail="Fold linkage not configured")
    pattern = f"%{q.strip()}%" if q.strip() else "%"
    with fold_db.fold_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT p.id, p.name AS title, p.published, c.name AS community_name
                  FROM post p
                  JOIN community c ON c.id = p.community_id
                 WHERE p.deleted = false
                   AND p.removed = false
                   AND p.name ILIKE :pattern
                 ORDER BY p.published DESC
                 LIMIT 50
                """
            ),
            {"pattern": pattern},
        ).mappings().all()
    return {
        "posts": [
            {
                "id": r["id"],
                "title": r["title"],
                "community_name": r["community_name"],
                "published": r["published"].isoformat() if r["published"] else None,
            }
            for r in rows
        ]
    }
