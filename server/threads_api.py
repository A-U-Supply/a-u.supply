"""Generic, anchor-typed threaded discussion backed by Lemmy.

Anchors supported in v1:
    - project  — threads anchored to a Latent (lives in the Latent's community)
    - slot     — threads anchored to a slot inside a Latent (also lives in that Latent's community)
    - media_item — threads anchored to any search doc (lives in the global `stacks` community)

The browser never talks to Lemmy. All reads and writes proxy through here.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.auth import get_db, require_admin
from server.models import MediaItem, Project, ProjectSlot, Thread, User
from server.lemmy_client import (
    LemmyNotLinked,
    LemmyUnavailable,
    LEMMY_URL,
    STACKS_COMMUNITY_NAME,
    _lemmy_safe_name,
    bulk_comment_counts as lemmy_bulk_comment_counts,
    create_comment as lemmy_create_comment,
    create_post as lemmy_create_post,
    delete_comment as lemmy_delete_comment,
    delete_post as lemmy_delete_post,
    edit_comment as lemmy_edit_comment,
    edit_post as lemmy_edit_post,
    ensure_project_community,
    ensure_stacks_community,
    get_post as lemmy_get_post,
    get_user_token,
    is_configured,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["Threads"])


VALID_ANCHOR_TYPES = {"project", "slot", "media_item"}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateThreadBody(BaseModel):
    anchor_type: str
    anchor_id: str
    title: str = Field(..., min_length=1, max_length=200)
    body: str | None = None
    url: str | None = None  # link-post — Lemmy fetches og preview


class UpdateThreadBody(BaseModel):
    title: str | None = None
    body: str | None = None


class CreateCommentBody(BaseModel):
    body: str = Field(..., min_length=1)
    parent_comment_id: int | None = None


class UpdateCommentBody(BaseModel):
    body: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _LemmyError(HTTPException):
    """Carries a structured `code` so the UI can branch (not_linked vs unavailable)."""

    def __init__(self, code: str, detail: str, status_code: int = 503):
        super().__init__(status_code=status_code, detail={"code": code, "message": detail})


def _require_token(db: Session, user: User) -> str:
    try:
        return get_user_token(db, user)
    except LemmyNotLinked as e:
        raise _LemmyError("not_linked", str(e))
    except LemmyUnavailable as e:
        raise _LemmyError("unavailable", str(e))


def _resolve_community_id(db: Session, anchor_type: str, anchor_id: str, token: str) -> int:
    """Resolve which Lemmy community an anchor lives in. Provisions if needed
    using the caller's token (band members on fold are admins, so this works)."""
    if anchor_type == "project":
        project = db.query(Project).filter(Project.id == anchor_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="anchor project not found")
        try:
            cid = ensure_project_community(db, project, token)
        except LemmyUnavailable as e:
            raise _LemmyError("unavailable", str(e))
        if not cid:
            raise _LemmyError("unavailable", "Lemmy not configured")
        return cid
    if anchor_type == "slot":
        slot = db.query(ProjectSlot).filter(ProjectSlot.id == anchor_id).first()
        if not slot:
            raise HTTPException(status_code=404, detail="anchor slot not found")
        project = db.query(Project).filter(Project.id == slot.project_id).first()
        try:
            cid = ensure_project_community(db, project, token)
        except LemmyUnavailable as e:
            raise _LemmyError("unavailable", str(e))
        if not cid:
            raise _LemmyError("unavailable", "Lemmy not configured")
        return cid
    if anchor_type == "media_item":
        mi = db.query(MediaItem).filter(MediaItem.id == anchor_id).first()
        if not mi:
            raise HTTPException(status_code=404, detail="anchor media_item not found")
        try:
            cid = ensure_stacks_community(token)
        except LemmyUnavailable as e:
            raise _LemmyError("unavailable", str(e))
        if not cid:
            raise _LemmyError("unavailable", "Lemmy not configured")
        return cid
    raise HTTPException(status_code=400, detail=f"unsupported anchor_type '{anchor_type}'")


def _community_name_for(db: Session, t: Thread) -> str | None:
    """Resolve the Lemmy community handle (used to build c/<name> links)."""
    if t.anchor_type == "media_item":
        return STACKS_COMMUNITY_NAME
    if t.anchor_type in ("project", "slot"):
        # Both project and slot threads live in the project's community.
        project_id = t.anchor_id
        if t.anchor_type == "slot":
            slot = db.query(ProjectSlot).filter(ProjectSlot.id == t.anchor_id).first()
            if not slot:
                return None
            project_id = slot.project_id
        project = db.query(Project).filter(Project.id == project_id).first()
        return _lemmy_safe_name(project.slug) if project and project.slug else None
    return None


def _thread_summary(t: Thread, post: Any | None = None, db: Session | None = None) -> dict:
    out = {
        "id": t.id,
        "anchor_type": t.anchor_type,
        "anchor_id": t.anchor_id,
        "lemmy_post_id": t.lemmy_post_id,
        "lemmy_community_id": t.lemmy_community_id,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "lemmy_url": f"{LEMMY_URL}/post/{t.lemmy_post_id}" if LEMMY_URL else None,
    }
    if db is not None:
        cname = _community_name_for(db, t)
        if cname:
            out["community_name"] = cname
            out["community_url"] = f"{LEMMY_URL}/c/{cname}" if LEMMY_URL else None
    # Title: prefer live post, else denormalized cache.
    if post is not None:
        out["title"] = post.name
        out["body"] = post.body
        out["url"] = post.url
        out["published"] = post.published
    elif t.title_cache:
        out["title"] = t.title_cache
    return out


def _comment_dict(c: Any) -> dict:
    return {
        "id": c.id,
        "content": c.content,
        "creator_id": c.creator_id,
        "parent_id": c.parent_id,
        "path": c.path,
        "published": c.published,
        "deleted": c.deleted,
    }


def _availability(user: User) -> dict:
    """UI-friendly status for the threads section."""
    from server.lemmy_client import status_for_user
    s = status_for_user(user)
    return {
        "configured": s["configured"],
        "linked": s["linked"],
        "lemmy_url": s["lemmy_url"],
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/counts", summary="Bulk thread counts + preview metadata by anchor_id")
def thread_counts(
    anchor_type: str = Query(...),
    anchor_ids: str = Query("", description="Comma-separated list of anchor ids"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-anchor preview payload for listing chips.

    Returns `{anchor_id: {count, thread_id, lemmy_post_id, lemmy_url, title,
    comment_count, community_name, community_url}}`. For count > 1 (legacy
    duplicates), the metadata reflects the most recent thread.

    `comment_count` requires one Lemmy `/post/list` call per distinct community
    in the result set. Degrades to omitting comment_count (chip falls back to
    thread count only) if Lemmy is unreachable.
    """
    if anchor_type not in VALID_ANCHOR_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported anchor_type '{anchor_type}'")
    ids = [s.strip() for s in (anchor_ids or "").split(",") if s.strip()]
    if not ids:
        return {"counts": {}}

    # All threads for these anchors, newest first — we need both the count
    # and the freshest thread per anchor for the preview metadata.
    rows = (
        db.query(Thread)
        .filter(Thread.anchor_type == anchor_type, Thread.anchor_id.in_(ids))
        .order_by(Thread.created_at.desc())
        .all()
    )

    # Group: per-anchor count + most-recent thread row.
    per_anchor: dict[str, dict[str, Any]] = {}
    latest_by_anchor: dict[str, Thread] = {}
    for t in rows:
        slot = per_anchor.setdefault(t.anchor_id, {"count": 0})
        slot["count"] += 1
        if t.anchor_id not in latest_by_anchor:
            latest_by_anchor[t.anchor_id] = t  # rows are ordered desc → first = newest

    # Hydrate metadata from the newest thread per anchor.
    for aid, t in latest_by_anchor.items():
        summary = _thread_summary(t, db=db)
        per_anchor[aid].update({
            "thread_id": summary["id"],
            "lemmy_post_id": summary["lemmy_post_id"],
            "lemmy_url": summary.get("lemmy_url"),
            "title": summary.get("title"),
            "community_name": summary.get("community_name"),
            "community_url": summary.get("community_url"),
        })

    # Best-effort comment_count enrichment: one /post/list call per distinct
    # Lemmy community. For media_item search results, this is always 1 call
    # (everything lives in `stacks`). Silent failure → chips degrade to count only.
    if rows and is_configured():
        try:
            token = get_user_token(db, user)
        except (LemmyUnavailable, LemmyNotLinked):
            token = None
        if token:
            community_ids: dict[int, list[Thread]] = {}
            for t in latest_by_anchor.values():
                community_ids.setdefault(t.lemmy_community_id, []).append(t)
            for cid, _threads in community_ids.items():
                try:
                    counts_map = lemmy_bulk_comment_counts(token, cid, limit=50)
                except Exception:
                    counts_map = {}
                for t in _threads:
                    cc = counts_map.get(int(t.lemmy_post_id))
                    if cc is not None:
                        per_anchor[t.anchor_id]["comment_count"] = cc

    return {"counts": per_anchor}


@router.get("", summary="List threads for an anchor")
def list_threads(
    anchor_type: str = Query(...),
    anchor_id: str = Query(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if anchor_type not in VALID_ANCHOR_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported anchor_type '{anchor_type}'")
    rows = (
        db.query(Thread)
        .filter(Thread.anchor_type == anchor_type, Thread.anchor_id == anchor_id)
        .order_by(Thread.created_at.desc())
        .all()
    )
    out: list[dict] = []
    avail = _availability(user)
    if not avail["configured"] or not avail["linked"] or not rows:
        for t in rows:
            out.append(_thread_summary(t, db=db))
        return {"threads": out, **avail}

    try:
        token = get_user_token(db, user)
    except (LemmyUnavailable, LemmyNotLinked):
        return {"threads": [_thread_summary(t, db=db) for t in rows], **avail}

    dirty = False
    for t in rows:
        post = None
        try:
            post, _ = lemmy_get_post(token, t.lemmy_post_id)
        except LemmyUnavailable:
            pass
        if post is not None and post.name and t.title_cache != post.name:
            t.title_cache = post.name
            dirty = True
        out.append(_thread_summary(t, post, db=db))
    if dirty:
        db.commit()
    return {"threads": out, **avail}


@router.post("", summary="Create a thread (or return existing one for media_item)")
def create_thread(
    body: CreateThreadBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a thread anchored to the given entity.

    For `anchor_type=media_item` this endpoint is **upsert-shaped**: if a
    thread already exists for the item, return it (200) instead of creating a
    duplicate. One canonical discussion per media item — multiple threads add
    fragmentation without payoff. Project/slot anchors can host many threads.
    """
    if body.anchor_type not in VALID_ANCHOR_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported anchor_type '{body.anchor_type}'")
    if not is_configured():
        raise _LemmyError("unavailable", "Discussion is unavailable (Lemmy not configured)")

    # Upsert path: media_item is single-thread by design.
    if body.anchor_type == "media_item":
        existing = (
            db.query(Thread)
            .filter(Thread.anchor_type == "media_item", Thread.anchor_id == body.anchor_id)
            .order_by(Thread.created_at.asc())
            .first()
        )
        if existing is not None:
            return _thread_summary(existing, db=db)

    token = _require_token(db, user)
    community_id = _resolve_community_id(db, body.anchor_type, body.anchor_id, token)

    try:
        post = lemmy_create_post(token, community_id, body.title, body=body.body, url=body.url)
    except LemmyUnavailable as e:
        raise _LemmyError("unavailable", str(e))

    thread = Thread(
        id=str(uuid.uuid4()),
        anchor_type=body.anchor_type,
        anchor_id=body.anchor_id,
        lemmy_post_id=post.id,
        lemmy_community_id=community_id,
        created_by=user.id,
        title_cache=post.name or body.title,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)

    try:
        from server.slack_notifier import notify_immediate
        notify_immediate(
            "latent.thread_created", user,
            anchor_type=body.anchor_type, anchor_id=body.anchor_id,
            title=body.title, thread_id=thread.id,
        )
    except Exception:
        logger.exception("slack notify_immediate(latent.thread_created) failed")

    return _thread_summary(thread, post, db=db)


@router.get("/{thread_id}", summary="Get a thread with comments")
def get_thread(
    thread_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(Thread).filter(Thread.id == thread_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="thread not found")
    avail = _availability(user)
    base = {**_thread_summary(t, db=db), "comments": [], **avail}
    if not avail["configured"] or not avail["linked"]:
        return base
    try:
        token = get_user_token(db, user)
        post, comments = lemmy_get_post(token, t.lemmy_post_id)
    except (LemmyUnavailable, LemmyNotLinked):
        return base
    # Refresh title_cache lazily on individual reads — keeps listing previews fresh.
    if post is not None and post.name and t.title_cache != post.name:
        t.title_cache = post.name
        db.commit()
    return {**_thread_summary(t, post, db=db), "comments": [_comment_dict(c) for c in comments], **avail}


@router.patch("/{thread_id}", summary="Edit a thread (title or body)")
def update_thread(
    thread_id: str,
    body: UpdateThreadBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(Thread).filter(Thread.id == thread_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="thread not found")
    if t.created_by != user.id:
        raise HTTPException(status_code=403, detail="only the author may edit a thread")
    token = _require_token(db, user)
    try:
        lemmy_edit_post(token, t.lemmy_post_id, title=body.title, body=body.body)
    except LemmyUnavailable as e:
        raise _LemmyError("unavailable", str(e))
    if body.title is not None:
        t.title_cache = body.title
        db.commit()
    return {"ok": True}


@router.delete("/{thread_id}", status_code=204, summary="Delete a thread")
def delete_thread(
    thread_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(Thread).filter(Thread.id == thread_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="thread not found")
    if t.created_by != user.id:
        raise HTTPException(status_code=403, detail="only the author may delete a thread")
    try:
        token = get_user_token(db, user)
        lemmy_delete_post(token, t.lemmy_post_id)
    except LemmyNotLinked:
        pass  # remove our row regardless so the UI clears
    except LemmyUnavailable:
        logger.warning("Failed to delete Lemmy post %s — proceeding with local removal", t.lemmy_post_id)
    db.delete(t)
    db.commit()
    return None


@router.post("/{thread_id}/comments", status_code=201, summary="Reply to a thread")
def create_thread_comment(
    thread_id: str,
    body: CreateCommentBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(Thread).filter(Thread.id == thread_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="thread not found")
    token = _require_token(db, user)
    try:
        comment = lemmy_create_comment(token, t.lemmy_post_id, body.body, parent_id=body.parent_comment_id)
    except LemmyUnavailable as e:
        raise _LemmyError("unavailable", str(e))
    return _comment_dict(comment)


@router.patch("/{thread_id}/comments/{comment_id}", summary="Edit own comment")
def update_thread_comment(
    thread_id: str,
    comment_id: int,
    body: UpdateCommentBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    token = _require_token(db, user)
    try:
        lemmy_edit_comment(token, comment_id, body.body)
    except LemmyUnavailable as e:
        raise _LemmyError("unavailable", str(e))
    return {"ok": True}


@router.delete("/{thread_id}/comments/{comment_id}", status_code=204, summary="Delete own comment")
def delete_thread_comment(
    thread_id: str,
    comment_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    token = _require_token(db, user)
    try:
        lemmy_delete_comment(token, comment_id)
    except LemmyUnavailable as e:
        raise _LemmyError("unavailable", str(e))
    return None
