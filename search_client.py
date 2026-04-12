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
    "transcript",
    "audio_transcript",
    "caption",
    "filename",
    "sources.source_title",
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
        _client = meilisearch.Client(MEILISEARCH_URL, MEILISEARCH_MASTER_KEY or None)
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
        # Get uploader name
        if src.uploader_id:
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

    # Base document
    doc = {
        "id": media_item.id,
        "filename": media_item.filename,
        "media_type": media_item.media_type,
        "file_size_bytes": media_item.file_size_bytes,
        "mime_type": media_item.mime_type,
        "description": media_item.description,
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
                doc["dominant_colors"] = json.loads(meta.dominant_colors)
            except (json.JSONDecodeError, TypeError):
                doc["dominant_colors"] = []
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


def multi_search(
    query: str,
    media_types: list[str] | None = None,
    filters: str | None = None,
    sort: list[str] | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """Execute a multi-index search across specified media type indexes.

    Returns combined results with hits, total counts, and facet distribution.
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
            "limit": per_page,
            "offset": (page - 1) * per_page,
            "facets": ["tags", "source_channels", "format", "mime_type"],
        }
        if filters:
            q["filter"] = filters
        if sort:
            q["sort"] = sort
        queries.append(q)

    if not queries:
        return {"hits": [], "total": 0, "facets": {}}

    try:
        response = client.multi_search(queries)
    except Exception:
        logger.exception("Meilisearch multi-search failed")
        return {"hits": [], "total": 0, "facets": {}}

    # Combine results from all indexes
    all_hits = []
    total = 0
    combined_facets: dict = {}

    for result in response.get("results", []):
        all_hits.extend(result.get("hits", []))
        total += result.get("estimatedTotalHits", 0)
        # Merge facet distributions
        for facet_name, facet_values in result.get("facetDistribution", {}).items():
            if facet_name not in combined_facets:
                combined_facets[facet_name] = {}
            for k, v in facet_values.items():
                combined_facets[facet_name][k] = combined_facets[facet_name].get(k, 0) + v

    return {
        "hits": all_hits,
        "total": total,
        "facets": combined_facets,
        "page": page,
        "per_page": per_page,
    }
