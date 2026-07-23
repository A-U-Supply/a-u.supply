"""Meilisearch client wrapper for media search engine."""

import json
import logging
import os
from datetime import datetime, timezone

import meilisearch
from sqlalchemy.orm import Session

from server.models import (
    MediaAudioMeta,
    MediaImageMeta,
    MediaItem,
    MediaPukeBoxMeta,
    MediaSessionMeta,
    MediaSource,
    MediaTag,
    MediaVideoMeta,
    MediaVote,
    ProjectItem,
    User,
)

logger = logging.getLogger(__name__)

MEILISEARCH_URL = os.environ.get("MEILISEARCH_URL", "http://127.0.0.1:7700")
MEILISEARCH_MASTER_KEY = os.environ.get("MEILISEARCH_MASTER_KEY", "")

_client = None

INDEX_NAMES = {"image": "images", "audio": "audio", "video": "video"}

# Emulsion is the destination for user-uploaded items (any media_type) and for
# the `session` media type (DAW/NLE project files), routed at index time.
EMULSION_INDEX = "emulsion"

# Samples-bored is the destination for the Music 2000 / one-shot sample library
# (instrument samples, drum hits, sound effects). Managed by scripts/index_samples.py.
SAMPLES_INDEX = "samples-bored"

# Puke Box is the destination for the Daily MIDI bot output (AI-generated MIDI
# entries scraped from the #midieval Slack channel). Each entry is one audio
# MediaItem (OGG preview) with structured musical metadata in MediaPukeBoxMeta.
PUKE_BOX_INDEX = "puke-box"

STACKS_COMMUNITY_NAME = "stacks"

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
    # AI vision enrichment (see docs/ai-image-descriptions.md).
    # Lower weight than caption (OCR is verbatim, AI prose is interpretive).
    "ai_description",
    "ai_tags",
    # Sample library index fields (samples-bored)
    "instruments",
    "dir",
    "source_name",
    "source_creator",
    "voice",
    "instrument",
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
    "sources.source_url",
    "sources.source_type",
    "output_index",
    "job_app",
    "job_recipe",
    "job_model",
    "has_transcript",
    "has_text",
    "project_ids",
    "tool",
    "extraction_status",
    "parent_media_item_id",
    # Votes (issue #318) — counts for range filters, parallel ID arrays for "my votes"
    "up_count",
    "down_count",
    "vote_score",
    "upvoter_user_ids",
    "downvoter_user_ids",
    # AI vision enrichment fields (see docs/ai-image-descriptions.md).
    "ai_tags",
    "ai_color_temperature",
    "ai_color_character",
    "ai_vibe",
    "has_ai_description",
    "is_screenshot",
    "is_meme",
    "is_photo",
    "is_artwork",
    "is_ai_generated",
    "has_human",
    "has_face",
    "has_text_overlay",
    "is_nsfw",
    # Sample library index fields (samples-bored)
    "instruments",
    "category",
    "dir",
    "sample_rate",
    "channels",
    "bit_depth",
    "source_name",
    "source_creator",
    "source_year",
    "royalty_free",
    "voice",
    "instrument",
    # Puke Box index fields (Daily MIDI entries)
    "scale",
    "root",
    "tempo",
    "chords",
    "entry_id",
]

SORTABLE_ATTRIBUTES = [
    "created_at",
    "updated_at",
    "total_reaction_count",
    "file_size_bytes",
    "duration_seconds",
    "tag_count",
    "tempo",
    # Votes (issue #318)
    "vote_score",
    "up_count",
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

    all_indexes = list(INDEX_NAMES.values()) + [EMULSION_INDEX, SAMPLES_INDEX, PUKE_BOX_INDEX]
    for index_name in all_indexes:
        try:
            client.create_index(index_name, {"primaryKey": "id"})
        except meilisearch.errors.MeilisearchApiError:
            # Index already exists
            pass
        client.index(index_name).update_settings(settings)
        logger.info("Configured Meilisearch index: %s", index_name)


def _index_for_media_item(media_item: MediaItem) -> str | None:
    """Decide which Meilisearch index a media item belongs in.

    - `session` media type always goes to Emulsion.
    - Items with a `session_extract` source (files harvested from a bundle) go to Emulsion.
    - Items whose only sources are `manual_upload` go to Emulsion (user uploads).
    - Items with a `sample_library` source type go to the Samples index.
    - Items with `output_index == "puke-box"` go to the Puke Box index.
    - Everything else routes by media_type via INDEX_NAMES.
    """
    if media_item.media_type == "session":
        return EMULSION_INDEX
    if getattr(media_item, "output_index", None) == PUKE_BOX_INDEX:
        return PUKE_BOX_INDEX
    sources = list(media_item.sources or [])
    for src in sources:
        if getattr(src, "source_type", None) == "session_extract":
            return EMULSION_INDEX
    if sources and all(getattr(s, "source_type", None) == "manual_upload" for s in sources):
        return EMULSION_INDEX
    for src in sources:
        if getattr(src, "source_type", None) == "sample_library":
            return SAMPLES_INDEX
    return INDEX_NAMES.get(media_item.media_type)


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


def _sample_output_index(media_item: MediaItem) -> str | None:
    """Return ``"samples-bored"`` if any source is sample_library, else None."""
    for src in (media_item.sources or []):
        if getattr(src, "source_type", None) == "sample_library":
            return SAMPLES_INDEX
    return None


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
        "output_index": _sample_output_index(media_item) or media_item.output_index,
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
        doc["has_text"] = bool(meta.caption)

        # AI vision enrichment (see docs/ai-image-descriptions.md).
        doc["ai_description"] = meta.ai_description
        doc["has_ai_description"] = bool(meta.ai_description)
        try:
            doc["ai_tags"] = json.loads(meta.ai_tags) if meta.ai_tags else []
        except (json.JSONDecodeError, TypeError):
            doc["ai_tags"] = []
        try:
            doc["ai_vibe"] = json.loads(meta.ai_vibe) if meta.ai_vibe else []
        except (json.JSONDecodeError, TypeError):
            doc["ai_vibe"] = []
        doc["ai_color_temperature"] = meta.ai_color_temperature
        doc["ai_color_character"] = meta.ai_color_character
        for _flag in (
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
            doc[_flag] = getattr(meta, _flag)

    elif media_item.media_type == "audio" and media_item.audio_meta:
        meta = media_item.audio_meta
        doc["duration_seconds"] = meta.duration_seconds
        doc["sample_rate"] = meta.sample_rate
        doc["channels"] = meta.channels
        doc["bit_depth"] = meta.bit_depth
        doc["transcript"] = meta.transcript
        doc["has_transcript"] = bool(meta.transcript)
        if meta.acoustic_tags:
            try:
                _at = json.loads(meta.acoustic_tags)
                if isinstance(_at, dict):
                    doc["acoustic_tags"] = _at.get("ai_tags", [])
                    doc["voice"] = _at.get("voice")
                    doc["instrument"] = _at.get("instrument")
                else:
                    doc["acoustic_tags"] = _at
            except (json.JSONDecodeError, TypeError):
                doc["acoustic_tags"] = []

    elif media_item.media_type == "video" and media_item.video_meta:
        meta = media_item.video_meta
        doc["duration_seconds"] = meta.duration_seconds
        doc["width"] = meta.width
        doc["height"] = meta.height
        doc["fps"] = meta.fps
        doc["audio_transcript"] = meta.audio_transcript
        doc["has_transcript"] = bool(meta.audio_transcript)

    elif media_item.media_type == "session" and media_item.session_meta:
        meta = media_item.session_meta
        doc["tool"] = meta.tool
        doc["tool_version"] = meta.tool_version
        doc["original_bundle_name"] = meta.original_bundle_name
        doc["bundle_size_bytes"] = meta.bundle_size_bytes
        doc["notes"] = meta.notes
        doc["extraction_status"] = meta.extraction_status
        doc["extracted_count"] = meta.extracted_count

    if media_item.parent_media_item_id:
        doc["parent_media_item_id"] = media_item.parent_media_item_id

    # Puke Box musical metadata (Daily MIDI entries)
    if media_item.puke_box_meta:
        pbm = media_item.puke_box_meta
        doc["entry_id"] = pbm.entry_id
        doc["scale"] = pbm.scale
        doc["root"] = pbm.root
        doc["tempo"] = pbm.tempo
        if pbm.chords:
            try:
                doc["chords"] = json.loads(pbm.chords)
            except (json.JSONDecodeError, TypeError):
                doc["chords"] = []
        else:
            doc["chords"] = []
        if pbm.description and not doc.get("description"):
            doc["description"] = pbm.description

    # Latents membership — filterable so a single doc can be found via any Latent it belongs to.
    project_ids = [pi.project_id for pi in db.query(ProjectItem).filter(ProjectItem.media_item_id == media_item.id).all()]
    doc["project_ids"] = list(set(project_ids))

    # Votes (issue #318) — denormalize aggregates AND voter identities so
    # hover tooltips render without an extra round-trip.
    doc.update(_vote_fields_for(db, media_item.id))

    return doc


def _vote_fields_for(db: Session, media_item_id: str) -> dict:
    """Return the vote-related fields for a single media item.

    Used both by `_build_document` (full-doc rebuild) and by
    `update_vote_fields` (partial Meili update on every vote click).
    """
    rows = (
        db.query(MediaVote.value, User.id, User.name)
        .join(User, User.id == MediaVote.user_id)
        .filter(MediaVote.media_item_id == media_item_id)
        .all()
    )
    upvoters: list[dict] = []
    downvoters: list[dict] = []
    upvoter_ids: list[int] = []
    downvoter_ids: list[int] = []
    for value, uid, name in rows:
        bucket = upvoters if value > 0 else downvoters
        id_bucket = upvoter_ids if value > 0 else downvoter_ids
        bucket.append({"user_id": uid, "name": name})
        id_bucket.append(uid)
    up = len(upvoters)
    down = len(downvoters)
    return {
        "up_count": up,
        "down_count": down,
        "vote_score": up - down,
        "upvoter_user_ids": upvoter_ids,
        "downvoter_user_ids": downvoter_ids,
        "upvoters": upvoters,
        "downvoters": downvoters,
    }


def update_vote_fields(db: Session, media_item: MediaItem) -> None:
    """Push just the vote-related fields into Meilisearch as a partial update.

    Avoids the cost of `sync_media_item` (which rebuilds color analysis,
    source enrichment, and every other heavy field) for the common case of
    a single click toggling a vote.
    """
    index_name = _index_for_media_item(media_item)
    if not index_name:
        return
    payload = {"id": media_item.id, **_vote_fields_for(db, media_item.id)}
    try:
        get_client().index(index_name).update_documents([payload])
    except Exception:
        logger.exception("Failed to push vote fields for %s to Meilisearch", media_item.id)


def sync_media_item(db: Session, media_item: MediaItem) -> None:
    """Build a document from a MediaItem and upsert it to the correct Meilisearch index."""
    index_name = _index_for_media_item(media_item)
    if not index_name:
        logger.warning("Unknown media_type '%s' for item %s", media_item.media_type, media_item.id)
        return

    doc = _build_document(db, media_item)
    client = get_client()
    try:
        client.index(index_name).add_documents([doc])
    except Exception:
        logger.exception("Failed to sync media item %s to Meilisearch", media_item.id)


def delete_media_item(
    media_item_id: str,
    media_type: str | None = None,
    *,
    source_type: str | None = None,
) -> None:
    """Remove a media item from the Meilisearch index.

    A media item's index depends on both `media_type` and `source_type`
    (manual uploads + sessions live in Emulsion regardless of type). Callers
    don't always have `source_type` handy — `batch_delete` in particular only
    has `media_type`. Rather than make every call site reconstruct routing
    state, we issue a delete against every plausible index for the given
    media_type and let Meilisearch no-op on the misses.

    When ``media_type`` is also unknown (e.g. cleaning up a stale Meili doc
    whose DB row was already deleted), we sweep every index.

    This was the cause of "Delete didn't work" on emulsion items: the call
    was hitting the wrong index, the DB row got removed but Meili kept
    returning the doc, and the next search showed the item still there.
    """
    candidate_indexes: list[str] = []
    if media_type is None:
        # Cleaning up a stale Meili entry where we don't know the original
        # routing. Sweep all indexes; misses are silent.
        candidate_indexes = list(INDEX_NAMES.values()) + [EMULSION_INDEX, SAMPLES_INDEX, PUKE_BOX_INDEX]
    elif source_type == "manual_upload" or media_type == "session":
        candidate_indexes.append(EMULSION_INDEX)
    else:
        type_index = INDEX_NAMES.get(media_type)
        if type_index:
            candidate_indexes.append(type_index)
        # An item we *think* is e.g. an image might actually be an emulsion
        # upload — without `source_type` we can't tell from media_type alone.
        # Belt-and-suspenders: also try the Emulsion index.
        candidate_indexes.append(EMULSION_INDEX)
        # Audio items may live in the puke-box index.
        if media_type == "audio":
            candidate_indexes.append(PUKE_BOX_INDEX)

    if not candidate_indexes:
        logger.warning("Unknown media_type '%s' for deletion of %s", media_type, media_item_id)
        return

    client = get_client()
    task_uids: list[int] = []
    for index_name in candidate_indexes:
        try:
            task = client.index(index_name).delete_document(media_item_id)
            uid = getattr(task, "task_uid", None) or getattr(task, "uid", None)
            if uid is not None:
                task_uids.append(int(uid))
        except Exception:
            logger.exception(
                "Failed to enqueue delete of media item %s from Meilisearch index %s",
                media_item_id,
                index_name,
            )

    # Wait for Meili to actually process the deletes before returning.
    # Otherwise the client's "refresh and verify it's gone" round-trip races
    # the indexer, and just-deleted items briefly reappear in search results
    # — the exact "delete doesn't work" symptom we keep chasing.
    for uid in task_uids:
        try:
            client.wait_for_task(uid, timeout_in_ms=5000, interval_in_ms=50)
        except Exception:
            logger.warning(
                "Meilisearch delete task %s did not finish in time", uid
            )


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
    include_emulsion: bool = False,
) -> dict:
    """Execute a multi-index search across specified media type indexes.

    Returns combined results with hits, total counts, facet distributions,
    facet stats (min/max for numeric fields), and per-type hit counts.

    `include_emulsion=True` adds the private Emulsion index (admin only).
    """
    client = get_client()

    # `None` → default to the three public indices. An explicit empty list is
    # respected so callers can opt into Emulsion-only searches (e.g. the
    # "Pull from index" modal restricted to Emulsion).
    if media_types is None:
        media_types = ["image", "audio", "video"]

    queries = []
    for mt in media_types:
        if mt == "emulsion" or mt == "session":
            if not include_emulsion:
                continue
            index_name = EMULSION_INDEX
        elif mt == "sample":
            index_name = SAMPLES_INDEX
        elif mt == "puke-box":
            index_name = PUKE_BOX_INDEX
        else:
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
        if sort and sort != ["random"]:
            q["sort"] = sort
        queries.append(q)
    if include_emulsion and EMULSION_INDEX not in [q.get("indexUid") for q in queries]:
        q = {
            "indexUid": EMULSION_INDEX,
            "q": query,
            "limit": 10000,
            "offset": 0,
            "facets": ALL_FACETS,
        }
        if filters:
            q["filter"] = filters
        if sort and sort != ["random"]:
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
    if sort and all_hits and sort[0] == "random":
        import random as _random
        _random.shuffle(all_hits)
    elif sort and all_hits:
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
