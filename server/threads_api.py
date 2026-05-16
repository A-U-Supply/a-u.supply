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
    LemmyUnavailable,
    create_comment as lemmy_create_comment,
    create_post as lemmy_create_post,
    delete_comment as lemmy_delete_comment,
    delete_post as lemmy_delete_post,
    edit_comment as lemmy_edit_comment,
    edit_post as lemmy_edit_post,
    ensure_project_community,
    ensure_stacks_community,
    ensure_user_and_token,
    get_post as lemmy_get_post,
    get_user_token,
    is_configured,
    list_posts as lemmy_list_posts,
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


def _resolve_community_id(db: Session, anchor_type: str, anchor_id: str) -> int:
    """Resolve which Lemmy community an anchor lives in. Provisions if needed."""
    if anchor_type == "project":
        project = db.query(Project).filter(Project.id == anchor_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="anchor project not found")
        cid = ensure_project_community(db, project)
        if not cid:
            raise HTTPException(status_code=503, detail="Lemmy not configured or unreachable")
        return cid
    if anchor_type == "slot":
        slot = db.query(ProjectSlot).filter(ProjectSlot.id == anchor_id).first()
        if not slot:
            raise HTTPException(status_code=404, detail="anchor slot not found")
        project = db.query(Project).filter(Project.id == slot.project_id).first()
        cid = ensure_project_community(db, project)
        if not cid:
            raise HTTPException(status_code=503, detail="Lemmy not configured or unreachable")
        return cid
    if anchor_type == "media_item":
        mi = db.query(MediaItem).filter(MediaItem.id == anchor_id).first()
        if not mi:
            raise HTTPException(status_code=404, detail="anchor media_item not found")
        cid = ensure_stacks_community()
        if not cid:
            raise HTTPException(status_code=503, detail="Lemmy not configured or unreachable")
        return cid
    raise HTTPException(status_code=400, detail=f"unsupported anchor_type '{anchor_type}'")


def _thread_summary(t: Thread, post: Any | None = None) -> dict:
    out = {
        "id": t.id,
        "anchor_type": t.anchor_type,
        "anchor_id": t.anchor_id,
        "lemmy_post_id": t.lemmy_post_id,
        "lemmy_community_id": t.lemmy_community_id,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
    if post is not None:
        out["title"] = post.name
        out["body"] = post.body
        out["url"] = post.url
        out["published"] = post.published
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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
    # Optionally enrich with Lemmy post titles. Degrade gracefully if unreachable.
    out: list[dict] = []
    if not is_configured() or not rows:
        for t in rows:
            out.append(_thread_summary(t))
        return {"threads": out, "lemmy_available": is_configured()}

    try:
        token = get_user_token(db, user)
    except LemmyUnavailable:
        token = None

    for t in rows:
        post = None
        if token:
            try:
                post, _ = lemmy_get_post(token, t.lemmy_post_id)
            except LemmyUnavailable:
                pass
        out.append(_thread_summary(t, post))
    return {"threads": out, "lemmy_available": is_configured()}


@router.post("", status_code=201, summary="Create a thread")
def create_thread(
    body: CreateThreadBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if body.anchor_type not in VALID_ANCHOR_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported anchor_type '{body.anchor_type}'")
    if not is_configured():
        raise HTTPException(status_code=503, detail="Discussion is unavailable (Lemmy not configured)")

    try:
        ensure_user_and_token(db, user)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    community_id = _resolve_community_id(db, body.anchor_type, body.anchor_id)
    token = get_user_token(db, user)
    if not token:
        raise HTTPException(status_code=503, detail="Could not authenticate to Lemmy")

    try:
        post = lemmy_create_post(token, community_id, body.title, body=body.body, url=body.url)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    thread = Thread(
        id=str(uuid.uuid4()),
        anchor_type=body.anchor_type,
        anchor_id=body.anchor_id,
        lemmy_post_id=post.id,
        lemmy_community_id=community_id,
        created_by=user.id,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)

    # Slack notification (immediate)
    try:
        from server.slack_notifier import notify_immediate
        notify_immediate("latent.thread_created", user, anchor_type=body.anchor_type, anchor_id=body.anchor_id, title=body.title, thread_id=thread.id)
    except Exception:
        logger.exception("slack notify_immediate(latent.thread_created) failed")

    return _thread_summary(thread, post)


@router.get("/{thread_id}", summary="Get a thread with comments")
def get_thread(
    thread_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(Thread).filter(Thread.id == thread_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="thread not found")
    if not is_configured():
        return {**_thread_summary(t), "comments": [], "lemmy_available": False}
    token = get_user_token(db, user)
    if not token:
        return {**_thread_summary(t), "comments": [], "lemmy_available": False}
    try:
        post, comments = lemmy_get_post(token, t.lemmy_post_id)
    except LemmyUnavailable:
        return {**_thread_summary(t), "comments": [], "lemmy_available": False}
    return {**_thread_summary(t, post), "comments": [_comment_dict(c) for c in comments], "lemmy_available": True}


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
    if not is_configured():
        raise HTTPException(status_code=503, detail="Discussion is unavailable")
    token = get_user_token(db, user)
    if not token:
        raise HTTPException(status_code=503, detail="Could not authenticate to Lemmy")
    try:
        lemmy_edit_post(token, t.lemmy_post_id, title=body.title, body=body.body)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
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
    if is_configured():
        token = get_user_token(db, user)
        if token:
            try:
                lemmy_delete_post(token, t.lemmy_post_id)
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
    if not is_configured():
        raise HTTPException(status_code=503, detail="Discussion is unavailable")
    try:
        ensure_user_and_token(db, user)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    token = get_user_token(db, user)
    if not token:
        raise HTTPException(status_code=503, detail="Could not authenticate to Lemmy")
    try:
        comment = lemmy_create_comment(token, t.lemmy_post_id, body.body, parent_id=body.parent_comment_id)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    return _comment_dict(comment)


@router.patch("/{thread_id}/comments/{comment_id}", summary="Edit own comment")
def update_thread_comment(
    thread_id: str,
    comment_id: int,
    body: UpdateCommentBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(Thread).filter(Thread.id == thread_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="thread not found")
    if not is_configured():
        raise HTTPException(status_code=503, detail="Discussion is unavailable")
    token = get_user_token(db, user)
    if not token:
        raise HTTPException(status_code=503, detail="Could not authenticate to Lemmy")
    try:
        lemmy_edit_comment(token, comment_id, body.body)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"ok": True}


@router.delete("/{thread_id}/comments/{comment_id}", status_code=204, summary="Delete own comment")
def delete_thread_comment(
    thread_id: str,
    comment_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not is_configured():
        raise HTTPException(status_code=503, detail="Discussion is unavailable")
    token = get_user_token(db, user)
    if not token:
        raise HTTPException(status_code=503, detail="Could not authenticate to Lemmy")
    try:
        lemmy_delete_comment(token, comment_id)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    return None
