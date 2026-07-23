"""Marginalia — timestamped comments and cue markers on media items.

Read endpoints (PR 2). Write endpoints arrive with the commenting UI (PR 3).

Data model: SQLite `annotations` is the source of truth; the `marginalia`
Meilisearch index holds a rebuildable projection for search (see
server/search_client.sync_annotation). Cues imported from session bundles
(WAV/AIFF cue chunks, MIDI markers, experimental Logic parse) anchor to the
item they describe; session-level cues are inherited by extracted children
via `parent_media_item_id`.

Registered before `search_router` in main.py so `/api/media/annotations/*`
isn't captured by `/media/{media_id}`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from server.auth import get_db, require_admin
from server.models import Annotation, MediaItem, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Marginalia"])


def _serialize(a: Annotation, author: User | None, *, with_replies: bool = True) -> dict:
    data = {
        "id": a.id,
        "media_item_id": a.media_item_id,
        "parent_id": a.parent_id,
        "kind": a.kind,
        "source": a.source,
        "position_seconds": a.position_seconds,
        "label": a.label,
        "body": a.body,
        "author": (
            {"id": author.id, "name": author.name} if author else None
        ),
        "resolved": a.resolved_at is not None,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "touched_by_user": bool(a.touched_by_user),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }
    if with_replies and a.replies:
        authors_by_id = {r.author_id: r.author for r in a.replies}
        data["replies"] = [
            _serialize(r, authors_by_id.get(r.author_id), with_replies=False)
            for r in sorted(
                a.replies, key=lambda r: r.created_at.timestamp() if r.created_at else 0
            )
        ]
    else:
        data["replies"] = []
    return data


@router.get(
    "/media/{media_id}/annotations",
    summary="List annotations (comments + cues) for a media item",
)
def list_annotations(
    media_id: str,
    _auth=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all annotations for a media item, ordered by timeline position.

    The response has two groups:

    - `annotations` — anchored to this item (top-level first, replies nested).
    - `inherited` — cues from the parent session item, when this item was
      extracted from a session bundle (MIDI/Logic markers describe the whole
      project timeline). `parent` identifies the source session.

    **Scope required:** admin
    """
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")

    rows = (
        db.query(Annotation)
        .filter(Annotation.media_item_id == media_id)
        .order_by(Annotation.position_seconds)
        .all()
    )
    top_level = [a for a in rows if a.parent_id is None]

    inherited = []
    parent_summary = None
    if item.parent_media_item_id:
        parent = db.query(MediaItem).filter(MediaItem.id == item.parent_media_item_id).first()
        if parent is not None:
            parent_summary = {"id": parent.id, "filename": parent.filename}
            inherited_rows = (
                db.query(Annotation)
                .filter(
                    Annotation.media_item_id == parent.id,
                    Annotation.kind == "cue",
                )
                .order_by(Annotation.position_seconds)
                .all()
            )
            inherited = [_serialize(a, a.author) for a in inherited_rows]

    return {
        "annotations": [_serialize(a, a.author) for a in top_level],
        "inherited": inherited,
        "parent": parent_summary,
    }


@router.get(
    "/media/annotations/counts",
    summary="Annotation counts for badge rendering",
)
def annotation_counts(
    media_ids: str = Query(..., description="Comma-separated media item ids (max 200)"),
    _auth=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-item annotation counts for slot rows and grid badges.

    Returns `{ "counts": { "<id>": { "comments": n, "cues": n, "unresolved": n } } }`.
    `unresolved` counts open comments (the actionable number for mix-note
    workflows); resolved comments still count in `comments`.

    **Scope required:** admin
    """
    ids = [i.strip() for i in media_ids.split(",") if i.strip()][:200]
    counts: dict[str, dict[str, int]] = {
        mid: {"comments": 0, "cues": 0, "unresolved": 0} for mid in ids
    }
    if not ids:
        return {"counts": counts}

    rows = (
        db.query(Annotation)
        .filter(Annotation.media_item_id.in_(ids))
        .all()
    )
    for a in rows:
        bucket = counts.get(a.media_item_id)
        if bucket is None:
            continue
        if a.kind == "comment":
            bucket["comments"] += 1
            if a.resolved_at is None:
                bucket["unresolved"] += 1
        else:
            bucket["cues"] += 1
    return {"counts": counts}
