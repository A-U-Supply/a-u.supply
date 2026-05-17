"""Latents — admin-only pre-release workspace API.

CRUD for projects, slots, items, documents, document revisions, and pins.
Lemmy-backed thread CRUD lives in `server.threads_api`.

All endpoints require admin auth. Slot auto-labels are derived from the parent
project's `kind` when a label isn't supplied.
"""

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


def _project_summary(p: Project) -> dict:
    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "kind": p.kind,
        "status": p.status,
        "hero_media_item_id": p.hero_media_item_id,
        "lemmy_community_id": p.lemmy_community_id,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _slot_summary(s: ProjectSlot, pins: dict[str, str] | None = None, thread_count: int = 0) -> dict:
    pin_map = pins or {}
    return {
        "id": s.id,
        "project_id": s.project_id,
        "position": s.position,
        "label": s.label,
        "status": s.status,
        "notes": s.notes,
        "notes_updated_at": s.notes_updated_at.isoformat() if s.notes_updated_at else None,
        "pinned": pin_map,
        "thread_count": int(thread_count),
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
        "media": {
            "id": mi.id,
            "filename": mi.filename,
            "media_type": mi.media_type,
            "mime_type": mi.mime_type,
            "file_size_bytes": mi.file_size_bytes,
        } if mi else None,
    }


def _pin_map_for_slot(db: Session, slot_id: str) -> dict[str, str]:
    pins = db.query(SlotPrimaryPin).filter(SlotPrimaryPin.slot_id == slot_id).all()
    return {p.media_type: p.media_item_id for p in pins}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


VALID_KINDS = {"album", "video", "zine", "other"}
VALID_PROJECT_STATUSES = {"forming", "developing", "fixing", "abandoned"}
VALID_SLOT_STATUSES = {"forming", "developing", "fixed"}
VALID_PIN_TYPES = {"image", "audio", "video", "session"}


class CreateProjectBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    kind: str = Field("other")


class UpdateProjectBody(BaseModel):
    name: str | None = None
    kind: str | None = None
    status: str | None = None
    hero_media_item_id: str | None = None


class CreateSlotBody(BaseModel):
    label: str | None = None
    position: int | None = None


class UpdateSlotBody(BaseModel):
    label: str | None = None
    status: str | None = None
    notes: str | None = None
    repo_id: str | None = None       # set to "" to clear
    repo_path: str | None = None     # set to "" to clear
    repo_ref: str | None = None      # set to "" to clear (falls back to repo default branch)
    run_command: str | None = None   # set to "" to clear (falls back to `python <path>`)


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
    slot_thread_counts: dict[str, int] = {}
    if slots:
        slot_ids = [s.id for s in slots]
        rows = (
            db.query(Thread.anchor_id, func.count(Thread.id))
            .filter(Thread.anchor_type == "slot", Thread.anchor_id.in_(slot_ids))
            .group_by(Thread.anchor_id)
            .all()
        )
        slot_thread_counts = {aid: int(c) for aid, c in rows}
    return {
        **_project_summary(p),
        "slots": [_slot_summary(s, slot_pins.get(s.id), slot_thread_counts.get(s.id, 0)) for s in slots],
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
    if body.kind is not None:
        if body.kind not in VALID_KINDS:
            raise HTTPException(status_code=400, detail=f"Invalid kind '{body.kind}'")
        p.kind = body.kind
    if body.status is not None:
        if body.status not in VALID_PROJECT_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status '{body.status}'")
        p.status = body.status
    if body.hero_media_item_id is not None:
        if body.hero_media_item_id:
            mi = db.query(MediaItem).filter(MediaItem.id == body.hero_media_item_id).first()
            if not mi:
                raise HTTPException(status_code=404, detail="hero_media_item_id refers to a missing media item")
            p.hero_media_item_id = body.hero_media_item_id
        else:
            p.hero_media_item_id = None
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
    return {"slots": [_slot_summary(s, _pin_map_for_slot(db, s.id)) for s in slots]}


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
    return _slot_summary(slot, _pin_map_for_slot(db, slot.id))


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
    db.commit()
    db.refresh(s)
    return _slot_summary(s, _pin_map_for_slot(db, s.id))


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
    return {"slots": [_slot_summary(by_id[sid], _pin_map_for_slot(db, sid)) for sid in body.order]}


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
                sync_media_item(db, pi.media_item)
    except Exception:
        logger.exception("meili re-sync after attach failed")

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
    pi.slot_id = new_slot_id
    db.commit()
    db.refresh(pi)
    return _item_summary(pi)


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
    db.delete(pi)
    db.commit()
    # Refresh the search doc — `project_ids` array shrinks.
    try:
        from server.search_client import sync_media_item
        mi = db.query(MediaItem).filter(MediaItem.id == mi_id).first()
        if mi:
            sync_media_item(db, mi)
    except Exception:
        logger.exception("meili re-sync after detach failed")
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
