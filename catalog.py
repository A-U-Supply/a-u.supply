"""Release catalog API endpoints."""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import func, insert
from sqlalchemy.orm import Session, joinedload

from auth import get_current_user, get_db, require_admin
from models import (
    DistributionLink,
    Entity,
    Release,
    ReleaseMetadata,
    Track,
    User,
    release_entities,
)

router = APIRouter(prefix="/api")

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "./data/media"))
THUMB_SIZE = (400, 400)

AUDIO_EXTENSIONS = {".flac", ".wav", ".mp3", ".ogg", ".aac", ".m4a", ".aiff"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[-\s]+", "-", text).strip("-")


def _release_dir(product_code: str) -> Path:
    return MEDIA_DIR / "releases" / product_code


def _get_duration(path: str) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
    except (KeyError, ValueError, FileNotFoundError):
        pass
    return None


def _generate_thumbnail(image_path: Path, thumb_path: Path) -> None:
    with Image.open(image_path) as img:
        img.thumbnail(THUMB_SIZE, Image.LANCZOS)
        img.save(thumb_path, "WEBP", quality=85)


def generate_product_code(db: Session, year: int, category: str = "MX") -> str:
    pattern = f"AU-{year}-%"
    count = db.query(Release).filter(Release.product_code.like(pattern)).count()
    seq = count + 1
    return f"AU-{year}-{category}-{seq:03d}"


def _get_release_or_404(db: Session, code: str, user: User | None = None) -> Release:
    release = (
        db.query(Release)
        .options(
            joinedload(Release.entities),
            joinedload(Release.tracks),
            joinedload(Release.distribution_links),
            joinedload(Release.metadata_pairs),
            joinedload(Release.creator),
        )
        .filter(Release.product_code == code)
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    if release.status == "draft" and user is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


def _entity_response(entity, db: Session | None = None) -> dict:
    d = {"id": entity.id, "name": entity.name, "slug": entity.slug}
    if db is not None:
        d["release_count"] = (
            db.query(func.count())
            .select_from(release_entities)
            .filter(release_entities.c.entity_id == entity.id)
            .scalar()
        )
    return d


def _track_response(track: Track, release_code: str) -> dict:
    return {
        "id": track.id,
        "track_number": track.track_number,
        "title": track.title,
        "duration_seconds": track.duration_seconds,
        "stream_url": f"/api/releases/{quote(release_code, safe='')}/tracks/{track.id}/stream",
    }


def _release_summary(release: Release) -> dict:
    return {
        "product_code": release.product_code,
        "title": release.title,
        "entities": [{"id": e.id, "name": e.name, "slug": e.slug} for e in release.entities],
        "release_date": release.release_date.isoformat() if release.release_date else None,
        "cover_art_url": f"/api/releases/{quote(release.product_code, safe='')}/cover" if release.cover_art_path else None,
        "status": release.status,
        "track_count": len(release.tracks),
        "total_duration_seconds": sum(t.duration_seconds or 0 for t in release.tracks),
    }


def _release_detail(release: Release) -> dict:
    d = _release_summary(release)
    d.update({
        "description": release.description,
        "format_specs": release.format_specs,
        "category": release.category,
        "created_by": {"id": release.creator.id, "name": release.creator.name} if release.creator else None,
        "created_at": release.created_at.isoformat() if release.created_at else None,
        "updated_at": release.updated_at.isoformat() if release.updated_at else None,
        "tracks": [_track_response(t, release.product_code) for t in release.tracks],
        "distribution_links": [
            {"id": dl.id, "platform": dl.platform, "url": dl.url, "label": dl.label}
            for dl in release.distribution_links
        ],
        "metadata": [
            {"id": m.id, "key": m.key, "value": m.value, "sort_order": m.sort_order}
            for m in sorted(release.metadata_pairs, key=lambda x: x.sort_order)
        ],
    })
    return d


# --- Pydantic Schemas ---


class DistLinkIn(BaseModel):
    """A link to an external distribution platform for a release."""
    platform: str = Field(..., description="Platform name: `bandcamp`, `archive.org`, `soundcloud`, `youtube`, or any freeform string.")
    url: str = Field(..., description="Full URL to the release on this platform.")
    label: str | None = Field(None, description="Optional display label override. If omitted, the platform name is used.")


class MetadataIn(BaseModel):
    """A freeform key-value metadata pair attached to a release. Use for credits, personnel, equipment, recording location, etc."""
    key: str = Field(..., description="Metadata field name (e.g. `credits`, `personnel`, `equipment`, `recording_location`).")
    value: str = Field(..., description="Metadata value. Can be multi-line text.")
    sort_order: int = Field(0, description="Display ordering. Lower numbers appear first.")


class ReleaseCreate(BaseModel):
    """Create a new release in the catalog.

    If `product_code` is omitted or null, one is auto-generated in the format
    `AU-{YYYY}-{CAT}-{SEQ}` (e.g. `AU-2026-MX-003`). The year is taken from
    `release_date` if provided, otherwise the current year. Category defaults to `MX`
    (mixed). The sequence number counts all releases for that year, not per category.
    """
    title: str = Field(..., description="Release title.")
    entity_ids: list[int] = Field([], description="List of entity (artist/manufacturer) IDs to link. Order determines display position.")
    product_code: str | None = Field(None, description="Custom product code. Must be unique. If omitted, one is auto-generated. Can contain special characters.")
    release_date: str | None = Field(None, description="Date of manufacture/release in `YYYY-MM-DD` format. Optional for drafts.")
    category: str | None = Field(None, description="Two-letter category code (LP, EP, SG, DA, CX, AR, MX). Also used for auto-generated product codes.")
    description: str | None = Field(None, description="Liner notes or long-form description. Plain text.")
    format_specs: str | None = Field(None, description="Format specification string, e.g. `Digital (YouTube)`, `Digital (Bandcamp, 24-bit/44.1kHz)`.")
    status: str = Field("draft", description="Initial status: `draft` (default, only visible to authenticated users) or `published` (publicly visible).")
    distribution_links: list[DistLinkIn] = Field([], description="Links to external distribution platforms.")
    metadata: list[MetadataIn] = Field([], description="Freeform key-value metadata pairs.")


class TrackUpdate(BaseModel):
    """A track entry in a release-update payload. Identifies an existing track by `id` and
    optionally renames/repositions it."""
    id: int = Field(..., description="Existing track ID. Must belong to this release.")
    title: str = Field(..., description="Track title (may be unchanged).")
    position: int = Field(..., description="1-based position within the release. Determines `track_number`.")


class ReleaseUpdate(BaseModel):
    """Update an existing release. Only provided fields are changed (partial update).

    **Important:** `distribution_links`, `metadata`, and `tracks` are replaced wholesale if
    provided — send the full list, not just the changes. Omit them entirely to leave them
    unchanged.

    **Tracks:** Providing `tracks` deletes any existing track whose ID is not in the list
    (including its audio file on disk), renames tracks in place, and renumbers them based
    on `position`. To add new tracks, use `POST /releases/{code}/tracks` — they cannot be
    created via this endpoint.

    **Product code rename:** If you change the product code, the media directory on disk is
    also renamed. All track stream URLs and cover art URLs will use the new code.
    """
    title: str | None = Field(None, description="New release title.")
    entity_ids: list[int] | None = Field(None, description="Full list of entity IDs (replaces existing). Order determines display position.")
    product_code: str | None = Field(None, description="New product code. Must be unique. Renames the media directory on disk.")
    release_date: str | None = Field(None, description="New release date in `YYYY-MM-DD` format.")
    category: str | None = Field(None, description="New category code (LP, EP, SG, DA, CX, AR, MX). Pass an empty string to clear.")
    description: str | None = Field(None, description="New description/liner notes.")
    format_specs: str | None = Field(None, description="New format specification string.")
    distribution_links: list[DistLinkIn] | None = Field(None, description="Full list of distribution links (replaces existing). Omit to leave unchanged.")
    metadata: list[MetadataIn] | None = Field(None, description="Full list of metadata pairs (replaces existing). Omit to leave unchanged.")
    tracks: list[TrackUpdate] | None = Field(None, description="Full list of existing tracks after edits. Tracks with IDs not in this list are deleted. Titles and positions are updated in place. Omit to leave tracks unchanged.")


class EntityCreate(BaseModel):
    """Create a new entity (artist/manufacturer/project name). A URL-safe slug is auto-generated from the name."""
    name: str = Field(..., description="Display name for the entity. Must be unique.")
    description: str | None = Field(None, description="Optional short description of the entity.")


class EntityUpdate(BaseModel):
    """Update an existing entity. Only provided fields are changed."""
    name: str | None = Field(None, description="New display name. Slug is regenerated automatically.")
    description: str | None = Field(None, description="New description.")


class TrackReorder(BaseModel):
    """Reorder tracks within a release by providing all track IDs in the desired order."""
    track_ids: list[int] = Field(..., description="All track IDs for this release in the desired playback order. Must include every track.")


# --- Auth helper ---


def optional_user(request: Request):
    """Return the current user or None if not authenticated."""
    from auth import COOKIE_NAME, SECRET_KEY, ALGORITHM
    from jose import JWTError, jwt as jose_jwt
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            return None
    except JWTError:
        return None
    db = next(get_db())
    try:
        return db.query(User).filter(User.email == email).first()
    finally:
        db.close()


# --- Entity endpoints ---


@router.get("/entities", tags=["Entities"], summary="List all entities")
def list_entities(db: Session = Depends(get_db)):
    """Return all entities (artists/manufacturers) sorted alphabetically by name.

    Each entity includes a `release_count` showing how many releases reference it.
    This endpoint is public — no authentication required.
    """
    entities = db.query(Entity).order_by(Entity.name).all()
    return [_entity_response(e, db) for e in entities]


@router.post("/entities", status_code=201, tags=["Entities"], summary="Create a new entity")
def create_entity(body: EntityCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a new entity (artist/manufacturer/project name).

    A URL-safe slug is auto-generated from the name (lowercased, special characters
    removed, spaces converted to hyphens). Returns 409 if an entity with the same
    name already exists.

    Any authenticated user can create entities — this is intentionally permissive so
    artists can be added during the release creation workflow.
    """
    slug = _slugify(body.name)
    if db.query(Entity).filter(Entity.name == body.name).first():
        raise HTTPException(status_code=409, detail="Entity already exists")
    entity = Entity(name=body.name, slug=slug, description=body.description)
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return _entity_response(entity, db)


@router.put("/entities/{entity_id}", tags=["Entities"], summary="Update an entity")
def update_entity(entity_id: int, body: EntityUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Update an entity's name and/or description. **Admin only.**

    If the name changes, the slug is automatically regenerated. This does not
    affect any releases that reference this entity — they continue to reference
    the same entity ID.
    """
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if body.name is not None:
        entity.name = body.name
        entity.slug = _slugify(body.name)
    if body.description is not None:
        entity.description = body.description
    db.commit()
    db.refresh(entity)
    return _entity_response(entity, db)


@router.delete("/entities/{entity_id}", tags=["Entities"], summary="Delete an entity")
def delete_entity(entity_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Delete an entity. **Admin only.**

    Returns 409 if any releases reference this entity — you must remove the entity
    from all releases before deleting it. This prevents orphaned references.
    """
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    ref_count = (
        db.query(func.count())
        .select_from(release_entities)
        .filter(release_entities.c.entity_id == entity_id)
        .scalar()
    )
    if ref_count > 0:
        raise HTTPException(status_code=409, detail="Entity is referenced by releases")
    db.delete(entity)
    db.commit()
    return {"ok": True}


# --- Product code preview ---


@router.get("/releases/next-code", tags=["Releases"], summary="Preview next auto-generated product code")
def next_code(
    year: int = Query(..., description="Year for the product code (e.g. 2026)."),
    category: str = Query("MX", description="Two-letter category code: `LP` (album), `EP`, `SG` (single), `DA` (double album), `CX` (compilation), `AR` (archive), `MX` (mixed/default)."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Preview the next auto-generated product code without creating a release.

    Product codes follow the format `AU-{YYYY}-{CAT}-{SEQ}`. The sequence number
    counts **all** releases for that year (not per category). For example, if 2026
    already has `AU-2026-DA-001` and `AU-2026-LP-002`, the next code for any category
    will be `AU-2026-XX-003`.

    Use this in the upload form to show the user what code will be assigned. The code
    is always editable — the user can replace it with anything unique.
    """
    return {"product_code": generate_product_code(db, year, category)}


# --- Release endpoints ---


@router.post("/releases", status_code=201, tags=["Releases"], summary="Create a new release")
def create_release(body: ReleaseCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a new release in the catalog (draft by default).

    If `product_code` is omitted, one is auto-generated using the year from
    `release_date` (or the current year) and category `MX`. The generated code
    follows the format `AU-{YYYY}-MX-{SEQ}`.

    **Workflow:**
    1. Create the release with metadata
    2. Upload tracks via `POST /api/releases/{code}/tracks`
    3. Upload cover art via `POST /api/releases/{code}/cover`
    4. Publish via `POST /api/releases/{code}/publish`

    Returns the full release detail object including the assigned product code.
    """
    # Determine product code
    product_code = body.product_code
    if not product_code:
        year = date.today().year
        if body.release_date:
            try:
                year = date.fromisoformat(body.release_date).year
            except ValueError:
                pass
        product_code = generate_product_code(db, year, body.category or "MX")

    if db.query(Release).filter(Release.product_code == product_code).first():
        raise HTTPException(status_code=409, detail="Product code already exists")

    release_date_val = None
    if body.release_date:
        try:
            release_date_val = date.fromisoformat(body.release_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid release_date format (use YYYY-MM-DD)")

    release = Release(
        product_code=product_code,
        title=body.title,
        release_date=release_date_val,
        category=body.category or None,
        description=body.description,
        format_specs=body.format_specs,
        status=body.status if body.status in ("draft", "published") else "draft",
        created_by=user.id,
    )
    db.add(release)
    db.flush()

    # Link entities
    for i, eid in enumerate(body.entity_ids):
        entity = db.query(Entity).filter(Entity.id == eid).first()
        if not entity:
            raise HTTPException(status_code=400, detail=f"Entity {eid} not found")
        db.execute(insert(release_entities).values(release_id=release.id, entity_id=eid, position=i))

    # Distribution links
    for dl in body.distribution_links:
        db.add(DistributionLink(release_id=release.id, platform=dl.platform, url=dl.url, label=dl.label))

    # Metadata
    for m in body.metadata:
        db.add(ReleaseMetadata(release_id=release.id, key=m.key, value=m.value, sort_order=m.sort_order))

    db.commit()

    release = _get_release_or_404(db, product_code, user)
    return _release_detail(release)


@router.get("/releases", tags=["Releases"], summary="List releases")
def list_releases(
    status: str = Query("published", description="Filter by status: `published` (default for public), `draft`, or `all`. Unauthenticated users always see only published."),
    entity: str | None = Query(None, description="Filter by entity slug (e.g. `complete`, `bdo`)."),
    year: int | None = Query(None, description="Filter by release year."),
    sort: str = Query("date_desc", description="Sort order: `date_desc` (default), `date_asc`, `title`, `code`."),
    page: int = Query(1, ge=1, description="Page number (1-indexed)."),
    per_page: int = Query(50, ge=1, le=200, description="Results per page (max 200)."),
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """List releases with optional filtering, sorting, and pagination.

    **Visibility rules:**
    - Unauthenticated users only see `published` releases (the `status` filter is ignored).
    - Authenticated users can filter by `status=draft`, `status=published`, or `status=all`.

    Returns paginated release summaries (no tracks or metadata — use
    `GET /api/releases/{code}` for full detail).
    """
    q = db.query(Release).options(joinedload(Release.entities), joinedload(Release.tracks))

    # Status filter
    if user is not None:
        if status != "all":
            q = q.filter(Release.status == status)
    else:
        q = q.filter(Release.status == "published")

    # Entity filter
    if entity:
        q = q.join(Release.entities).filter(Entity.slug == entity)

    # Year filter
    if year:
        q = q.filter(func.strftime("%Y", Release.release_date) == str(year))

    # Sort
    if sort == "date_asc":
        q = q.order_by(Release.release_date.asc().nullslast())
    elif sort == "title":
        q = q.order_by(Release.title)
    elif sort == "code":
        q = q.order_by(Release.product_code)
    else:
        q = q.order_by(Release.release_date.desc().nullslast())

    total = q.count()
    releases = q.offset((page - 1) * per_page).limit(per_page).all()

    # Deduplicate (joinedload can produce duplicates)
    seen = set()
    unique = []
    for r in releases:
        if r.id not in seen:
            seen.add(r.id)
            unique.append(r)

    return {
        "releases": [_release_summary(r) for r in unique],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/releases/{code}", tags=["Releases"], summary="Get release detail")
def get_release(code: str, db: Session = Depends(get_db), user: User | None = Depends(optional_user)):
    """Return the full detail for a single release, including tracks, distribution links,
    and freeform metadata.

    **Visibility:** Published releases are public. Draft releases return 404 for
    unauthenticated users (they appear to not exist).

    **Note:** The `code` path parameter must be URL-encoded if it contains special
    characters (e.g. `A-U%23%20M5497.H37` for `A-U# M5497.H37`).
    """
    release = _get_release_or_404(db, code, user)
    return _release_detail(release)


@router.put("/releases/{code}", tags=["Releases"], summary="Update a release")
def update_release(code: str, body: ReleaseUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Update release metadata. **Admin only.** Supports partial updates — only
    provided fields are changed.

    **Replacement fields:** `entity_ids`, `distribution_links`, and `metadata` are
    replaced wholesale if provided. Send the complete list, not just additions.
    Omit them entirely to leave them unchanged.

    **Product code rename:** If `product_code` is provided and different from the
    current code, the media directory on disk is renamed. All existing track stream
    URLs and cover art URLs will automatically use the new code. Returns 409 if the
    new code is already in use.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    if body.title is not None:
        release.title = body.title
    if body.description is not None:
        release.description = body.description
    if body.format_specs is not None:
        release.format_specs = body.format_specs
    if body.category is not None:
        release.category = body.category or None
    if body.release_date is not None:
        try:
            release.release_date = date.fromisoformat(body.release_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid release_date format")

    # Product code rename
    new_code = body.product_code
    if new_code and new_code != code:
        if db.query(Release).filter(Release.product_code == new_code).first():
            raise HTTPException(status_code=409, detail="Product code already exists")
        old_dir = _release_dir(code)
        if old_dir.exists():
            new_dir = _release_dir(new_code)
            old_dir.rename(new_dir)
        release.product_code = new_code

    # Replace entity links
    if body.entity_ids is not None:
        db.execute(release_entities.delete().where(release_entities.c.release_id == release.id))
        for i, eid in enumerate(body.entity_ids):
            if not db.query(Entity).filter(Entity.id == eid).first():
                raise HTTPException(status_code=400, detail=f"Entity {eid} not found")
            db.execute(insert(release_entities).values(release_id=release.id, entity_id=eid, position=i))

    # Replace distribution links
    if body.distribution_links is not None:
        for dl in release.distribution_links:
            db.delete(dl)
        for dl in body.distribution_links:
            db.add(DistributionLink(release_id=release.id, platform=dl.platform, url=dl.url, label=dl.label))

    # Replace metadata
    if body.metadata is not None:
        for m in release.metadata_pairs:
            db.delete(m)
        for m in body.metadata:
            db.add(ReleaseMetadata(release_id=release.id, key=m.key, value=m.value, sort_order=m.sort_order))

    # Track edits: delete removed tracks, rename + renumber the rest
    if body.tracks is not None:
        current_tracks = {
            t.id: t for t in db.query(Track).filter(Track.release_id == release.id).all()
        }
        incoming_ids = {t.id for t in body.tracks}

        for t in body.tracks:
            if t.id not in current_tracks:
                raise HTTPException(status_code=400, detail=f"Track {t.id} not found in this release")

        # Delete tracks not in the incoming list (along with their audio files)
        for tid, track in list(current_tracks.items()):
            if tid not in incoming_ids:
                if track.audio_file_path:
                    fpath = MEDIA_DIR / track.audio_file_path
                    if fpath.exists():
                        fpath.unlink()
                db.delete(track)
                del current_tracks[tid]
        db.flush()

        # Two-phase renumber: shift to negative temp numbers first to avoid
        # collisions with the (release_id, track_number) unique constraint
        for i, t in enumerate(body.tracks):
            current_tracks[t.id].track_number = -(i + 1)
        db.flush()

        for t in body.tracks:
            current_tracks[t.id].track_number = t.position
            current_tracks[t.id].title = t.title

    db.commit()
    final_code = new_code if new_code else code
    release = _get_release_or_404(db, final_code, admin)
    return _release_detail(release)


@router.post("/releases/{code}/publish", tags=["Releases"], summary="Publish a release")
def publish_release(code: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Set a release's status to `published`, making it publicly visible.

    **Admin only.** Published releases appear on the public catalog page and their
    tracks can be streamed without authentication.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    release.status = "published"
    db.commit()
    return {"ok": True}


@router.post("/releases/{code}/unpublish", tags=["Releases"], summary="Unpublish a release")
def unpublish_release(code: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Revert a release's status to `draft`, hiding it from the public.

    **Admin only.** The release and its tracks will only be accessible to
    authenticated users. Existing direct links will return 404 for unauthenticated
    visitors.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    release.status = "draft"
    db.commit()
    return {"ok": True}


@router.delete("/releases/{code}", tags=["Releases"], summary="Delete a release")
def delete_release(code: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Permanently delete a release and all associated data. **Admin only.**

    This deletes:
    - The release record and all database associations (tracks, distribution links, metadata, entity links)
    - All media files on disk (audio tracks, cover art, thumbnails)

    **This action is irreversible.** The media directory for this release is removed entirely.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    # Remove media files
    rdir = _release_dir(code)
    if rdir.exists():
        shutil.rmtree(rdir)
    db.delete(release)
    db.commit()
    return {"ok": True}


# --- Track endpoints ---


@router.post("/releases/{code}/tracks", status_code=201, tags=["Tracks"], summary="Upload audio tracks")
async def upload_tracks(
    code: str,
    files: list[UploadFile] = File(..., description="One or more audio files. Supported formats: FLAC, WAV, MP3, OGG, AAC, M4A, AIFF."),
    titles: str | None = Form(None, description="JSON array of track titles, one per file. If omitted, titles default to the filename without extension."),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Upload one or more audio tracks to a release. **Admin only.**

    Tracks are appended after existing tracks — track numbers are auto-assigned
    sequentially. Duration is extracted server-side via `ffprobe`.

    **File naming on disk:** Tracks are stored as `{NN}-{slugified-title}.{ext}` in
    the release's media directory (e.g. `01-heat.flac`).

    **Titles:** Pass a JSON array of strings in the `titles` form field to set custom
    track titles. If omitted or if there are fewer titles than files, remaining tracks
    use the filename (without extension) as their title.

    Returns the list of created track objects with stream URLs.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    title_list = json.loads(titles) if titles else []
    track_dir = _release_dir(code) / "tracks"
    track_dir.mkdir(parents=True, exist_ok=True)

    max_num = db.query(func.max(Track.track_number)).filter(Track.release_id == release.id).scalar() or 0

    created = []
    for i, file in enumerate(files):
        ext = Path(file.filename or "").suffix.lower()
        if ext not in AUDIO_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported audio format: {ext}")

        track_num = max_num + i + 1
        title = title_list[i] if i < len(title_list) else Path(file.filename or f"track-{track_num}").stem
        slug = _slugify(title)
        filename = f"{track_num:02d}-{slug}{ext}"
        filepath = track_dir / filename

        content = await file.read()
        filepath.write_bytes(content)

        duration = _get_duration(str(filepath))

        track = Track(
            release_id=release.id,
            title=title,
            track_number=track_num,
            audio_file_path=str(filepath.relative_to(MEDIA_DIR)),
            duration_seconds=duration,
        )
        db.add(track)
        db.flush()
        created.append(_track_response(track, code))

    db.commit()
    return created


@router.delete("/releases/{code}/tracks/{track_id}", tags=["Tracks"], summary="Delete a track")
def delete_track(code: str, track_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Delete a track from a release and remove its audio file from disk. **Admin only.**

    **Auto-renumbering:** After deletion, all remaining tracks in the release are
    renumbered sequentially (1, 2, 3, ...) to fill the gap. This means track numbers
    may change — use track IDs (not track numbers) as stable identifiers.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    track = db.query(Track).filter(Track.id == track_id, Track.release_id == release.id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # Remove file
    if track.audio_file_path:
        fpath = MEDIA_DIR / track.audio_file_path
        if fpath.exists():
            fpath.unlink()

    db.delete(track)
    db.flush()

    # Renumber remaining tracks
    remaining = db.query(Track).filter(Track.release_id == release.id).order_by(Track.track_number).all()
    for idx, t in enumerate(remaining, 1):
        t.track_number = idx

    db.commit()
    return {"ok": True}


@router.put("/releases/{code}/tracks/reorder", tags=["Tracks"], summary="Reorder tracks")
def reorder_tracks(code: str, body: TrackReorder, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Reorder tracks within a release. **Admin only.**

    Provide an array of all track IDs in the desired playback order. Track numbers
    are reassigned sequentially (1, 2, 3, ...) based on the array order.

    **All track IDs must be included.** If a track ID is missing or doesn't belong
    to this release, the request fails with 400.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    tracks_by_id = {t.id: t for t in db.query(Track).filter(Track.release_id == release.id).all()}
    for idx, tid in enumerate(body.track_ids, 1):
        if tid not in tracks_by_id:
            raise HTTPException(status_code=400, detail=f"Track {tid} not found in this release")
        tracks_by_id[tid].track_number = idx

    db.commit()
    return {"ok": True}


@router.get("/tracks/{track_id}", tags=["Tracks"], summary="Get track by ID")
def get_track_by_id(track_id: int, db: Session = Depends(get_db), user: User | None = Depends(optional_user)):
    """Get a single track with release context. Public if release is published."""
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    release = db.query(Release).filter(Release.id == track.release_id).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    if release.status == "draft" and user is None:
        raise HTTPException(status_code=404, detail="Track not found")

    encoded = quote(release.product_code, safe="")
    cover_url = f"/api/releases/{encoded}/cover?size=thumb" if release.cover_art_path else None

    return {
        "id": track.id,
        "title": track.title,
        "track_number": track.track_number,
        "duration_seconds": track.duration_seconds,
        "release_code": release.product_code,
        "release_title": release.title,
        "cover_url": cover_url,
        "stream_url": track.audio_file_path and f"/api/releases/{encoded}/tracks/{track.id}/stream" or None,
    }


@router.get("/releases/{code}/tracks/{track_id}/stream", tags=["Tracks"], summary="Stream an audio track")
def stream_track(code: str, track_id: int, request: Request, db: Session = Depends(get_db), user: User | None = Depends(optional_user)):
    """Stream an audio file with full HTTP Range request support for seeking.

    **Visibility:** Public if the release is published. Returns 404 if the release is
    a draft and the user is not authenticated.

    **Range requests:** The endpoint supports `Range: bytes=START-END` headers for
    partial content (HTTP 206). This enables seeking in audio players without
    downloading the entire file. Without a Range header, the full file is returned.

    **Content-Type** is auto-detected from the file extension.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    if release.status == "draft" and user is None:
        raise HTTPException(status_code=404, detail="Release not found")

    track = db.query(Track).filter(Track.id == track_id, Track.release_id == release.id).first()
    if not track or not track.audio_file_path:
        raise HTTPException(status_code=404, detail="Track not found")

    fpath = MEDIA_DIR / track.audio_file_path
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    import mimetypes
    from fastapi.responses import Response, StreamingResponse
    mime_type = mimetypes.guess_type(str(fpath))[0] or "application/octet-stream"
    file_size = fpath.stat().st_size
    filename = fpath.name

    range_header = request.headers.get("range")
    if range_header:
        # Parse "bytes=start-end"
        range_spec = range_header.replace("bytes=", "")
        parts = range_spec.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        def iter_chunk():
            with open(fpath, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            iter_chunk(),
            status_code=206,
            media_type=mime_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
                "Content-Disposition": f'inline; filename="{filename}"',
            },
        )

    from fastapi.responses import FileResponse
    return FileResponse(fpath, media_type=mime_type, filename=filename, headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)})


@router.get("/releases/{code}/download", tags=["Tracks"], summary="Download all tracks as ZIP")
def download_release_zip(code: str, db: Session = Depends(get_db), user: User | None = Depends(optional_user)):
    """Download all tracks of a release as a ZIP archive.

    **Visibility:** Public if the release is published. Returns 404 for drafts
    unless the user is authenticated.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    if release.status == "draft" and user is None:
        raise HTTPException(status_code=404, detail="Release not found")

    tracks = db.query(Track).filter(Track.release_id == release.id).order_by(Track.track_number).all()
    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found")

    import io
    import zipfile
    from fastapi.responses import StreamingResponse

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in tracks:
            if not t.audio_file_path:
                continue
            fpath = MEDIA_DIR / t.audio_file_path
            if not fpath.exists():
                continue
            zf.write(fpath, fpath.name)

    buf.seek(0)
    safe_code = code.replace("/", "_").replace("\\", "_")
    zip_filename = f"{safe_code}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


# --- Cover art endpoints ---


@router.post("/releases/{code}/cover", tags=["Cover Art"], summary="Upload cover art")
async def upload_cover(code: str, file: UploadFile = File(..., description="Image file. Supported formats: JPG, PNG, WEBP, GIF."), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Upload or replace cover art for a release. **Admin only.**

    On upload, two files are stored in the release's media directory:
    - `cover.{ext}` — the original image at full resolution
    - `cover_thumb.webp` — a 400x400 WebP thumbnail for use in grids and the player

    If cover art already exists, it is replaced (both the original and thumbnail are
    deleted before the new files are written).

    Supported formats: JPG, JPEG, PNG, WEBP, GIF.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported image format: {ext}")

    rdir = _release_dir(code)
    rdir.mkdir(parents=True, exist_ok=True)

    # Remove old cover
    for old in rdir.glob("cover.*"):
        old.unlink()
    for old in rdir.glob("cover_thumb.*"):
        old.unlink()

    cover_path = rdir / f"cover{ext}"
    content = await file.read()
    cover_path.write_bytes(content)

    # Generate thumbnail
    thumb_path = rdir / "cover_thumb.webp"
    _generate_thumbnail(cover_path, thumb_path)

    release.cover_art_path = str(cover_path.relative_to(MEDIA_DIR))
    db.commit()

    return {"ok": True, "cover_art_url": f"/api/releases/{code}/cover"}


@router.get("/releases/{code}/cover", tags=["Cover Art"], summary="Get cover art")
def serve_cover(
    code: str,
    size: str | None = Query(None, description="Pass `thumb` to get the 400x400 WebP thumbnail instead of the full image."),
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Serve cover art for a release.

    **Visibility:** Public if the release is published. Returns 404 for draft releases
    when unauthenticated.

    **Thumbnail:** Pass `?size=thumb` to get the auto-generated 400x400 WebP thumbnail.
    This is what the catalog grid and audio player use.

    Returns the image file with appropriate Content-Type header.
    """
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    if release.status == "draft" and user is None:
        raise HTTPException(status_code=404, detail="Release not found")
    if not release.cover_art_path:
        raise HTTPException(status_code=404, detail="No cover art")

    if size == "thumb":
        thumb = _release_dir(code) / "cover_thumb.webp"
        if thumb.exists():
            from fastapi.responses import FileResponse
            return FileResponse(thumb, media_type="image/webp")

    fpath = MEDIA_DIR / release.cover_art_path
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Cover art file not found")

    from fastapi.responses import FileResponse
    return FileResponse(fpath)
