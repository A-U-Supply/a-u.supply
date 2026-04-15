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

from auth import get_db, require_scope
from models import (
    AppDefinition,
    Job,
    JobOutput,
    MediaAudioMeta,
    MediaItem,
    MediaVideoMeta,
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
            dep_values = depends_on.get("values", [depends_on["value"]] if "value" in depends_on else [])
            if params.get(dep_param) not in dep_values:
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
            if not options and "option_groups" in spec:
                for group in spec["option_groups"]:
                    for opt in group.get("options", []):
                        options.append(opt["value"] if isinstance(opt, dict) else opt)
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
    - ``flag`` — CLI flag the worker uses when invoking the container
      (e.g. ``"-m"`` for model). Bool params: flag included when true, omitted
      when false. Params at their default value are omitted.

    ## Command building

    The worker builds the full ``docker run`` command from the manifest:

    1. ``command`` — subcommand (e.g. ``rave``)
    2. Input files — passed as positional args (``input_mode = "positional"``,
       the default) or via a named flag (``input_mode = "flag"``, set ``input_flag``)
    3. Params — each param with a ``flag`` field is mapped to a CLI flag + value
    4. ``output_flag`` — static string appended (e.g. ``"-o /work/output/output.wav"``)

    Example result::

        rotten rave /work/input/drums.wav -m vintage -t 1.5 -r -o /work/output/output.wav

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


class IndexMetadata(BaseModel):
    """Optional metadata to attach when indexing job outputs.

    When indexing outputs, you can provide a description, additional tags, and
    an output index override. If omitted, defaults are inferred from the app
    manifest and job parameters:

    - ``output_index``: defaults to the manifest's ``[output].index`` value
    - ``description``: auto-generated summary from job context
    - ``tags``: auto-generated from app name, recipe, model, etc.

    Auto-generated tags include ``app:<name>``, ``recipe:<name>`` (if applicable),
    ``model:<name>`` (if applicable), and ``index:<name>`` (if output_index is set).
    User-supplied tags are added alongside auto-generated ones.
    """
    description: str | None = Field(None, description="Free-text description. If omitted, an auto-generated summary is used.")
    tags: list[str] = Field(default_factory=list, description="Additional tags to apply alongside auto-generated tags.")
    output_index: str | None = Field(None, description="Override the output index from the app manifest's ``[output].index``.")


class BulkIndexRequest(BaseModel):
    """Index selected job outputs into the search engine.

    All metadata fields are applied uniformly to every output in the batch.
    See ``IndexMetadata`` for field descriptions.
    """
    output_ids: list[str] = Field(..., description="List of output IDs to index.")
    description: str | None = Field(None, description="Free-text description for all outputs.")
    tags: list[str] = Field(default_factory=list, description="Additional tags for all outputs.")
    output_index: str | None = Field(None, description="Override output index for all outputs.")


# ---------------------------------------------------------------------------
# Workspace endpoints
# ---------------------------------------------------------------------------


@router.post("/workspaces", tags=["Workspaces"], summary="Create a workspace")
def create_workspace(
    body: WorkspaceCreate,
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
    """List media items in a workspace with basic metadata, paginated."""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id, Workspace.created_by == user.id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    total = db.query(func.count(WorkspaceItem.id)).filter(WorkspaceItem.workspace_id == ws.id).scalar()
    rows = (
        db.query(WorkspaceItem)
        .options(
            joinedload(WorkspaceItem.media_item).joinedload(MediaItem.audio_meta),
            joinedload(WorkspaceItem.media_item).joinedload(MediaItem.video_meta),
        )
        .filter(WorkspaceItem.workspace_id == ws.id)
        .order_by(WorkspaceItem.added_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    items = []
    for wi in rows:
        mi = wi.media_item
        dur = None
        if mi.media_type == "audio" and mi.audio_meta:
            dur = mi.audio_meta.duration_seconds
        elif mi.media_type == "video" and mi.video_meta:
            dur = mi.video_meta.duration_seconds
        entry: dict = {
            "workspace_item_id": wi.id,
            "media_item_id": mi.id,
            "filename": mi.filename,
            "media_type": mi.media_type,
            "file_size_bytes": mi.file_size_bytes,
            "added_at": wi.added_at.isoformat(),
        }
        if dur is not None:
            entry["duration_seconds"] = dur
        items.append(entry)

    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ---------------------------------------------------------------------------
# App registry endpoints
# ---------------------------------------------------------------------------


@router.get("/apps", tags=["Apps"], summary="List available apps")
def list_apps(
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    user = auth[0]
    """Register a new app from a TOML manifest. Admin only.

    The manifest is parsed and validated, then stored. The app becomes
    available in the "Process with..." dropdown immediately.

    **Manifest format** (TOML)::

        name = "my-app"
        display_name = "My App"
        description = "Short description."
        long_description = \"\"\"Detailed multi-line description shown in the config dialog.\"\"\"
        image = "ghcr.io/org/image:latest"
        command = "process"
        timeout_seconds = 600
        input_mode = "positional"
        output_flag = "-o /work/output/output.wav"

        [command_map]                   # Optional: dynamic command based on a param
        param = "processing_mode"
        [command_map.values]
        single_pass = "rave"
        recipe = "recipe run"

        [input]
        min_items = 1
        max_items = 10
        media_types = ["audio", "video"]

        [output]                        # Optional: output indexing config
        index = "my-outputs"            # Default output_index for indexed outputs

        [params.my_param]
        type = "select"                 # select, float, int, bool, string
        label = "My Param"
        description = "Help text."
        options = ["a", "b", "c"]
        default = "a"
        required = true
        flag = "-m"                     # CLI flag (omit for UI-only params)
        position = 1                    # Positional arg instead of flag
        value_template = "/path/{}.ext" # Transform value before passing to CLI
        depends_on = { param = "mode", value = "advanced" }  # Conditional visibility
        no_flag = true                  # UI-only, never passed to CLI

    See ``/docs`` for the full API reference and the ``apps/*.toml`` files
    in the repository for working examples.
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
    auth: tuple[User, str] = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
    """Get full details for a job including status, params, log output, and output files."""
    job = db.query(Job).options(joinedload(Job.outputs)).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Load manifest output config and resolve input filenames
    app_def = db.query(AppDefinition).filter(AppDefinition.name == job.app_name).first()
    manifest = _parse_manifest(app_def.manifest) if app_def else {}
    output_config = manifest.get("output", {})

    input_ids = json.loads(job.input_items)
    input_filenames = [
        r[0] for r in db.query(MediaItem.filename).filter(MediaItem.id.in_(input_ids)).all()
    ] if input_ids else []

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
        "input_filenames": input_filenames,
        "manifest_output": output_config,
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("admin")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
    """Download a single output file from a completed job."""
    from fastapi.responses import FileResponse

    output = db.query(JobOutput).filter(JobOutput.id == output_id, JobOutput.job_id == job_id).first()
    if not output:
        raise HTTPException(status_code=404, detail="Output not found")

    file_path = JOB_DATA_DIR / job_id / "output" / output.file_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Output file missing from disk")

    return FileResponse(file_path, filename=output.filename)


def _do_index_output(output_id: str, job_id: str, user, db: Session,
                     metadata: IndexMetadata | None = None) -> dict:
    """Internal: index a single job output into the search engine.

    Copies the file to the search media directory, creates a ``MediaItem``
    with enriched metadata, runs extraction, and syncs to Meilisearch.

    The app manifest's ``[output].index`` provides the default output index.
    Auto-generated tags include ``app:<name>``, and conditionally
    ``recipe:<name>``, ``model:<name>``, ``index:<name>``.

    Indexed fields in Meilisearch: ``output_index``, ``job_app``, ``job_recipe``,
    ``job_model``, ``job_runtime_seconds``, ``job_input_count`` — all filterable.
    """
    output = db.query(JobOutput).filter(JobOutput.id == output_id, JobOutput.job_id == job_id).first()
    if not output:
        raise HTTPException(status_code=404, detail="Output not found")
    if output.indexed:
        # If the linked media item was deleted, allow re-indexing
        if output.media_item_id and not db.query(MediaItem).filter(MediaItem.id == output.media_item_id).first():
            output.indexed = False
            output.media_item_id = None
            db.flush()
        else:
            raise HTTPException(status_code=400, detail="Already indexed")

    job = db.query(Job).filter(Job.id == job_id).first()
    source_path = JOB_DATA_DIR / job_id / "output" / output.file_path
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Output file missing from disk")

    import hashlib
    import shutil
    from datetime import datetime, timezone

    from models import MediaSource, MediaTag

    # Load manifest for output config
    app_def = db.query(AppDefinition).filter(AppDefinition.name == job.app_name).first()
    manifest = _parse_manifest(app_def.manifest) if app_def else {}
    output_config = manifest.get("output", {})

    # Resolve output_index: user override > manifest default
    resolved_index = (metadata.output_index if metadata and metadata.output_index
                      else output_config.get("index"))

    search_media_dir = Path(os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data"))
    media_type = output.media_type or _infer_media_type(output.filename)
    if not media_type:
        media_type = "audio"

    sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()

    # Dedup
    existing = db.query(MediaItem).filter(MediaItem.sha256 == sha256).first()
    if existing:
        output.indexed = True
        output.media_item_id = existing.id
        db.commit()
        return {"ok": True, "media_item_id": existing.id, "duplicate": True}

    # Copy file
    now = datetime.now(timezone.utc)
    dest_dir = search_media_dir / media_type / now.strftime("%Y-%m")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{sha256[:8]}_{output.filename}"
    dest_path = dest_dir / dest_name
    shutil.copy2(source_path, dest_path)

    # Build enriched source_metadata
    params = json.loads(job.params)
    input_ids = json.loads(job.input_items)
    input_filenames = [
        r[0] for r in db.query(MediaItem.filename).filter(MediaItem.id.in_(input_ids)).all()
    ] if input_ids else []

    runtime_seconds = None
    if job.started_at and job.completed_at:
        runtime_seconds = round((job.completed_at - job.started_at).total_seconds(), 1)

    source_meta = {
        "job_id": job_id,
        "app_name": job.app_name,
        "recipe": params.get("recipe"),
        "model": params.get("model"),
        "processing_mode": params.get("processing_mode"),
        "input_combination": params.get("input_combination"),
        "runtime_seconds": runtime_seconds,
        "input_count": len(input_ids),
        "input_filenames": input_filenames,
        "params": params,
    }

    # Auto-generate description
    parts = [app_def.display_name if app_def else job.app_name]
    if params.get("recipe"):
        parts.append(params["recipe"])
    elif params.get("model"):
        parts.append(params["model"])
    if params.get("input_combination") and params["input_combination"] != "splice":
        parts.append(params["input_combination"])
    parts.append(f"{len(input_ids)} input{'s' if len(input_ids) != 1 else ''}")
    if runtime_seconds is not None:
        parts.append(f"{runtime_seconds}s")
    auto_description = " / ".join(parts)

    description = (metadata.description if metadata and metadata.description
                   else auto_description)

    import mimetypes as mt
    mime, _ = mt.guess_type(output.filename)

    media_item = MediaItem(
        sha256=sha256,
        filename=output.filename,
        file_path=str(dest_path),
        media_type=media_type,
        file_size_bytes=source_path.stat().st_size,
        mime_type=mime or "application/octet-stream",
        description=description,
        output_index=resolved_index,
    )
    db.add(media_item)
    db.flush()

    # Source record with full job context
    source = MediaSource(
        media_item_id=media_item.id,
        source_type="job_output",
        source_metadata=json.dumps(source_meta),
    )
    db.add(source)

    # Auto-tags
    auto_tags = [f"app:{job.app_name}"]
    if params.get("recipe"):
        auto_tags.append(f"recipe:{params['recipe']}")
    if params.get("model"):
        auto_tags.append(f"model:{params['model']}")
    if resolved_index:
        auto_tags.append(f"index:{resolved_index}")
    # Add user-supplied tags
    if metadata and metadata.tags:
        auto_tags.extend(metadata.tags)

    for t in auto_tags:
        db.add(MediaTag(media_item_id=media_item.id, tag=t, tagged_by=user.id))

    output.indexed = True
    output.media_item_id = media_item.id
    db.commit()

    try:
        from extraction import run_extraction_async
        run_extraction_async(media_item.id, str(dest_path), media_type, db)
    except Exception:
        logger.exception("Extraction failed for indexed output %s", output.id)

    return {"ok": True, "media_item_id": media_item.id}


@router.post("/jobs/{job_id}/outputs/{output_id}/index", tags=["Job Outputs"],
             summary="Index an output into the search engine")
def index_output(
    output_id: str,
    job_id: str,
    body: IndexMetadata | None = None,
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Promote a job output to the search engine with metadata.

    Copies the file to the search media directory, creates a ``media_item``
    with enriched metadata (app, recipe, model, runtime, input files),
    runs extraction, and syncs to Meilisearch.

    The app manifest's ``[output]`` section provides defaults::

        [output]
        index = "rgz9-outputs"    # default output_index for this app

    Auto-generated tags: ``app:<name>``, ``recipe:<name>``, ``model:<name>``,
    ``index:<name>``. Filterable Meilisearch fields: ``output_index``,
    ``job_app``, ``job_recipe``, ``job_model``, ``job_runtime_seconds``,
    ``job_input_count``.
    """
    return _do_index_output(output_id, job_id, auth[0], db, body)


@router.post("/jobs/{job_id}/outputs/index", tags=["Job Outputs"], summary="Bulk index outputs")
def bulk_index_outputs(
    job_id: str,
    body: BulkIndexRequest,
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    """Index multiple job outputs into the search engine at once.

    All metadata fields are applied uniformly to every output in the batch.
    See the single-output index endpoint for details on auto-tags, manifest
    defaults, and Meilisearch fields.
    """
    user = auth[0]
    meta = IndexMetadata(
        description=body.description,
        tags=body.tags,
        output_index=body.output_index,
    )
    results = []
    for output_id in body.output_ids:
        try:
            result = _do_index_output(output_id, job_id, user, db, meta)
            results.append({"output_id": output_id, **result})
        except HTTPException as e:
            results.append({"output_id": output_id, "error": e.detail})
    return {"results": results}


@router.delete("/jobs/{job_id}/outputs", tags=["Job Outputs"], summary="Discard all outputs")
def discard_outputs(
    job_id: str,
    auth: tuple[User, str] = Depends(require_scope("write")),
    db: Session = Depends(get_db),
):
    user = auth[0]
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
