"""Release catalog API endpoints."""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from PIL import Image
from pydantic import BaseModel
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
        "stream_url": f"/api/releases/{release_code}/tracks/{track.id}/stream",
    }


def _release_summary(release: Release) -> dict:
    return {
        "product_code": release.product_code,
        "title": release.title,
        "entities": [{"id": e.id, "name": e.name, "slug": e.slug} for e in release.entities],
        "release_date": release.release_date.isoformat() if release.release_date else None,
        "cover_art_url": f"/api/releases/{release.product_code}/cover" if release.cover_art_path else None,
        "status": release.status,
        "track_count": len(release.tracks),
        "total_duration_seconds": sum(t.duration_seconds or 0 for t in release.tracks),
    }


def _release_detail(release: Release) -> dict:
    d = _release_summary(release)
    d.update({
        "description": release.description,
        "format_specs": release.format_specs,
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
    platform: str
    url: str
    label: str | None = None


class MetadataIn(BaseModel):
    key: str
    value: str
    sort_order: int = 0


class ReleaseCreate(BaseModel):
    title: str
    entity_ids: list[int] = []
    product_code: str | None = None
    release_date: str | None = None
    description: str | None = None
    format_specs: str | None = None
    status: str = "draft"
    distribution_links: list[DistLinkIn] = []
    metadata: list[MetadataIn] = []


class ReleaseUpdate(BaseModel):
    title: str | None = None
    entity_ids: list[int] | None = None
    product_code: str | None = None
    release_date: str | None = None
    description: str | None = None
    format_specs: str | None = None
    distribution_links: list[DistLinkIn] | None = None
    metadata: list[MetadataIn] | None = None


class EntityCreate(BaseModel):
    name: str
    description: str | None = None


class EntityUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class TrackReorder(BaseModel):
    track_ids: list[int]


# --- Entity endpoints ---


@router.get("/entities")
def list_entities(db: Session = Depends(get_db)):
    entities = db.query(Entity).order_by(Entity.name).all()
    return [_entity_response(e, db) for e in entities]


@router.post("/entities", status_code=201)
def create_entity(body: EntityCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    slug = _slugify(body.name)
    if db.query(Entity).filter(Entity.name == body.name).first():
        raise HTTPException(status_code=409, detail="Entity already exists")
    entity = Entity(name=body.name, slug=slug, description=body.description)
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return _entity_response(entity, db)


@router.put("/entities/{entity_id}")
def update_entity(entity_id: int, body: EntityUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@router.delete("/entities/{entity_id}")
def delete_entity(entity_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@router.get("/releases/next-code")
def next_code(
    year: int = Query(...),
    category: str = Query("MX"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"product_code": generate_product_code(db, year, category)}


# --- Release endpoints ---


@router.post("/releases", status_code=201)
def create_release(body: ReleaseCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Determine product code
    product_code = body.product_code
    if not product_code:
        year = date.today().year
        if body.release_date:
            try:
                year = date.fromisoformat(body.release_date).year
            except ValueError:
                pass
        # Guess category from track count hint (not available yet, use MX)
        product_code = generate_product_code(db, year, "MX")

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


@router.get("/releases")
def list_releases(
    status: str = Query("published"),
    entity: str | None = Query(None),
    year: int | None = Query(None),
    sort: str = Query("date_desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User | None = Depends(lambda request: _optional_user(request)),
):
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


def _optional_user(request):
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


@router.get("/releases/{code}")
def get_release(code: str, db: Session = Depends(get_db), user: User | None = Depends(lambda request: _optional_user(request))):
    release = _get_release_or_404(db, code, user)
    return _release_detail(release)


@router.put("/releases/{code}")
def update_release(code: str, body: ReleaseUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    if body.title is not None:
        release.title = body.title
    if body.description is not None:
        release.description = body.description
    if body.format_specs is not None:
        release.format_specs = body.format_specs
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

    db.commit()
    final_code = new_code if new_code else code
    release = _get_release_or_404(db, final_code, admin)
    return _release_detail(release)


@router.post("/releases/{code}/publish")
def publish_release(code: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    release.status = "published"
    db.commit()
    return {"ok": True}


@router.post("/releases/{code}/unpublish")
def unpublish_release(code: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    release = db.query(Release).filter(Release.product_code == code).first()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    release.status = "draft"
    db.commit()
    return {"ok": True}


@router.delete("/releases/{code}")
def delete_release(code: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@router.post("/releases/{code}/tracks", status_code=201)
async def upload_tracks(
    code: str,
    files: list[UploadFile] = File(...),
    titles: str | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
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


@router.delete("/releases/{code}/tracks/{track_id}")
def delete_track(code: str, track_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@router.put("/releases/{code}/tracks/reorder")
def reorder_tracks(code: str, body: TrackReorder, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@router.get("/releases/{code}/tracks/{track_id}/stream")
def stream_track(code: str, track_id: int, db: Session = Depends(get_db), user: User | None = Depends(lambda request: _optional_user(request))):
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

    from fastapi.responses import FileResponse
    return FileResponse(fpath, media_type="application/octet-stream", headers={"Accept-Ranges": "bytes"})


# --- Cover art endpoints ---


@router.post("/releases/{code}/cover")
async def upload_cover(code: str, file: UploadFile = File(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@router.get("/releases/{code}/cover")
def serve_cover(
    code: str,
    size: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User | None = Depends(lambda request: _optional_user(request)),
):
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
