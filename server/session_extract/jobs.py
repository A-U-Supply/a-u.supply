"""Orchestration: register files harvested from a session bundle as media items.

Entry points:

- :func:`run_session_extraction_async` — spawn a daemon thread (used by the
  bundle-upload endpoint and by zipped-bundle uploads on the legacy path).
- :func:`run_session_extraction` — synchronous; also directly callable in tests.

Each harvested audio file becomes a first-class media item (Emulsion-routed via
its ``session_extract`` source), linked to the parent session item through
``MediaItem.parent_media_item_id`` and attached to every Latent/slot the parent
is attached to. MIDI harvesting lands in PR 2 — MIDI files are collected by the
extractors but skipped here for now.

Per-file failures are recorded in ``extraction_failures`` and don't abort the
set; the terminal status lands on ``media_session_meta.extraction_status``.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from server.extraction import record_extraction_failure, run_extraction
from server.models import (
    MediaItem,
    MediaSource,
    ProjectItem,
    SessionLocal,
)
from server.session_extract.base import ExtractedFile
from server.session_extract.logic import LogicExtractor

logger = logging.getLogger(__name__)

# Registered extractors by MediaSessionMeta.tool. Ableton & friends plug in here.
EXTRACTORS = {"logic": LogicExtractor()}


def _get_search_media_dir() -> Path:
    from server.search_api import _get_search_media_dir as _f

    return _f()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _find_bundle_root(extract_dir: Path) -> Path:
    """Locate the bundle root inside a freshly-unzipped archive.

    Zips made in Finder usually contain a single top-level ``Name.logicx``
    directory; hand-made zips may contain the bundle contents directly.
    """
    entries = [
        e
        for e in extract_dir.iterdir()
        if not e.name.startswith(".") and e.name != "__MACOSX"
    ]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extract_dir


def run_session_extraction_async(media_item_id: str) -> None:
    """Run session extraction in a background thread so uploads don't block."""
    thread = threading.Thread(
        target=run_session_extraction,
        args=(media_item_id,),
        daemon=True,
    )
    thread.start()
    logger.info("Started background session extraction for %s", media_item_id)


def run_session_extraction(media_item_id: str) -> None:
    """Harvest a session bundle into child media items.

    Handles both directory-backed bundles (multi-part bundle upload) and
    zip-backed bundles (legacy single-file upload of a zipped package).
    """
    db = SessionLocal()
    try:
        item = db.query(MediaItem).filter(MediaItem.id == media_item_id).first()
        if item is None or item.session_meta is None:
            logger.error("Session item %s not found, skipping extraction.", media_item_id)
            return
        meta = item.session_meta
        meta.extraction_status = "processing"
        meta.extraction_error = None
        db.commit()

        tmp_dir: Path | None = None
        try:
            bundle_path = _get_search_media_dir() / item.file_path
            if bundle_path.is_dir():
                bundle_dir = bundle_path
            elif bundle_path.is_file() and bundle_path.suffix.lower() == ".zip":
                tmp_dir = Path(tempfile.mkdtemp(prefix="sessx_"))
                with zipfile.ZipFile(bundle_path) as zf:
                    zf.extractall(tmp_dir)
                bundle_dir = _find_bundle_root(tmp_dir)
            else:
                raise RuntimeError(f"bundle not found on disk: {item.file_path}")

            extractor = EXTRACTORS.get(meta.tool)
            if extractor is None:
                raise RuntimeError(f"no extractor registered for tool '{meta.tool}'")
            if not extractor.detect(bundle_dir):
                raise RuntimeError(
                    f"bundle at {item.file_path} is not recognised as a '{meta.tool}' bundle"
                )

            result = extractor.harvest(bundle_dir)
            midi_skipped = sum(1 for f in result.files if f.kind == "midi")
            if midi_skipped:
                logger.info(
                    "Session %s: %d MIDI file(s) collected but not registered yet (PR 2)",
                    media_item_id,
                    midi_skipped,
                )

            count = 0
            for extracted in result.files:
                if extracted.kind != "audio":
                    continue
                try:
                    if _register_child(db, item, extracted):
                        count += 1
                except Exception as exc:  # per-file isolation — keep harvesting
                    logger.exception(
                        "Failed to register %s from session %s", extracted.rel_path, media_item_id
                    )
                    record_extraction_failure(db, item.id, "session_extract", exc)

            meta.extraction_status = "done"
            meta.extracted_count = count
            db.commit()
            logger.info("Session %s: extracted %d audio file(s)", media_item_id, count)
        except Exception as exc:
            logger.exception("Session extraction failed for %s", media_item_id)
            meta.extraction_status = "failed"
            meta.extraction_error = str(exc)[:500]
            db.commit()
            record_extraction_failure(db, item.id, "session_extract", exc)
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)
    finally:
        db.close()


def _register_child(db, parent: MediaItem, extracted: ExtractedFile) -> bool:
    """Register one harvested file as a child media item.

    Returns True when a new media item was created, False when the file's
    content hash matched an existing item (which is reused and re-attached).
    """
    media_dir = _get_search_media_dir()
    sha256 = _sha256_file(extracted.path)

    child = db.query(MediaItem).filter(MediaItem.sha256 == sha256).first()
    created = False
    if child is not None:
        logger.info(
            "Bundle file %s duplicates existing item %s — attaching instead",
            extracted.rel_path,
            child.id,
        )
        if child.parent_media_item_id is None:
            child.parent_media_item_id = parent.id
    else:
        date_dir = datetime.now(timezone.utc).strftime("%Y-%m")
        rel_storage = f"audio/{date_dir}/{sha256[:8]}_{extracted.path.name}"
        dest = media_dir / rel_storage
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(extracted.path, dest)
        except OSError:
            shutil.copy2(extracted.path, dest)

        child = MediaItem(
            id=str(uuid.uuid4()),
            sha256=sha256,
            filename=extracted.path.name,
            file_path=rel_storage,
            media_type="audio",
            file_size_bytes=extracted.size_bytes,
            mime_type=mimetypes.guess_type(extracted.path.name)[0] or "audio/wav",
            parent_media_item_id=parent.id,
        )
        db.add(child)
        db.add(MediaSource(media_item_id=child.id, source_type="session_extract"))
        created = True

    # Attach the child everywhere the parent is attached (same Latent + slot).
    parent_items = db.query(ProjectItem).filter(ProjectItem.media_item_id == parent.id).all()
    for pi in parent_items:
        dup = (
            db.query(ProjectItem)
            .filter(
                ProjectItem.project_id == pi.project_id,
                ProjectItem.slot_id == pi.slot_id,
                ProjectItem.media_item_id == child.id,
            )
            .first()
        )
        if not dup:
            db.add(
                ProjectItem(
                    project_id=pi.project_id,
                    slot_id=pi.slot_id,
                    media_item_id=child.id,
                    added_by=pi.added_by,
                )
            )
    db.commit()

    # Standard audio extraction (ffprobe duration, AI text tags) for new items —
    # opens its own DB session; ours is committed so project_ids land in the
    # Meili doc. Deduped items just re-sync (attachment may have changed).
    if created:
        run_extraction(child.id, str(media_dir / child.file_path), "audio")
    else:
        from server.extraction import _sync_to_search

        _sync_to_search(db, child)
    return created
