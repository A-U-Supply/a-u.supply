"""Bookmarks API — star/save items across the app."""

import hashlib
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import Integer, and_, case, cast, func, literal_column, or_, select
from sqlalchemy.orm import Session

from server.auth import get_db, require_scope
from server.models import Bookmark, MediaItem, Release, Track, User

router = APIRouter(prefix="/api")

VALID_TYPES = {"media_item", "release", "track"}
VALID_SORTS = {"newest", "oldest", "name"}


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


def _build_meta_map(db: Session, rows: list[Bookmark]) -> dict[tuple[str, str], dict]:
    """Bulk-load display metadata for a page of bookmarks.

    Three IN-queries (one per target type) rather than N round-trips. Returns
    a {(target_type, target_id): meta_dict} map; callers attach `None` for
    misses (target deleted or never existed).
    """
    media_ids = [b.target_id for b in rows if b.target_type == "media_item"]
    release_codes = [b.target_id for b in rows if b.target_type == "release"]
    track_ids_str = [b.target_id for b in rows if b.target_type == "track"]

    meta: dict[tuple[str, str], dict] = {}

    if media_ids:
        for item in db.query(MediaItem).filter(MediaItem.id.in_(media_ids)).all():
            has_thumb = item.media_type in ("image", "video")
            meta[("media_item", item.id)] = {
                "name": item.filename or "Untitled",
                "sub": item.media_type,
                "media_type": item.media_type,
                "thumb": f"/api/media/{item.id}/thumbnail" if has_thumb else None,
                "thumb_sm": f"/api/media/{item.id}/thumbnail?size=sm" if has_thumb else None,
                "href": f"/admin/search/detail?id={item.id}",
                "playable": item.media_type in ("audio", "video"),
            }

    if release_codes:
        for rel in db.query(Release).filter(Release.product_code.in_(release_codes)).all():
            encoded = quote(rel.product_code, safe="")
            meta[("release", rel.product_code)] = {
                "name": rel.title or rel.product_code,
                "sub": ", ".join(e.name for e in rel.entities),
                "media_type": "release",
                "thumb": f"/api/releases/{encoded}/cover?size=thumb" if rel.cover_art_path else None,
                "href": f"/catalog/release?code={encoded}",
                "playable": False,
            }

    if track_ids_str:
        # Track.id is an Integer but Bookmark.target_id is a String, so coerce.
        track_ids = [int(t) for t in track_ids_str if t.isdigit()]
        if track_ids:
            for t in db.query(Track).filter(Track.id.in_(track_ids)).all():
                rel = t.release
                if rel is None:
                    continue
                encoded = quote(rel.product_code, safe="")
                meta[("track", str(t.id))] = {
                    "name": t.title or f"Track {t.id}",
                    "sub": rel.title or "",
                    "media_type": "audio",
                    "thumb": f"/api/releases/{encoded}/cover?size=thumb" if rel.cover_art_path else None,
                    "href": f"/catalog/release?code={encoded}",
                    "playable": bool(t.audio_file_path),
                    "stream_url": f"/api/releases/{encoded}/tracks/{t.id}/stream" if t.audio_file_path else None,
                }

    return meta


def _name_subquery():
    """A CASE expression that yields the display name for each Bookmark row.

    Three correlated lookups by target_type, used by both the `q` search filter
    and the `sort=name` ordering so they stay consistent.
    """
    media_name = select(MediaItem.filename).where(MediaItem.id == Bookmark.target_id).correlate(Bookmark).scalar_subquery()
    release_name = select(Release.title).where(Release.product_code == Bookmark.target_id).correlate(Bookmark).scalar_subquery()
    # Track.id is Integer; Bookmark.target_id is String — cast to compare safely.
    track_name = select(Track.title).where(Track.id == cast(Bookmark.target_id, Integer)).correlate(Bookmark).scalar_subquery()
    return case(
        (Bookmark.target_type == "media_item", media_name),
        (Bookmark.target_type == "release", release_name),
        (Bookmark.target_type == "track", track_name),
        else_=literal_column("''"),
    )


@router.get("/bookmarks", tags=["Bookmarks"])
def list_bookmarks(
    request: Request,
    response: Response,
    target_type: str | None = Query(None, description="Filter by target_type"),
    q: str | None = Query(None, description="Substring match against display name", max_length=200),
    sort: str = Query("newest", description="Sort order: newest | oldest | name"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    auth: tuple[User, str] = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """List bookmarks for the current user.

    Each item carries a `meta` field with display data (name, thumbnail URL,
    href, playable, etc.) bulk-loaded server-side, so the client renders the
    page in one round trip instead of N.

    Supports filtering by `target_type`, substring search by `q` (matched
    against MediaItem.filename / Release.title / Track.title), and sorting by
    `newest` (default), `oldest`, or `name`.

    Response carries `ETag` + `Cache-Control: private, max-age=30`. A
    matching `If-None-Match` returns 304 with no body.
    """
    user = auth[0]
    if sort not in VALID_SORTS:
        sort = "newest"

    base = db.query(Bookmark).filter(Bookmark.user_id == user.id)
    if target_type and target_type in VALID_TYPES:
        base = base.filter(Bookmark.target_type == target_type)

    if q:
        like = f"%{q.strip()}%"
        media_match = select(MediaItem.id).where(MediaItem.filename.ilike(like)).scalar_subquery()
        release_match = select(Release.product_code).where(Release.title.ilike(like)).scalar_subquery()
        # Coerce Track ids to text so the IN matches Bookmark.target_id's String type.
        track_match = select(cast(Track.id, Bookmark.target_id.type)).where(Track.title.ilike(like)).scalar_subquery()
        base = base.filter(
            or_(
                and_(Bookmark.target_type == "media_item", Bookmark.target_id.in_(media_match)),
                and_(Bookmark.target_type == "release", Bookmark.target_id.in_(release_match)),
                and_(Bookmark.target_type == "track", Bookmark.target_id.in_(track_match)),
            )
        )

    total = base.count()

    # Cheap fingerprint of "the state this user could have for these params".
    # Includes max(id) so deletions/additions invalidate even when total is
    # equal by coincidence.
    max_id = base.with_entities(func.max(Bookmark.id)).scalar() or 0
    fingerprint = f"{user.id}:{total}:{max_id}:{q or ''}:{sort}:{target_type or ''}:{page}:{per_page}"
    etag = 'W/"' + hashlib.sha256(fingerprint.encode()).hexdigest()[:16] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, max-age=30"})

    if sort == "name":
        # lower() so "Zebra" doesn't sort before "apple" on SQLite's binary default.
        ordered = base.order_by(func.lower(_name_subquery()).asc(), Bookmark.created_at.desc())
    elif sort == "oldest":
        ordered = base.order_by(Bookmark.created_at.asc())
    else:
        ordered = base.order_by(Bookmark.created_at.desc())

    rows = ordered.offset((page - 1) * per_page).limit(per_page).all()

    meta_map = _build_meta_map(db, rows)

    items = [
        {
            "id": bm.id,
            "target_type": bm.target_type,
            "target_id": bm.target_id,
            "created_at": bm.created_at.isoformat(),
            "meta": meta_map.get((bm.target_type, bm.target_id)),
        }
        for bm in rows
    ]

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=30"
    return {"items": items, "total": total, "page": page, "per_page": per_page}
