"""App Runner API: workspaces, app registry, jobs, and output management.

Provides a job queue system for processing media through containerized apps.
Users select media items into workspaces, choose an app, configure parameters,
and submit jobs. A separate worker process polls for pending jobs and runs
them in Docker containers.

## Workflow

1. **Create a workspace** — a persistent cart for collecting media items
2. **Add items** — select media from the search engine across multiple sessions
3. **Pick an app** — each app defines what media types and params it accepts
4. **Submit a job** — input is validated against the app manifest before queuing
5. **Worker runs it** — pulls the Docker image, mounts input files, collects output
6. **Review outputs** — preview results, then index into the search engine or discard
"""

import json
import logging
import os
import random
import tomllib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from auth import get_current_user, get_db, require_admin, require_scope
from models import (
    AppDefinition,
    Job,
    JobOutput,
    MediaItem,
    User,
    Workspace,
    WorkspaceItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

JOB_DATA_DIR = Path(os.environ.get("JOB_DATA_DIR", "/app/job-data"))

# Media type to common extensions mapping for type inference
MEDIA_TYPE_EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".svg"},
    "audio": {".wav", ".mp3", ".flac", ".ogg", ".aac", ".m4a", ".aiff", ".opus"},
    "video": {".mp4", ".webm", ".mkv", ".avi", ".mov", ".wmv", ".flv"},
}


def _infer_media_type(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    for media_type, extensions in MEDIA_TYPE_EXTENSIONS.items():
        if ext in extensions:
            return media_type
    return None


def _parse_manifest(toml_text: str) -> dict:
    return tomllib.loads(toml_text)


def _validate_job_input(
    manifest: dict,
    media_items: list[MediaItem],
    params: dict,
) -> list[dict]:
    """Validate media items and params against an app manifest.

    Returns a list of error dicts. Empty list means valid.
    """
    errors = []
    input_spec = manifest.get("input", {})

    # Validate media types
    allowed_types = input_spec.get("media_types", [])
    if allowed_types:
        for item in media_items:
            if item.media_type not in allowed_types:
                errors.append({
                    "field": "input_items",
                    "message": f"Item '{item.filename}' is {item.media_type}, "
                               f"but this app only accepts: {', '.join(allowed_types)}",
                })

    # Validate count
    min_items = input_spec.get("min_items", 1)
    max_items = input_spec.get("max_items")
    if len(media_items) < min_items:
        errors.append({
            "field": "input_items",
            "message": f"At least {min_items} item(s) required, got {len(media_items)}",
        })
    if max_items and len(media_items) > max_items:
        errors.append({
            "field": "input_items",
            "message": f"At most {max_items} item(s) allowed, got {len(media_items)}",
        })

    # Validate params
    param_specs = manifest.get("params", {})
    for param_name, spec in param_specs.items():
        value = params.get(param_name)

        # Check depends_on — skip validation if dependency not met
        depends_on = spec.get("depends_on")
        if depends_on:
            dep_param = depends_on.get("param")
            dep_value = depends_on.get("value")
            if params.get(dep_param) != dep_value:
                continue

        required = spec.get("required", False)
        if value is None:
            if required:
                errors.append({
                    "field": f"params.{param_name}",
                    "message": f"Required parameter '{param_name}' is missing",
                })
            continue

        param_type = spec.get("type", "string")

        if param_type == "select":
            options = spec.get("options", [])
            if options and value not in options:
                errors.append({
                    "field": f"params.{param_name}",
                    "message": f"Must be one of: {', '.join(str(o) for o in options)}",
                })

        elif param_type == "multi_select":
            options = spec.get("options", [])
            if not isinstance(value, list):
                errors.append({
                    "field": f"params.{param_name}",
                    "message": "Must be a list",
                })
            elif options:
                invalid = [v for v in value if v not in options]
                if invalid:
                    errors.append({
                        "field": f"params.{param_name}",
                        "message": f"Invalid options: {', '.join(str(v) for v in invalid)}",
                    })
            min_sel = spec.get("min_selections")
            if min_sel and isinstance(value, list) and len(value) < min_sel:
                errors.append({
                    "field": f"params.{param_name}",
                    "message": f"At least {min_sel} selection(s) required",
                })

        elif param_type in ("float", "int"):
            try:
                num = float(value) if param_type == "float" else int(value)
            except (ValueError, TypeError):
                errors.append({
                    "field": f"params.{param_name}",
                    "message": f"Must be a valid {param_type}",
                })
                continue
            min_val = spec.get("min")
            max_val = spec.get("max")
            if min_val is not None and num < min_val:
                errors.append({
                    "field": f"params.{param_name}",
                    "message": f"Must be at least {min_val}",
                })
            if max_val is not None and num > max_val:
                errors.append({
                    "field": f"params.{param_name}",
                    "message": f"Must be at most {max_val}",
                })

        elif param_type == "bool":
            if not isinstance(value, bool):
                errors.append({
                    "field": f"params.{param_name}",
                    "message": "Must be true or false",
                })

    return errors


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class WorkspaceCreate(BaseModel):
    """Create a new workspace for collecting media items."""
    name: str = Field(..., description="Display name for the workspace.")


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    item_count: int = 0
    created_at: str
    updated_at: str


class WorkspaceItemsAdd(BaseModel):
    """Add media items to a workspace by ID."""
    media_item_ids: list[str] = Field(..., description="List of media item IDs to add.")


class WorkspaceItemsRemove(BaseModel):
    """Remove media items from a workspace by ID."""
    media_item_ids: list[str] = Field(..., description="List of media item IDs to remove.")


class AppResponse(BaseModel):
    name: str
    display_name: str
    description: str | None
    image: str
    enabled: bool
    manifest: dict
    created_at: str


class AppRegister(BaseModel):
    """Register a new app from a TOML manifest.

    The manifest defines the app's Docker image, accepted input types,
    parameter schema, and execution settings. See the ``apps/`` directory
    for examples.

    ## Manifest format (TOML)

    Top-level fields:

    - ``name`` — unique identifier, lowercase with hyphens
    - ``display_name`` — human-readable name
    - ``description`` — what the app does
    - ``image`` — Docker image URI (e.g. ``ghcr.io/a-u-supply/rottengenizdat:latest``)
    - ``command`` — the command to run inside the container
    - ``timeout_seconds`` — max execution time before the worker kills the container

    ## ``[input]`` section

    - ``media_types`` — list of accepted types: ``["audio"]``, ``["image", "video"]``, etc.
    - ``min_items`` — minimum number of input files required
    - ``max_items`` — maximum number of input files allowed
    - ``allow_random_fill`` — if true, the system can auto-fill remaining input slots
      with random media of the correct type from the search engine

    ## ``[params.*]`` sections

    Each parameter is defined as ``[params.name]`` with these fields:

    - ``type`` — one of: ``select``, ``multi_select``, ``float``, ``int``, ``string``, ``bool``
    - ``label`` — display label for the UI
    - ``description`` — help text explaining the parameter
    - ``required`` — whether the parameter must be provided (default: false)
    - ``default`` — default value if not provided
    - ``options`` — list of valid values (for ``select`` and ``multi_select``)
    - ``min`` / ``max`` — numeric bounds (for ``float`` and ``int``)
    - ``min_selections`` — minimum selections required (for ``multi_select``)
    - ``depends_on`` — conditional: ``{param = "mode", value = "chain"}``

    ## Container contract

    The worker mounts a job directory at ``/work`` inside the container:

    - ``/work/input/`` — input media files
    - ``/work/job.json`` — job metadata (job_id, params, input file list)
    - ``/work/output/`` — where the app must write its results

    ### Exit codes

    - ``0`` — success, output files are in ``/work/output/``
    - ``1`` — expected failure (bad input, unsupported format). Error details in stderr.
    - ``2`` — configuration error (missing model, invalid params). Error details in stderr.
    - Any other code — unexpected crash.

    ### Output manifest (optional)

    If the app writes ``/work/output/manifest.json``, the worker uses it to
    catalog outputs with proper media types and descriptions::

        {
          "outputs": [
            {"filename": "result.wav", "media_type": "audio", "description": "Processed audio"}
          ]
        }

    If no manifest.json is present, media types are inferred from file extensions.

    ### Testing locally

    Build your image and run::

        docker run --rm -v ./test-input:/work/input -v ./test-output:/work/output your-image:latest

    """
    manifest_toml: str = Field(
        ...,
        description="The full app manifest in TOML format. See the docstring above for the schema.",
    )


class JobCreate(BaseModel):
    """Submit a job for processing.

    The input items and params are validated against the app's manifest before
    the job is queued. If validation fails, a 422 response is returned with
    a structured list of errors.

    If the app allows ``random_fill`` and you provide fewer items than
    ``min_items``, set ``random_fill=true`` and the system will randomly
    select additional items of the correct media type from the search engine.
    """
    app_name: str = Field(..., description="Name of the registered app to run.")
    media_item_ids: list[str] = Field(..., description="List of media item IDs to process.")
    params: dict = Field(default_factory=dict, description="App-specific parameters. Validated against the app manifest.")
    random_fill: bool = Field(False, description="If true, auto-fill remaining input slots with random media of the correct type.")
    priority: int = Field(100, description="Job priority. Lower numbers run first. Default is 100.")


class JobResponse(BaseModel):
    id: str
    app_name: str
    status: str
    params: dict
    input_item_count: int
    priority: int
    created_at: str
    started_at: str | None
    completed_at: str | None
    error_message: str | None
    retry_count: int
    log_tail: str | None
    output_count: int = 0


class JobOutputResponse(BaseModel):
    id: str
    filename: str
    media_type: str | None
    file_size_bytes: int | None
    indexed: bool
    media_item_id: str | None


class ValidationErrorResponse(BaseModel):
    detail: str = "Validation failed"
    errors: list[dict] = Field(..., description="List of ``{field, message}`` error objects.")


class ValidateRequest(BaseModel):
    """Validate input items and params against an app manifest without creating a job."""
    media_item_ids: list[str] = Field(..., description="Media item IDs to validate.")
    params: dict = Field(default_factory=dict, description="App-specific parameters to validate.")


class BulkIndexRequest(BaseModel):
    """Index selected job outputs into the search engine."""
    output_ids: list[str] = Field(..., description="List of output IDs to index.")


# ---------------------------------------------------------------------------
# Workspace endpoints
# ---------------------------------------------------------------------------


@router.post("/workspaces", tags=["Workspaces"], summary="Create a workspace")
def create_workspace(
    body: WorkspaceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new workspace for collecting media items before processing.

    Workspaces are like shopping carts — add items from the search engine
    across multiple sessions, then submit them to an app for processing.
    """
    ws = Workspace(name=body.name, created_by=user.id)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return WorkspaceResponse(
        id=ws.id, name=ws.name, item_count=0,
        created_at=ws.created_at.isoformat(), updated_at=ws.updated_at.isoformat(),
    )


@router.get("/workspaces", tags=["Workspaces"], summary="List your workspaces")
def list_workspaces(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all workspaces owned by the current user, with item counts."""
    rows = (
        db.query(Workspace, func.count(WorkspaceItem.id).label("item_count"))
        .outerjoin(WorkspaceItem)
        .filter(Workspace.created_by == user.id)
        .group_by(Workspace.id)
        .order_by(Workspace.updated_at.desc())
        .all()
    )
    return [
        WorkspaceResponse(
            id=ws.id, name=ws.name, item_count=count,
            created_at=ws.created_at.isoformat(), updated_at=ws.updated_at.isoformat(),
        )
        for ws, count in rows
    ]


@router.get("/workspaces/{workspace_id}", tags=["Workspaces"], summary="Get workspace details")
def get_workspace(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a workspace with its item count."""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id, Workspace.created_by == user.id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    count = db.query(func.count(WorkspaceItem.id)).filter(WorkspaceItem.workspace_id == ws.id).scalar()
    return WorkspaceResponse(
        id=ws.id, name=ws.name, item_count=count,
        created_at=ws.created_at.isoformat(), updated_at=ws.updated_at.isoformat(),
    )


@router.put("/workspaces/{workspace_id}", tags=["Workspaces"], summary="Rename workspace")
def rename_workspace(
    workspace_id: str,
    body: WorkspaceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename an existing workspace."""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id, Workspace.created_by == user.id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    ws.name = body.name
    db.commit()
    return {"ok": True}


@router.delete("/workspaces/{workspace_id}", tags=["Workspaces"], summary="Delete workspace")
def delete_workspace(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a workspace and all its item associations.

    This does not delete the media items themselves — just removes them from the workspace.
    """
    ws = db.query(Workspace).filter(Workspace.id == workspace_id, Workspace.created_by == user.id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    db.delete(ws)
    db.commit()
    return {"ok": True}


@router.post("/workspaces/{workspace_id}/items", tags=["Workspaces"], summary="Add items to workspace")
def add_workspace_items(
    workspace_id: str,
    body: WorkspaceItemsAdd,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add media items to a workspace. Duplicates are silently ignored."""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id, Workspace.created_by == user.id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    existing = set(
        row[0] for row in
        db.query(WorkspaceItem.media_item_id)
        .filter(WorkspaceItem.workspace_id == ws.id, WorkspaceItem.media_item_id.in_(body.media_item_ids))
        .all()
    )

    added = 0
    for mid in body.media_item_ids:
        if mid not in existing:
            db.add(WorkspaceItem(workspace_id=ws.id, media_item_id=mid))
            added += 1

    db.commit()
    return {"ok": True, "added": added, "already_present": len(body.media_item_ids) - added}


@router.delete("/workspaces/{workspace_id}/items", tags=["Workspaces"], summary="Remove items from workspace")
def remove_workspace_items(
    workspace_id: str,
    body: WorkspaceItemsRemove,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove specific media items from a workspace."""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id, Workspace.created_by == user.id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    deleted = (
        db.query(WorkspaceItem)
        .filter(WorkspaceItem.workspace_id == ws.id, WorkspaceItem.media_item_id.in_(body.media_item_ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "removed": deleted}


@router.get("/workspaces/{workspace_id}/items", tags=["Workspaces"], summary="List workspace items")
def list_workspace_items(
    workspace_id: str,
    page: int = Query(1, ge=1, description="Page number."),
    per_page: int = Query(50, ge=1, le=200, description="Items per page."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List media items in a workspace with basic metadata, paginated."""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id, Workspace.created_by == user.id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    total = db.query(func.count(WorkspaceItem.id)).filter(WorkspaceItem.workspace_id == ws.id).scalar()
    rows = (
        db.query(WorkspaceItem)
        .options(joinedload(WorkspaceItem.media_item))
        .filter(WorkspaceItem.workspace_id == ws.id)
        .order_by(WorkspaceItem.added_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    items = []
    for wi in rows:
        mi = wi.media_item
        items.append({
            "workspace_item_id": wi.id,
            "media_item_id": mi.id,
            "filename": mi.filename,
            "media_type": mi.media_type,
            "file_size_bytes": mi.file_size_bytes,
            "added_at": wi.added_at.isoformat(),
        })

    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ---------------------------------------------------------------------------
# App registry endpoints
# ---------------------------------------------------------------------------


@router.get("/apps", tags=["Apps"], summary="List available apps")
def list_apps(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all enabled apps available for processing.

    Each app defines what media types it accepts, what parameters it takes,
    and how to run it. Use the manifest to build a parameter form in the UI.
    """
    apps = db.query(AppDefinition).filter(AppDefinition.enabled == True).order_by(AppDefinition.display_name).all()
    return [
        AppResponse(
            name=a.name, display_name=a.display_name, description=a.description,
            image=a.image, enabled=a.enabled, manifest=_parse_manifest(a.manifest),
            created_at=a.created_at.isoformat(),
        )
        for a in apps
    ]


@router.get("/apps/{name}", tags=["Apps"], summary="Get app details")
def get_app(
    name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get full details for a registered app, including its parsed manifest."""
    app = db.query(AppDefinition).filter(AppDefinition.name == name).first()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    return AppResponse(
        name=app.name, display_name=app.display_name, description=app.description,
        image=app.image, enabled=app.enabled, manifest=_parse_manifest(app.manifest),
        created_at=app.created_at.isoformat(),
    )


@router.post("/apps", tags=["Apps"], summary="Register a new app")
def register_app(
    body: AppRegister,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Register a new app from a TOML manifest. Admin only.

    The manifest is parsed and validated, then stored. The app becomes
    available in the "Process with..." dropdown immediately.
    """
    try:
        manifest = _parse_manifest(body.manifest_toml)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid TOML: {e}")

    required = ["name", "display_name", "image"]
    for field in required:
        if field not in manifest:
            raise HTTPException(status_code=422, detail=f"Manifest missing required field: {field}")

    name = manifest["name"]
    if db.query(AppDefinition).filter(AppDefinition.name == name).first():
        raise HTTPException(status_code=409, detail=f"App '{name}' already exists")

    app = AppDefinition(
        name=name,
        display_name=manifest["display_name"],
        description=manifest.get("description"),
        image=manifest["image"],
        manifest=body.manifest_toml,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return AppResponse(
        name=app.name, display_name=app.display_name, description=app.description,
        image=app.image, enabled=app.enabled, manifest=manifest,
        created_at=app.created_at.isoformat(),
    )


@router.put("/apps/{name}", tags=["Apps"], summary="Update app manifest")
def update_app(
    name: str,
    body: AppRegister,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replace an app's manifest. Admin only."""
    app = db.query(AppDefinition).filter(AppDefinition.name == name).first()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")

    try:
        manifest = _parse_manifest(body.manifest_toml)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid TOML: {e}")

    app.display_name = manifest.get("display_name", app.display_name)
    app.description = manifest.get("description", app.description)
    app.image = manifest.get("image", app.image)
    app.manifest = body.manifest_toml
    db.commit()
    return {"ok": True}


@router.delete("/apps/{name}", tags=["Apps"], summary="Disable or remove an app")
def delete_app(
    name: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Disable an app so it no longer appears in the UI. Admin only.

    Existing jobs that used this app are not affected.
    """
    app = db.query(AppDefinition).filter(AppDefinition.name == name).first()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    app.enabled = False
    db.commit()
    return {"ok": True}


@router.post("/apps/{name}/validate", tags=["Apps"], summary="Validate inputs against app manifest",
             responses={422: {"model": ValidationErrorResponse}})
def validate_app_input(
    name: str,
    body: ValidateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dry-run validation: check if the given items and params would pass
    the app's manifest requirements. Returns 200 if valid, 422 with errors if not.
    """
    app = db.query(AppDefinition).filter(AppDefinition.name == name).first()
    if not app:
        raise HTTPException(status_code=404, detail="App not found")

    manifest = _parse_manifest(app.manifest)
    items = db.query(MediaItem).filter(MediaItem.id.in_(body.media_item_ids)).all()
    if len(items) != len(body.media_item_ids):
        found_ids = {i.id for i in items}
        missing = [mid for mid in body.media_item_ids if mid not in found_ids]
        raise HTTPException(status_code=422, detail=f"Media items not found: {', '.join(missing)}")

    errors = _validate_job_input(manifest, items, body.params)
    if errors:
        raise HTTPException(status_code=422, detail={"detail": "Validation failed", "errors": errors})

    return {"ok": True, "item_count": len(items)}


# ---------------------------------------------------------------------------
# Job endpoints
# ---------------------------------------------------------------------------


@router.post("/jobs", tags=["Jobs"], summary="Submit a job",
             responses={422: {"model": ValidationErrorResponse}})
def create_job(
    body: JobCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit a job for processing.

    The input items and params are validated against the app's manifest.
    If validation fails, a 422 response is returned with structured errors.

    If ``random_fill=true`` and you have fewer items than the app requires,
    the system fills remaining slots with random media items of the correct
    type from the search engine. The response shows which items were added.
    """
    app = db.query(AppDefinition).filter(AppDefinition.name == body.app_name, AppDefinition.enabled == True).first()
    if not app:
        raise HTTPException(status_code=404, detail=f"App '{body.app_name}' not found or disabled")

    manifest = _parse_manifest(app.manifest)
    input_spec = manifest.get("input", {})

    # Fetch media items
    items = db.query(MediaItem).filter(MediaItem.id.in_(body.media_item_ids)).all()
    if len(items) != len(body.media_item_ids):
        found_ids = {i.id for i in items}
        missing = [mid for mid in body.media_item_ids if mid not in found_ids]
        raise HTTPException(status_code=422, detail=f"Media items not found: {', '.join(missing)}")

    # Random fill if requested and allowed
    filled_ids = []
    min_items = input_spec.get("min_items", 1)
    if body.random_fill and input_spec.get("allow_random_fill") and len(items) < min_items:
        allowed_types = input_spec.get("media_types", [])
        need = min_items - len(items)
        existing_ids = {i.id for i in items}

        candidates = (
            db.query(MediaItem)
            .filter(MediaItem.media_type.in_(allowed_types), MediaItem.id.notin_(existing_ids))
            .all()
        )
        if len(candidates) < need:
            raise HTTPException(
                status_code=422,
                detail=f"Not enough {', '.join(allowed_types)} items to fill. Need {need} more, only {len(candidates)} available.",
            )
        chosen = random.sample(candidates, need)
        items.extend(chosen)
        filled_ids = [c.id for c in chosen]

    # Validate
    errors = _validate_job_input(manifest, items, body.params)
    if errors:
        raise HTTPException(status_code=422, detail={"detail": "Validation failed", "errors": errors})

    all_item_ids = [i.id for i in items]

    job = Job(
        app_name=body.app_name,
        input_items=json.dumps(all_item_ids),
        params=json.dumps(body.params),
        priority=body.priority,
        created_by=user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    result = {
        "id": job.id,
        "app_name": job.app_name,
        "status": job.status,
        "input_item_count": len(all_item_ids),
        "priority": job.priority,
        "created_at": job.created_at.isoformat(),
    }
    if filled_ids:
        result["random_filled"] = filled_ids

    return result


@router.get("/jobs", tags=["Jobs"], summary="List jobs")
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status: pending, running, completed, failed, cancelled."),
    app_name: Optional[str] = Query(None, description="Filter by app name."),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List jobs, newest first. Filterable by status and app name."""
    q = db.query(Job).options(joinedload(Job.outputs))
    if status:
        q = q.filter(Job.status == status)
    if app_name:
        q = q.filter(Job.app_name == app_name)

    total = q.count()
    jobs = q.order_by(Job.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "jobs": [
            JobResponse(
                id=j.id, app_name=j.app_name, status=j.status,
                params=json.loads(j.params), input_item_count=len(json.loads(j.input_items)),
                priority=j.priority, created_at=j.created_at.isoformat(),
                started_at=j.started_at.isoformat() if j.started_at else None,
                completed_at=j.completed_at.isoformat() if j.completed_at else None,
                error_message=j.error_message, retry_count=j.retry_count,
                log_tail=j.log_tail, output_count=len(j.outputs),
            )
            for j in jobs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/jobs/{job_id}", tags=["Jobs"], summary="Get job details")
def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get full details for a job including status, params, log output, and output files."""
    job = db.query(Job).options(joinedload(Job.outputs)).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job": JobResponse(
            id=job.id, app_name=job.app_name, status=job.status,
            params=json.loads(job.params), input_item_count=len(json.loads(job.input_items)),
            priority=job.priority, created_at=job.created_at.isoformat(),
            started_at=job.started_at.isoformat() if job.started_at else None,
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            error_message=job.error_message, retry_count=job.retry_count,
            log_tail=job.log_tail, output_count=len(job.outputs),
        ),
        "input_items": json.loads(job.input_items),
        "outputs": [
            JobOutputResponse(
                id=o.id, filename=o.filename, media_type=o.media_type,
                file_size_bytes=o.file_size_bytes, indexed=o.indexed,
                media_item_id=o.media_item_id,
            )
            for o in job.outputs
        ],
    }


@router.post("/jobs/{job_id}/cancel", tags=["Jobs"], summary="Cancel a job")
def cancel_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel a pending or running job.

    Pending jobs are cancelled immediately. Running jobs are marked for
    cancellation — the worker will stop the container on its next check.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel a {job.status} job")
    job.status = "cancelled"
    db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/retry", tags=["Jobs"], summary="Retry a failed job")
def retry_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-queue a failed job for another attempt.

    Resets the job status to ``pending`` and increments the retry count.
    The worker will pick it up on its next poll.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "failed":
        raise HTTPException(status_code=400, detail=f"Can only retry failed jobs, this one is {job.status}")
    if job.retry_count >= job.max_retries:
        raise HTTPException(status_code=400, detail=f"Max retries ({job.max_retries}) reached")
    job.status = "pending"
    job.error_message = None
    job.log_tail = None
    job.started_at = None
    job.completed_at = None
    db.commit()
    return {"ok": True}


@router.delete("/jobs/{job_id}", tags=["Jobs"], summary="Delete a job and its outputs")
def delete_job(
    job_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Permanently delete a job and all its output files. Admin only."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Cannot delete a running job — cancel it first")

    # Delete output files from disk
    job_dir = JOB_DATA_DIR / job.id
    if job_dir.is_dir():
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

    db.delete(job)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Job output endpoints
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/outputs", tags=["Job Outputs"], summary="List job outputs")
def list_job_outputs(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all output files produced by a job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    outputs = db.query(JobOutput).filter(JobOutput.job_id == job_id).order_by(JobOutput.filename).all()
    return [
        JobOutputResponse(
            id=o.id, filename=o.filename, media_type=o.media_type,
            file_size_bytes=o.file_size_bytes, indexed=o.indexed,
            media_item_id=o.media_item_id,
        )
        for o in outputs
    ]


@router.get("/jobs/{job_id}/outputs/{output_id}/download", tags=["Job Outputs"], summary="Download an output file")
def download_output(
    job_id: str,
    output_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Download a single output file from a completed job."""
    from fastapi.responses import FileResponse

    output = db.query(JobOutput).filter(JobOutput.id == output_id, JobOutput.job_id == job_id).first()
    if not output:
        raise HTTPException(status_code=404, detail="Output not found")

    file_path = JOB_DATA_DIR / job_id / "output" / output.file_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Output file missing from disk")

    return FileResponse(file_path, filename=output.filename)


@router.post("/jobs/{job_id}/outputs/{output_id}/index", tags=["Job Outputs"],
             summary="Index an output into the search engine")
def index_output(
    output_id: str,
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Promote a job output to the search engine.

    This copies the file to the search media directory, creates a ``media_item``
    record, runs metadata extraction, and syncs to Meilisearch. The output is
    tagged with ``job:<app_name>`` for discoverability.
    """
    output = db.query(JobOutput).filter(JobOutput.id == output_id, JobOutput.job_id == job_id).first()
    if not output:
        raise HTTPException(status_code=404, detail="Output not found")
    if output.indexed:
        raise HTTPException(status_code=400, detail="Already indexed")

    job = db.query(Job).filter(Job.id == job_id).first()
    source_path = JOB_DATA_DIR / job_id / "output" / output.file_path
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Output file missing from disk")

    # Import here to avoid circular imports
    import hashlib
    import shutil
    import uuid
    from datetime import datetime, timezone

    from models import MediaSource, MediaTag

    search_media_dir = Path(os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data"))
    media_type = output.media_type or _infer_media_type(output.filename)
    if not media_type:
        media_type = "audio"  # Default for unknown

    # Compute sha256
    sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    # Check for duplicate
    existing = db.query(MediaItem).filter(MediaItem.sha256 == sha256).first()
    if existing:
        output.indexed = True
        output.media_item_id = existing.id
        db.commit()
        return {"ok": True, "media_item_id": existing.id, "duplicate": True}

    # Copy file to search media dir
    now = datetime.now(timezone.utc)
    dest_dir = search_media_dir / media_type / now.strftime("%Y-%m")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{sha256[:8]}_{output.filename}"
    dest_path = dest_dir / dest_name
    shutil.copy2(source_path, dest_path)

    # Create media item
    import mimetypes as mt
    mime, _ = mt.guess_type(output.filename)

    media_item = MediaItem(
        sha256=sha256,
        filename=output.filename,
        file_path=str(dest_path),
        media_type=media_type,
        file_size_bytes=source_path.stat().st_size,
        mime_type=mime or "application/octet-stream",
    )
    db.add(media_item)

    # Add source record
    source = MediaSource(
        media_item_id=media_item.id,
        source_type="job_output",
        source_metadata=json.dumps({"job_id": job_id, "app_name": job.app_name}),
    )
    db.add(source)

    # Tag with app name
    tag = MediaTag(media_item_id=media_item.id, tag=f"job:{job.app_name}", tagged_by=user.id)
    db.add(tag)

    output.indexed = True
    output.media_item_id = media_item.id
    db.commit()

    # Run extraction + Meilisearch sync in background
    try:
        from extraction import run_extraction_async
        run_extraction_async(media_item.id, str(dest_path), media_type, db)
    except Exception:
        logger.exception("Extraction failed for indexed output %s", output.id)

    return {"ok": True, "media_item_id": media_item.id}


@router.post("/jobs/{job_id}/outputs/index", tags=["Job Outputs"], summary="Bulk index outputs")
def bulk_index_outputs(
    job_id: str,
    body: BulkIndexRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Index multiple job outputs into the search engine at once."""
    results = []
    for output_id in body.output_ids:
        try:
            result = index_output(output_id=output_id, job_id=job_id, user=user, db=db)
            results.append({"output_id": output_id, **result})
        except HTTPException as e:
            results.append({"output_id": output_id, "error": e.detail})
    return {"results": results}


@router.delete("/jobs/{job_id}/outputs", tags=["Job Outputs"], summary="Discard all outputs")
def discard_outputs(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete all output files for a job from disk and database.

    Outputs that have already been indexed into the search engine are not
    affected — only the job output records and files are removed.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    output_dir = JOB_DATA_DIR / job_id / "output"
    if output_dir.is_dir():
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)

    db.query(JobOutput).filter(JobOutput.job_id == job_id).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}
