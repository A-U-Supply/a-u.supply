"""Bookmarks API — star/save items across the app."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import get_db, require_scope
from models import Bookmark, User

router = APIRouter(prefix="/api")

VALID_TYPES = {"media_item", "release", "track"}


class ToggleRequest(BaseModel):
    target_type: str
    target_id: str


class ToggleResponse(BaseModel):
    bookmarked: bool


@router.post("/bookmarks", response_model=ToggleResponse, tags=["Bookmarks"])
def toggle_bookmark(
    body: ToggleRequest,
    auth: tuple[User, str] = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Toggle a bookmark on/off. Returns the new state."""
    user = auth[0]
    if body.target_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid target_type. Must be one of: {', '.join(sorted(VALID_TYPES))}")

    existing = (
        db.query(Bookmark)
        .filter(
            Bookmark.user_id == user.id,
            Bookmark.target_type == body.target_type,
            Bookmark.target_id == body.target_id,
        )
        .first()
    )

    if existing:
        db.delete(existing)
        db.commit()
        return {"bookmarked": False}
    else:
        bm = Bookmark(user_id=user.id, target_type=body.target_type, target_id=body.target_id)
        db.add(bm)
        db.commit()
        return {"bookmarked": True}


class CheckRequest(BaseModel):
    target_type: str
    target_ids: list[str]


@router.post("/bookmarks/check", tags=["Bookmarks"])
def check_bookmarks(
    body: CheckRequest,
    auth: tuple[User, str] = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Check which IDs are bookmarked. Returns set of bookmarked IDs."""
    user = auth[0]
    if body.target_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="Invalid target_type")

    rows = (
        db.query(Bookmark.target_id)
        .filter(
            Bookmark.user_id == user.id,
            Bookmark.target_type == body.target_type,
            Bookmark.target_id.in_(body.target_ids),
        )
        .all()
    )
    return {"bookmarked_ids": [r[0] for r in rows]}


@router.get("/bookmarks", tags=["Bookmarks"])
def list_bookmarks(
    target_type: str | None = Query(None, description="Filter by type"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    auth: tuple[User, str] = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """List all bookmarks for the current user, newest first."""
    user = auth[0]
    q = db.query(Bookmark).filter(Bookmark.user_id == user.id)
    if target_type and target_type in VALID_TYPES:
        q = q.filter(Bookmark.target_type == target_type)

    total = q.count()
    rows = q.order_by(Bookmark.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    items = []
    for bm in rows:
        items.append({
            "id": bm.id,
            "target_type": bm.target_type,
            "target_id": bm.target_id,
            "created_at": bm.created_at.isoformat(),
        })

    return {"items": items, "total": total, "page": page, "per_page": per_page}
