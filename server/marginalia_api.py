"""Marginalia — timestamped comments and cue markers on media items.

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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.auth import get_db, require_admin
from server.models import Annotation, MediaItem, Project, ProjectItem, User
from server.search_client import (
    MARGINALIA_INDEX,
    delete_annotation_doc,
    get_client,
    sync_annotation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Marginalia"])

VALID_KINDS = {"comment", "cue"}


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


# ---------------------------------------------------------------------------
# Write endpoints
# ---------------------------------------------------------------------------


def _get_user(auth) -> User:
    return auth[0] if isinstance(auth, tuple) else auth


def _get_annotation_or_404(db: Session, annotation_id: str) -> Annotation:
    a = db.query(Annotation).filter(Annotation.id == annotation_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    return a


def _fmt_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _notify_annotation_created(db: Session, user: User, item: MediaItem, annotation: Annotation) -> None:
    try:
        from server.slack_notifier import notify_immediate

        attach = (
            db.query(ProjectItem).filter(ProjectItem.media_item_id == item.id).first()
        )
        project_name = None
        if attach:
            project = db.query(Project).filter(Project.id == attach.project_id).first()
            project_name = project.name if project else None
        excerpt = (annotation.body or annotation.label or "")[:80].strip()
        notify_immediate(
            "latent.annotation_created",
            user,
            media_item_id=item.id,
            filename=item.filename,
            project_name=project_name,
            timestamp=_fmt_timestamp(annotation.position_seconds),
            excerpt=excerpt,
        )
    except Exception:
        logger.exception("slack notify_immediate(latent.annotation_created) failed")


class AnnotationCreate(BaseModel):
    kind: str = Field("comment", description="'comment' (has body) or 'cue' (label-only marker)")
    position_seconds: float = Field(..., ge=0, description="Playback position in seconds")
    body: str | None = Field(None, description="Comment text (required for kind='comment')")
    label: str | None = Field(None, description="Marker label (optional for kind='cue')")
    parent_id: str | None = Field(None, description="Reply to this annotation (one level deep)")


class AnnotationUpdate(BaseModel):
    body: str | None = None
    label: str | None = None
    position_seconds: float | None = Field(None, ge=0)


@router.post(
    "/media/{media_id}/annotations",
    status_code=201,
    summary="Add a timestamped comment or marker",
)
def create_annotation(
    media_id: str,
    body: AnnotationCreate,
    _auth=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create an annotation at a playback position.

    - `comment` — human discussion; requires `body`. May be a reply (`parent_id`,
      one level deep).
    - `cue` — a marker with an optional `label` (no text required).

    **Scope required:** admin
    """
    user = _get_user(_auth)
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")

    kind = body.kind if body.kind in VALID_KINDS else "comment"
    if kind == "comment" and not (body.body or "").strip():
        raise HTTPException(status_code=400, detail="Comments require a body")

    parent_id = None
    if body.parent_id:
        parent = db.query(Annotation).filter(Annotation.id == body.parent_id).first()
        if parent is None or parent.media_item_id != media_id:
            raise HTTPException(status_code=404, detail="Parent annotation not found")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Replies are one level deep")
        parent_id = parent.id
        kind = "comment"  # replies are always comments

    annotation = Annotation(
        media_item_id=media_id,
        parent_id=parent_id,
        kind=kind,
        source="user",
        position_seconds=body.position_seconds,
        label=(body.label or "").strip() or None,
        body=(body.body or "").strip() or None,
        author_id=user.id,
        touched_by_user=True,
    )
    db.add(annotation)
    db.commit()
    db.refresh(annotation)
    sync_annotation(db, annotation)
    _notify_annotation_created(db, user, item, annotation)
    return _serialize(annotation, user)


@router.patch("/annotations/{annotation_id}", summary="Edit an annotation")
def update_annotation(
    annotation_id: str,
    body: AnnotationUpdate,
    _auth=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Edit body, label, or position. Author or admin only. Marks the row
    `touched_by_user` so re-extraction never reverts the edit.

    **Scope required:** admin
    """
    user = _get_user(_auth)
    annotation = _get_annotation_or_404(db, annotation_id)
    if annotation.author_id is not None and annotation.author_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only the author can edit this annotation")

    if body.body is not None:
        if annotation.kind == "comment" and not body.body.strip():
            raise HTTPException(status_code=400, detail="Comments require a body")
        annotation.body = body.body.strip() or None
    if body.label is not None:
        annotation.label = body.label.strip() or None
    if body.position_seconds is not None:
        annotation.position_seconds = body.position_seconds
    annotation.touched_by_user = True
    db.commit()
    db.refresh(annotation)
    sync_annotation(db, annotation)
    return _serialize(annotation, annotation.author)


@router.post("/annotations/{annotation_id}/resolve", summary="Toggle resolved state")
def resolve_annotation(
    annotation_id: str,
    _auth=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Mark an annotation resolved, or re-open it. Resolved comments collapse
    in the UI but stay visible — the mix-note workflow ("fix this transition"
    → done).

    **Scope required:** admin
    """
    user = _get_user(_auth)
    annotation = _get_annotation_or_404(db, annotation_id)
    if annotation.resolved_at is None:
        annotation.resolved_at = datetime.now(timezone.utc)
        annotation.resolved_by = user.id
    else:
        annotation.resolved_at = None
        annotation.resolved_by = None
    db.commit()
    db.refresh(annotation)
    sync_annotation(db, annotation)
    return _serialize(annotation, annotation.author)


@router.delete("/annotations/{annotation_id}", status_code=204, summary="Delete an annotation")
def delete_annotation(
    annotation_id: str,
    _auth=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete an annotation (replies cascade). Author or admin only.

    **Scope required:** admin
    """
    user = _get_user(_auth)
    annotation = _get_annotation_or_404(db, annotation_id)
    if annotation.author_id is not None and annotation.author_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only the author can delete this annotation")

    reply_ids = [r.id for r in annotation.replies] if annotation.replies else []
    db.delete(annotation)
    db.commit()
    delete_annotation_doc(annotation_id)
    for rid in reply_ids:
        delete_annotation_doc(rid)
    return None


# ---------------------------------------------------------------------------
# Search (marginalia index)
# ---------------------------------------------------------------------------


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


@router.get("/annotations", summary="Search annotations across media items")
def search_annotations(
    q: str = Query("", description="Full-text query over label/body/filename/author"),
    media_item_id: str | None = None,
    project_id: str | None = None,
    author_id: int | None = None,
    kind: str | None = Query(None, description="'comment' | 'cue'"),
    source: str | None = None,
    resolved: bool | None = None,
    sort: str = Query("created_at:desc", pattern="^(created_at|position_seconds):(asc|desc)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    _auth=Depends(require_admin),
):
    """Search the marginalia index — full-text over comment bodies and marker
    labels, filterable by item, Latent, author, kind, source, and resolution.

    **Scope required:** admin
    """
    filters = []
    if media_item_id:
        filters.append(f'media_item_id = "{_esc(media_item_id)}"')
    if project_id:
        filters.append(f'project_ids = "{_esc(project_id)}"')
    if author_id is not None:
        filters.append(f"author_id = {author_id}")
    if kind:
        filters.append(f'kind = "{_esc(kind)}"')
    if source:
        filters.append(f'source = "{_esc(source)}"')
    if resolved is not None:
        filters.append(f"resolved = {'true' if resolved else 'false'}")

    try:
        result = (
            get_client()
            .index(MARGINALIA_INDEX)
            .search(
                q or "",
                {
                    "filter": " AND ".join(filters) if filters else None,
                    "sort": [sort],
                    "page": page,
                    "hitsPerPage": per_page,
                },
            )
        )
    except Exception as exc:
        logger.exception("Marginalia search failed")
        raise HTTPException(status_code=503, detail="Search temporarily unavailable") from exc

    return {
        "annotations": result.get("hits", []),
        "total": result.get("totalHits", 0),
        "page": result.get("page", page),
        "per_page": result.get("hitsPerPage", per_page),
    }
