"""Media search engine API endpoints."""

import hashlib
import io
import logging
import mimetypes
import os
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from auth import get_db, require_scope
from models import (
    ApiKey,
    ExtractionFailure,
    MediaItem,
    MediaSource,
    MediaTag,
    TagVocabulary,
    User,
)
from search_client import delete_media_item as meili_delete, sync_media_item as meili_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

def _get_search_media_dir() -> Path:
    return Path(os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data"))


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SearchFilters(BaseModel):
    tags: list[str] | None = None
    source_channels: list[str] | None = None
    poster: str | None = None  # filter by uploader name
    color: str | None = None  # filter by dominant color hex
    date_range: dict | None = None  # {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}
    reaction_count: dict | None = None  # {"min": N}
    tag_count: dict | None = None  # {"min": N}


class SearchRequest(BaseModel):
    query: str = ""
    media_types: list[str] | None = None
    filters: SearchFilters | None = None
    sort: str | None = None  # e.g. "created_at:desc"
    page: int = 1
    per_page: int = 20


class MediaUpdateRequest(BaseModel):
    description: str | None = None


class TagsRequest(BaseModel):
    tags: list[str]


class BatchTagsRequest(BaseModel):
    media_ids: list[str]
    tags: list[str]


class BatchDeleteRequest(BaseModel):
    media_ids: list[str]


class BatchReExtractRequest(BaseModel):
    media_ids: list[str]


class BatchExportRequest(BaseModel):
    media_ids: list[str]


class ApiKeyCreateRequest(BaseModel):
    label: str
    scope: str  # read, write, admin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _media_type_from_mime(mime: str) -> str | None:
    """Derive media_type (image/audio/video) from a MIME type string."""
    if mime.startswith("image/"):
        return "image"
    elif mime.startswith("audio/"):
        return "audio"
    elif mime.startswith("video/"):
        return "video"
    return None


def _get_media_item_or_404(db: Session, media_id: str) -> MediaItem:
    item = (
        db.query(MediaItem)
        .options(
            joinedload(MediaItem.sources),
            joinedload(MediaItem.tags),
            joinedload(MediaItem.image_meta),
            joinedload(MediaItem.audio_meta),
            joinedload(MediaItem.video_meta),
            joinedload(MediaItem.extraction_failures),
        )
        .filter(MediaItem.id == media_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    return item


def _media_item_response(item: MediaItem) -> dict:
    """Build a full metadata response dict for a MediaItem."""
    data = {
        "id": item.id,
        "sha256": item.sha256,
        "filename": item.filename,
        "file_path": item.file_path,
        "media_type": item.media_type,
        "file_size_bytes": item.file_size_bytes,
        "mime_type": item.mime_type,
        "description": item.description,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "tags": [t.tag for t in item.tags],
        "sources": [
            {
                "id": s.id,
                "source_type": s.source_type,
                "source_channel": s.source_channel,
                "slack_message_text": s.slack_message_text,
                "reaction_count": s.reaction_count,
                "source_url": s.source_url,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in item.sources
        ],
        "image_meta": None,
        "audio_meta": None,
        "video_meta": None,
        "extraction_failures": [
            {
                "id": f.id,
                "extraction_type": f.extraction_type,
                "error_message": f.error_message,
                "attempts": f.attempts,
                "last_attempt_at": f.last_attempt_at.isoformat() if f.last_attempt_at else None,
                "resolved": f.resolved,
            }
            for f in item.extraction_failures
        ],
    }
    if item.image_meta:
        m = item.image_meta
        data["image_meta"] = {
            "width": m.width,
            "height": m.height,
            "format": m.format,
            "dominant_colors": m.dominant_colors,
            "caption": m.caption,
        }
    if item.audio_meta:
        m = item.audio_meta
        data["audio_meta"] = {
            "duration_seconds": m.duration_seconds,
            "sample_rate": m.sample_rate,
            "channels": m.channels,
            "bit_depth": m.bit_depth,
            "transcript": m.transcript,
        }
    if item.video_meta:
        m = item.video_meta
        data["video_meta"] = {
            "duration_seconds": m.duration_seconds,
            "width": m.width,
            "height": m.height,
            "fps": m.fps,
            "audio_transcript": m.audio_transcript,
        }
    return data


def _build_meili_filter(filters: SearchFilters | None) -> str | None:
    """Convert SearchFilters into a Meilisearch filter string."""
    if not filters:
        return None

    parts = []

    if filters.tags:
        tag_clauses = [f'tags = "{t}"' for t in filters.tags]
        parts.append("(" + " AND ".join(tag_clauses) + ")")

    if filters.source_channels:
        ch_clauses = [f'source_channels = "{c}"' for c in filters.source_channels]
        parts.append("(" + " OR ".join(ch_clauses) + ")")

    if filters.date_range:
        if filters.date_range.get("from"):
            try:
                dt = datetime.fromisoformat(filters.date_range["from"])
                ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
                parts.append(f"created_at >= {ts}")
            except ValueError:
                pass
        if filters.date_range.get("to"):
            try:
                dt = datetime.fromisoformat(filters.date_range["to"])
                ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
                parts.append(f"created_at <= {ts}")
            except ValueError:
                pass

    if filters.poster:
        parts.append(f'sources.uploader = "{filters.poster}"')

    if filters.color:
        parts.append(f'dominant_colors = "{filters.color}"')

    if filters.reaction_count and filters.reaction_count.get("min") is not None:
        parts.append(f"total_reaction_count >= {filters.reaction_count['min']}")

    if filters.tag_count and filters.tag_count.get("min") is not None:
        parts.append(f"tag_count >= {filters.tag_count['min']}")

    return " AND ".join(parts) if parts else None


def _normalize_tag(tag: str) -> str:
    """Normalize a tag: lowercase, strip whitespace."""
    return tag.lower().strip()


def _update_vocabulary(db: Session, tag: str, delta: int) -> None:
    """Increment or decrement usage_count in TagVocabulary. Create if needed."""
    vocab = db.query(TagVocabulary).filter(TagVocabulary.tag == tag).first()
    if vocab:
        vocab.usage_count = max(0, vocab.usage_count + delta)
    elif delta > 0:
        vocab = TagVocabulary(tag=tag, usage_count=delta)
        db.add(vocab)


# ---------------------------------------------------------------------------
# Search endpoints
# ---------------------------------------------------------------------------


@router.post("/search")
def search_media(
    body: SearchRequest,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Multi-index media search with filters, facets, and pagination."""
    from search_client import multi_search

    meili_filter = _build_meili_filter(body.filters)
    sort_list = [body.sort] if body.sort else None

    # If filtering by color, only search images (other indexes don't have dominant_colors)
    media_types = body.media_types
    if body.filters and body.filters.color:
        media_types = ["image"]

    results = multi_search(
        query=body.query,
        media_types=media_types,
        filters=meili_filter,
        sort=sort_list,
        page=body.page,
        per_page=body.per_page,
    )
    return results


@router.get("/search/facets")
def search_facets(
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Return available filter options: channels and uploaders."""
    from models import MediaSource
    from sqlalchemy import distinct, func

    # Distinct channels
    channels = [
        r[0] for r in
        db.query(distinct(MediaSource.source_channel))
        .filter(MediaSource.source_channel.isnot(None))
        .all()
    ]

    # Distinct uploaders — extract from source_metadata JSON "poster" field
    import json as _json
    uploaders_set = set()
    sources_with_meta = (
        db.query(MediaSource.source_metadata)
        .filter(MediaSource.source_metadata.isnot(None))
        .distinct()
        .all()
    )
    for (meta_str,) in sources_with_meta:
        try:
            meta = _json.loads(meta_str)
            if isinstance(meta, dict) and meta.get("poster"):
                uploaders_set.add(meta["poster"])
        except (ValueError, TypeError):
            pass
    uploaders = list(uploaders_set)

    return {
        "channels": sorted(channels),
        "uploaders": sorted(uploaders),
    }


@router.get("/tags")
def list_tags(
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """List all tags with usage counts from the vocabulary."""
    tags = db.query(TagVocabulary).order_by(TagVocabulary.usage_count.desc()).all()
    return [
        {"tag": t.tag, "usage_count": t.usage_count, "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in tags
    ]


# ---------------------------------------------------------------------------
# Media static routes (must be defined before /media/{media_id} to avoid capture)
# ---------------------------------------------------------------------------


@router.post("/media/upload", status_code=201)
async def upload_media(
    file: UploadFile = File(...),
    tags: str = Form(""),
    description: str = Form(""),
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Upload a media file with optional tags and description.

    Tags are comma-separated. Media type is auto-detected from MIME type.
    Deduplication is done via SHA-256 hash.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Detect MIME type
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    media_type = _media_type_from_mime(mime)
    if not media_type:
        raise HTTPException(status_code=400, detail=f"Unsupported MIME type: {mime}")

    # SHA-256 hash for dedup
    sha256 = hashlib.sha256(content).hexdigest()

    # Check for duplicate
    existing = db.query(MediaItem).filter(MediaItem.sha256 == sha256).first()
    if existing:
        # Add a new source pointing to the existing item
        source = MediaSource(
            media_item_id=existing.id,
            source_type="manual_upload",
        )
        db.add(source)
        db.commit()
        meili_sync(db, existing)
        item = _get_media_item_or_404(db, existing.id)
        return _media_item_response(item)

    # Build storage path: {media_type}/{YYYY-MM}/{8char-sha256}_{filename}
    now = datetime.now(timezone.utc)
    date_dir = now.strftime("%Y-%m")
    safe_filename = file.filename or "upload"
    relative_path = f"{media_type}/{date_dir}/{sha256[:8]}_{safe_filename}"
    full_path = _get_search_media_dir() / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)

    # Create MediaItem
    item_id = str(uuid.uuid4())
    media_item = MediaItem(
        id=item_id,
        sha256=sha256,
        filename=safe_filename,
        file_path=relative_path,
        media_type=media_type,
        file_size_bytes=len(content),
        mime_type=mime,
        description=description or None,
    )
    db.add(media_item)

    # Create source
    source = MediaSource(
        media_item_id=item_id,
        source_type="manual_upload",
    )
    db.add(source)

    # Process tags
    tag_list = [_normalize_tag(t) for t in tags.split(",") if t.strip()] if tags else []
    for tag in tag_list:
        if not tag:
            continue
        media_tag = MediaTag(media_item_id=item_id, tag=tag)
        db.add(media_tag)
        _update_vocabulary(db, tag, 1)

    db.commit()
    db.refresh(media_item)
    meili_sync(db, media_item)

    item = _get_media_item_or_404(db, item_id)
    return _media_item_response(item)


# ---------------------------------------------------------------------------
# Batch operations (must be before /media/{media_id} to avoid route capture)
# ---------------------------------------------------------------------------


@router.post("/media/batch/tags")
def batch_add_tags(
    body: BatchTagsRequest,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Add tags to multiple media items."""
    results = {}
    for media_id in body.media_ids:
        item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
        if not item:
            results[media_id] = {"error": "not found"}
            continue

        existing_tags = {t.tag for t in db.query(MediaTag).filter(MediaTag.media_item_id == media_id).all()}
        added = []
        for raw_tag in body.tags:
            tag = _normalize_tag(raw_tag)
            if not tag or tag in existing_tags:
                continue
            media_tag = MediaTag(media_item_id=media_id, tag=tag)
            db.add(media_tag)
            _update_vocabulary(db, tag, 1)
            existing_tags.add(tag)
            added.append(tag)
        results[media_id] = {"added": added}

    db.commit()

    for media_id in body.media_ids:
        item = (
            db.query(MediaItem)
            .options(joinedload(MediaItem.sources), joinedload(MediaItem.tags))
            .filter(MediaItem.id == media_id)
            .first()
        )
        if item:
            meili_sync(db, item)

    return {"ok": True, "results": results}


@router.post("/media/batch/delete")
def batch_delete(
    body: BatchDeleteRequest,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Delete multiple media items and their files."""
    deleted = []
    not_found = []
    for media_id in body.media_ids:
        item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
        if not item:
            not_found.append(media_id)
            continue

        media_type = item.media_type
        file_path = _get_search_media_dir() / item.file_path
        if file_path.exists():
            file_path.unlink()
        thumb = file_path.with_name(file_path.stem + "_thumb.webp")
        if thumb.exists():
            thumb.unlink()

        db.delete(item)
        meili_delete(media_id, media_type)
        deleted.append(media_id)

    db.commit()
    return {"ok": True, "deleted": deleted, "not_found": not_found}


@router.post("/media/batch/re-extract")
def batch_re_extract_endpoint(
    body: BatchReExtractRequest,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Trigger re-extraction for selected media items."""
    try:
        from extraction import run_extraction_async
    except ImportError:
        logger.warning("extraction module not available; re-extract is a no-op")
        return {"ok": False, "detail": "Extraction module not yet available", "queued": []}

    queued = []
    not_found = []
    for media_id in body.media_ids:
        item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
        if not item:
            not_found.append(media_id)
            continue
        try:
            full_path = str(_get_search_media_dir() / item.file_path)
            run_extraction_async(item.id, full_path, item.media_type)
            queued.append(media_id)
        except Exception as e:
            logger.exception("Failed to queue re-extraction for %s", media_id)
            not_found.append(media_id)

    return {"ok": True, "queued": queued, "not_found": not_found}


@router.post("/media/batch/export")
def batch_export(
    body: BatchExportRequest,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Create a zip archive of selected media files and return as download."""
    if not body.media_ids:
        raise HTTPException(status_code=400, detail="No media IDs provided")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for media_id in body.media_ids:
            item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
            if not item:
                continue
            file_path = _get_search_media_dir() / item.file_path
            if file_path.exists():
                arcname = item.filename or file_path.name
                zf.write(file_path, arcname)

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=media_export.zip"},
    )


# ---------------------------------------------------------------------------
# Media CRUD (parameterized routes — must come after static /media/* routes)
# ---------------------------------------------------------------------------


@router.get("/media/{media_id}")
def get_media(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Get full metadata for a media item."""
    item = _get_media_item_or_404(db, media_id)
    return _media_item_response(item)


@router.get("/media/{media_id}/file")
def get_media_file(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Stream or download the media file."""
    item = _get_media_item_or_404(db, media_id)
    file_path = _get_search_media_dir() / item.file_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    mime = item.mime_type or "application/octet-stream"
    return FileResponse(file_path, media_type=mime, filename=item.filename)


@router.get("/media/{media_id}/thumbnail")
def get_media_thumbnail(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Serve the thumbnail for images and videos."""
    item = _get_media_item_or_404(db, media_id)

    # For videos, use the thumbnail_path from video meta
    if item.media_type == "video" and item.video_meta and item.video_meta.thumbnail_path:
        thumb_path = _get_search_media_dir() / item.video_meta.thumbnail_path
        if thumb_path.exists():
            return FileResponse(thumb_path, media_type="image/webp")

    # For images (and fallback), look for _thumb.webp alongside the original
    if item.file_path:
        original = _get_search_media_dir() / item.file_path
        thumb = original.with_name(original.stem + "_thumb.webp")
        if thumb.exists():
            return FileResponse(thumb, media_type="image/webp")

    # Fallback: serve the original for images
    if item.media_type == "image":
        file_path = _get_search_media_dir() / item.file_path
        if file_path.exists():
            return FileResponse(file_path, media_type=item.mime_type or "application/octet-stream")

    raise HTTPException(status_code=404, detail="Thumbnail not found")


@router.put("/media/{media_id}")
def update_media(
    media_id: str,
    body: MediaUpdateRequest,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Update a media item's description."""
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    if body.description is not None:
        item.description = body.description

    db.commit()
    db.refresh(item)
    meili_sync(db, item)

    return _media_item_response(_get_media_item_or_404(db, media_id))


@router.delete("/media/{media_id}")
def delete_media(
    media_id: str,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Delete a media item and its file from disk."""
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    media_type = item.media_type

    # Remove file from disk
    file_path = _get_search_media_dir() / item.file_path
    if file_path.exists():
        file_path.unlink()
    # Remove thumbnail if it exists
    thumb = file_path.with_name(file_path.stem + "_thumb.webp")
    if thumb.exists():
        thumb.unlink()

    db.delete(item)
    db.commit()
    meili_delete(media_id, media_type)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Tagging endpoints
# ---------------------------------------------------------------------------


@router.post("/media/{media_id}/tags")
def add_tags(
    media_id: str,
    body: TagsRequest,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Add tags to a media item."""
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    existing_tags = {t.tag for t in db.query(MediaTag).filter(MediaTag.media_item_id == media_id).all()}

    added = []
    for raw_tag in body.tags:
        tag = _normalize_tag(raw_tag)
        if not tag or tag in existing_tags:
            continue
        media_tag = MediaTag(media_item_id=media_id, tag=tag)
        db.add(media_tag)
        _update_vocabulary(db, tag, 1)
        existing_tags.add(tag)
        added.append(tag)

    db.commit()
    meili_sync(db, _get_media_item_or_404(db, media_id))

    return {"ok": True, "added": added}


@router.delete("/media/{media_id}/tags/{tag}")
def remove_tag(
    media_id: str,
    tag: str,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Remove a tag from a media item."""
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    normalized = _normalize_tag(tag)
    media_tag = (
        db.query(MediaTag)
        .filter(MediaTag.media_item_id == media_id, MediaTag.tag == normalized)
        .first()
    )
    if not media_tag:
        raise HTTPException(status_code=404, detail="Tag not found on this item")

    db.delete(media_tag)
    _update_vocabulary(db, normalized, -1)
    db.commit()
    meili_sync(db, _get_media_item_or_404(db, media_id))

    return {"ok": True}


@router.get("/tags/suggest")
def suggest_tags(
    q: str = Query("", min_length=1),
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Autocomplete tag suggestions from vocabulary, sorted by usage count."""
    tags = (
        db.query(TagVocabulary)
        .filter(TagVocabulary.tag.ilike(f"%{q}%"))
        .order_by(TagVocabulary.usage_count.desc())
        .limit(20)
        .all()
    )
    return [{"tag": t.tag, "usage_count": t.usage_count} for t in tags]


# ---------------------------------------------------------------------------
# Ingest endpoints
# ---------------------------------------------------------------------------


@router.post("/ingest/slack")
def ingest_slack(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Trigger a Slack scrape."""
    try:
        from slack_scraper import trigger_scrape
    except ImportError:
        logger.warning("slack_scraper module not available")
        return {"ok": False, "detail": "Slack scraper module not yet available"}

    try:
        result = trigger_scrape()
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("Slack scrape failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingest/slack/status")
def ingest_slack_status(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Check the last Slack scrape run status."""
    try:
        from slack_scraper import get_scrape_status
    except ImportError:
        return {"ok": False, "detail": "Slack scraper module not yet available", "last_run": None}

    try:
        scrape_status = get_scrape_status()
        return {"ok": True, "status": scrape_status}
    except Exception as e:
        logger.exception("Failed to get scrape status")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/slack/dry-run")
def ingest_slack_dry_run(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Run a dry-run Slack scrape (calculate sizes without downloading)."""
    try:
        from slack_scraper import trigger_dry_run
    except ImportError:
        return {"ok": False, "detail": "Slack scraper module not yet available"}

    try:
        result = trigger_dry_run()
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("Slack dry-run failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# API Key management
# ---------------------------------------------------------------------------


@router.get("/keys")
def list_api_keys(
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """List active API keys for the current user."""
    user, _ = _auth
    keys = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return [
        {
            "id": k.id,
            "key_prefix": k.key_prefix,
            "label": k.label,
            "scope": k.scope,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }
        for k in keys
    ]


@router.post("/keys", status_code=201)
def create_api_key(
    body: ApiKeyCreateRequest,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Generate a new API key. The raw key is returned only once."""
    if body.scope not in ("read", "write", "admin"):
        raise HTTPException(status_code=400, detail="Scope must be read, write, or admin")

    user, _ = _auth

    from auth import generate_api_key, hash_api_key

    # Generate a secure random key
    raw_key = "au_" + generate_api_key()
    key_hash = hash_api_key(raw_key)
    key_prefix = raw_key[:11]  # "au_" + 8 chars

    api_key = ApiKey(
        user_id=user.id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        label=body.label,
        scope=body.scope,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return {
        "id": api_key.id,
        "key": raw_key,  # Only returned once
        "key_prefix": key_prefix,
        "label": api_key.label,
        "scope": api_key.scope,
        "created_at": api_key.created_at.isoformat() if api_key.created_at else None,
    }


@router.delete("/keys/{key_id}")
def revoke_api_key(
    key_id: str,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Revoke an API key."""
    user, _ = _auth
    api_key = (
        db.query(ApiKey)
        .filter(ApiKey.id == key_id, ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
        .first()
    )
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    api_key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Extraction failure endpoints
# ---------------------------------------------------------------------------


@router.get("/extraction-failures")
def list_extraction_failures(
    resolved: bool = Query(False),
    extraction_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """List extraction failures with optional filters."""
    q = db.query(ExtractionFailure).options(joinedload(ExtractionFailure.media_item))
    q = q.filter(ExtractionFailure.resolved == resolved)

    if extraction_type:
        q = q.filter(ExtractionFailure.extraction_type == extraction_type)

    total = q.count()
    failures = q.order_by(ExtractionFailure.last_attempt_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "failures": [
            {
                "id": f.id,
                "media_item_id": f.media_item_id,
                "extraction_type": f.extraction_type,
                "error_message": f.error_message,
                "attempts": f.attempts,
                "last_attempt_at": f.last_attempt_at.isoformat() if f.last_attempt_at else None,
                "resolved": f.resolved,
                "media_filename": f.media_item.filename if f.media_item else None,
            }
            for f in failures
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.post("/extraction-failures/{failure_id}/retry")
def retry_extraction_failure(
    failure_id: str,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Retry a single extraction failure."""
    failure = db.query(ExtractionFailure).filter(ExtractionFailure.id == failure_id).first()
    if not failure:
        raise HTTPException(status_code=404, detail="Extraction failure not found")

    try:
        from extraction import retry_extraction
    except ImportError:
        logger.warning("extraction module not available")
        return {"ok": False, "detail": "Extraction module not yet available"}

    try:
        retry_extraction(failure_id)
        db.refresh(failure)
        return {"ok": True, "resolved": failure.resolved}
    except Exception as e:
        logger.exception("Retry failed for extraction failure %s", failure_id)
        return {"ok": False, "detail": str(e)}


@router.post("/extraction-failures/{failure_id}/resolve")
def resolve_extraction_failure(
    failure_id: str,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Mark an extraction failure as resolved without retrying."""
    failure = db.query(ExtractionFailure).filter(ExtractionFailure.id == failure_id).first()
    if not failure:
        raise HTTPException(status_code=404, detail="Extraction failure not found")

    failure.resolved = True
    failure.last_attempt_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}
