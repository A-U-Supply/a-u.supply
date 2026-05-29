"""Media search engine API endpoints."""

import hashlib
import io
import json
import logging
import mimetypes
import os
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, joinedload

from server.auth import get_db, require_admin, require_scope
from server.models import (
    ApiKey,
    ExtractionFailure,
    Job,
    JobOutput,
    MediaItem,
    MediaSlackShare,
    MediaSource,
    MediaTag,
    MediaVote,
    TagVocabulary,
    User,
)
from server.search_client import delete_media_item as meili_delete, sync_media_item as meili_sync
from server.slack_notifier import queue_batched
from server import vote_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

def _get_search_media_dir() -> Path:
    return Path(os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data"))


def content_disposition(disposition: str, filename: str) -> str:
    """Build an RFC 6266 Content-Disposition value that survives unicode.

    HTTP header values must encode as latin-1, so filenames containing
    characters like `\\u202f` (narrow no-break space — common in Slack
    uploads from French locales) crash starlette if inserted raw. Emit
    an ASCII-safe `filename="..."` for legacy clients plus a
    `filename*=UTF-8''...` form (RFC 5987) with the real name.
    """
    clean = (filename or "").replace('"', "").replace("\r", "").replace("\n", "")
    ascii_name = clean.encode("ascii", "replace").decode("ascii").replace("?", "_")
    if not ascii_name:
        ascii_name = "download"
    utf8_name = quote(clean, safe="")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SearchFilters(BaseModel):
    """Filters for the media search endpoint. All filters are optional and combined with AND logic."""
    tags: list[str] | None = Field(None, description="Filter to items that have ALL of these tags (AND logic).")
    source_channels: list[str] | None = Field(None, description="Filter to items from ANY of these Slack channels (OR logic).")
    poster: str | None = Field(None, description="Filter by uploader/poster name (exact match).")
    color: str | None = Field(None, description="Filter images by exact dominant color hex value (e.g. `#1a1a2e`). Only returns images.")
    color_group: list[str] | None = Field(None, description="Filter images by color group names: `red`, `orange`, `yellow`, `green`, `cyan`, `blue`, `purple`, `pink`, `brown`, `gray`, `white`, `black`. Only returns images.")
    date_range: dict | None = Field(None, description="Date range filter: `{\"from\": \"YYYY-MM-DD\", \"to\": \"YYYY-MM-DD\"}`. Both are optional.")
    reaction_count: dict | None = Field(None, description="Minimum reaction count: `{\"min\": 3}`.")
    tag_count: dict | None = Field(None, description="Tag count filter: `{\"min\": 1}` and/or `{\"max\": 5}`.")
    output_index: list[str] | None = Field(None, description="Filter by one or more output index/collection names. Use `__inputs__` for items not produced by a job. Pass a list to OR multiple together (e.g. `[\"__inputs__\", \"outputs\"]`).")
    has_transcript: bool | None = Field(None, description="Filter audio/video items by whether they have a transcript.")
    has_text: bool | None = Field(None, description="Filter images by whether they have OCR-extracted text.")
    job_app: str | None = Field(None, description="Filter outputs by app name (e.g. `rottengenizdat`).")
    vote_score: dict | None = Field(None, description="Net vote score (up minus down): `{\"min\": 3}` and/or `{\"max\": -1}`.")
    up_count: dict | None = Field(None, description="Upvote count range: `{\"min\": 1}` and/or `{\"max\": 10}`.")
    down_count: dict | None = Field(None, description="Downvote count range: `{\"min\": 1}` and/or `{\"max\": 0}`.")
    my_votes: str | None = Field(None, description="Filter to items the calling user has voted on: `up`, `down`, `any`, or `none`.")

    # ---- AI vision enrichment filters (see docs/ai-image-descriptions.md) ----
    has_ai_description: bool | None = Field(None, description="Filter images by whether they have an AI-generated description.")
    ai_vibe: list[str] | None = Field(None, description="Filter images by AI-derived vibe (controlled vocab; OR within the list).")
    ai_color_temperature: list[str] | None = Field(None, description="Filter images by AI color temperature: `warm`, `cool`, `neutral`.")
    ai_color_character: list[str] | None = Field(None, description="Filter images by AI color character: `vibrant`, `muted`, `pastel`, `monochrome`, `high-contrast`, `earthy`, `dark`, `light`.")
    is_screenshot:    bool | None = Field(None, description="Filter images by `is_screenshot`.")
    is_meme:          bool | None = Field(None, description="Filter images by `is_meme`.")
    is_photo:         bool | None = Field(None, description="Filter images by `is_photo`.")
    is_artwork:       bool | None = Field(None, description="Filter images by `is_artwork`.")
    is_ai_generated:  bool | None = Field(None, description="Filter images by `is_ai_generated`.")
    has_human:        bool | None = Field(None, description="Filter images by `has_human`.")
    has_face:         bool | None = Field(None, description="Filter images by `has_face`.")
    has_text_overlay: bool | None = Field(None, description="Filter images by `has_text_overlay`.")
    is_nsfw:          bool | None = Field(None, description="Filter images by `is_nsfw`. Most useful as `false` to exclude NSFW.")

    @field_validator("ai_vibe", "ai_color_temperature", "ai_color_character", mode="before")
    @classmethod
    def coerce_ai_lists(cls, v):
        if isinstance(v, str):
            return [v] if v else None
        return v

    @field_validator("color_group", mode="before")
    @classmethod
    def coerce_color_group(cls, v):
        if isinstance(v, str):
            return [v] if v else None
        return v

    @field_validator("output_index", mode="before")
    @classmethod
    def coerce_output_index(cls, v):
        if isinstance(v, str):
            return [v] if v else None
        return v


class SearchRequest(BaseModel):
    """Search request body for the multi-index media search.

    Leave `query` empty and use only filters to browse all items. The search is
    typo-tolerant and searches across tags, descriptions, Slack message text,
    transcripts, captions, filenames, and source titles.
    """
    query: str = Field("", description="Search query string. Typo-tolerant full-text search. Leave empty to browse all items.")
    media_types: list[str] | None = Field(None, description="Filter by media type: `image`, `audio`, `video`. Null or omitted searches all types.")
    filters: SearchFilters | None = Field(None, description="Additional filters (tags, channels, dates, etc.).")
    sort: str | None = Field(None, description="Sort field and direction, e.g. `created_at:desc`, `total_reaction_count:desc`, `file_size_bytes:asc`. Null for relevance sorting.")
    page: int = Field(1, ge=1, description="Page number (1-indexed).")
    per_page: int = Field(20, ge=1, le=500, description="Results per page (max 500).")
    include_emulsion: bool = Field(False, description="Also search the private Emulsion (Latents) index. Admin only.")


class MediaUpdateRequest(BaseModel):
    """Update a media item's metadata."""
    description: str | None = Field(None, description="New description/notes for the media item. Set to empty string to clear.")


class TagsRequest(BaseModel):
    """Add tags to a media item."""
    tags: list[str] = Field(..., description="Tags to add. Normalized automatically (lowercased, trimmed). Duplicates are silently ignored.")


class BatchTagsRequest(BaseModel):
    """Add tags to multiple media items at once."""
    media_ids: list[str] = Field(..., description="List of media item UUIDs to tag.")
    tags: list[str] = Field(..., description="Tags to add to all specified items.")


class BatchDeleteRequest(BaseModel):
    """Delete multiple media items at once."""
    media_ids: list[str] = Field(..., description="List of media item UUIDs to delete.")


class BatchReExtractRequest(BaseModel):
    """Re-run the metadata extraction pipeline on selected items."""
    media_ids: list[str] = Field(..., description="List of media item UUIDs to re-extract metadata for.")


class BatchExportRequest(BaseModel):
    """Download multiple media files as a ZIP archive."""
    media_ids: list[str] = Field(..., description="List of media item UUIDs to include in the ZIP export.")


class AiFieldsOverrideRequest(BaseModel):
    """Patch the AI-derived fields on an image with human corrections.

    Any field present in the request body is updated AND marked in
    ``ai_overrides`` so subsequent AI regenerations preserve it.
    """
    is_screenshot:        bool | None = None
    is_meme:              bool | None = None
    is_photo:             bool | None = None
    is_artwork:           bool | None = None
    is_ai_generated:      bool | None = None
    has_human:            bool | None = None
    has_face:             bool | None = None
    has_text_overlay:     bool | None = None
    is_nsfw:              bool | None = None
    ai_color_temperature: str | None = None
    ai_color_character:   str | None = None
    ai_vibe:              list[str] | None = None


class ApiKeyCreateRequest(BaseModel):
    """Generate a new API key for programmatic access."""
    label: str = Field(..., description="Human-readable label for the key (e.g. `my-script`, `laptop`). Shown in the key list for identification.")
    scope: str = Field(..., description="Permission scope: `read` (search/view/download), `write` (read + upload/tag/edit), or `admin` (write + delete/manage).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Thumbnail resolution + placeholders
#
# Every thumbnail endpoint (authed, public-outputs, og-thumb) uses the same
# resolution chain so behavior is consistent:
#
#   video: video_meta.thumbnail_path → <stem>_thumb.webp → video placeholder
#   image: <stem>_thumb.webp → original file → image placeholder (rare)
#   audio: <stem>_thumb.webp → audio placeholder SVG
#
# Placeholders are tiny inline SVGs so callers never see a broken image.
# ---------------------------------------------------------------------------

_AUDIO_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" '
    b'width="400" height="400" role="img" aria-label="Audio">'
    b'<rect width="400" height="400" fill="#1a1a1a"/>'
    b'<g fill="none" stroke="#888" stroke-width="6" stroke-linecap="round">'
    b'<line x1="70" y1="200" x2="70" y2="180"/>'
    b'<line x1="110" y1="160" x2="110" y2="240"/>'
    b'<line x1="150" y1="120" x2="150" y2="280"/>'
    b'<line x1="190" y1="150" x2="190" y2="250"/>'
    b'<line x1="230" y1="100" x2="230" y2="300"/>'
    b'<line x1="270" y1="170" x2="270" y2="230"/>'
    b'<line x1="310" y1="130" x2="310" y2="270"/>'
    b'<line x1="350" y1="190" x2="350" y2="210"/>'
    b"</g></svg>"
)

_VIDEO_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" '
    b'width="400" height="400" role="img" aria-label="Video">'
    b'<rect width="400" height="400" fill="#1a1a1a"/>'
    b'<polygon points="170,140 170,260 270,200" fill="#888"/>'
    b"</svg>"
)

_IMAGE_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" '
    b'width="400" height="400" role="img" aria-label="Image">'
    b'<rect width="400" height="400" fill="#1a1a1a"/>'
    b'<rect x="100" y="120" width="200" height="160" rx="6" '
    b'fill="none" stroke="#888" stroke-width="6"/>'
    b'<circle cx="160" cy="170" r="15" fill="#888"/>'
    b'<polyline points="110,260 180,210 220,240 290,180 290,275 110,275" fill="#888"/>'
    b"</svg>"
)


def _resolve_thumbnail_path(item: "MediaItem", size: str = "md") -> tuple[Path, str] | None:
    """Return (path, mime) for the best on-disk thumbnail for an item, or None.

    ``size`` picks the preferred variant (with progressive fallback):

    - ``"sm"`` — 128px (``_thumb_sm.webp``); falls back to md → lg → original
    - ``"md"`` — 400px (``_thumb.webp``); falls back to lg → sm → original
    - ``"lg"`` — 1600px (``_thumb_lg.webp``); falls back to md → sm → original

    Callers should fall back to :func:`_placeholder_response` when None.
    """
    media_dir = _get_search_media_dir()

    # Video: dedicated frame grab wins (single size; same file regardless of `size`)
    if item.media_type == "video" and item.video_meta and item.video_meta.thumbnail_path:
        p = media_dir / item.video_meta.thumbnail_path
        if p.exists():
            return (p, "image/webp")

    # Try the requested size first, then progressively fall back.
    if item.file_path:
        original = media_dir / item.file_path

        def sibling(suffix: str) -> Path:
            return original.with_name(original.stem + suffix)

        # Preference order per requested size.
        if size == "sm":
            order = ["_thumb_sm.webp", "_thumb.webp", "_thumb_lg.webp"]
        elif size == "lg":
            order = ["_thumb_lg.webp", "_thumb.webp", "_thumb_sm.webp"]
        else:  # "md" (default) or anything else
            order = ["_thumb.webp", "_thumb_lg.webp", "_thumb_sm.webp"]

        for suffix in order:
            candidate = sibling(suffix)
            if candidate.exists():
                return (candidate, "image/webp")

    # Image: original as last-resort (race window before extraction runs)
    if item.media_type == "image" and item.file_path:
        p = media_dir / item.file_path
        if p.exists():
            return (p, item.mime_type or "application/octet-stream")

    return None


def _placeholder_response(media_type: str, cache_control: str) -> Response:
    """Return a tiny inline SVG placeholder for a media type."""
    if media_type == "audio":
        svg = _AUDIO_PLACEHOLDER_SVG
    elif media_type == "video":
        svg = _VIDEO_PLACEHOLDER_SVG
    else:
        svg = _IMAGE_PLACEHOLDER_SVG
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": cache_control},
    )


def _thumbnail_response(item: "MediaItem", cache_control: str, size: str = "md") -> Response:
    """Return a FileResponse for the best thumbnail, or a placeholder SVG."""
    resolved = _resolve_thumbnail_path(item, size=size)
    if resolved is None:
        return _placeholder_response(item.media_type, cache_control)
    path, mime = resolved
    return FileResponse(
        path,
        media_type=mime,
        headers={"Cache-Control": cache_control},
    )


def _media_type_from_mime(mime: str) -> str | None:
    """Derive media_type (image/audio/video) from a MIME type string."""
    if mime.startswith("image/"):
        return "image"
    elif mime.startswith("audio/"):
        return "audio"
    elif mime.startswith("video/"):
        return "video"
    return None


# DAW / NLE / photo project-file extensions → tool name. Used to detect `session`
# uploads when the MIME type is generic (zip / octet-stream).
SESSION_EXT_TO_TOOL = {
    ".logicx": "logic",
    ".als": "ableton",
    ".flp": "flstudio",
    ".ptx": "protools",
    ".ptxt": "protools",
    ".rpp": "reaper",
    ".bwproject": "bitwig",
    ".prproj": "premiere",
    ".drp": "davinci",
    ".fcpbundle": "finalcut",
    ".aep": "aftereffects",
    ".lrcat": "lightroom",
    ".cosession": "captureone",
}


def _detect_session_tool(filename: str | None) -> str | None:
    """Inspect a filename for a known DAW/NLE/photo project extension.

    Returns the canonical tool name, or None if not a recognised session bundle.
    macOS bundle uploads are typically zipped (the bundle directory + .zip) but
    the original filename usually retains the source extension.
    """
    if not filename:
        return None
    name_lower = filename.lower()
    for ext, tool in SESSION_EXT_TO_TOOL.items():
        if name_lower.endswith(ext) or name_lower.endswith(ext + ".zip"):
            return tool
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


_SLACK_CHANNEL_IDS: dict[str, str] = {
    "image-gen": os.environ.get("SLACK_CHANNEL_IMAGE_GEN", ""),
    "sample-sale": os.environ.get("SLACK_CHANNEL_SAMPLE_SALE", ""),
}

_SHARE_CHANNEL_MAP: dict[str, tuple[str, str]] = {
    "image": ("img-junkyard", os.environ.get("SLACK_CHANNEL_IMG_JUNKYARD", "")),
    "audio": ("sample-sale",  os.environ.get("SLACK_CHANNEL_SAMPLE_SALE", "")),
}


def _slack_message_link(channel_name: str | None, message_ts: str | None) -> str | None:
    """Build a Slack deep link from channel name and message timestamp."""
    if not channel_name or not message_ts:
        return None
    channel_id = _SLACK_CHANNEL_IDS.get(channel_name)
    if not channel_id:
        return None
    ts_clean = message_ts.replace(".", "")
    return f"https://au-supply.slack.com/archives/{channel_id}/p{ts_clean}"


def _source_response(s) -> dict:
    """Build a source dict for API responses."""
    return {
        "id": s.id,
        "source_type": s.source_type,
        "source_channel": s.source_channel,
        "slack_message_ts": s.slack_message_ts,
        "slack_message_text": s.slack_message_text,
        "slack_link": _slack_message_link(s.source_channel, s.slack_message_ts),
        "reaction_count": s.reaction_count,
        "uploader_name": s.uploader.name if s.uploader else None,
        "source_url": s.source_url,
        "source_metadata": s.source_metadata,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


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
        "output_index": item.output_index,
        "sources": [_source_response(s) for s in item.sources],
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
        try:
            ai_tags_list = json.loads(m.ai_tags) if m.ai_tags else []
        except (json.JSONDecodeError, TypeError):
            ai_tags_list = []
        try:
            ai_vibe_list = json.loads(m.ai_vibe) if m.ai_vibe else []
        except (json.JSONDecodeError, TypeError):
            ai_vibe_list = []
        try:
            ai_overrides_dict = json.loads(m.ai_overrides) if m.ai_overrides else {}
        except (json.JSONDecodeError, TypeError):
            ai_overrides_dict = {}
        data["image_meta"] = {
            "width": m.width,
            "height": m.height,
            "format": m.format,
            "dominant_colors": m.dominant_colors,
            "caption": m.caption,
            # AI vision enrichment (see docs/ai-image-descriptions.md).
            "ai_description": m.ai_description,
            "ai_description_model": m.ai_description_model,
            "ai_description_prompt_v": m.ai_description_prompt_v,
            "ai_description_generated_at": m.ai_description_generated_at.isoformat() if m.ai_description_generated_at else None,
            "ai_description_tokens_in": m.ai_description_tokens_in,
            "ai_description_tokens_out": m.ai_description_tokens_out,
            "ai_tags": ai_tags_list,
            "ai_color_temperature": m.ai_color_temperature,
            "ai_color_character": m.ai_color_character,
            "ai_vibe": ai_vibe_list,
            "ai_overrides": ai_overrides_dict,
            "is_screenshot": m.is_screenshot,
            "is_meme": m.is_meme,
            "is_photo": m.is_photo,
            "is_artwork": m.is_artwork,
            "is_ai_generated": m.is_ai_generated,
            "has_human": m.has_human,
            "has_face": m.has_face,
            "has_text_overlay": m.has_text_overlay,
            "is_nsfw": m.is_nsfw,
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


def _escape_filter_value(val: str) -> str:
    """Escape values interpolated into Meilisearch filter strings."""
    return val.replace("\\", "\\\\").replace('"', '\\"')


def _build_meili_filter(
    filters: SearchFilters | None,
    *,
    user_id: int | None = None,
) -> str | None:
    """Convert SearchFilters into a Meilisearch filter string.

    `user_id` is consulted only by the `my_votes` filter — pass the calling
    user's id when the request might filter by personal vote state.
    """
    if not filters:
        return None

    parts = []

    if filters.tags:
        tag_clauses = [f'tags = "{_escape_filter_value(t)}"' for t in filters.tags]
        parts.append("(" + " AND ".join(tag_clauses) + ")")

    if filters.source_channels:
        ch_clauses = [f'source_channels = "{_escape_filter_value(c)}"' for c in filters.source_channels]
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
        parts.append(f'sources.uploader = "{_escape_filter_value(filters.poster)}"')

    if filters.color:
        parts.append(f'dominant_colors = "{_escape_filter_value(filters.color)}"')

    if filters.color_group:
        cg_clauses = [f'primary_color_group = "{_escape_filter_value(g)}"' for g in filters.color_group]
        parts.append("(" + " OR ".join(cg_clauses) + ")")

    if filters.reaction_count and filters.reaction_count.get("min") is not None:
        parts.append(f"total_reaction_count >= {filters.reaction_count['min']}")

    if filters.tag_count:
        if filters.tag_count.get("min") is not None:
            parts.append(f"tag_count >= {filters.tag_count['min']}")
        if filters.tag_count.get("max") is not None:
            parts.append(f"tag_count <= {filters.tag_count['max']}")

    if filters.output_index:
        # `__emulsion__` is a UI-only marker meaning "also include the
        # private Emulsion index in the result set" — it's handled at the
        # multi_search layer (include_emulsion=True) and never becomes a
        # filter clause on its own.
        public_selections = [n for n in filters.output_index if n != "__emulsion__"]
        wants_inputs = "__inputs__" in public_selections
        wants_any_output = "__outputs__" in public_selections
        wants_emulsion_only = (
            "__emulsion__" in filters.output_index and not public_selections
        )
        if wants_emulsion_only:
            # Emulsion-only is enforced at the multi_search layer by
            # restricting media_types to ["emulsion"]; we deliberately add
            # *no* filter clause here so emulsion docs aren't filtered out.
            pass
        elif not (wants_inputs and wants_any_output):
            clauses = []
            if wants_inputs:
                clauses.append("output_index IS NULL")
            if wants_any_output:
                # Subsumes any specific named output, so we skip the named list.
                clauses.append("output_index IS NOT NULL")
            else:
                for name in public_selections:
                    if name and name not in ("__inputs__", "__outputs__"):
                        clauses.append(f'output_index = "{_escape_filter_value(name)}"')
            if clauses:
                parts.append(
                    "(" + " OR ".join(clauses) + ")" if len(clauses) > 1 else clauses[0]
                )

    if filters.has_transcript is not None:
        parts.append(f"has_transcript = {str(filters.has_transcript).lower()}")

    if filters.has_text is not None:
        parts.append(f"has_text = {str(filters.has_text).lower()}")

    # ---- AI vision enrichment filters ----
    if filters.has_ai_description is not None:
        parts.append(f"has_ai_description = {str(filters.has_ai_description).lower()}")
    if filters.ai_vibe:
        vibes = " OR ".join(f'ai_vibe = "{_escape_filter_value(v)}"' for v in filters.ai_vibe)
        parts.append(f"({vibes})")
    if filters.ai_color_temperature:
        temps = " OR ".join(
            f'ai_color_temperature = "{_escape_filter_value(v)}"' for v in filters.ai_color_temperature
        )
        parts.append(f"({temps})")
    if filters.ai_color_character:
        chars = " OR ".join(
            f'ai_color_character = "{_escape_filter_value(v)}"' for v in filters.ai_color_character
        )
        parts.append(f"({chars})")
    for _flag_name in (
        "is_screenshot",
        "is_meme",
        "is_photo",
        "is_artwork",
        "is_ai_generated",
        "has_human",
        "has_face",
        "has_text_overlay",
        "is_nsfw",
    ):
        _flag_val = getattr(filters, _flag_name)
        if _flag_val is not None:
            parts.append(f"{_flag_name} = {str(_flag_val).lower()}")

    if filters.job_app:
        parts.append(f'job_app = "{_escape_filter_value(filters.job_app)}"')

    # Votes (issue #318)
    if filters.vote_score:
        if filters.vote_score.get("min") is not None:
            parts.append(f"vote_score >= {int(filters.vote_score['min'])}")
        if filters.vote_score.get("max") is not None:
            parts.append(f"vote_score <= {int(filters.vote_score['max'])}")
    if filters.up_count:
        if filters.up_count.get("min") is not None:
            parts.append(f"up_count >= {int(filters.up_count['min'])}")
        if filters.up_count.get("max") is not None:
            parts.append(f"up_count <= {int(filters.up_count['max'])}")
    if filters.down_count:
        if filters.down_count.get("min") is not None:
            parts.append(f"down_count >= {int(filters.down_count['min'])}")
        if filters.down_count.get("max") is not None:
            parts.append(f"down_count <= {int(filters.down_count['max'])}")
    if filters.my_votes:
        if filters.my_votes == "none":
            parts.append("up_count = 0 AND down_count = 0")
        elif user_id is not None:
            uid = int(user_id)
            if filters.my_votes == "up":
                parts.append(f"upvoter_user_ids = {uid}")
            elif filters.my_votes == "down":
                parts.append(f"downvoter_user_ids = {uid}")
            elif filters.my_votes == "any":
                parts.append(f"(upvoter_user_ids = {uid} OR downvoter_user_ids = {uid})")
        # If `my_votes` is set but `user_id` is unknown (anonymous read scope
        # via a bearer key without a linked user), silently skip — better than
        # returning everything or nothing.

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


@router.post("/search", tags=["Media Search"], summary="Search media")
def search_media(
    body: SearchRequest,
    auth: tuple[User, str] = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Multi-index media search with filters, facets, and pagination.

    Searches across tags, descriptions, Slack message text, uploader names,
    speech transcripts, image captions, filenames, source titles, and color names.
    Typo-tolerant (powered by Meilisearch).

    **Search behavior:**
    - Empty query with no filters returns all items (sorted by newest first)
    - Filters are combined with AND logic (e.g. tags AND channel AND date range)
    - Within `tags`, items must match ALL specified tags (AND)
    - Within `source_channels`, items can match ANY channel (OR)
    - Color filters (`color` or `color_group`) automatically restrict results to images

    **Sort options:** `created_at:desc`, `created_at:asc`, `total_reaction_count:desc`,
    `file_size_bytes:asc`, `duration_seconds:desc`, `tag_count:desc`. Omit for relevance sorting.

    **Scope required:** `read`
    """
    from server.search_client import multi_search

    user, _scope = auth
    meili_filter = _build_meili_filter(body.filters, user_id=user.id if user else None)
    sort_list = [body.sort] if body.sort else None

    # Delegate index routing to shared helper (handles color → image,
    # emulsion, and samples-bored output_index routing)
    media_types, include_emulsion = _resolve_index_routing(body)

    results = multi_search(
        query=body.query,
        media_types=media_types,
        filters=meili_filter,
        sort=sort_list,
        page=body.page,
        per_page=body.per_page,
        include_emulsion=include_emulsion,
    )
    return results


# Cap "Select all matching" expansions. Keeps batch ops sane and protects
# against runaway selections in a query that suddenly matches everything.
SELECT_ALL_CAP = 10000


def _resolve_index_routing(body: SearchRequest) -> tuple[list[str] | None, bool]:
    """Mirror the index-routing decisions /api/search applies, so callers like
    ``/api/search/stats`` and ``/api/search/ids`` produce the same result set
    as the visible page.

    Returns ``(media_types, include_emulsion)`` — pass straight to
    ``multi_search``. Handles:
      - color filters narrow to the image index
      - ``__emulsion__`` alone in ``output_index`` forces emulsion-only
      - ``__emulsion__`` alongside other indexes turns on include_emulsion
      - ``samples-bored`` in output_index adds "sample" to searched indexes
    """
    from server.search_client import SAMPLES_INDEX

    media_types = body.media_types
    include_emulsion = body.include_emulsion
    if body.filters and (body.filters.color or body.filters.color_group):
        media_types = ["image"]
    if body.filters and body.filters.output_index:
        only_emulsion = (
            "__emulsion__" in body.filters.output_index
            and not any(n != "__emulsion__" for n in body.filters.output_index)
        )
        if only_emulsion:
            media_types = ["emulsion"]
            include_emulsion = True
        elif "__emulsion__" in body.filters.output_index:
            include_emulsion = True

        # If samples-bored is selected in the Index dropdown, add it to the
        # searched indexes alongside whatever types are already selected.
        if SAMPLES_INDEX in body.filters.output_index:
            mtypes = set(media_types or ["image", "audio", "video"])
            mtypes.add("sample")
            media_types = list(mtypes)

    return media_types, include_emulsion


@router.post("/search/ids", tags=["Media Search"], summary="Get all matching IDs")
def search_media_ids(
    body: SearchRequest,
    auth: tuple[User, str] = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Return just the IDs of every item matching the search, up to a cap.

    Backs the "Select all N matching" affordance on the search page — the
    grid/feed/list views only load a page (or a few pages) at a time, so
    expanding a selection beyond what's loaded needs a server round-trip.

    Same request body as ``/api/search``; ``page`` and ``per_page`` are
    ignored. Returns ``{ids, total, capped}``. If the matching set exceeds
    ``SELECT_ALL_CAP`` (10,000), ``ids`` is truncated and ``capped: true``.

    **Scope required:** ``read``
    """
    from server.search_client import multi_search

    user, _scope = auth
    meili_filter = _build_meili_filter(body.filters, user_id=user.id if user else None)
    sort_list = [body.sort] if body.sort else None

    # Must use the exact same routing as /api/search — otherwise a user on the
    # emulsion-only view sees "Select all 97 matching" but the IDs endpoint
    # ignores the emulsion routing, searches the public indexes instead, and
    # returns thousands of unrelated items. That bug almost got me killed.
    media_types, include_emulsion = _resolve_index_routing(body)

    # multi_search already fetches up to 10,000 per index internally and
    # slices to the requested page. Asking for page=1 with per_page=CAP
    # returns the full set in one call without a second code path.
    results = multi_search(
        query=body.query,
        media_types=media_types,
        filters=meili_filter,
        sort=sort_list,
        page=1,
        per_page=SELECT_ALL_CAP,
        include_emulsion=include_emulsion,
    )
    hits = results.get("hits", [])
    ids = [h["id"] for h in hits if "id" in h]
    total = results.get("total", len(ids))
    return {"ids": ids, "total": total, "capped": total > len(ids)}


@router.post("/search/stats", tags=["Media Search"], summary="Search aggregation stats")
def search_stats(
    body: SearchRequest,
    auth: tuple[User, str] = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Return aggregation stats for the current search query and filters.

    Returns: counts by media type, top tags, top uploaders, date histogram
    (monthly buckets), reaction distribution, and facet stats (min/max for
    numeric fields). Accepts the same request body as ``/api/search``.

    **Scope required:** ``read``
    """
    from server.search_client import multi_search
    import json as _json

    user, _scope = auth
    meili_filter = _build_meili_filter(body.filters, user_id=user.id if user else None)

    media_types, include_emulsion = _resolve_index_routing(body)

    # Search with limit=0: we only want facets and stats, not hits
    results = multi_search(
        query=body.query,
        media_types=media_types,
        filters=meili_filter,
        sort=None,
        page=1,
        per_page=0,
        include_emulsion=include_emulsion,
    )

    facets = results.get("facets", {})
    facet_stats = results.get("facet_stats", {})
    counts_by_type = results.get("counts_by_type", {})

    # Top tags (sorted by count, limited to top 30)
    raw_tags = facets.get("tags", {})
    top_tags = sorted(raw_tags.items(), key=lambda x: x[1], reverse=True)[:30]

    # Source channels with counts
    channels = facets.get("source_channels", {})

    # Color distribution
    color_groups = facets.get("primary_color_group", {})

    # Reaction distribution — build histogram buckets from DB
    reaction_buckets = _build_reaction_histogram(db, media_types)

    # Date histogram — monthly buckets from DB
    date_histogram = _build_date_histogram(db, media_types)

    # Top uploaders from DB
    top_uploaders = _get_top_uploaders(db, limit=15)

    return {
        "total": results.get("total", 0),
        "counts_by_type": counts_by_type,
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
        "channels": channels,
        "color_groups": color_groups,
        "reaction_buckets": reaction_buckets,
        "date_histogram": date_histogram,
        "top_uploaders": top_uploaders,
        "facet_stats": facet_stats,
    }


def _build_reaction_histogram(db: Session, media_types: list[str] | None) -> list[dict]:
    """Build reaction count histogram with fixed buckets."""
    query = db.query(MediaSource.reaction_count).join(MediaItem)
    if media_types:
        query = query.filter(MediaItem.media_type.in_(media_types))

    counts = [r[0] or 0 for r in query.all()]

    buckets = [
        ("0", 0, 0),
        ("1-2", 1, 2),
        ("3-5", 3, 5),
        ("6-10", 6, 10),
        ("11-25", 11, 25),
        ("26+", 26, None),
    ]

    result = []
    for label, lo, hi in buckets:
        if hi is not None:
            n = sum(1 for c in counts if lo <= c <= hi)
        else:
            n = sum(1 for c in counts if c >= lo)
        result.append({"label": label, "count": n})

    return result


def _build_date_histogram(db: Session, media_types: list[str] | None) -> list[dict]:
    """Build monthly date histogram from MediaItem.created_at."""
    from sqlalchemy import func

    query = db.query(
        func.strftime("%Y-%m", MediaItem.created_at).label("month"),
        func.count(MediaItem.id).label("count"),
    )
    if media_types:
        query = query.filter(MediaItem.media_type.in_(media_types))

    rows = (
        query
        .filter(MediaItem.created_at.isnot(None))
        .group_by("month")
        .order_by("month")
        .all()
    )

    return [{"month": r.month, "count": r.count} for r in rows]


def _get_top_uploaders(db: Session, limit: int = 15) -> list[dict]:
    """Get top uploaders by post count from source_metadata."""
    import json as _json
    from collections import Counter

    sources = (
        db.query(MediaSource.source_metadata)
        .filter(MediaSource.source_metadata.isnot(None))
        .all()
    )

    poster_counts: Counter = Counter()
    for (meta_str,) in sources:
        try:
            meta = _json.loads(meta_str)
            if isinstance(meta, dict) and meta.get("poster"):
                poster_counts[meta["poster"]] += 1
        except (ValueError, TypeError):
            pass

    return [
        {"uploader": name, "count": count}
        for name, count in poster_counts.most_common(limit)
    ]


@router.get("/search/facets", tags=["Media Search"], summary="Get filter facets")
def search_facets(
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Return available filter options for the search UI.

    Returns lists of all known source channels and uploaders, useful for populating
    dropdown filters in the search interface.

    **Scope required:** `read`
    """
    from server.models import MediaItem, MediaSource
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

    # Distinct job apps from source_metadata
    app_names_set = set()
    for (meta_str,) in sources_with_meta:
        try:
            meta = _json.loads(meta_str)
            if isinstance(meta, dict) and meta.get("app_name"):
                app_names_set.add(meta["app_name"])
        except (ValueError, TypeError):
            pass

    # Distinct output index names (excludes NULL — those are "inputs", surfaced
    # in the UI as a synthetic `__inputs__` pill).
    from server.search_client import SAMPLES_INDEX
    output_indexes = {
        r[0] for r in
        db.query(distinct(MediaItem.output_index))
        .filter(MediaItem.output_index.isnot(None))
        .all()
    }
    output_indexes.add(SAMPLES_INDEX)

    return {
        "channels": sorted(channels),
        "uploaders": sorted(uploaders),
        "job_apps": sorted(app_names_set),
        "output_indexes": sorted(output_indexes),
    }


@router.get("/tags", tags=["Tagging"], summary="List all tags")
def list_tags(
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """List all tags in the vocabulary, sorted by usage count (most popular first).

    Each tag includes its `usage_count` (number of media items using it) and
    `created_at` timestamp.

    **Scope required:** `read`
    """
    tags = db.query(TagVocabulary).order_by(TagVocabulary.usage_count.desc()).all()
    return [
        {"tag": t.tag, "usage_count": t.usage_count, "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in tags
    ]


# ---------------------------------------------------------------------------
# Public OG image route (no auth — used by Slack/Twitter/iMessage unfurlers)
# ---------------------------------------------------------------------------


@router.get(
    "/media/{media_id}/og-thumb",
    tags=["Media Items"],
    summary="Public thumbnail for Open Graph unfurling",
    include_in_schema=False,
)
def get_media_og_thumb(media_id: str, db: Session = Depends(get_db)):
    """Serve a thumbnail without auth so link-unfurling bots can fetch og:image."""
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    response = _thumbnail_response(item, "public, max-age=86400")
    db.close()
    return response


# ---------------------------------------------------------------------------
# Public outputs index (no auth — the entire indexed-outputs catalog)
# ---------------------------------------------------------------------------


@router.get(
    "/public/outputs",
    tags=["Public Outputs"],
    summary="Public: list indexed outputs",
)
def list_public_outputs(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    output_index: Optional[str] = Query(None, description="Filter to a specific output index name"),
    media_type: Optional[str] = Query(None, description="Filter by media_type: image, audio, or video"),
    db: Session = Depends(get_db),
):
    """List items from the public outputs index, no auth required.

    Returns every ``MediaItem`` whose ``output_index`` is set (i.e. items that
    have been indexed as outputs). Ordered newest-first.
    """
    q = (
        db.query(MediaItem)
        .options(joinedload(MediaItem.image_meta), joinedload(MediaItem.video_meta))
        .filter(MediaItem.output_index.isnot(None))
    )
    if output_index is not None:
        q = q.filter(MediaItem.output_index == output_index)
    if media_type is not None:
        q = q.filter(MediaItem.media_type == media_type)

    total = q.count()
    rows = q.order_by(MediaItem.created_at.desc()).limit(limit).offset(offset).all()

    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_public_output_dict(item) for item in rows],
    }


def _public_output_dict(item: MediaItem) -> dict:
    """Serialize a MediaItem for the public outputs list.

    Includes width/height/dominant_colors so clients can reserve layout
    space and paint a placeholder before image bytes arrive.
    """
    width: int | None = None
    height: int | None = None
    dominant_colors: list[str] | None = None

    if item.media_type == "image" and item.image_meta is not None:
        width = item.image_meta.width
        height = item.image_meta.height
        if item.image_meta.dominant_colors:
            try:
                parsed = json.loads(item.image_meta.dominant_colors)
                if isinstance(parsed, list):
                    dominant_colors = [str(c) for c in parsed]
            except (ValueError, TypeError):
                dominant_colors = None
    elif item.media_type == "video" and item.video_meta is not None:
        width = item.video_meta.width
        height = item.video_meta.height

    return {
        "id": item.id,
        "filename": item.filename,
        "media_type": item.media_type,
        "mime_type": item.mime_type,
        "file_size_bytes": item.file_size_bytes,
        "output_index": item.output_index,
        "description": item.description,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "width": width,
        "height": height,
        "dominant_colors": dominant_colors,
        "file_url": f"/api/public/outputs/{item.id}/file",
        "thumbnail_url": f"/api/public/outputs/{item.id}/thumbnail",
        "thumbnail_sm_url": f"/api/public/outputs/{item.id}/thumbnail?size=sm",
        "thumbnail_lg_url": f"/api/public/outputs/{item.id}/thumbnail?size=lg",
    }


@router.get(
    "/public/outputs/{media_id}/file",
    tags=["Public Outputs"],
    summary="Public: serve an indexed output's media file",
)
def get_public_output_file(media_id: str, db: Session = Depends(get_db)):
    """Serve the media file for an indexed output, no auth required.

    404s for items that are not in the outputs index (``output_index IS NULL``),
    so this route cannot be used to fetch unindexed inputs.
    """
    item = (
        db.query(MediaItem)
        .filter(MediaItem.id == media_id, MediaItem.output_index.isnot(None))
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Not found")

    file_path = _get_search_media_dir() / item.file_path
    mime = item.mime_type or "application/octet-stream"
    filename = item.filename
    db.close()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    safe_filename = filename.replace('"', "")
    return FileResponse(
        file_path,
        media_type=mime,
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename}"',
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )


@router.get(
    "/public/outputs/{media_id}/thumbnail",
    tags=["Public Outputs"],
    summary="Public: serve an indexed output's thumbnail",
)
def get_public_output_thumbnail(
    media_id: str,
    size: str = Query("md", pattern="^(sm|md|lg)$", description="`sm` 128px, `md` 400px (default), or `lg` 1600px."),
    db: Session = Depends(get_db),
):
    """Serve the thumbnail for an indexed output, no auth required.

    Pass ``?size=sm`` for the 128px variant (great for mobile grids and
    narrow ambient strips). Falls back to an inline SVG placeholder
    (never 404s) so waterfall clients don't have to handle missing tiles.
    404s only if the item itself is not in the outputs index.
    """
    item = (
        db.query(MediaItem)
        .filter(MediaItem.id == media_id, MediaItem.output_index.isnot(None))
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    response = _thumbnail_response(item, "public, max-age=86400, immutable", size=size)
    db.close()
    return response


# ---------------------------------------------------------------------------
# Media static routes (must be defined before /media/{media_id} to avoid capture)
# ---------------------------------------------------------------------------


@router.post("/media/upload", status_code=201, tags=["Media Items"], summary="Upload a media file")
async def upload_media(
    file: UploadFile = File(..., description="The media file to upload. Supported types: images, audio, video, session (DAW/NLE bundles)."),
    tags: str = Form("", description="Comma-separated tags to apply (e.g. `drums,percussive,loop`). Optional."),
    description: str = Form("", description="Freeform notes or description. Optional."),
    output_index: str = Form("", description="Index name to file this item under (e.g. `outputs`). Optional."),
    project_id: str = Form("", description="If set, the uploaded item is auto-attached to this Latent and indexed into Emulsion."),
    slot_id: str = Form("", description="If set with project_id, the item also attaches to this slot inside the Latent."),
    force_session: str = Form("", description="If 'true', treat the file as a `session` media type (DAW/NLE project file)."),
    tool: str = Form("", description="For session uploads, the tool that produced the file (e.g. 'logic', 'ableton')."),
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Upload a media file with optional tags and description.

    **Deduplication:** Files are identified by SHA-256 hash. If you upload a file that
    already exists in the system, no duplicate is created — instead, a new **source**
    record is added to the existing media item (recording that the file was uploaded
    again from a different context). The existing item is returned.

    **Media type** is auto-detected from the file's MIME type. Unsupported types are
    rejected with 400.

    **Storage path:** Files are stored as `{media_type}/{YYYY-MM}/{8char-sha256}_{filename}`.

    **Tags** are normalized (lowercased, trimmed) and added to the shared vocabulary
    for autocomplete.

    **Scope required:** `write`
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Detect MIME type
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"

    # Session detection — explicit override OR known DAW/NLE/photo extension
    detected_tool = _detect_session_tool(file.filename)
    is_session = (force_session.lower() == "true") or bool(detected_tool)
    if is_session:
        media_type = "session"
        session_tool = (tool or detected_tool or "other").lower()
    else:
        media_type = _media_type_from_mime(mime)
        session_tool = None

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
        if output_index and not existing.output_index:
            existing.output_index = output_index
        # If uploaded inside a Latent, attach the existing item (idempotent via UniqueConstraint)
        if project_id:
            from server.models import Project, ProjectItem, ProjectSlot
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
            resolved_slot_id = None
            if slot_id:
                slot = db.query(ProjectSlot).filter(
                    ProjectSlot.id == slot_id,
                    ProjectSlot.project_id == project_id,
                ).first()
                if not slot:
                    raise HTTPException(status_code=404, detail=f"Slot {slot_id} not found in project {project_id}")
                resolved_slot_id = slot.id
            dup_attach = db.query(ProjectItem).filter(
                ProjectItem.project_id == project_id,
                ProjectItem.slot_id == resolved_slot_id,
                ProjectItem.media_item_id == existing.id,
            ).first()
            if not dup_attach:
                db.add(ProjectItem(
                    project_id=project_id,
                    slot_id=resolved_slot_id,
                    media_item_id=existing.id,
                    added_by=(_auth[0].id if isinstance(_auth, tuple) else _auth.id),
                ))
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
        output_index=output_index or None,
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

    # Session metadata sidecar
    if is_session:
        from server.models import MediaSessionMeta
        session_meta = MediaSessionMeta(
            media_item_id=item_id,
            tool=session_tool or "other",
            original_bundle_name=safe_filename,
            bundle_size_bytes=len(content),
        )
        db.add(session_meta)

    # Auto-attach to a Latent (and optional slot) when project_id provided
    if project_id:
        from server.models import Project, ProjectItem, ProjectSlot
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
        # Verify slot belongs to project if provided
        resolved_slot_id = None
        if slot_id:
            slot = db.query(ProjectSlot).filter(
                ProjectSlot.id == slot_id,
                ProjectSlot.project_id == project_id,
            ).first()
            if not slot:
                raise HTTPException(status_code=404, detail=f"Slot {slot_id} not found in project {project_id}")
            resolved_slot_id = slot.id
        attachment = ProjectItem(
            project_id=project_id,
            slot_id=resolved_slot_id,
            media_item_id=item_id,
            added_by=(_auth[0].id if isinstance(_auth, tuple) else _auth.id),
        )
        db.add(attachment)

    db.commit()
    db.refresh(media_item)
    meili_sync(db, media_item)

    item = _get_media_item_or_404(db, item_id)
    return _media_item_response(item)


# ---------------------------------------------------------------------------
# Batch operations (must be before /media/{media_id} to avoid route capture)
# ---------------------------------------------------------------------------


@router.post("/media/batch/tags", tags=["Batch Operations"], summary="Batch add tags")
def batch_add_tags(
    body: BatchTagsRequest,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Add tags to multiple media items at once.

    Tags are normalized and deduplicated per item — if an item already has a tag,
    it's silently skipped. The response includes per-item results showing which
    tags were actually added.

    Items that don't exist are reported as errors but don't fail the entire request.

    **Scope required:** `write`
    """
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


@router.post("/media/batch/delete", tags=["Batch Operations"], summary="Batch delete media")
def batch_delete(
    body: BatchDeleteRequest,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Delete multiple media items and their files from disk. **Admin only.**

    Deletes both the database records and the actual files (including thumbnails).
    Items are also removed from the search index.

    Items that don't exist are silently skipped and reported in `not_found`.

    **Scope required:** `admin`
    """
    deleted = []
    not_found = []
    for media_id in body.media_ids:
        item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
        if not item:
            # Item is gone from the DB but might still be a stale doc in
            # Meili — that's exactly how a user ends up "deleting" rows that
            # come back on refresh. Sweep every index to be sure.
            meili_delete(media_id)
            not_found.append(media_id)
            continue

        media_type = item.media_type
        # Capture source_type *before* db.delete cascades the sources away.
        # Without this, emulsion items would route to the wrong Meili index.
        source_type = (
            item.sources[0].source_type
            if item.sources and getattr(item.sources[0], "source_type", None)
            else None
        )
        file_path = _get_search_media_dir() / item.file_path
        if file_path.exists():
            file_path.unlink()
        thumb = file_path.with_name(file_path.stem + "_thumb.webp")
        if thumb.exists():
            thumb.unlink()

        db.delete(item)
        meili_delete(media_id, media_type, source_type=source_type)
        deleted.append(media_id)

    db.commit()
    return {"ok": True, "deleted": deleted, "not_found": not_found}


@router.post("/media/batch/re-extract", tags=["Batch Operations"], summary="Batch re-extract metadata")
def batch_re_extract_endpoint(
    body: BatchReExtractRequest,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Re-run the metadata extraction pipeline on selected media items. **Admin only.**

    This queues the items for background extraction (image dimensions, dominant colors,
    audio transcripts via Whisper, video thumbnails, etc.). Use this after fixing an
    extraction issue or when extraction was skipped during initial ingest.

    Results are returned as `queued` (successfully scheduled) and `not_found`.

    **Scope required:** `admin`
    """
    try:
        from server.extraction import run_extraction_async
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


@router.post("/media/batch/export", tags=["Batch Operations"], summary="Export media as ZIP")
def batch_export(
    body: BatchExportRequest,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Download selected media files as a ZIP archive.

    Creates an in-memory ZIP file containing the original media files for all
    specified items. Files are named using their original filenames.

    Items that don't exist or whose files are missing on disk are silently skipped.

    **Scope required:** `read`
    """
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


@router.get("/media/{media_id}", tags=["Media Items"], summary="Get media item detail")
def get_media(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Return full metadata for a single media item, including:

    - Basic info: filename, SHA-256 hash, media type, file size, MIME type
    - Description and timestamps
    - All tags
    - All sources (where this file came from — Slack messages, manual uploads, etc.)
    - Type-specific metadata (image dimensions/colors, audio duration/transcript, video FPS/thumbnail)
    - Any extraction failures

    **Deduplication note:** A single media item can have multiple sources if the same
    file was posted in different Slack channels or uploaded multiple times. The `sources`
    array shows all contexts where this file appeared.

    **Scope required:** `read`
    """
    item = _get_media_item_or_404(db, media_id)
    return _media_item_response(item)


@router.get("/media/{media_id}/related", tags=["Media Items"], summary="Get related inputs/outputs")
def get_related(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Return items related to this media item via job processing.

    For outputs: returns the input items that were used to create it.
    For inputs: returns any output items that were produced from it.

    **Scope required:** `read`
    """
    import json as _json

    item = _get_media_item_or_404(db, media_id)
    inputs: list[dict] = []
    outputs: list[dict] = []

    # If this is an output: find its inputs via JobOutput → Job → input_items
    job_output = db.query(JobOutput).filter(
        JobOutput.media_item_id == media_id
    ).first()
    if job_output:
        job = db.query(Job).filter(Job.id == job_output.job_id).first()
        if job:
            input_ids = _json.loads(job.input_items)
            for inp in db.query(MediaItem).filter(MediaItem.id.in_(input_ids)).all():
                inputs.append(_related_item(db, inp))

    # If this is an input: find outputs from jobs that used it
    # Search all jobs whose input_items JSON contains this media_id
    jobs = db.query(Job).filter(
        Job.input_items.contains(media_id),
        Job.status == "completed",
    ).all()
    for job in jobs:
        input_ids = _json.loads(job.input_items)
        if media_id not in input_ids:
            continue
        for jo in job.outputs:
            if jo.media_item_id and jo.indexed:
                out_item = db.query(MediaItem).filter(MediaItem.id == jo.media_item_id).first()
                if out_item:
                    outputs.append(_related_item(db, out_item))

    return {"inputs": inputs, "outputs": outputs}


def _related_item(db: Session, item: MediaItem) -> dict:
    """Build a compact response for a related item."""
    slack_link = None
    for s in item.sources:
        link = _slack_message_link(s.source_channel, s.slack_message_ts)
        if link:
            slack_link = link
            break
    return {
        "id": item.id,
        "filename": item.filename,
        "media_type": item.media_type,
        "output_index": item.output_index,
        "slack_link": slack_link,
    }


@router.get("/media/{media_id}/file", tags=["Media Items"], summary="Download media file")
def get_media_file(
    media_id: str,
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Download or stream the original media file.

    Returns the file with the correct MIME type and the original filename in the
    `Content-Disposition` header.

    **Scope required:** `read`
    """
    item = _get_media_item_or_404(db, media_id)
    file_path = _get_search_media_dir() / item.file_path
    mime = item.mime_type or "application/octet-stream"
    filename = item.filename
    # Close the DB session before streaming so the pool isn't pinned for the
    # whole download. Without this, pages that embed many file URLs can
    # exhaust QueuePool.
    db.close()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    # Serve inline so clicking a media URL opens full-size in the browser
    # instead of forcing a download. Explicit Download UI uses <a download>.
    # `private` because the endpoint is authed; the browser can cache per-user
    # but no shared cache should store the response.
    return FileResponse(
        file_path,
        media_type=mime,
        headers={
            "Content-Disposition": content_disposition("inline", filename),
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.get("/media/{media_id}/thumbnail", tags=["Media Items"], summary="Get media thumbnail")
def get_media_thumbnail(
    media_id: str,
    size: str = Query("md", pattern="^(sm|md|lg)$", description="`sm` 128px, `md` 400px (default), or `lg` 1600px."),
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Serve the thumbnail for a media item.

    **Thumbnail sources by type:**
    - **Videos**: auto-generated frame grab, then `_thumb.webp` sibling, else video placeholder SVG
    - **Images**: for `size=sm` tries `_thumb_sm.webp` first, then `_thumb.webp`, then original, else placeholder
    - **Audio**: inline audio-waveform placeholder SVG (always 200, never 404)

    **Scope required:** `read`
    """
    item = _get_media_item_or_404(db, media_id)
    response = _thumbnail_response(item, "private, max-age=86400", size=size)
    db.close()
    return response


@router.post("/media/{media_id}/slack-share", tags=["Media Items"], summary="Share media to Slack")
async def slack_share_media(
    media_id: str,
    auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Upload the media file to its target Slack channel.

    Images go to #img-junkyard, audio to #sample-sale. Video is unsupported.
    Returns 200 with already_shared=true if already sent to that channel.

    **Scope required:** `write`
    """
    import httpx

    item = _get_media_item_or_404(db, media_id)

    if item.media_type not in _SHARE_CHANNEL_MAP:
        raise HTTPException(status_code=422, detail="Video sharing not supported")

    channel_name, channel_id = _SHARE_CHANNEL_MAP[item.media_type]

    if not channel_id:
        raise HTTPException(status_code=503, detail=f"Slack channel not configured for {channel_name}")

    existing = db.query(MediaSlackShare).filter_by(
        media_item_id=media_id, channel_name=channel_name
    ).first()
    if existing:
        return {"already_shared": True, "channel": channel_name, "sent_at": existing.sent_at}

    file_path = _get_search_media_dir() / item.file_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="Slack not configured")

    file_bytes = file_path.read_bytes()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        r1 = await client.get(
            "https://slack.com/api/files.getUploadURLExternal",
            headers=headers,
            params={"filename": item.filename, "length": len(file_bytes)},
        )
        d1 = r1.json()
        if not d1.get("ok"):
            raise HTTPException(status_code=502, detail=f"Slack upload URL error: {d1.get('error')}")

        upload_url = d1["upload_url"]
        file_id = d1["file_id"]

        r2 = await client.post(
            upload_url,
            content=file_bytes,
            headers={"Content-Type": item.mime_type or "application/octet-stream"},
        )
        if r2.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail="Slack file upload failed")

        app_name = None
        for src in item.sources:
            if src.source_type == "job_output" and src.source_metadata:
                meta = json.loads(src.source_metadata or "{}")
                app_name = meta.get("app_display_name") or meta.get("app_name")
                break

        site_url = os.environ.get("SITE_URL", "https://a-u.supply")
        detail_url = f"{site_url}/admin/search/detail?id={media_id}"
        caption_parts = [f"`{item.filename}`"]
        if app_name:
            caption_parts.append(f"from *{app_name}*")
        caption_parts.append(f"<{detail_url}|view in stacks>")
        initial_comment = " · ".join(caption_parts)

        r3 = await client.post(
            "https://slack.com/api/files.completeUploadExternal",
            headers=headers,
            json={
                "files": [{"id": file_id}],
                "channel_id": channel_id,
                "initial_comment": initial_comment,
            },
        )
        d3 = r3.json()
        if not d3.get("ok"):
            raise HTTPException(status_code=502, detail=f"Slack complete error: {d3.get('error')}")

    share = MediaSlackShare(
        media_item_id=media_id,
        channel_name=channel_name,
        slack_file_id=file_id,
        sent_by_user_id=getattr(auth, "id", None),
    )
    db.add(share)
    db.commit()

    return {"already_shared": False, "channel": channel_name, "sent_at": share.sent_at}


@router.put("/media/{media_id}", tags=["Media Items"], summary="Update media description")
def update_media(
    media_id: str,
    body: MediaUpdateRequest,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Update a media item's description/notes.

    Currently only the `description` field can be updated. Other metadata (tags, sources)
    have their own dedicated endpoints.

    The search index is automatically updated after the change.

    **Scope required:** `write`
    """
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    if body.description is not None:
        item.description = body.description

    db.commit()
    db.refresh(item)
    meili_sync(db, item)

    return _media_item_response(_get_media_item_or_404(db, media_id))


@router.delete("/media/{media_id}", tags=["Media Items"], summary="Delete a media item")
def delete_media(
    media_id: str,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Permanently delete a media item, its file, and its thumbnail from disk. **Admin only.**

    Also removes the item from the search index. All associated data (sources, tags,
    extraction failures, type-specific metadata) is cascade-deleted.

    **This action is irreversible.**

    **Scope required:** `admin`
    """
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

    # Explicit project_items cleanup. The FK is ON DELETE CASCADE, but for
    # databases provisioned before PRAGMA foreign_keys=ON shipped, the
    # cascade never ran — surface those rows as gone here so the UI never
    # shows "(unknown)" stragglers.
    from server.models import ProjectItem  # local import: avoid cycle on module load
    db.query(ProjectItem).filter(ProjectItem.media_item_id == media_id).delete(synchronize_session=False)

    db.delete(item)
    db.commit()
    meili_delete(media_id, media_type)

    return {"ok": True}


@router.patch(
    "/media/{media_id}/ai-fields",
    tags=["Media Items"],
    summary="Override AI-derived fields with human corrections",
)
def override_ai_fields(
    media_id: str,
    body: AiFieldsOverrideRequest,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Set human corrections for AI-derived fields (bool flags, vibe, color mood).

    Each field present in the request body is written to the image_meta row
    AND its name is added to the ``ai_overrides`` JSON dict, so that future
    AI regenerations preserve this value rather than clobbering it.

    Bool fields are tri-state: omit to leave unchanged; ``true``/``false`` to set.

    String/list fields are clamped to the controlled vocabulary (server/ai_vocab.py);
    out-of-vocab values are rejected.

    **Scope required:** `write`
    """
    from server.ai_vocab import (
        COLOR_CHARACTER_VOCAB,
        COLOR_TEMPERATURE_VOCAB,
        VIBE_VOCAB,
    )

    item = _get_media_item_or_404(db, media_id)
    if item.media_type != "image" or item.image_meta is None:
        raise HTTPException(status_code=400, detail="Item is not an image with metadata")

    meta = item.image_meta
    try:
        overrides = json.loads(meta.ai_overrides) if meta.ai_overrides else {}
    except (json.JSONDecodeError, TypeError):
        overrides = {}

    patch = body.model_dump(exclude_unset=True)

    # Validate controlled-vocab fields.
    if "ai_color_temperature" in patch and patch["ai_color_temperature"] is not None:
        if patch["ai_color_temperature"] not in COLOR_TEMPERATURE_VOCAB:
            raise HTTPException(status_code=400, detail="invalid ai_color_temperature")
    if "ai_color_character" in patch and patch["ai_color_character"] is not None:
        if patch["ai_color_character"] not in COLOR_CHARACTER_VOCAB:
            raise HTTPException(status_code=400, detail="invalid ai_color_character")
    if "ai_vibe" in patch and patch["ai_vibe"] is not None:
        bad = [v for v in patch["ai_vibe"] if v not in VIBE_VOCAB]
        if bad:
            raise HTTPException(status_code=400, detail=f"invalid ai_vibe entries: {bad}")
        if len(patch["ai_vibe"]) > 3:
            raise HTTPException(status_code=400, detail="ai_vibe accepts at most 3 entries")

    # Apply the patch and mark each touched field as overridden.
    for key, value in patch.items():
        if key == "ai_vibe":
            setattr(meta, "ai_vibe", json.dumps(value))
        else:
            setattr(meta, key, value)
        overrides[key] = True
    meta.ai_overrides = json.dumps(overrides)
    db.commit()
    db.refresh(item)

    # Sync the updated row to the search index immediately.
    try:
        from server.search_client import sync_media_item

        sync_media_item(db, item)
    except Exception:
        logger.exception("Meilisearch sync after ai-fields override failed for %s", media_id)

    return _media_item_response(item)


@router.post(
    "/media/{media_id}/regenerate-ai-description",
    tags=["Media Items"],
    summary="Re-run the vision model on a single image",
)
def regenerate_ai_description(
    media_id: str,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Synchronously re-run the AI vision pipeline for one image.

    Honours ai_overrides — fields the user has manually set are preserved.
    Returns the updated media item including the fresh ai_* fields.

    **Scope required:** `write`
    """
    from server.ai_description import generate_ai_description, DeepSeekError
    from server.extraction import (
        SEARCH_MEDIA_DIR,
        _apply_ai_description,
        _upsert_meta,
        _sync_to_search,
    )
    from server.models import MediaImageMeta

    item = _get_media_item_or_404(db, media_id)
    if item.media_type != "image":
        raise HTTPException(status_code=400, detail="Not an image")
    full_path = os.path.join(SEARCH_MEDIA_DIR, item.file_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Image file missing on disk")

    meta = item.image_meta
    ocr_caption = meta.caption if meta else None
    try:
        overrides = json.loads(meta.ai_overrides) if meta and meta.ai_overrides else {}
    except (json.JSONDecodeError, TypeError):
        overrides = {}

    try:
        ai = generate_ai_description(full_path, ocr_caption=ocr_caption)
    except DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=f"vision API error: {exc}") from exc

    kwargs: dict = {}
    _apply_ai_description(kwargs, ai, ai_overrides=overrides)
    _upsert_meta(db, MediaImageMeta, item.id, kwargs)
    db.refresh(item)
    _sync_to_search(db, item)
    return _media_item_response(item)


# ---------------------------------------------------------------------------
# Tagging endpoints
# ---------------------------------------------------------------------------


@router.post("/media/{media_id}/tags", tags=["Tagging"], summary="Add tags to a media item")
def add_tags(
    media_id: str,
    body: TagsRequest,
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Add one or more tags to a media item.

    Tags are automatically normalized (lowercased, whitespace trimmed). Duplicate tags
    (tags the item already has) are silently ignored — they won't cause an error.

    New tags are added to the shared vocabulary for autocomplete. The search index is
    updated automatically.

    **Scope required:** `write`
    """
    user = auth[0]
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    existing_tags = {t.tag for t in db.query(MediaTag).filter(MediaTag.media_item_id == media_id).all()}

    added = []
    for raw_tag in body.tags:
        tag = _normalize_tag(raw_tag)
        if not tag or tag in existing_tags:
            continue
        media_tag = MediaTag(media_item_id=media_id, tag=tag, tagged_by=user.id)
        db.add(media_tag)
        _update_vocabulary(db, tag, 1)
        existing_tags.add(tag)
        added.append(tag)

    db.commit()
    meili_sync(db, _get_media_item_or_404(db, media_id))

    if added:
        queue_batched(
            "tag.added",
            user,
            count=1,
            media_id=media_id,
            tags=added,
        )

    return {"ok": True, "added": added}


@router.delete("/media/{media_id}/tags/{tag}", tags=["Tagging"], summary="Remove a tag from a media item")
def remove_tag(
    media_id: str,
    tag: str,
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Remove a specific tag from a media item.

    The tag is normalized before lookup, so `Drums`, `drums`, and `DRUMS` all match
    the same tag. Returns 404 if the tag doesn't exist on this item.

    The vocabulary usage count is decremented. The search index is updated automatically.

    **Scope required:** `write`
    """
    user = auth[0]
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

    queue_batched(
        "tag.removed",
        user,
        count=1,
        media_id=media_id,
        tag=normalized,
    )

    return {"ok": True}


@router.get("/tags/suggest", tags=["Tagging"], summary="Autocomplete tag suggestions")
def suggest_tags(
    q: str = Query("", min_length=1, description="Search prefix or substring to match against known tags."),
    _auth=Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Get tag autocomplete suggestions from the vocabulary.

    Searches by **substring** (not just prefix) — searching for `rum` will match
    both `drums` and `rum`. Results are sorted by usage count (most popular first),
    limited to 20 suggestions.

    **Scope required:** `read`
    """
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


@router.post("/ingest/slack", tags=["Slack Ingestion"], summary="Trigger a full Slack scrape")
def ingest_slack(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Trigger a full Slack scrape of all configured channels. **Admin only.**

    Pulls all messages posted since the last scrape timestamp. On the **first run**,
    this fetches the entire channel history (which can be large — consider running a
    dry-run first).

    For each message, the scraper:
    1. Downloads file attachments via the Slack API
    2. Downloads linked media (YouTube, TikTok, SoundCloud) via yt-dlp
    3. Deduplicates by SHA-256 hash and Slack file ID
    4. Creates media items and triggers the extraction pipeline
    5. Indexes everything in the search engine

    **Scope required:** `admin`
    """
    try:
        from server.slack_scraper import trigger_scrape
    except ImportError:
        logger.warning("slack_scraper module not available")
        return {"ok": False, "detail": "Slack scraper module not yet available"}

    try:
        result = trigger_scrape()
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("Slack scrape failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingest/slack/status", tags=["Slack Ingestion"], summary="Get scrape status")
def ingest_slack_status(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Check the status of the last Slack scrape run. **Admin only.**

    Returns information about when the last scrape ran, how many items were processed,
    and whether it succeeded or failed.

    **Scope required:** `admin`
    """
    try:
        from server.slack_scraper import get_scrape_status
    except ImportError:
        return {"ok": False, "detail": "Slack scraper module not yet available", "last_run": None}

    try:
        scrape_status = get_scrape_status()
        return {"ok": True, "status": scrape_status}
    except Exception as e:
        logger.exception("Failed to get scrape status")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/slack/reactions", tags=["Slack Ingestion"], summary="Refresh reaction counts")
def ingest_slack_reactions(
    days_back: int = Query(7, ge=1, le=365, description="How many days back to refresh reactions for (1-365). Default: 7 days."),
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Refresh Slack reaction counts for recently posted media. **Admin only.**

    Calls the Slack API to get current reaction counts for media ingested within the
    specified number of days. Updates `slack_reactions` and `reaction_count` fields.

    Reactions on older posts are considered settled and are not re-fetched (to avoid
    excessive API calls).

    **Scope required:** `admin`
    """
    try:
        from server.slack_scraper import trigger_reaction_refresh
    except ImportError:
        return {"ok": False, "detail": "Slack scraper module not yet available"}

    try:
        result = trigger_reaction_refresh(days_back=days_back)
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("Reaction refresh failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/slack/sync", tags=["Slack Ingestion"], summary="Sync now (incremental scrape + reactions)")
def ingest_slack_sync(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Run an incremental scrape and reaction refresh in one call. **Admin only.**

    This is what the **"Sync Now"** button in the admin UI does. It:
    1. Refreshes reaction counts for the last 7 days (fast, runs first)
    2. Runs an incremental scrape to pull new messages since the last scrape

    Use this for a quick catch-up. For a full historical backfill, use
    `POST /api/ingest/slack` instead.

    **Scope required:** `admin`
    """
    try:
        from server.slack_scraper import trigger_incremental_scrape, trigger_reaction_refresh
    except ImportError:
        return {"ok": False, "detail": "Slack scraper module not yet available"}

    try:
        # Kick off incremental scrape in background
        scrape_result = trigger_incremental_scrape()
        # Run reactions in background too — it makes hundreds of API calls
        import threading
        threading.Thread(
            target=trigger_reaction_refresh, kwargs={"days_back": 7}, daemon=True,
        ).start()
        return {
            "ok": True,
            "scrape": scrape_result,
            "reactions": {"status": "started"},
        }
    except Exception as e:
        logger.exception("Sync failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/slack/dry-run", tags=["Slack Ingestion"], summary="Dry-run scrape (calculate sizes)")
def ingest_slack_dry_run(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Run a dry-run Slack scrape that calculates download sizes without fetching anything. **Admin only.**

    Use this before a large backfill to estimate how much data will be downloaded
    and how many items will be ingested. No files are downloaded, no database records
    are created.

    **Scope required:** `admin`
    """
    try:
        from server.slack_scraper import trigger_dry_run
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


@router.get("/keys", tags=["API Keys"], summary="List your API keys")
def list_api_keys(
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """List all active (non-revoked) API keys belonging to the current user.

    Each key shows its prefix (first 11 characters, for identification), label, scope,
    creation date, and last usage timestamp.

    **You can only see your own keys.** The full key value is never shown again after
    creation — only the prefix is stored for display.

    **Scope required:** `write`
    """
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


@router.post("/keys", status_code=201, tags=["API Keys"], summary="Create a new API key")
def create_api_key(
    body: ApiKeyCreateRequest,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Generate a new API key for programmatic access. **The raw key is returned only once.**

    The key is prefixed with `au_` and followed by a cryptographically random string.
    Send it as a Bearer token in the `Authorization` header:

    ```
    Authorization: Bearer au_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    ```

    **IMPORTANT:** Copy the `key` field from the response immediately. It is stored as
    a bcrypt hash and cannot be recovered. If you lose it, revoke the key and create a
    new one.

    **Scope hierarchy:** A key's scope determines what it can access:
    - `read`: Search, view, stream, download
    - `write`: Everything in read + upload, tag, edit, manage keys
    - `admin`: Everything in write + delete, manage users, trigger scrapes

    **Scope required:** `write`
    """
    if body.scope not in ("read", "write", "admin"):
        raise HTTPException(status_code=400, detail="Scope must be read, write, or admin")

    user, _ = _auth

    from server.auth import generate_api_key, hash_api_key

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


@router.delete("/keys/{key_id}", tags=["API Keys"], summary="Revoke an API key")
def revoke_api_key(
    key_id: str,
    _auth=Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Revoke an API key, making it immediately unusable.

    Revocation is instant — the next request using this key will be rejected with 401.
    The key record is not deleted; it's marked with a `revoked_at` timestamp.

    **You can only revoke your own keys.** Returns 404 if the key doesn't exist, doesn't
    belong to you, or is already revoked.

    **Scope required:** `write`
    """
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


@router.get("/extraction-failures", tags=["Extraction Failures"], summary="List extraction failures")
def list_extraction_failures(
    resolved: bool = Query(False, description="If true, show resolved failures. Default: false (show unresolved)."),
    extraction_type: str | None = Query(None, description="Filter by extraction type: `whisper`, `ffprobe`, `dominant_colors`, `thumbnail`, `yt-dlp`."),
    page: int = Query(1, ge=1, description="Page number (1-indexed)."),
    per_page: int = Query(50, ge=1, le=200, description="Results per page (max 200)."),
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """List extraction failures with optional filtering and pagination. **Admin only.**

    The extraction pipeline runs asynchronously after media ingest. When any step fails
    (e.g. Whisper transcription, ffprobe, dominant color extraction, thumbnail generation,
    yt-dlp download), the failure is logged here with error details.

    Use this to identify and fix ingestion issues. Failed items can be retried via
    `POST /api/extraction-failures/{id}/retry` or marked as resolved via
    `POST /api/extraction-failures/{id}/resolve`.

    **Scope required:** `admin`
    """
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


@router.post("/extraction-failures/{failure_id}/retry", tags=["Extraction Failures"], summary="Retry a failed extraction")
def retry_extraction_failure(
    failure_id: str,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Retry a specific extraction failure. **Admin only.**

    Re-runs the failed extraction step (e.g. re-attempts Whisper transcription or
    dominant color extraction). The `attempts` counter is incremented.

    If the retry succeeds, the failure is automatically marked as resolved.
    If it fails again, the error message is updated with the new error.

    **Scope required:** `admin`
    """
    failure = db.query(ExtractionFailure).filter(ExtractionFailure.id == failure_id).first()
    if not failure:
        raise HTTPException(status_code=404, detail="Extraction failure not found")

    try:
        from server.extraction import retry_extraction
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


# ---------------------------------------------------------------------------
# Votes — per-user up/down on a media item ("Acclaim" / "Disavow"). Issue #318.
# ---------------------------------------------------------------------------


class VoteRequest(BaseModel):
    """Body for `POST /api/search/{media_id}/vote`.

    `value` is one of:
    - `+1` — record an upvote (acclaim); switches an existing downvote.
    - `-1` — record a downvote (disavow); switches an existing upvote.
    - `0`  — retract whatever the user's existing vote was; no-op if absent.

    Idempotent on `(user, media_id)`.
    """

    value: int = Field(..., description="-1, 0, or +1.")

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: int) -> int:
        if v not in (-1, 0, 1):
            raise ValueError("value must be -1, 0, or 1")
        return v


def _vote_aggregates(db: Session, media_item_id: str, user_id: int) -> dict:
    """Compute fresh aggregates + the current user's vote in one place."""
    rows = (
        db.query(MediaVote.user_id, MediaVote.value)
        .filter(MediaVote.media_item_id == media_item_id)
        .all()
    )
    up = sum(1 for _uid, v in rows if v > 0)
    down = sum(1 for _uid, v in rows if v < 0)
    my_vote = next((v for uid, v in rows if uid == user_id), 0)
    return {
        "up_count": up,
        "down_count": down,
        "vote_score": up - down,
        "my_vote": my_vote,
    }


@router.post(
    "/search/{media_id}/vote",
    tags=["Media Votes"],
    summary="Acclaim / disavow a media item",
)
def vote_on_media(
    media_id: str,
    body: VoteRequest,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Record (or retract) the calling user's vote on a media item.

    Returns the post-state aggregates plus `my_vote` so the client can
    confirm its optimistic update without a follow-up GET.

    **Admin only.** Voting is admin-side curation (`/admin/search`).
    """
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    existing = (
        db.query(MediaVote)
        .filter(MediaVote.media_item_id == media_id, MediaVote.user_id == user.id)
        .first()
    )
    prior_value = existing.value if existing else 0

    if body.value == 0:
        if existing is not None:
            db.delete(existing)
    else:
        if existing is None:
            db.add(MediaVote(media_item_id=media_id, user_id=user.id, value=body.value))
        elif existing.value != body.value:
            existing.value = body.value
        # Re-voting the same direction is a no-op — keep the existing row to
        # preserve its created_at.

    db.commit()

    # Schedule the debounced Meili partial-update. Other admins see the new
    # counts within ~500ms.
    vote_sync.schedule(media_id)

    # Activity feed + Slack rollup. Only emit on actual state change so
    # idempotent re-clicks don't pollute the channel.
    if body.value != prior_value:
        queue_batched(
            "media.voted",
            user,
            media_item_id=media_id,
            filename=item.filename,
            prior_value=prior_value,
            new_value=body.value,
        )

    return _vote_aggregates(db, media_id, user.id)


@router.get(
    "/search/{media_id}/voters",
    tags=["Media Votes"],
    summary="List voters on a media item",
)
def get_voters(
    media_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return the full voter list for one item.

    The denormalized voter arrays in the Meilisearch doc are normally
    enough for hover tooltips. This endpoint exists as a fallback for
    callers that don't have the Meili payload handy (e.g. a freshly-voted
    item before partial-update has propagated).
    """
    if not db.query(MediaItem.id).filter(MediaItem.id == media_id).first():
        raise HTTPException(status_code=404, detail="Media item not found")
    rows = (
        db.query(MediaVote.value, User.id, User.name)
        .join(User, User.id == MediaVote.user_id)
        .filter(MediaVote.media_item_id == media_id)
        .all()
    )
    upvoters: list[dict] = []
    downvoters: list[dict] = []
    for value, uid, name in rows:
        (upvoters if value > 0 else downvoters).append({"user_id": uid, "name": name})
    return {"upvoters": upvoters, "downvoters": downvoters}


@router.get(
    "/search/votes/mine",
    tags=["Media Votes"],
    summary="List my own votes",
)
def list_my_votes(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return the calling user's votes as two id lists.

    The client uses this to seed optimistic state after a hard reload so
    the up/down arrows render highlighted before the first search response
    comes back.
    """
    rows = db.query(MediaVote.media_item_id, MediaVote.value).filter(MediaVote.user_id == user.id).all()
    up = [mid for mid, v in rows if v > 0]
    down = [mid for mid, v in rows if v < 0]
    return {"up": up, "down": down}


@router.post("/extraction-failures/{failure_id}/resolve", tags=["Extraction Failures"], summary="Mark failure as resolved")
def resolve_extraction_failure(
    failure_id: str,
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Mark an extraction failure as resolved without retrying. **Admin only.**

    Use this to dismiss failures that don't need to be fixed (e.g. a corrupt file
    that will never extract successfully, or a yt-dlp failure for a deleted video).

    The media item itself is not affected — it remains in the system with whatever
    metadata was successfully extracted.

    **Scope required:** `admin`
    """
    failure = db.query(ExtractionFailure).filter(ExtractionFailure.id == failure_id).first()
    if not failure:
        raise HTTPException(status_code=404, detail="Extraction failure not found")

    failure.resolved = True
    failure.last_attempt_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}
