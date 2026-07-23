"""Latents — admin-only pre-release workspace API.

CRUD for projects, slots, items, documents, document revisions, and pins.
Lemmy-backed thread CRUD lives in `server.threads_api`.

All endpoints require admin auth. Slot auto-labels are derived from the parent
project's `kind` when a label isn't supplied.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from server.auth import get_db, require_admin
from server.models import (
    MediaItem,
    Project,
    ProjectDocument,
    ProjectDocumentRevision,
    ProjectItem,
    ProjectLink,
    ProjectSlot,
    SlotPrimaryPin,
    Thread,
    User,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["Latents"])

# How many revisions to keep per document.
MAX_REVISIONS_PER_DOCUMENT = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(name: str) -> str:
    """Slugify a project name. Falls back to a random suffix if empty."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        slug = "latent"
    return slug[:48]


def _unique_slug(db: Session, base: str) -> str:
    """Find a unique project slug derived from `base`."""
    slug = base
    suffix = 2
    while db.query(Project).filter(Project.slug == slug).first() is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _slot_label_default(kind: str, position: int) -> str:
    """Default label for a new slot, derived from its parent's kind."""
    return {
        "album": f"Track {position}",
        "video": f"Scene {position}",
        "zine": f"Spread {position}",
        "session": f"Cut {position}",
    }.get(kind, f"Part {position}")


def _project_or_404(db: Session, project_id: str) -> Project:
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return p


def _slot_or_404(db: Session, project_id: str, slot_id: str) -> ProjectSlot:
    s = db.query(ProjectSlot).filter(
        ProjectSlot.id == slot_id,
        ProjectSlot.project_id == project_id,
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Slot {slot_id} not found in project {project_id}")
    return s


def _document_or_404(db: Session, project_id: str, document_id: str) -> ProjectDocument:
    d = db.query(ProjectDocument).filter(
        ProjectDocument.id == document_id,
        ProjectDocument.project_id == project_id,
    ).first()
    if not d:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return d


def _parse_metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _project_summary(p: Project) -> dict:
    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "kind": p.kind,
        "status": p.status,
        "description": p.description,
        "metadata": _parse_metadata(p.metadata_json),
        "hero_media_item_id": p.hero_media_item_id,
        "hero_style": p.hero_style or "scrim",
        "hero_accent": p.hero_accent_override or p.hero_accent_auto,
        "hero_accent_auto": p.hero_accent_auto,
        "hero_accent_override": p.hero_accent_override,
        "section_styles": _parse_metadata(p.section_styles),
        "lemmy_community_id": p.lemmy_community_id,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _slot_summary(
    s: ProjectSlot,
    pins: dict[str, str] | None = None,
    thread_count: int = 0,
    item_count: int = 0,
    primary_image_media_id: str | None = None,
) -> dict:
    pin_map = pins or {}
    style = _parse_metadata(s.style_json)
    return {
        "id": s.id,
        "project_id": s.project_id,
        "position": s.position,
        "label": s.label,
        "status": s.status,
        "notes": s.notes,
        "notes_updated_at": s.notes_updated_at.isoformat() if s.notes_updated_at else None,
        "description": s.description,
        "metadata": _parse_metadata(s.metadata_json),
        "style": style,
        "accent_auto": s.accent_auto,
        # Effective accent: manual override > solid face color > extracted.
        # Mirrored client-side in latentStyles.ts effectiveAccent().
        "accent": style.get("accent")
        or (style.get("bg_color") if style.get("bg_mode") == "solid" else None)
        or s.accent_auto,
        "primary_image_media_id": primary_image_media_id,
        "pinned": pin_map,
        "thread_count": int(thread_count),
        "item_count": int(item_count),
        "repo_id": s.repo_id,
        "repo_path": s.repo_path,
        "repo_ref": s.repo_ref,
        "run_command": s.run_command,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _document_summary(d: ProjectDocument, include_content: bool = False) -> dict:
    out = {
        "id": d.id,
        "project_id": d.project_id,
        "position": d.position,
        "name": d.name,
        "updated_by": d.updated_by,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
    if include_content:
        out["content"] = d.content
    return out


def _item_summary(pi: ProjectItem) -> dict:
    mi = pi.media_item
    return {
        "id": pi.id,
        "project_id": pi.project_id,
        "slot_id": pi.slot_id,
        "media_item_id": pi.media_item_id,
        "added_by": pi.added_by,
        "added_at": pi.added_at.isoformat() if pi.added_at else None,
        "is_primary": bool(pi.is_primary),
        "media": {
            "id": mi.id,
            "filename": mi.filename,
            "media_type": mi.media_type,
            "mime_type": mi.mime_type,
            "file_size_bytes": mi.file_size_bytes,
            "parent_media_item_id": mi.parent_media_item_id,
            "session_extraction_status": mi.session_meta.extraction_status if mi.session_meta else None,
            "session_extracted_count": mi.session_meta.extracted_count if mi.session_meta else None,
        } if mi else None,
    }


def _pin_map_for_slot(db: Session, slot_id: str) -> dict[str, str]:
    pins = db.query(SlotPrimaryPin).filter(SlotPrimaryPin.slot_id == slot_id).all()
    return {p.media_type: p.media_item_id for p in pins}


def _primary_image_map(db: Session, slot_ids: list[str]) -> dict[str, str]:
    """slot_id -> media_item_id of the slot's ★ starred image.

    Latest `added_at` wins, id as a deterministic tie-break: rows are scanned
    ascending so the last overwrite is the winner. Must stay in step with
    `_recompute_slot_accent`, which colors from the same pick.
    """
    if not slot_ids:
        return {}
    rows = (
        db.query(ProjectItem.slot_id, ProjectItem.media_item_id)
        .join(MediaItem, MediaItem.id == ProjectItem.media_item_id)
        .filter(
            ProjectItem.slot_id.in_(slot_ids),
            ProjectItem.is_primary.is_(True),
            MediaItem.media_type == "image",
        )
        .order_by(ProjectItem.added_at.asc(), ProjectItem.id.asc())
        .all()
    )
    return {sid: mid for sid, mid in rows}


def _primary_image_for_slot(db: Session, slot_id: str) -> str | None:
    return _primary_image_map(db, [slot_id]).get(slot_id)


def _slot_count_maps(
    db: Session, project_id: str, slot_ids: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    """(item_counts, thread_counts) per slot, bulk grouped queries.

    Every slot summary must carry real counts — the client full-replaces or
    spread-merges slot objects from PATCH/reorder responses, so a defaulted
    0 here becomes a visible "0 files" in the UI.
    """
    item_counts: dict[str, int] = {}
    thread_counts: dict[str, int] = {}
    if not slot_ids:
        return item_counts, thread_counts
    for sid, c in (
        db.query(ProjectItem.slot_id, func.count(ProjectItem.id))
        .filter(ProjectItem.project_id == project_id, ProjectItem.slot_id.in_(slot_ids))
        .group_by(ProjectItem.slot_id)
        .all()
    ):
        item_counts[sid] = int(c)
    for sid, c in (
        db.query(Thread.anchor_id, func.count(Thread.id))
        .filter(Thread.anchor_type == "slot", Thread.anchor_id.in_(slot_ids))
        .group_by(Thread.anchor_id)
        .all()
    ):
        thread_counts[sid] = int(c)
    return item_counts, thread_counts


def _recompute_slot_accent(db: Session, slot: ProjectSlot) -> None:
    """Refresh a slot's auto accent from its ★ starred image. Best-effort:
    extraction failures leave the accent None, never block the write."""
    mid = _primary_image_for_slot(db, slot.id)
    if not mid:
        slot.accent_auto = None
        return
    mi = db.query(MediaItem).filter(MediaItem.id == mid).first()
    slot.accent_auto = _compute_hero_accent(mi) if mi else None


def _compute_hero_accent(mi: MediaItem) -> str | None:
    """Best-effort accent extraction for a hero image. Never raises.

    Prefers the worker-populated `dominant_colors` on image_meta; falls back
    to a live extraction off the smallest on-disk rendition (an image set as
    hero seconds after upload may not have meta or thumbnails yet). The live
    result is deliberately not written back to image_meta — that stays the
    extraction worker's job.
    """
    from server.extraction import extract_dominant_colors, pick_accent_color

    try:
        colors: list = []
        meta = mi.image_meta
        if meta and meta.dominant_colors:
            try:
                parsed = json.loads(meta.dominant_colors)
                if isinstance(parsed, list):
                    colors = parsed
            except (ValueError, TypeError):
                colors = []
        if not colors:
            from server.search_api import _get_search_media_dir, _resolve_thumbnail_path

            resolved = _resolve_thumbnail_path(mi, size="sm")
            path = resolved[0] if resolved else None
            if path is None and mi.file_path:
                candidate = _get_search_media_dir() / mi.file_path
                path = candidate if candidate.exists() else None
            if path is None:
                return None
            colors = extract_dominant_colors(str(path))
        return pick_accent_color(colors)
    except Exception:
        logger.exception("Hero accent extraction failed for media %s", mi.id)
        return None


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


VALID_KINDS = {"album", "video", "zine", "session", "other"}
VALID_PROJECT_STATUSES = {"forming", "developing", "fixing", "abandoned"}
VALID_SLOT_STATUSES = {"forming", "developing", "fixed"}
VALID_PIN_TYPES = {"image", "audio", "video", "session"}
VALID_HERO_STYLES = {"scrim", "plate", "treat"}

# The entire style-injection defense for the accent: the client writes the
# stored value into a `style="--latent-accent:…"` attribute verbatim, so
# nothing outside this grammar may ever be persisted.
_HERO_ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Section/slot style grammar (2026-07-18-latent-faces revision of the
# 07-17 plan). Every stored value lands in a client `style` attribute, so
# hex keys share the hero-accent injection stance above. `head_tint` was
# retired by the faces redesign — it now 400s as an unknown key and gets
# scrubbed from stored styles on the next write.
_STYLE_HEX_KEYS = {"accent", "bg_color", "border", "text"}
VALID_SLOT_BG_MODES = {"auto", "image", "solid", "none"}
VALID_SECTION_BG_MODES = {"image", "solid", "none"}  # sections have no starred image
VALID_SECTION_KEYS = {"repo", "links", "docs", "slots", "loose", "threads"}


def _merge_style_patch(
    db: Session,
    stored_raw: str | None,
    patch: dict,
    bg_modes: set[str],
    what: str,
) -> str | None:
    """Merge a partial style dict into the stored JSON, validating every key.

    `""` deletes a key (reset to auto/default); unknown keys 400. Returns the
    new JSON string, or None when nothing remains.
    """
    merged = _parse_metadata(stored_raw)
    for key, value in patch.items():
        if key in _STYLE_HEX_KEYS:
            if value == "":
                merged.pop(key, None)
            elif isinstance(value, str) and _HERO_ACCENT_RE.match(value):
                merged[key] = value.lower()
            else:
                raise HTTPException(status_code=400, detail=f"{what}.{key} must be '#rrggbb'")
        elif key == "bg_mode":
            if value == "":
                merged.pop(key, None)
            elif value in bg_modes:
                merged[key] = value
            else:
                raise HTTPException(status_code=400, detail=f"Invalid {what}.bg_mode '{value}'")
        elif key == "bg_style":
            # Image-face treatment (scrim | plate | treat — the hero-card
            # vocabulary). Legal in any mode so single-key PATCHes stay
            # order-independent; solid/none faces simply ignore it.
            if value == "":
                merged.pop(key, None)
            elif value in VALID_HERO_STYLES:
                merged[key] = value
            else:
                raise HTTPException(status_code=400, detail=f"Invalid {what}.bg_style '{value}'")
        elif key == "bg_media_item_id":
            if value == "":
                merged.pop(key, None)
            elif not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"{what}.bg_media_item_id must be a media item id")
            else:
                mi = db.query(MediaItem).filter(MediaItem.id == value).first()
                if not mi:
                    raise HTTPException(status_code=404, detail=f"{what}.bg_media_item_id refers to a missing media item")
                if mi.media_type != "image":
                    raise HTTPException(status_code=400, detail=f"{what}.bg_media_item_id must be an image media item")
                merged[key] = value
        else:
            raise HTTPException(status_code=400, detail=f"Unknown {what} key '{key}'")
    merged.pop("head_tint", None)  # scrub pre-faces leftovers on any write
    return json.dumps(merged) if merged else None


def _merge_section_styles(db: Session, stored_raw: str | None, patch: dict) -> str | None:
    """Merge a partial {section_key: style object} dict. An empty section
    object deletes that section's entry."""
    merged = _parse_metadata(stored_raw)
    for section, sub in patch.items():
        if section not in VALID_SECTION_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown section '{section}'")
        if not isinstance(sub, dict):
            raise HTTPException(status_code=400, detail=f"section_styles['{section}'] must be an object")
        if not sub:
            merged.pop(section, None)
            continue
        current_raw = json.dumps(merged[section]) if isinstance(merged.get(section), dict) else None
        new_raw = _merge_style_patch(db, current_raw, sub, VALID_SECTION_BG_MODES, f"section_styles.{section}")
        if new_raw is None:
            merged.pop(section, None)
        else:
            merged[section] = json.loads(new_raw)
    return json.dumps(merged) if merged else None


class CreateProjectBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    kind: str = Field("other")


class UpdateProjectBody(BaseModel):
    name: str | None = None
    slug: str | None = None
    kind: str | None = None
    status: str | None = None
    description: str | None = None
    metadata: dict | None = None
    hero_media_item_id: str | None = None
    hero_style: str | None = None
    hero_accent_override: str | None = None  # "" resets to auto
    section_styles: dict | None = None       # partial merge; {} clears all; per-key "" deletes


class CreateSlotBody(BaseModel):
    label: str | None = None
    position: int | None = None


class UpdateSlotBody(BaseModel):
    label: str | None = None
    status: str | None = None
    notes: str | None = None
    description: str | None = None
    metadata: dict | None = None
    repo_id: str | None = None       # set to "" to clear
    repo_path: str | None = None     # set to "" to clear
    repo_ref: str | None = None      # set to "" to clear (falls back to repo default branch)
    run_command: str | None = None   # set to "" to clear (falls back to `python <path>`)
    style: dict | None = None        # partial merge; {} clears all; per-key "" deletes


class ReorderSlotsBody(BaseModel):
    order: list[str]  # slot ids in new order


class AttachItemsBody(BaseModel):
    media_item_ids: list[str]
    slot_id: str | None = None


class MoveItemBody(BaseModel):
    slot_id: str | None = None  # null = detach from slot (move to loose)


class SetPinBody(BaseModel):
    media_type: str
    media_item_id: str


class CreateDocumentBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class UpdateDocumentBody(BaseModel):
    name: str | None = None
    content: str | None = None


class ReorderDocumentsBody(BaseModel):
    order: list[str]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/by-slug/{slug}", summary="Resolve a project by its slug")
def project_by_slug(
    slug: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = db.query(Project).filter(Project.slug == slug).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"No project with slug '{slug}'")
    return {"id": p.id, "slug": p.slug, "name": p.name}


@router.get("", summary="List Latents")
def list_projects(
    status: str | None = Query(None),
    kind: str | None = Query(None),
    created_by: int | None = Query(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Project)
    if status:
        q = q.filter(Project.status == status)
    if kind:
        q = q.filter(Project.kind == kind)
    if created_by:
        q = q.filter(Project.created_by == created_by)
    items = q.order_by(Project.updated_at.desc()).all()
    return {"projects": [_project_summary(p) for p in items]}


@router.post("", status_code=201, summary="Create a Latent")
def create_project(
    body: CreateProjectBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    kind = body.kind if body.kind in VALID_KINDS else "other"
    base_slug = _slugify(body.name)
    slug = _unique_slug(db, base_slug)
    project = Project(
        id=str(uuid.uuid4()),
        slug=slug,
        name=body.name,
        kind=kind,
        status="forming",
        created_by=user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    # Lemmy community is provisioned lazily on first Threads action by a
    # linked user (see threads_api). Don't block Latent creation on Lemmy.

    # Slack notification (immediate tier)
    try:
        from server.slack_notifier import notify_immediate
        notify_immediate("latent.created", user, project_id=project.id, project_slug=project.slug, name=project.name, kind=project.kind)
    except Exception:
        logger.exception("slack notify_immediate(latent.created) failed")

    return _project_summary(project)


@router.get("/{project_id}", summary="Get a Latent (header + summaries)")
def get_project(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = _project_or_404(db, project_id)

    # Lazy Lemmy community provisioning: if the calling user is fold-linked
    # and the Latent doesn't have a community yet, fire it off best-effort
    # so threads work the moment they're opened. Silent on any failure.
    if not p.lemmy_community_id and getattr(user, "lemmy_token_encrypted", None):
        try:
            from server.lemmy_client import (
                ensure_project_community,
                get_user_token,
                is_configured,
            )
            if is_configured():
                token = get_user_token(db, user)
                ensure_project_community(db, p, token)
                db.refresh(p)
        except Exception:
            logger.exception("Lazy Lemmy provisioning failed for project %s", project_id)

    slots = db.query(ProjectSlot).filter(ProjectSlot.project_id == project_id).order_by(ProjectSlot.position).all()
    slot_pins = {s.id: _pin_map_for_slot(db, s.id) for s in slots}
    documents = db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).order_by(ProjectDocument.position).all()
    item_count = db.query(func.count(ProjectItem.id)).filter(ProjectItem.project_id == project_id).scalar() or 0
    loose_count = db.query(func.count(ProjectItem.id)).filter(
        ProjectItem.project_id == project_id,
        ProjectItem.slot_id.is_(None),
    ).scalar() or 0
    project_thread_count = db.query(func.count(Thread.id)).filter(
        Thread.anchor_type == "project", Thread.anchor_id == project_id,
    ).scalar() or 0
    slot_item_counts, slot_thread_counts = _slot_count_maps(
        db, project_id, [s.id for s in slots],
    )
    primary_images = _primary_image_map(db, [s.id for s in slots])
    return {
        **_project_summary(p),
        "slots": [
            _slot_summary(
                s,
                slot_pins.get(s.id),
                slot_thread_counts.get(s.id, 0),
                slot_item_counts.get(s.id, 0),
                primary_image_media_id=primary_images.get(s.id),
            )
            for s in slots
        ],
        "documents": [_document_summary(d) for d in documents],
        "item_count": int(item_count),
        "loose_item_count": int(loose_count),
        "thread_count": int(project_thread_count),
    }


@router.post("/{project_id}/lemmy/provision", summary="Provision the Latent's Lemmy community now")
def provision_community(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Trigger Lemmy community creation explicitly. Useful for repos linked
    before the lazy-provision behavior shipped, or after fold link changes.
    """
    from server.lemmy_client import (
        LemmyNotLinked,
        LemmyUnavailable,
        ensure_project_community,
        get_user_token,
        is_configured,
    )
    p = _project_or_404(db, project_id)
    if not is_configured():
        raise HTTPException(status_code=503, detail="Lemmy not configured")
    try:
        token = get_user_token(db, user)
    except LemmyNotLinked as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        cid = ensure_project_community(db, p, token)
    except LemmyUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e))
    db.refresh(p)
    return {"lemmy_community_id": cid, "project_slug": p.slug}


@router.patch("/{project_id}", summary="Update a Latent")
def update_project(
    project_id: str,
    body: UpdateProjectBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = _project_or_404(db, project_id)
    prior_status = p.status
    if body.name is not None:
        p.name = body.name
    if body.slug is not None:
        desired = _slugify(body.slug)
        if desired != p.slug:
            clash = db.query(Project).filter(Project.slug == desired, Project.id != p.id).first()
            if clash:
                raise HTTPException(status_code=409, detail=f"Slug '{desired}' is already taken")
            p.slug = desired
    if body.kind is not None:
        if body.kind not in VALID_KINDS:
            raise HTTPException(status_code=400, detail=f"Invalid kind '{body.kind}'")
        p.kind = body.kind
    if body.status is not None:
        if body.status not in VALID_PROJECT_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status '{body.status}'")
        p.status = body.status
    if body.description is not None:
        p.description = body.description or None
    if body.metadata is not None:
        p.metadata_json = json.dumps(body.metadata) if body.metadata else None
    if body.hero_media_item_id is not None:
        if body.hero_media_item_id:
            mi = db.query(MediaItem).filter(MediaItem.id == body.hero_media_item_id).first()
            if not mi:
                raise HTTPException(status_code=404, detail="hero_media_item_id refers to a missing media item")
            if mi.media_type != "image":
                raise HTTPException(status_code=400, detail="Hero must be an image media item")
            if body.hero_media_item_id != p.hero_media_item_id:
                p.hero_accent_auto = _compute_hero_accent(mi)
            p.hero_media_item_id = body.hero_media_item_id
        else:
            p.hero_media_item_id = None
            p.hero_accent_auto = None  # override survives; auto belongs to the departed image
    if body.hero_style is not None:
        if body.hero_style not in VALID_HERO_STYLES:
            raise HTTPException(status_code=400, detail=f"Invalid hero_style '{body.hero_style}'")
        p.hero_style = body.hero_style
    if body.hero_accent_override is not None:
        if body.hero_accent_override == "":
            p.hero_accent_override = None  # reset to auto
        elif _HERO_ACCENT_RE.match(body.hero_accent_override):
            p.hero_accent_override = body.hero_accent_override.lower()
        else:
            raise HTTPException(status_code=400, detail="hero_accent_override must be '#rrggbb'")
    if body.section_styles is not None:
        if body.section_styles == {}:
            p.section_styles = None
        else:
            p.section_styles = _merge_section_styles(db, p.section_styles, body.section_styles)
    db.commit()
    db.refresh(p)

    if body.status is not None and body.status != prior_status:
        try:
            from server.slack_notifier import notify_immediate
            event = "latent.abandoned" if body.status == "abandoned" else "latent.status_changed"
            notify_immediate(event, user, project_id=p.id, project_slug=p.slug, name=p.name, status=p.status, prior_status=prior_status)
        except Exception:
            logger.exception("slack notify_immediate(latent status) failed")

    return _project_summary(p)


@router.delete("/{project_id}", status_code=204, summary="Delete a Latent")
def delete_project(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = _project_or_404(db, project_id)
    db.delete(p)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


@router.get("/{project_id}/slots", summary="List slots")
def list_slots(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    slots = db.query(ProjectSlot).filter(ProjectSlot.project_id == project_id).order_by(ProjectSlot.position).all()
    # item_count + thread_count per slot — same bulk-counts shape as
    # get_project so the LatentSlots component can eagerly load files for
    # any slot with items > 0.
    slot_ids = [s.id for s in slots]
    item_counts, thread_counts = _slot_count_maps(db, project_id, slot_ids)
    primary_images = _primary_image_map(db, slot_ids)
    return {
        "slots": [
            _slot_summary(
                s,
                _pin_map_for_slot(db, s.id),
                thread_counts.get(s.id, 0),
                item_counts.get(s.id, 0),
                primary_image_media_id=primary_images.get(s.id),
            )
            for s in slots
        ]
    }


@router.post("/{project_id}/slots", status_code=201, summary="Create a slot")
def create_slot(
    project_id: str,
    body: CreateSlotBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = _project_or_404(db, project_id)
    max_pos = db.query(func.max(ProjectSlot.position)).filter(ProjectSlot.project_id == project_id).scalar() or 0
    position = body.position if body.position is not None else max_pos + 1
    label = body.label or _slot_label_default(p.kind, position)
    slot = ProjectSlot(
        id=str(uuid.uuid4()),
        project_id=project_id,
        position=position,
        label=label,
        status="forming",
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    ic, tc = _slot_count_maps(db, project_id, [slot.id])
    return _slot_summary(
        slot, _pin_map_for_slot(db, slot.id),
        tc.get(slot.id, 0), ic.get(slot.id, 0),
    )


@router.patch("/{project_id}/slots/{slot_id}", summary="Update a slot")
def update_slot(
    project_id: str,
    slot_id: str,
    body: UpdateSlotBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = _slot_or_404(db, project_id, slot_id)
    if body.label is not None:
        s.label = body.label
    if body.status is not None:
        if body.status not in VALID_SLOT_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid slot status '{body.status}'")
        s.status = body.status
    if body.notes is not None:
        s.notes = body.notes
        s.notes_updated_at = _utcnow()
    if body.description is not None:
        s.description = body.description or None
    if body.metadata is not None:
        s.metadata_json = json.dumps(body.metadata) if body.metadata else None
    # Repo file linkage. Empty string clears the field.
    if body.repo_id is not None:
        if body.repo_id == "":
            s.repo_id = None
        else:
            from server.models import ProjectRepo
            repo = db.query(ProjectRepo).filter(
                ProjectRepo.id == body.repo_id, ProjectRepo.project_id == project_id,
            ).first()
            if not repo:
                raise HTTPException(status_code=400, detail="repo_id not linked to this project")
            s.repo_id = body.repo_id
    if body.repo_path is not None:
        s.repo_path = body.repo_path or None
    if body.repo_ref is not None:
        s.repo_ref = body.repo_ref or None
    if body.run_command is not None:
        s.run_command = body.run_command or None
    if body.style is not None:
        if body.style == {}:
            s.style_json = None
        else:
            s.style_json = _merge_style_patch(db, s.style_json, body.style, VALID_SLOT_BG_MODES, "style")
    db.commit()
    db.refresh(s)
    ic, tc = _slot_count_maps(db, project_id, [s.id])
    return _slot_summary(
        s, _pin_map_for_slot(db, s.id),
        tc.get(s.id, 0), ic.get(s.id, 0),
        primary_image_media_id=_primary_image_for_slot(db, s.id),
    )


@router.delete("/{project_id}/slots/{slot_id}", status_code=204, summary="Delete a slot")
def delete_slot(
    project_id: str,
    slot_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = _slot_or_404(db, project_id, slot_id)
    db.delete(s)
    db.commit()
    return None


@router.post("/{project_id}/slots/reorder", summary="Reorder slots")
def reorder_slots(
    project_id: str,
    body: ReorderSlotsBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    slots = db.query(ProjectSlot).filter(ProjectSlot.project_id == project_id).all()
    by_id = {s.id: s for s in slots}
    if set(body.order) != set(by_id.keys()):
        raise HTTPException(status_code=400, detail="Order must contain every slot id exactly once")
    # Two-pass to dodge the (project_id, position) unique constraint while shuffling.
    for i, s in enumerate(slots):
        s.position = -(i + 1)
    db.flush()
    for i, sid in enumerate(body.order):
        by_id[sid].position = i + 1
    db.commit()
    primary_images = _primary_image_map(db, body.order)
    ic, tc = _slot_count_maps(db, project_id, body.order)
    return {
        "slots": [
            _slot_summary(
                by_id[sid], _pin_map_for_slot(db, sid),
                tc.get(sid, 0), ic.get(sid, 0),
                primary_image_media_id=primary_images.get(sid),
            )
            for sid in body.order
        ]
    }


# ---------------------------------------------------------------------------
# Slot primary pins
# ---------------------------------------------------------------------------


@router.put("/{project_id}/slots/{slot_id}/pin", summary="Pin a primary file for a media type")
def set_slot_pin(
    project_id: str,
    slot_id: str,
    body: SetPinBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = _slot_or_404(db, project_id, slot_id)
    if body.media_type not in VALID_PIN_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid pin media_type '{body.media_type}'")
    mi = db.query(MediaItem).filter(MediaItem.id == body.media_item_id).first()
    if not mi:
        raise HTTPException(status_code=404, detail="media_item_id not found")
    # Replace existing pin for that type
    existing = db.query(SlotPrimaryPin).filter(
        SlotPrimaryPin.slot_id == slot_id,
        SlotPrimaryPin.media_type == body.media_type,
    ).first()
    if existing:
        existing.media_item_id = body.media_item_id
    else:
        db.add(SlotPrimaryPin(
            slot_id=slot_id,
            media_type=body.media_type,
            media_item_id=body.media_item_id,
        ))
    db.commit()
    return {"slot_id": slot_id, "pinned": _pin_map_for_slot(db, slot_id)}


@router.delete("/{project_id}/slots/{slot_id}/pin/{media_type}", status_code=204, summary="Clear a pin")
def clear_slot_pin(
    project_id: str,
    slot_id: str,
    media_type: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _slot_or_404(db, project_id, slot_id)
    existing = db.query(SlotPrimaryPin).filter(
        SlotPrimaryPin.slot_id == slot_id,
        SlotPrimaryPin.media_type == media_type,
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
    return None


# ---------------------------------------------------------------------------
# Items (attach / detach / move)
# ---------------------------------------------------------------------------


@router.get("/{project_id}/items", summary="List attached items")
def list_items(
    project_id: str,
    slot_id: str | None = Query(None),
    loose_only: bool = Query(False),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    q = db.query(ProjectItem).options(joinedload(ProjectItem.media_item)).filter(ProjectItem.project_id == project_id)
    if loose_only:
        q = q.filter(ProjectItem.slot_id.is_(None))
    elif slot_id:
        q = q.filter(ProjectItem.slot_id == slot_id)
    items = q.order_by(ProjectItem.added_at.desc()).all()
    # Self-heal: any ProjectItem whose media_item vanished (cascade failed
    # before PRAGMA foreign_keys=ON shipped) gets purged on read so the UI
    # never has to render "(unknown)" placeholders.
    orphan_ids = [pi.id for pi in items if pi.media_item is None]
    if orphan_ids:
        db.query(ProjectItem).filter(ProjectItem.id.in_(orphan_ids)).delete(synchronize_session=False)
        db.commit()
        items = [pi for pi in items if pi.id not in set(orphan_ids)]
    return {"items": [_item_summary(pi) for pi in items]}


@router.post("/{project_id}/items", status_code=201, summary="Attach media items to a Latent")
def attach_items(
    project_id: str,
    body: AttachItemsBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = _project_or_404(db, project_id)
    resolved_slot_id = None
    if body.slot_id:
        slot = _slot_or_404(db, project_id, body.slot_id)
        resolved_slot_id = slot.id

    attached = []
    for mid in body.media_item_ids:
        mi = db.query(MediaItem).filter(MediaItem.id == mid).first()
        if not mi:
            continue  # silently skip unknown ids
        existing = db.query(ProjectItem).filter(
            ProjectItem.project_id == project_id,
            ProjectItem.slot_id == resolved_slot_id,
            ProjectItem.media_item_id == mid,
        ).first()
        if existing:
            attached.append(existing)
            continue
        pi = ProjectItem(
            project_id=project_id,
            slot_id=resolved_slot_id,
            media_item_id=mid,
            added_by=user.id,
        )
        db.add(pi)
        attached.append(pi)
    db.commit()
    # Re-sync attached media items so their project_ids array is fresh in Meilisearch.
    try:
        from server.search_client import sync_media_item
        for pi in attached:
            db.refresh(pi)
            if pi.media_item:
                try:
                    sync_media_item(db, pi.media_item)
                except Exception as exc:
                    logger.exception("meili re-sync after attach failed for %s", pi.media_item.id)
                    from server.extraction import record_extraction_failure
                    record_extraction_failure(db, pi.media_item.id, "meilisearch_sync", exc)
    except Exception:
        logger.exception("meili re-sync after attach failed (loop)")

    try:
        from server.slack_notifier import queue_batched
        queue_batched("latent.items_added", user, project_id=p.id, project_slug=p.slug, count=len(attached), slot_id=resolved_slot_id)
    except Exception:
        logger.exception("slack queue_batched(latent.items_added) failed")

    return {"items": [_item_summary(pi) for pi in attached]}


@router.patch("/{project_id}/items/{item_id}", summary="Move an attached item between slot / loose")
def move_item(
    project_id: str,
    item_id: str,
    body: MoveItemBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    pi = db.query(ProjectItem).options(joinedload(ProjectItem.media_item)).filter(
        ProjectItem.id == item_id,
        ProjectItem.project_id == project_id,
    ).first()
    if not pi:
        raise HTTPException(status_code=404, detail="Attachment not found")
    new_slot_id = None
    if body.slot_id:
        slot = _slot_or_404(db, project_id, body.slot_id)
        new_slot_id = slot.id
    old_slot_id = pi.slot_id
    pi.slot_id = new_slot_id
    if pi.is_primary and pi.media_item and pi.media_item.media_type == "image":
        for sid in {old_slot_id, new_slot_id} - {None}:
            s = db.query(ProjectSlot).filter(ProjectSlot.id == sid).first()
            if s:
                _recompute_slot_accent(db, s)
    db.commit()
    db.refresh(pi)
    return _item_summary(pi)


class SetPrimaryBody(BaseModel):
    is_primary: bool


@router.put("/{project_id}/items/{item_id}/primary", summary="Star/unstar an item as primary")
def set_item_primary(
    project_id: str,
    item_id: str,
    body: SetPrimaryBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    pi = db.query(ProjectItem).options(joinedload(ProjectItem.media_item)).filter(
        ProjectItem.id == item_id,
        ProjectItem.project_id == project_id,
    ).first()
    if not pi:
        raise HTTPException(status_code=404, detail="Attachment not found")
    pi.is_primary = bool(body.is_primary)
    slot = None
    if pi.slot_id:
        slot = db.query(ProjectSlot).filter(ProjectSlot.id == pi.slot_id).first()
        if slot and pi.media_item and pi.media_item.media_type == "image":
            _recompute_slot_accent(db, slot)
    db.commit()
    db.refresh(pi)
    out = _item_summary(pi)
    if slot:
        # Fresh slot summary rides along (additive) so the card can repaint
        # its auto accent/background without a second request.
        db.refresh(slot)
        ic, tc = _slot_count_maps(db, project_id, [slot.id])
        out["slot"] = _slot_summary(
            slot, _pin_map_for_slot(db, slot.id),
            tc.get(slot.id, 0), ic.get(slot.id, 0),
            primary_image_media_id=_primary_image_for_slot(db, slot.id),
        )
    return out


@router.delete("/{project_id}/slots/{slot_id}/items", summary="Detach every item from a slot (optionally purge Emulsion-only uploads)")
def clear_slot_items(
    project_id: str,
    slot_id: str,
    purge: bool = Query(False, description="If true, also delete media items that live only in Emulsion and aren't attached anywhere else."),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    cleared_slot = _slot_or_404(db, project_id, slot_id)
    pis = db.query(ProjectItem).options(joinedload(ProjectItem.media_item)).filter(
        ProjectItem.project_id == project_id,
        ProjectItem.slot_id == slot_id,
    ).all()
    detached_ids: list[str] = []
    purged_media: list[tuple[str, str, str | None]] = []  # (id, media_type, file_path) for post-commit cleanup
    for pi in pis:
        mi = pi.media_item
        db.delete(pi)
        detached_ids.append(pi.id)
        if purge and mi is not None:
            # Only nuke media items that won't be referenced from anywhere
            # else after this detach (other slots/projects), and which live
            # in the private Emulsion index. Public scrape items stay put.
            other = db.query(ProjectItem).filter(
                ProjectItem.media_item_id == mi.id,
                ProjectItem.id != pi.id,
            ).count()
            is_emulsion_only = (mi.output_index is None) and (mi.source_id is None)
            if other == 0 and is_emulsion_only:
                purged_media.append((mi.id, mi.media_type, mi.file_path))
                db.delete(mi)
    cleared_slot.accent_auto = None  # no items remain, so no starred image
    db.commit()
    # Disk + Meili cleanup for purged items.
    if purged_media:
        from server.search_api import _get_search_media_dir
        media_root = _get_search_media_dir()
        for mi_id, media_type, file_path in purged_media:
            if file_path:
                p = media_root / file_path
                if p.exists():
                    p.unlink()
                thumb = p.with_name(p.stem + "_thumb.webp")
                if thumb.exists():
                    thumb.unlink()
            try:
                from server.search_client import delete_media_item as meili_delete
                meili_delete(mi_id, media_type)
            except Exception:
                logger.exception("meili delete failed for %s", mi_id)
    return {"detached": len(detached_ids), "purged": len(purged_media)}


@router.delete("/{project_id}/items/{item_id}", status_code=204, summary="Detach a media item")
def detach_item(
    project_id: str,
    item_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    pi = db.query(ProjectItem).filter(
        ProjectItem.id == item_id,
        ProjectItem.project_id == project_id,
    ).first()
    if not pi:
        raise HTTPException(status_code=404, detail="Attachment not found")
    mi_id = pi.media_item_id
    recompute_slot_id = (
        pi.slot_id
        if pi.is_primary and pi.media_item and pi.media_item.media_type == "image"
        else None
    )
    db.delete(pi)
    if recompute_slot_id:
        db.flush()  # the accent query must not see the departing row
        s = db.query(ProjectSlot).filter(ProjectSlot.id == recompute_slot_id).first()
        if s:
            _recompute_slot_accent(db, s)
    db.commit()
    # Refresh the search doc — `project_ids` array shrinks.
    try:
        from server.search_client import sync_media_item
        mi = db.query(MediaItem).filter(MediaItem.id == mi_id).first()
        if mi:
            sync_media_item(db, mi)
    except Exception as exc:
        logger.exception("meili re-sync after detach failed")
        if mi_id:
            from server.extraction import record_extraction_failure
            record_extraction_failure(db, mi_id, "meilisearch_sync", exc)
    return None


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@router.get("/{project_id}/documents", summary="List documents")
def list_documents(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    docs = db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).order_by(ProjectDocument.position).all()
    return {"documents": [_document_summary(d) for d in docs]}


@router.post("/{project_id}/documents", status_code=201, summary="Create a document")
def create_document(
    project_id: str,
    body: CreateDocumentBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    max_pos = db.query(func.max(ProjectDocument.position)).filter(ProjectDocument.project_id == project_id).scalar() or 0
    doc = ProjectDocument(
        id=str(uuid.uuid4()),
        project_id=project_id,
        position=max_pos + 1,
        name=body.name,
        content="",
        updated_by=user.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return _document_summary(doc, include_content=True)


@router.get("/{project_id}/documents/{document_id}", summary="Get a document with content")
def get_document(
    project_id: str,
    document_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    d = _document_or_404(db, project_id, document_id)
    return _document_summary(d, include_content=True)


@router.patch("/{project_id}/documents/{document_id}", summary="Update a document (name or content)")
def update_document(
    project_id: str,
    document_id: str,
    body: UpdateDocumentBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    d = _document_or_404(db, project_id, document_id)
    prior_content = d.content
    if body.name is not None:
        d.name = body.name
    if body.content is not None and body.content != prior_content:
        # Snapshot prior content into revisions before overwriting.
        if prior_content or d.id:
            rev = ProjectDocumentRevision(
                id=str(uuid.uuid4()),
                document_id=d.id,
                content=prior_content,
                saved_by=d.updated_by,
            )
            db.add(rev)
        d.content = body.content
        d.updated_by = user.id

        try:
            from server.slack_notifier import queue_batched
            queue_batched("latent.document_edited", user, project_id=project_id, document_id=d.id, document_name=d.name)
        except Exception:
            logger.exception("slack queue_batched(latent.document_edited) failed")
    db.commit()
    db.refresh(d)

    # Trim revisions beyond cap
    extras = (
        db.query(ProjectDocumentRevision)
        .filter(ProjectDocumentRevision.document_id == d.id)
        .order_by(ProjectDocumentRevision.saved_at.desc())
        .offset(MAX_REVISIONS_PER_DOCUMENT)
        .all()
    )
    for r in extras:
        db.delete(r)
    if extras:
        db.commit()

    return _document_summary(d, include_content=True)


@router.delete("/{project_id}/documents/{document_id}", status_code=204, summary="Delete a document")
def delete_document(
    project_id: str,
    document_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    d = _document_or_404(db, project_id, document_id)
    db.delete(d)
    db.commit()
    return None


@router.get("/{project_id}/documents/{document_id}/revisions", summary="Document revision history")
def list_document_revisions(
    project_id: str,
    document_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _document_or_404(db, project_id, document_id)
    revs = (
        db.query(ProjectDocumentRevision)
        .filter(ProjectDocumentRevision.document_id == document_id)
        .order_by(ProjectDocumentRevision.saved_at.desc())
        .all()
    )
    return {
        "revisions": [
            {
                "id": r.id,
                "content": r.content,
                "saved_by": r.saved_by,
                "saved_at": r.saved_at.isoformat() if r.saved_at else None,
            }
            for r in revs
        ]
    }


@router.post("/{project_id}/documents/reorder", summary="Reorder documents")
def reorder_documents(
    project_id: str,
    body: ReorderDocumentsBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    docs = db.query(ProjectDocument).filter(ProjectDocument.project_id == project_id).all()
    by_id = {d.id: d for d in docs}
    if set(body.order) != set(by_id.keys()):
        raise HTTPException(status_code=400, detail="Order must contain every document id exactly once")
    for i, did in enumerate(body.order):
        by_id[did].position = i + 1
    db.commit()
    return {"documents": [_document_summary(by_id[did]) for did in body.order]}


# ---------------------------------------------------------------------------
# Links — free-form URLs pinned to a Latent or one of its slots.
# Uses the same Slack permalink recognition as the search engine indexes.
# ---------------------------------------------------------------------------


_SLACK_PERMALINK_RE = re.compile(
    r"^https?://(?:[\w-]+\.)?slack\.com/archives/[A-Z0-9]+/p\d+",
    re.IGNORECASE,
)
_DRIVE_RE = re.compile(r"^https?://drive\.google\.com/", re.IGNORECASE)
_DROPBOX_RE = re.compile(r"^https?://(?:www\.)?dropbox\.com/", re.IGNORECASE)
_SOUNDCLOUD_RE = re.compile(r"^https?://(?:www\.)?soundcloud\.com/", re.IGNORECASE)
_YOUTUBE_RE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/", re.IGNORECASE)
_GITHUB_RE = re.compile(r"^https?://(?:www\.)?github\.com/", re.IGNORECASE)


def _detect_link_kind(url: str) -> str:
    if not url:
        return "link"
    if _SLACK_PERMALINK_RE.match(url):
        return "slack"
    if _DRIVE_RE.match(url):
        return "drive"
    if _DROPBOX_RE.match(url):
        return "dropbox"
    if _SOUNDCLOUD_RE.match(url):
        return "soundcloud"
    if _YOUTUBE_RE.match(url):
        return "youtube"
    if _GITHUB_RE.match(url):
        return "github"
    return "link"


def _link_summary(link: ProjectLink) -> dict:
    return {
        "id": link.id,
        "project_id": link.project_id,
        "slot_id": link.slot_id,
        "url": link.url,
        "label": link.label,
        "kind": link.kind,
        "position": link.position,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "created_by": link.created_by,
    }


class CreateLinkBody(BaseModel):
    url: str = Field(..., min_length=4)
    label: str | None = None
    slot_id: str | None = None
    position: int | None = None


class UpdateLinkBody(BaseModel):
    url: str | None = Field(None, min_length=4)
    label: str | None = None  # empty string clears label


@router.get("/{project_id}/links", summary="List links for a Latent (incl. all its slots)")
def list_links(
    project_id: str,
    slot_id: str | None = Query(None, description="If set, return only links anchored to this slot."),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    q = db.query(ProjectLink).filter(ProjectLink.project_id == project_id)
    if slot_id is not None:
        if slot_id == "":  # explicit empty = top-level only
            q = q.filter(ProjectLink.slot_id.is_(None))
        else:
            q = q.filter(ProjectLink.slot_id == slot_id)
    rows = q.order_by(ProjectLink.position, ProjectLink.created_at).all()
    return {"links": [_link_summary(r) for r in rows]}


@router.post("/{project_id}/links", status_code=201, summary="Add a link to a Latent or slot")
def create_link(
    project_id: str,
    body: CreateLinkBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    resolved_slot_id = None
    if body.slot_id:
        slot = _slot_or_404(db, project_id, body.slot_id)
        resolved_slot_id = slot.id
    if body.position is None:
        max_pos = db.query(func.max(ProjectLink.position)).filter(
            ProjectLink.project_id == project_id,
            ProjectLink.slot_id == resolved_slot_id,
        ).scalar() or 0
        position = max_pos + 1
    else:
        position = body.position
    link = ProjectLink(
        id=str(uuid.uuid4()),
        project_id=project_id,
        slot_id=resolved_slot_id,
        url=body.url.strip(),
        label=(body.label or None),
        kind=_detect_link_kind(body.url.strip()),
        position=position,
        created_by=user.id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return _link_summary(link)


@router.patch("/{project_id}/links/{link_id}", summary="Update a link's URL or label")
def update_link(
    project_id: str,
    link_id: str,
    body: UpdateLinkBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    link = db.query(ProjectLink).filter(
        ProjectLink.id == link_id, ProjectLink.project_id == project_id,
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    if body.url is not None:
        new_url = body.url.strip()
        link.url = new_url
        link.kind = _detect_link_kind(new_url)
    if body.label is not None:
        cleaned = body.label.strip()
        link.label = cleaned or None
    db.commit()
    db.refresh(link)
    return _link_summary(link)


@router.delete("/{project_id}/links/{link_id}", status_code=204, summary="Remove a link")
def delete_link(
    project_id: str,
    link_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    link = db.query(ProjectLink).filter(
        ProjectLink.id == link_id, ProjectLink.project_id == project_id,
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(link)
    db.commit()
    return None


# Companion endpoint mounted on the media router would be ideal, but keeping
# it inside the Latents router avoids touching search_api.py: the search
# detail page calls this directly. Anchored under /api/media to read as
# media-centric, but the implementation belongs with project linkage.
links_for_media_router = APIRouter(prefix="/api/media", tags=["Latents"])


@links_for_media_router.get(
    "/{media_item_id}/latent-links",
    summary="All Latent + slot links related to this media item",
)
def latent_links_for_media(
    media_item_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """For the search detail page: return every link from every Latent and
    every slot this media item is attached to. Each link carries
    project_slug + project_name + slot_label so the UI can group them."""
    rows = (
        db.query(ProjectItem.project_id, ProjectItem.slot_id)
        .filter(ProjectItem.media_item_id == media_item_id)
        .distinct()
        .all()
    )
    if not rows:
        return {"links": []}
    project_ids = {r[0] for r in rows}
    slot_ids = {r[1] for r in rows if r[1] is not None}

    projects_by_id = {
        p.id: p
        for p in db.query(Project).filter(Project.id.in_(project_ids)).all()
    }
    slots_by_id = {
        s.id: s
        for s in db.query(ProjectSlot).filter(ProjectSlot.id.in_(slot_ids)).all()
    } if slot_ids else {}

    # Pull all links for those projects (project-level) and those slots.
    proj_links = (
        db.query(ProjectLink)
        .filter(ProjectLink.project_id.in_(project_ids), ProjectLink.slot_id.is_(None))
        .all()
    )
    slot_links = (
        db.query(ProjectLink)
        .filter(ProjectLink.slot_id.in_(slot_ids))
        .all() if slot_ids else []
    )

    out: list[dict] = []
    for link in proj_links + slot_links:
        p = projects_by_id.get(link.project_id)
        s = slots_by_id.get(link.slot_id) if link.slot_id else None
        out.append({
            **_link_summary(link),
            "project_slug": p.slug if p else None,
            "project_name": p.name if p else None,
            "slot_label": s.label if s else None,
            "slot_position": s.position if s else None,
        })
    out.sort(key=lambda x: (x["project_name"] or "", x.get("slot_position") or 0, x["position"]))
    return {"links": out}
