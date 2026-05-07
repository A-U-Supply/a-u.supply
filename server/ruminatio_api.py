"""Ruminatio — comments and reactions API for the output feed."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.auth import get_current_user, get_db, require_scope
from server.models import Comment, Reaction, User

router = APIRouter(prefix="/api", tags=["Ruminatio"])

MAX_EMOJI_LEN = 64  # accommodate ZWJ sequences


# ── Schemas ────────────────────────────────────────────────────────────────────

class CommentCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    parent_id: int | None = None


class ReactionToggle(BaseModel):
    emoji: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _comment_dict(c: Comment, current_user_id: int) -> dict:
    return {
        "id": c.id,
        "text": c.text,
        "created_at": c.created_at.isoformat(),
        "author_id": c.user_id,
        "author_name": c.user.name if c.user else None,
        "is_mine": c.user_id == current_user_id,
        "replies": [_comment_dict(r, current_user_id) for r in sorted(c.replies, key=lambda x: x.created_at)],
    }


# ── Comments ───────────────────────────────────────────────────────────────────

@router.get("/media/{media_id}/comments/count", summary="Count top-level comments for a media item")
def count_comments(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    count = (
        db.query(Comment)
        .filter(Comment.media_id == media_id, Comment.parent_id.is_(None))
        .count()
    )
    return {"count": count}


@router.get("/media/{media_id}/comments", summary="List comments for a media item")
def list_comments(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    user, _ = _auth
    top_level = (
        db.query(Comment)
        .filter(Comment.media_id == media_id, Comment.parent_id.is_(None))
        .order_by(Comment.created_at)
        .all()
    )
    return [_comment_dict(c, user.id) for c in top_level]


@router.post("/media/{media_id}/comments", status_code=201, summary="Add a comment to a media item")
def create_comment(
    media_id: str,
    body: CommentCreate,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user, _ = _auth
    if body.parent_id is not None:
        parent = db.query(Comment).filter(
            Comment.id == body.parent_id, Comment.media_id == media_id
        ).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent comment not found")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Replies can only be one level deep")
    comment = Comment(
        media_id=media_id,
        user_id=user.id,
        parent_id=body.parent_id,
        text=body.text.strip(),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _comment_dict(comment, user.id)


@router.delete("/media/{media_id}/comments/{comment_id}", status_code=204, summary="Delete a comment")
def delete_comment(
    media_id: str,
    comment_id: int,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user, _ = _auth
    comment = db.query(Comment).filter(
        Comment.id == comment_id, Comment.media_id == media_id
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your comment")
    db.delete(comment)
    db.commit()


# ── Reactions ──────────────────────────────────────────────────────────────────

@router.get("/media/{media_id}/reactions", summary="Get reaction counts for a media item")
def get_reactions(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    user, _ = _auth
    rows = db.query(Reaction).filter(Reaction.media_id == media_id).all()
    counts: dict[str, int] = {}
    mine: list[str] = []
    for r in rows:
        counts[r.emoji] = counts.get(r.emoji, 0) + 1
        if r.user_id == user.id:
            mine.append(r.emoji)
    return {"counts": counts, "mine": mine}


@router.post("/media/{media_id}/reactions", summary="Toggle a reaction on a media item")
def toggle_reaction(
    media_id: str,
    body: ReactionToggle,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user, _ = _auth
    if not body.emoji or len(body.emoji) > MAX_EMOJI_LEN:
        raise HTTPException(status_code=400, detail="Invalid emoji")
    existing = db.query(Reaction).filter(
        Reaction.media_id == media_id,
        Reaction.user_id == user.id,
        Reaction.emoji == body.emoji,
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        return {"action": "removed", "emoji": body.emoji}
    reaction = Reaction(media_id=media_id, user_id=user.id, emoji=body.emoji)
    db.add(reaction)
    db.commit()
    return {"action": "added", "emoji": body.emoji}
