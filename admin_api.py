"""Admin dashboard API — stats, action queue, and activity feed.

These endpoints back the Auspices page (admin dashboard). They're read-only
aggregates built on the same data other admin surfaces already use, so the
numbers here should always agree with what's shown on Nomenclator, The Fallen,
The Midden, and The Canon.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from auth import get_db, require_scope
from jobs_api import MIDDEN_TTL_HOURS
from models import (
    ExtractionFailure,
    Job,
    JobOutput,
    MediaItem,
    MediaSource,
    MediaTag,
    Release,
    Track,
    User,
)


router = APIRouter(prefix="/api/admin", tags=["Admin Dashboard"])


def _midden_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=MIDDEN_TTL_HOURS)


# ---------------------------------------------------------------------------
# Stats — totals across the whole site
# ---------------------------------------------------------------------------


@router.get("/stats", summary="Site-wide totals for the Auspices dashboard")
def get_stats(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Return aggregate counts: releases (Canon), tracks (Hymnals),
    media items (Stores), and jobs ever run (Offerings)."""
    releases = db.query(func.count(Release.id)).filter(Release.status == "published").scalar() or 0
    tracks = db.query(func.count(Track.id)).scalar() or 0
    media_items = db.query(func.count(MediaItem.id)).scalar() or 0
    jobs = db.query(func.count(Job.id)).scalar() or 0
    return {
        "releases": releases,
        "tracks": tracks,
        "media_items": media_items,
        "jobs": jobs,
    }


# ---------------------------------------------------------------------------
# Action queue — things that need attention
# ---------------------------------------------------------------------------


@router.get("/action-queue", summary="Counts of items awaiting attention")
def get_action_queue(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Return counts surfaced as action cards on the dashboard:

    - `awaiting_nomenclature`: media items with zero tags (same predicate as
      Nomenclator's `tag_count: {max: 0}` filter).
    - `fallen`: unresolved extraction failures.
    - `midden_rotting`: job outputs in the Midden still within the 24h TTL.
    - `next_purge_at`: ISO timestamp of the earliest discarded item's purge
      time (oldest `discarded_at` + 24h), used by the live countdown. Null if
      the Midden is empty.
    """
    tagged_subq = db.query(MediaTag.media_item_id).distinct().subquery()
    awaiting_nomenclature = (
        db.query(func.count(MediaItem.id))
        .filter(MediaItem.id.notin_(db.query(tagged_subq.c.media_item_id)))
        .scalar()
        or 0
    )

    fallen = (
        db.query(func.count(ExtractionFailure.id))
        .filter(ExtractionFailure.resolved == False)  # noqa: E712
        .scalar()
        or 0
    )

    cutoff = _midden_cutoff()
    midden_q = db.query(JobOutput).filter(
        JobOutput.indexed == False,  # noqa: E712
        JobOutput.discarded_at.isnot(None),
        JobOutput.discarded_at >= cutoff,
    )
    midden_rotting = midden_q.with_entities(func.count(JobOutput.id)).scalar() or 0
    oldest_discard = midden_q.with_entities(func.min(JobOutput.discarded_at)).scalar()
    next_purge_at = None
    if oldest_discard is not None:
        if oldest_discard.tzinfo is None:
            oldest_discard = oldest_discard.replace(tzinfo=timezone.utc)
        next_purge_at = (oldest_discard + timedelta(hours=MIDDEN_TTL_HOURS)).isoformat()

    return {
        "awaiting_nomenclature": awaiting_nomenclature,
        "fallen": fallen,
        "midden_rotting": midden_rotting,
        "next_purge_at": next_purge_at,
    }


# ---------------------------------------------------------------------------
# Activity feed — recent events across the site
# ---------------------------------------------------------------------------


def _ensure_aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


@router.get("/activity-feed", summary="Recent events across the site")
def get_activity_feed(
    limit: int = Query(20, ge=1, le=100),
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Return the last N events union'd across several tables, newest first.

    Event types: `upload`, `job_completed`, `job_failed`, `release_published`,
    `extraction_failed`. Each row carries a timestamp, a short sentence with
    thematic phrasing, an actor name when resolvable, and an optional link.
    """
    over_fetch = max(limit * 3, 50)
    events: list[dict] = []

    uploads = (
        db.query(MediaItem, MediaSource, User)
        .join(MediaSource, MediaSource.media_item_id == MediaItem.id)
        .outerjoin(User, User.id == MediaSource.uploader_id)
        .order_by(MediaItem.created_at.desc())
        .limit(over_fetch)
        .all()
    )
    seen_uploads: set[str] = set()
    for item, source, user in uploads:
        if item.id in seen_uploads:
            continue
        seen_uploads.add(item.id)
        actor = (user.name if user else None) or "someone"
        events.append({
            "type": "upload",
            "timestamp": _ensure_aware(item.created_at).isoformat(),
            "actor_name": actor,
            "description": f"{actor} offered {item.filename}",
            "link": f"/admin/search/detail?id={item.id}",
        })

    jobs = (
        db.query(Job)
        .filter(Job.completed_at.isnot(None))
        .order_by(Job.completed_at.desc())
        .limit(over_fetch)
        .all()
    )
    for job in jobs:
        ts = _ensure_aware(job.completed_at)
        if job.status == "completed":
            try:
                import json as _json
                n_in = len(_json.loads(job.input_items or "[]"))
            except Exception:
                n_in = 0
            desc = f"{job.app_name} consumed {n_in} {'offering' if n_in == 1 else 'offerings'}"
            events.append({
                "type": "job_completed",
                "timestamp": ts.isoformat(),
                "actor_name": job.app_name,
                "description": desc,
                "link": f"/admin/jobs/detail?id={job.id}",
            })
        elif job.status == "failed":
            events.append({
                "type": "job_failed",
                "timestamp": ts.isoformat(),
                "actor_name": job.app_name,
                "description": f"{job.app_name} rejected the offering",
                "link": f"/admin/jobs/detail?id={job.id}",
            })

    releases = (
        db.query(Release)
        .filter(Release.status == "published")
        .order_by(Release.updated_at.desc())
        .limit(over_fetch)
        .all()
    )
    for release in releases:
        events.append({
            "type": "release_published",
            "timestamp": _ensure_aware(release.updated_at).isoformat(),
            "actor_name": None,
            "description": f"{release.title} inscribed into The Canon",
            "link": f"/catalog/{release.product_code}",
        })

    failures = (
        db.query(ExtractionFailure)
        .options(joinedload(ExtractionFailure.media_item))
        .order_by(ExtractionFailure.last_attempt_at.desc())
        .limit(over_fetch)
        .all()
    )
    for f in failures:
        fname = (f.media_item.filename if f.media_item else f.media_item_id) or "an offering"
        events.append({
            "type": "extraction_failed",
            "timestamp": _ensure_aware(f.last_attempt_at).isoformat(),
            "actor_name": f.extraction_type,
            "description": f"{fname} fell during {f.extraction_type}",
            "link": "/admin/search/failures",
        })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return {"events": events[:limit]}


# ---------------------------------------------------------------------------
# Altar of the Day — single random playable media item with uploader name
# ---------------------------------------------------------------------------


@router.get("/altar", summary="Daily media item for the Altar of the Day")
def get_altar(
    _auth=Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    """Pick one MediaItem (audio, video, or image) that stays stable for the
    whole UTC day, and return enough detail for the dashboard card to render
    it inline and/or queue it in the site player.

    Stability is achieved by seeding Python's RNG with today's ISO date and
    sampling an offset into the qualifying set — same day, same item; new UTC
    day, new item.
    """
    import random as _random

    base = db.query(MediaItem).filter(
        MediaItem.media_type.in_(["audio", "video", "image"])
    )
    total = base.with_entities(func.count(MediaItem.id)).scalar() or 0
    if total == 0:
        return {"item": None}

    today_key = datetime.now(timezone.utc).date().isoformat()
    offset = _random.Random(today_key).randrange(total)

    item = (
        base.options(
            joinedload(MediaItem.audio_meta),
            joinedload(MediaItem.video_meta),
            joinedload(MediaItem.image_meta),
            joinedload(MediaItem.sources).joinedload(MediaSource.uploader),
        )
        .order_by(MediaItem.id)
        .offset(offset)
        .limit(1)
        .first()
    )
    if item is None:
        return {"item": None}

    duration = 0.0
    if item.media_type == "audio" and item.audio_meta:
        duration = item.audio_meta.duration_seconds or 0.0
    elif item.media_type == "video" and item.video_meta:
        duration = item.video_meta.duration_seconds or 0.0

    uploader_name: Optional[str] = None
    source_channel: Optional[str] = None
    for src in item.sources:
        if src.uploader and src.uploader.name:
            uploader_name = src.uploader.name
        if src.source_channel and not source_channel:
            source_channel = src.source_channel
        if uploader_name:
            break

    if not uploader_name:
        import json as _json
        for src in item.sources:
            if not src.source_metadata:
                continue
            try:
                meta = _json.loads(src.source_metadata)
            except Exception:
                continue
            if isinstance(meta, dict) and meta.get("poster"):
                uploader_name = meta["poster"]
                break

    return {
        "item": {
            "id": item.id,
            "filename": item.filename,
            "media_type": item.media_type,
            "duration_seconds": duration,
            "uploader_name": uploader_name,
            "source_channel": source_channel,
            "stream_url": f"/api/media/{item.id}/file",
            "cover_url": f"/api/media/{item.id}/thumbnail",
        }
    }
