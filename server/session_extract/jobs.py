"""Orchestration: register files harvested from a session bundle as media items.

Entry points:

- :func:`run_session_extraction_async` — spawn a daemon thread (used by the
  bundle-upload endpoint and by zipped-bundle uploads on the legacy path).
- :func:`run_session_extraction` — synchronous; also directly callable in tests.

Each harvested audio file becomes a first-class media item (Emulsion-routed via
its ``session_extract`` source), linked to the parent session item through
``MediaItem.parent_media_item_id`` and attached to every Latent/slot the parent
is attached to. MIDI files become playable `midi` items with parsed metadata
and a synthesized preview; their marker meta-events anchor to the parent
session item as cue annotations. WAV/AIFF cue chunks anchor to their own file.

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
    Annotation,
    MediaItem,
    MediaSource,
    ProjectItem,
    SessionLocal,
)
from server.session_extract.base import ExtractedFile
from server.session_extract.cues import Cue, parse_cues
from server.session_extract.logic import LogicExtractor

logger = logging.getLogger(__name__)

# Registered extractors by MediaSessionMeta.tool. Ableton & friends plug in here.
EXTRACTORS = {"logic": LogicExtractor()}

SESSION_LOGIC_PARSE = os.environ.get("SESSION_LOGIC_PARSE", "").lower() in ("1", "true", "yes")


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

            count = 0
            for extracted in result.files:
                try:
                    if extracted.kind == "audio":
                        if _register_child(db, item, extracted):
                            count += 1
                    elif extracted.kind == "midi":
                        if _register_midi_child(db, item, extracted):
                            count += 1
                except Exception as exc:  # per-file isolation — keep harvesting
                    logger.exception(
                        "Failed to register %s from session %s", extracted.rel_path, media_item_id
                    )
                    record_extraction_failure(db, item.id, "session_extract", exc)

            # Experimental: markers straight out of Logic's ProjectData binary
            # (flag-gated, log-only, best-effort — see logic_markers.py).
            if SESSION_LOGIC_PARSE and meta.tool == "logic":
                try:
                    from server.session_extract.logic_markers import (
                        extract_markers,
                        find_project_data_files,
                    )

                    for project_data in find_project_data_files(bundle_dir):
                        markers = extract_markers(project_data)
                        if markers:
                            added = _import_cues(db, item.id, markers, source="logic")
                            logger.info(
                                "Session %s: imported %d Logic marker(s) from %s",
                                media_item_id,
                                added,
                                project_data.name,
                            )
                            break  # first alternative with markers wins
                except Exception:
                    logger.exception("Logic ProjectData marker scan failed (non-fatal)")

            meta.extraction_status = "done"
            meta.extracted_count = count
            db.commit()
            logger.info(
                "Session %s: extracted %d file(s) (audio + midi)", media_item_id, count
            )
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

    # Embedded cue points (WAV/AIFF marker chunks) become cue annotations on
    # this file. Idempotent — re-extraction only adds missing rows.
    try:
        cues, cue_source = parse_cues(extracted.path)
        if cues:
            _import_cues(db, child.id, cues, source=cue_source)
    except Exception:
        logger.exception("Cue import failed for %s (non-fatal)", extracted.rel_path)

    # Standard audio extraction (ffprobe duration, AI text tags) for new items —
    # opens its own DB session; ours is committed so project_ids land in the
    # Meili doc. Deduped items just re-sync (attachment may have changed).
    if created:
        run_extraction(child.id, str(media_dir / child.file_path), "audio")
    else:
        from server.extraction import _sync_to_search

        _sync_to_search(db, child)
    return created


def _register_midi_child(db, parent: MediaItem, extracted: ExtractedFile) -> bool:
    """Register a harvested MIDI file as a playable `midi` media item.

    Marker meta-events anchor to the parent SESSION item (they describe the
    project timeline); the MIDI item sees them through marker inheritance.
    """
    media_dir = _get_search_media_dir()
    sha256 = _sha256_file(extracted.path)

    child = db.query(MediaItem).filter(MediaItem.sha256 == sha256).first()
    created = False
    if child is not None:
        if child.parent_media_item_id is None:
            child.parent_media_item_id = parent.id
    else:
        date_dir = datetime.now(timezone.utc).strftime("%Y-%m")
        rel_storage = f"midi/{date_dir}/{sha256[:8]}_{extracted.path.name}"
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
            media_type="midi",
            file_size_bytes=extracted.size_bytes,
            mime_type="audio/midi",
            parent_media_item_id=parent.id,
        )
        db.add(child)
        db.add(MediaSource(media_item_id=child.id, source_type="session_extract"))
        db.flush()  # child.id needed by register_midi_item

        from server.session_extract.midi import register_midi_item

        parsed = register_midi_item(db, child, dest, media_dir)

        # MIDI markers describe the project timeline — anchor to the session.
        if parsed["markers"]:
            _import_cues(db, parent.id, parsed["markers"], source="midi")
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

    from server.extraction import _sync_to_search

    _sync_to_search(db, child)
    return created


def _import_cues(db, media_item_id: str, cues: list[Cue], source: str) -> int:
    """Insert imported cue annotations, skipping ones already present.

    Dedupe rules (per source, positions rounded to 10ms):
    - exact duplicate (same position + label) → skip;
    - any human-edited (touched_by_user) row at the same position → skip —
      the human's edit replaced the import, so it is never "restored".

    Existing rows are never modified or deleted by imports.
    """
    existing = (
        db.query(Annotation)
        .filter(Annotation.media_item_id == media_item_id, Annotation.source == source)
        .all()
    )
    by_position: dict[float, list[Annotation]] = {}
    for a in existing:
        by_position.setdefault(round(a.position_seconds, 2), []).append(a)

    added = 0
    for cue in cues:
        position = round(cue.position_seconds, 2)
        rows_at_position = by_position.get(position, [])
        if any((a.label or "") == (cue.label or "") for a in rows_at_position):
            continue
        if any(a.touched_by_user for a in rows_at_position):
            continue
        annotation = Annotation(
            id=str(uuid.uuid4()),
            media_item_id=media_item_id,
            kind="cue",
            source=source,
            position_seconds=cue.position_seconds,
            label=cue.label or None,
        )
        db.add(annotation)
        db.flush()
        try:
            from server.search_client import sync_annotation

            sync_annotation(db, annotation)
        except Exception:
            logger.exception("Marginalia sync failed for cue %s", annotation.id)
        by_position.setdefault(position, []).append(annotation)
        added += 1
    if added:
        db.commit()
    return added
