"""Multi-part DAW session bundle uploads.

A `.logicx` (or other DAW bundle) is a macOS package directory — browsers
can't upload it as a single file, so the client walks the dropped bundle and
uploads each contained file as an individual streamed part:

1. `POST /api/media/bundles` — start a bundle (name + optional Latent attach).
2. `POST /api/media/bundles/{id}/files` — one raw streamed part per call,
   path in the `X-Bundle-Path` header (percent-encoded). Parallel-safe.
3. `POST /api/media/bundles/{id}/complete` — register the bundle as a single
   `session` media item and enqueue audio extraction.
4. `DELETE /api/media/bundles/{id}` — abort and discard staging.

Parts stream straight to disk (no in-memory buffering) so multi-GB bundles are
safe. Abandoned staging directories are reaped after 24h on app startup.

Registered before `search_router` in main.py so `/api/media/bundles/*` is not
captured by `/media/{media_id}`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.auth import get_db, require_admin
from server.models import (
    MediaItem,
    MediaSessionMeta,
    MediaSource,
    Project,
    ProjectItem,
    ProjectSlot,
    User,
)
from server.search_api import (
    _detect_session_tool,
    _get_media_item_or_404,
    _get_search_media_dir,
    _media_item_response,
)
from server.search_client import sync_media_item as meili_sync
from server.session_extract.jobs import run_session_extraction_async

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media/bundles", tags=["Bundles"])

MAX_UPLOAD_PART_BYTES = int(os.environ.get("MAX_UPLOAD_PART_BYTES", str(2 * 1024**3)))
BUNDLE_STALE_HOURS = int(os.environ.get("BUNDLE_STALE_HOURS", "24"))

_STATE_FILE = ".bundle.json"


# ---------------------------------------------------------------------------
# Staging helpers
# ---------------------------------------------------------------------------


def _staging_root() -> Path:
    return _get_search_media_dir() / ".bundles"


def _staging_dir(bundle_id: str) -> Path:
    return _staging_root() / bundle_id


def _write_state(staging: Path, state: dict) -> None:
    tmp = staging / f"{_STATE_FILE}.tmp"
    tmp.write_text(json.dumps(state))
    os.replace(tmp, staging / _STATE_FILE)


def _load_bundle(bundle_id: str) -> tuple[Path, dict]:
    if not bundle_id or "/" in bundle_id or ".." in bundle_id:
        raise HTTPException(status_code=404, detail="Bundle not found")
    staging = _staging_dir(bundle_id)
    state_path = staging / _STATE_FILE
    if not state_path.exists():
        raise HTTPException(status_code=404, detail="Bundle not found or already completed")
    try:
        state = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupt bundle staging state")
    return staging, state


def _sanitize_rel_path(raw: str) -> str:
    """Validate a client-supplied bundle-relative path.

    Rejects absolute paths, traversal, and NUL bytes. If the first segment is
    the bundle directory itself (e.g. `Heliotrope.logicx/Media/…`), it is
    stripped so both client conventions work.
    """
    if not raw or "\x00" in raw:
        raise HTTPException(status_code=400, detail="Invalid bundle path")
    p = PurePosixPath(raw)
    if p.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute paths are not allowed")
    parts = [part for part in p.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")
    if parts[0].lower().endswith(".logicx") and len(parts) > 1:
        parts = parts[1:]
    return PurePosixPath(*parts).as_posix()


def _attach_to_project(
    db: Session, item_id: str, project_id: str, slot_id: str | None, user_id: int
) -> None:
    """Attach a media item to a Latent (idempotent), mirroring upload's rules."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    resolved_slot_id = None
    if slot_id:
        slot = (
            db.query(ProjectSlot)
            .filter(ProjectSlot.id == slot_id, ProjectSlot.project_id == project_id)
            .first()
        )
        if not slot:
            raise HTTPException(
                status_code=404, detail=f"Slot {slot_id} not found in project {project_id}"
            )
        resolved_slot_id = slot.id
    dup = (
        db.query(ProjectItem)
        .filter(
            ProjectItem.project_id == project_id,
            ProjectItem.slot_id == resolved_slot_id,
            ProjectItem.media_item_id == item_id,
        )
        .first()
    )
    if not dup:
        db.add(
            ProjectItem(
                project_id=project_id,
                slot_id=resolved_slot_id,
                media_item_id=item_id,
                added_by=user_id,
            )
        )


def reap_stale_bundles() -> int:
    """Delete staging directories older than BUNDLE_STALE_HOURS. Returns count."""
    root = _staging_root()
    if not root.exists():
        return 0
    cutoff = time.time() - BUNDLE_STALE_HOURS * 3600
    reaped = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                reaped += 1
        except OSError:
            logger.warning("Failed to reap stale bundle staging: %s", child)
    if reaped:
        logger.info("Reaped %d stale bundle staging directorie(s)", reaped)
    return reaped


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class BundleStartRequest(BaseModel):
    name: str = Field(..., description="Bundle filename, e.g. `Heliotrope.logicx`")
    project_id: str | None = Field(None, description="Attach the finished bundle to this Latent")
    slot_id: str | None = Field(None, description="Attach to this slot (requires project_id)")


class BundleCompleteRequest(BaseModel):
    project_id: str | None = Field(None, description="Override the Latent given at start")
    slot_id: str | None = Field(None, description="Override the slot given at start")


def _user_id(auth) -> int:
    user = auth[0] if isinstance(auth, tuple) else auth
    return user.id


@router.post("", status_code=201, summary="Start a bundle upload")
def start_bundle(
    body: BundleStartRequest,
    _auth=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Open a staging area for a multi-part session bundle upload.

    The bundle name must carry a recognised DAW extension (`.logicx`, `.als`,
    …). If `project_id` is given it is validated now and the finished bundle
    is attached on completion.

    **Scope required:** admin
    """
    name = PurePosixPath(body.name).name  # strip any client-supplied path
    if not name or name != body.name.strip():
        raise HTTPException(status_code=400, detail="Invalid bundle name")
    tool = _detect_session_tool(name)
    if not tool:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported bundle type for '{name}'. Expected a DAW bundle extension.",
        )
    if body.slot_id and not body.project_id:
        raise HTTPException(status_code=400, detail="slot_id requires project_id")
    if body.project_id:
        project = db.query(Project).filter(Project.id == body.project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")
        if body.slot_id:
            slot = (
                db.query(ProjectSlot)
                .filter(ProjectSlot.id == body.slot_id, ProjectSlot.project_id == body.project_id)
                .first()
            )
            if not slot:
                raise HTTPException(status_code=404, detail="Slot not found in project")

    bundle_id = str(uuid.uuid4())
    staging = _staging_dir(bundle_id)
    staging.mkdir(parents=True, exist_ok=False)
    _write_state(
        staging,
        {
            "name": name,
            "tool": tool,
            "project_id": body.project_id,
            "slot_id": body.slot_id,
            "created_by": _user_id(_auth),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": {},
        },
    )
    return {"bundle_id": bundle_id, "name": name, "tool": tool}


@router.post("/{bundle_id}/files", summary="Upload one bundle file (streamed)")
async def upload_bundle_part(
    bundle_id: str,
    request: Request,
    x_bundle_path: str = Header(..., description="Percent-encoded path inside the bundle"),
):
    """Stream one file of the bundle to staging.

    The body is the raw file content (no multipart); the destination path is
    given in `X-Bundle-Path` (percent-encoded, relative to the bundle root —
    the leading `Name.logicx/` segment may be included and is stripped).
    Parts may be uploaded in parallel and retried individually.

    **Size limit:** `MAX_UPLOAD_PART_BYTES` per part (default 2 GiB).

    **Scope required:** admin
    """
    staging, state = _load_bundle(bundle_id)
    rel = _sanitize_rel_path(unquote(x_bundle_path))

    dest = staging / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    try:
        with open(dest, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_PART_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {MAX_UPLOAD_PART_BYTES}-byte part limit",
                    )
                hasher.update(chunk)
                f.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    state["files"][rel] = {"size": size, "sha256": hasher.hexdigest()}
    _write_state(staging, state)
    return {"path": rel, "size": size, "sha256": state["files"][rel]["sha256"]}


@router.get("/{bundle_id}", summary="Bundle upload status")
def bundle_status(bundle_id: str, _auth=Depends(require_admin)):
    """Report files received so far and their total size.

    **Scope required:** admin
    """
    _, state = _load_bundle(bundle_id)
    files = state.get("files", {})
    return {
        "bundle_id": bundle_id,
        "name": state["name"],
        "tool": state["tool"],
        "state": "open",
        "file_count": len(files),
        "total_bytes": sum(f["size"] for f in files.values()),
    }


@router.post("/{bundle_id}/complete", status_code=201, summary="Finish a bundle upload")
def complete_bundle(
    bundle_id: str,
    body: BundleCompleteRequest | None = None,
    _auth=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Register a fully-uploaded bundle as a `session` media item.

    Validates the staging area, computes the manifest hash (used for exact
    dedup of re-uploaded identical bundles), moves the bundle into media
    storage, creates the media item + session metadata, attaches it to the
    Latent/slot, and enqueues audio extraction (harvested files become child
    media items in the same slot).

    **Scope required:** admin
    """
    staging, state = _load_bundle(bundle_id)
    files = state.get("files", {})
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded for this bundle")

    project_id = (body.project_id if body else None) or state.get("project_id")
    slot_id = (body.slot_id if body else None) or state.get("slot_id")

    manifest = {
        "name": state["name"],
        "total_bytes": sum(f["size"] for f in files.values()),
        "files": [
            {"path": path, "size": info["size"], "sha256": info["sha256"]}
            for path, info in sorted(files.items())
        ],
    }
    manifest_json = json.dumps(manifest, sort_keys=True)
    manifest_sha = hashlib.sha256(manifest_json.encode()).hexdigest()

    existing = db.query(MediaItem).filter(MediaItem.sha256 == manifest_sha).first()
    if existing:
        if project_id:
            _attach_to_project(db, existing.id, project_id, slot_id, _user_id(_auth))
            db.commit()
            meili_sync(db, existing)
        shutil.rmtree(staging, ignore_errors=True)
        item = _get_media_item_or_404(db, existing.id)
        response = _media_item_response(item)
        response["deduplicated"] = True
        return response

    date_dir = datetime.now(timezone.utc).strftime("%Y-%m")
    final_rel = f"session/{date_dir}/{manifest_sha[:8]}_{state['name']}"
    final_abs = _get_search_media_dir() / final_rel
    final_abs.parent.mkdir(parents=True, exist_ok=True)
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (staging / _STATE_FILE).unlink(missing_ok=True)
    shutil.move(str(staging), str(final_abs))

    item_id = str(uuid.uuid4())
    media_item = MediaItem(
        id=item_id,
        sha256=manifest_sha,
        filename=state["name"],
        file_path=final_rel,
        media_type="session",
        file_size_bytes=manifest["total_bytes"],
        mime_type="application/octet-stream",
    )
    db.add(media_item)
    db.add(
        MediaSource(
            media_item_id=item_id,
            source_type="manual_upload",
            uploader_id=_user_id(_auth),
        )
    )
    db.add(
        MediaSessionMeta(
            media_item_id=item_id,
            tool=state["tool"],
            original_bundle_name=state["name"],
            bundle_size_bytes=manifest["total_bytes"],
            extraction_status="pending",
            extracted_count=0,
        )
    )
    if project_id:
        _attach_to_project(db, item_id, project_id, slot_id, _user_id(_auth))

    db.commit()
    meili_sync(db, media_item)
    run_session_extraction_async(item_id)

    item = _get_media_item_or_404(db, item_id)
    return _media_item_response(item)


@router.delete("/{bundle_id}", status_code=204, summary="Abort a bundle upload")
def abort_bundle(bundle_id: str, _auth=Depends(require_admin)):
    """Discard a staging area and everything uploaded into it.

    **Scope required:** admin
    """
    staging, _ = _load_bundle(bundle_id)
    shutil.rmtree(staging, ignore_errors=True)
    return None
