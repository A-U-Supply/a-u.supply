"""Meilisearch client wrapper for media search engine."""

import json
import logging
import os
from datetime import datetime, timezone

import meilisearch
from sqlalchemy.orm import Session

from models import (
    MediaAudioMeta,
    MediaImageMeta,
    MediaItem,
    MediaSource,
    MediaTag,
    MediaVideoMeta,
    User,
)

logger = logging.getLogger(__name__)

MEILISEARCH_URL = os.environ.get("MEILISEARCH_URL", "http://127.0.0.1:7700")
MEILISEARCH_MASTER_KEY = os.environ.get("MEILISEARCH_MASTER_KEY", "")

_client = None

INDEX_NAMES = {"image": "images", "audio": "audio", "video": "video"}

# Shared index configuration
SEARCHABLE_ATTRIBUTES = [
    "tags",
    "description",
    "sources.message_text",
    "sources.uploader",
    "transcript",
    "audio_transcript",
    "caption",
    "filename",
    "sources.source_title",
    "color_names",
    "job_app",
    "job_recipe",
    "job_model",
]

FILTERABLE_ATTRIBUTES = [
    "media_type",
    "tags",
    "tag_count",
    "source_channels",
    "total_reaction_count",
    "created_at",
    "width",
    "height",
    "duration_seconds",
    "format",
    "mime_type",
    "dominant_colors",
    "color_groups",
    "primary_color_group",
    "sources.uploader",
    "output_index",
    "job_app",
    "job_recipe",
    "job_model",
]

SORTABLE_ATTRIBUTES = [
    "created_at",
    "updated_at",
    "total_reaction_count",
    "file_size_bytes",
    "duration_seconds",
    "tag_count",
]


def get_client() -> meilisearch.Client:
    """Return a singleton Meilisearch client."""
    global _client
    if _client is None:
        _client = meilisearch.Client(MEILISEARCH_URL, MEILISEARCH_MASTER_KEY or None, timeout=5)
    return _client


def configure_indexes() -> None:
    """Create or update Meilisearch indexes with the correct settings."""
    client = get_client()

    settings = {
        "searchableAttributes": SEARCHABLE_ATTRIBUTES,
        "filterableAttributes": FILTERABLE_ATTRIBUTES,
        "sortableAttributes": SORTABLE_ATTRIBUTES,
        "faceting": {
            "maxValuesPerFacet": 200,
        },
        "pagination": {
            "maxTotalHits": 10000,
        },
        "typoTolerance": {
            "disableOnNumbers": True,
            "minWordSizeForTypos": {
                "oneTypo": 5,
                "twoTypos": 9,
            },
        },
        "displayedAttributes": ["*"],
    }

    for index_name in INDEX_NAMES.values():
        try:
            client.create_index(index_name, {"primaryKey": "id"})
        except meilisearch.errors.MeilisearchApiError:
            # Index already exists
            pass
        client.index(index_name).update_settings(settings)
        logger.info("Configured Meilisearch index: %s", index_name)


def _hex_to_color_name(hex_color: str) -> str:
    """Convert a hex color like '#1a2b3c' to human-readable color names.

    Returns space-separated descriptors like 'dark blue' or 'bright red'.
    """
    try:
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    except (ValueError, IndexError):
        return ""

    # Lightness
    lightness = (r + g + b) / (3 * 255)
    if lightness < 0.15:
        return "black dark"
    if lightness > 0.85:
        return "white bright light"

    # Saturation (rough)
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    saturation = (max_c - min_c) / max_c if max_c > 0 else 0

    if saturation < 0.15:
        if lightness < 0.4:
            return "dark gray grey"
        return "light gray grey"

    # Hue-based naming
    names = []
    if lightness < 0.35:
        names.append("dark")
    elif lightness > 0.65:
        names.append("light bright")

    if r > g and r > b:
        if g > b * 1.5:
            names.append("orange warm")
        elif b > g * 0.8:
            names.append("pink magenta")
        else:
            names.append("red warm")
    elif g > r and g > b:
        if b > r * 1.2:
            names.append("teal cyan")
        elif r > b * 1.2:
            names.append("yellow green warm")
        else:
            names.append("green")
    elif b > r and b > g:
        if r > g * 1.5:
            names.append("purple violet")
        elif g > r * 0.8:
            names.append("cyan teal blue")
        else:
            names.append("blue cool")
    elif abs(r - g) < 30 and r > b:
        names.append("yellow gold warm")
    elif abs(g - b) < 30 and g > r:
        names.append("cyan teal cool")
    elif abs(r - b) < 30 and r > g:
        names.append("magenta pink purple")

    return " ".join(names)


def _hex_to_color_groups(hex_color: str) -> list[str]:
    """Map a hex color to color group names for filtering.

    Groups: red, orange, yellow, green, teal, blue, purple, pink,
            brown, beige, gray, black, white.
    """
    try:
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    except (ValueError, IndexError):
        return []

    lightness = (r + g + b) / (3 * 255)
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    sat = (max_c - min_c) / max_c if max_c > 0 else 0

    # Achromatic: very dark, very light, or desaturated
    if lightness < 0.10:
        return ["black"]
    if lightness > 0.90:
        return ["white"]
    if sat < 0.10:
        return ["gray"]

    # Compute hue (0-360)
    if max_c == min_c:
        hue = 0
    elif max_c == r:
        hue = 60 * ((g - b) / (max_c - min_c) % 6)
    elif max_c == g:
        hue = 60 * ((b - r) / (max_c - min_c) + 2)
    else:
        hue = 60 * ((r - g) / (max_c - min_c) + 4)
    if hue < 0:
        hue += 360

    # Low saturation warm tones → brown/beige (not chromatic)
    if sat < 0.30 and 10 < hue < 50:
        if lightness < 0.45:
            return ["brown"]
        return ["beige"]

    # Low saturation cool tones → gray
    if sat < 0.20:
        return ["gray"]

    # Chromatic colors — require meaningful saturation
    if hue < 10 or hue >= 350:
        return ["red"]
    if hue < 35:
        return ["orange"]
    if hue < 55:
        return ["yellow"]
    if hue < 160:
        return ["green"]
    if hue < 195:
        return ["teal"]
    if hue < 260:
        return ["blue"]
    if hue < 300:
        return ["purple"]
    return ["pink"]


def _build_document(db: Session, media_item: MediaItem) -> dict:
    """Build a flat Meilisearch document from a MediaItem and its relations."""
    # Collect tags
    tags = [t.tag for t in media_item.tags]

    # Collect sources
    sources = []
    total_reaction_count = 0
    source_channels = set()
    for src in media_item.sources:
        source_doc = {
            "source_type": src.source_type,
            "source_channel": src.source_channel,
            "message_text": src.slack_message_text,
            "reaction_count": src.reaction_count or 0,
        }
        # Get uploader name — from source_metadata.poster or from User table
        if src.source_metadata:
            try:
                sm = json.loads(src.source_metadata)
                if isinstance(sm, dict) and sm.get("poster"):
                    source_doc["uploader"] = sm["poster"]
            except (json.JSONDecodeError, TypeError):
                pass
        if "uploader" not in source_doc and src.uploader_id:
            uploader = db.query(User).filter(User.id == src.uploader_id).first()
            if uploader:
                source_doc["uploader"] = uploader.name
        # Parse reactions JSON
        if src.slack_reactions:
            try:
                source_doc["reactions"] = json.loads(src.slack_reactions)
            except (json.JSONDecodeError, TypeError):
                source_doc["reactions"] = {}
        # Source URL and title from metadata
        if src.source_url:
            source_doc["source_url"] = src.source_url
        if src.source_metadata:
            try:
                meta = json.loads(src.source_metadata)
                if isinstance(meta, dict) and "title" in meta:
                    source_doc["source_title"] = meta["title"]
            except (json.JSONDecodeError, TypeError):
                pass
        total_reaction_count += src.reaction_count or 0
        if src.source_channel:
            source_channels.add(src.source_channel)
        sources.append(source_doc)

    # Extract job output metadata from sources
    job_app = None
    job_recipe = None
    job_model = None
    job_runtime_seconds = None
    job_input_count = None
    for src in media_item.sources:
        if src.source_type == "job_output" and src.source_metadata:
            try:
                meta = json.loads(src.source_metadata)
                if isinstance(meta, dict):
                    job_app = meta.get("app_name")
                    job_recipe = meta.get("recipe")
                    job_model = meta.get("model")
                    job_runtime_seconds = meta.get("runtime_seconds")
                    job_input_count = meta.get("input_count")
            except (json.JSONDecodeError, TypeError):
                pass
            break

    # Base document
    doc = {
        "id": media_item.id,
        "filename": media_item.filename,
        "media_type": media_item.media_type,
        "file_size_bytes": media_item.file_size_bytes,
        "mime_type": media_item.mime_type,
        "description": media_item.description,
        "output_index": media_item.output_index,
        "job_app": job_app,
        "job_recipe": job_recipe,
        "job_model": job_model,
        "job_runtime_seconds": job_runtime_seconds,
        "job_input_count": job_input_count,
        "tags": tags,
        "tag_count": len(tags),
        "sources": sources,
        "total_reaction_count": total_reaction_count,
        "source_channels": list(source_channels),
        "created_at": int(media_item.created_at.replace(tzinfo=timezone.utc).timestamp())
        if media_item.created_at
        else 0,
        "updated_at": int(media_item.updated_at.replace(tzinfo=timezone.utc).timestamp())
        if media_item.updated_at
        else 0,
    }

    # Type-specific metadata
    if media_item.media_type == "image" and media_item.image_meta:
        meta = media_item.image_meta
        doc["width"] = meta.width
        doc["height"] = meta.height
        doc["format"] = meta.format
        if meta.dominant_colors:
            try:
                colors = json.loads(meta.dominant_colors)
                doc["dominant_colors"] = colors
                doc["color_names"] = " ".join(_hex_to_color_name(c) for c in colors)
                doc["color_groups"] = list(set(g for c in colors for g in _hex_to_color_groups(c)))
                # Visual color = first chromatic (non-neutral) group from ranked colors.
                # Most images have a neutral background as the biggest cluster;
                # the first saturated color is what the image "looks like" to a human.
                neutrals = {"gray", "black", "white", "brown", "beige"}
                visual_group = ""
                for c in colors:
                    for g in _hex_to_color_groups(c):
                        if g not in neutrals:
                            visual_group = g
                            break
                    if visual_group:
                        break
                # Fallback: if all colors are neutral, use the #1 dominant
                if not visual_group:
                    primary_groups = _hex_to_color_groups(colors[0]) if colors else []
                    visual_group = primary_groups[0] if primary_groups else ""
                doc["primary_color_group"] = visual_group
            except (json.JSONDecodeError, TypeError):
                doc["dominant_colors"] = []
                doc["color_names"] = ""
        doc["caption"] = meta.caption

    elif media_item.media_type == "audio" and media_item.audio_meta:
        meta = media_item.audio_meta
        doc["duration_seconds"] = meta.duration_seconds
        doc["sample_rate"] = meta.sample_rate
        doc["channels"] = meta.channels
        doc["bit_depth"] = meta.bit_depth
        doc["transcript"] = meta.transcript
        if meta.acoustic_tags:
            try:
                doc["acoustic_tags"] = json.loads(meta.acoustic_tags)
            except (json.JSONDecodeError, TypeError):
                doc["acoustic_tags"] = []

    elif media_item.media_type == "video" and media_item.video_meta:
        meta = media_item.video_meta
        doc["duration_seconds"] = meta.duration_seconds
        doc["width"] = meta.width
        doc["height"] = meta.height
        doc["fps"] = meta.fps
        doc["audio_transcript"] = meta.audio_transcript

    return doc


def sync_media_item(db: Session, media_item: MediaItem) -> None:
    """Build a document from a MediaItem and upsert it to the correct Meilisearch index."""
    index_name = INDEX_NAMES.get(media_item.media_type)
    if not index_name:
        logger.warning("Unknown media_type '%s' for item %s", media_item.media_type, media_item.id)
        return

    doc = _build_document(db, media_item)
    client = get_client()
    try:
        client.index(index_name).add_documents([doc])
    except Exception:
        logger.exception("Failed to sync media item %s to Meilisearch", media_item.id)


def delete_media_item(media_item_id: str, media_type: str) -> None:
    """Remove a media item from the Meilisearch index."""
    index_name = INDEX_NAMES.get(media_type)
    if not index_name:
        logger.warning("Unknown media_type '%s' for deletion of %s", media_type, media_item_id)
        return

    client = get_client()
    try:
        client.index(index_name).delete_document(media_item_id)
    except Exception:
        logger.exception("Failed to delete media item %s from Meilisearch", media_item_id)


ALL_FACETS = [
    "tags",
    "source_channels",
    "format",
    "mime_type",
    "color_groups",
    "primary_color_group",
    # Numeric facets — requesting them gives us facetStats (min/max)
    "total_reaction_count",
    "created_at",
    "tag_count",
]


def multi_search(
    query: str,
    media_types: list[str] | None = None,
    filters: str | None = None,
    sort: list[str] | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """Execute a multi-index search across specified media type indexes.

    Returns combined results with hits, total counts, facet distributions,
    facet stats (min/max for numeric fields), and per-type hit counts.
    """
    client = get_client()

    if not media_types:
        media_types = ["image", "audio", "video"]

    queries = []
    for mt in media_types:
        index_name = INDEX_NAMES.get(mt)
        if not index_name:
            continue
        q = {
            "indexUid": index_name,
            "q": query,
            "limit": 10000,
            "offset": 0,
            "facets": ALL_FACETS,
        }
        if filters:
            q["filter"] = filters
        if sort:
            q["sort"] = sort
        queries.append(q)

    if not queries:
        return {"hits": [], "total": 0, "facets": {}, "facet_stats": {}, "counts_by_type": {}}

    try:
        response = client.multi_search(queries)
    except Exception:
        logger.exception("Meilisearch multi-search failed")
        return {"hits": [], "total": 0, "facets": {}, "facet_stats": {}, "counts_by_type": {}}

    # Combine results from all indexes
    all_hits = []
    total = 0
    combined_facets: dict = {}
    combined_stats: dict = {}
    counts_by_type: dict = {}

    # Reverse lookup: index name → media type
    index_to_type = {v: k for k, v in INDEX_NAMES.items()}

    for result in response.get("results", []):
        index_uid = result.get("indexUid", "")
        media_type = index_to_type.get(index_uid, index_uid)
        hits_count = result.get("estimatedTotalHits", 0)
        counts_by_type[media_type] = hits_count

        all_hits.extend(result.get("hits", []))
        total += hits_count

        # Merge facet distributions
        for facet_name, facet_values in result.get("facetDistribution", {}).items():
            if facet_name not in combined_facets:
                combined_facets[facet_name] = {}
            for k, v in facet_values.items():
                combined_facets[facet_name][k] = combined_facets[facet_name].get(k, 0) + v

        # Merge facet stats (min/max across indexes)
        for stat_name, stat_values in result.get("facetStats", {}).items():
            if stat_name not in combined_stats:
                combined_stats[stat_name] = dict(stat_values)
            else:
                existing = combined_stats[stat_name]
                if "min" in stat_values:
                    existing["min"] = min(existing.get("min", float("inf")), stat_values["min"])
                if "max" in stat_values:
                    existing["max"] = max(existing.get("max", float("-inf")), stat_values["max"])

    # Interleave results from different indexes by the active sort field
    if sort and all_hits:
        sort_field = sort[0].split(":")[0]
        sort_dir = sort[0].split(":")[-1] if ":" in sort[0] else "asc"
        reverse = sort_dir == "desc"
        all_hits.sort(key=lambda h: h.get(sort_field, 0) or 0, reverse=reverse)
    elif not query and all_hits:
        # Default: newest first when browsing without a query
        all_hits.sort(key=lambda h: h.get("created_at", 0) or 0, reverse=True)

    # Slice to the requested page
    start = (page - 1) * per_page
    all_hits = all_hits[start:start + per_page]

    return {
        "hits": all_hits,
        "total": total,
        "facets": combined_facets,
        "facet_stats": combined_stats,
        "counts_by_type": counts_by_type,
        "page": page,
        "per_page": per_page,
    }
