"""GitHub-side endpoints for Latents.

- PAT management (/api/github/tokens) — admin-only personal-access tokens
  stored encrypted, used to read private repos and (later) clone for runs.
- Per-Latent repo linkage (/api/projects/:id/repo).
- Manifest sync (/api/projects/:id/repo/sync, /repo/manifest/apply).
- File-content proxy for slot source previews.
- Run trigger (/api/projects/:id/slots/:slot_id/run).
- Push webhook (/api/github/webhook).
"""

import json
import logging
import tomllib
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from server.auth import get_db, require_admin
from server.github_client import (
    GithubError,
    blob_url,
    commit_url,
    canonical_url,
    decrypt_token,
    encrypt_token,
    file_content,
    gen_webhook_secret,
    head_commit,
    parse_repo_url,
    repo_meta,
    validate_token,
    verify_webhook,
)
from server.models import (
    GithubToken,
    Project,
    ProjectRepo,
    ProjectSlot,
    RepoRun,
    User,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["GitHub"])

MANIFEST_PATH = ".au-supply/latent.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _project_or_404(db: Session, project_id: str) -> Project:
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return p


def _repo_for_project(db: Session, project_id: str) -> ProjectRepo | None:
    return db.query(ProjectRepo).filter(ProjectRepo.project_id == project_id).first()


def _token_or_none(db: Session, repo: ProjectRepo) -> str | None:
    if not repo.github_token_id:
        return None
    tok = db.query(GithubToken).filter(GithubToken.id == repo.github_token_id).first()
    if not tok:
        return None
    try:
        return decrypt_token(tok.token_encrypted)
    except Exception:
        logger.exception("Decrypt GitHub token failed for %s", tok.id)
        return None


def _repo_summary(repo: ProjectRepo) -> dict:
    return {
        "id": repo.id,
        "project_id": repo.project_id,
        "url": repo.url,
        "owner": repo.owner,
        "repo_name": repo.repo_name,
        "default_branch": repo.default_branch,
        "visibility": repo.visibility,
        "github_token_id": repo.github_token_id,
        "last_sha": repo.last_sha,
        "last_synced_at": repo.last_synced_at.isoformat() if repo.last_synced_at else None,
        "has_manifest": bool(repo.manifest_json),
        "webhook_secret": repo.webhook_secret,
        "blob_url_template": f"https://github.com/{repo.owner}/{repo.repo_name}/blob/{{ref}}/{{path}}",
    }


def _run_summary(r: RepoRun) -> dict:
    try:
        outputs = json.loads(r.outputs_json) if r.outputs_json else []
    except (ValueError, TypeError):
        outputs = []
    return {
        "id": r.id,
        "project_id": r.project_id,
        "slot_id": r.slot_id,
        "repo_id": r.repo_id,
        "job_id": r.job_id,
        "ref": r.ref,
        "command": r.command,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "exit_code": r.exit_code,
        "outputs": outputs,
        "stderr_tail": r.stderr_tail,
        "triggered_by": r.triggered_by,
    }


# ---------------------------------------------------------------------------
# PAT management
# ---------------------------------------------------------------------------


class CreateTokenBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    token: str = Field(..., min_length=10)


@router.get("/github/tokens", summary="List the calling user's PATs")
def list_tokens(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(GithubToken)
        .filter(GithubToken.user_id == user.id)
        .order_by(GithubToken.created_at.desc())
        .all()
    )
    return {
        "tokens": [
            {
                "id": t.id,
                "label": t.label,
                "scopes": (t.scopes or "").split(",") if t.scopes else [],
                "github_login": t.github_login,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            }
            for t in rows
        ]
    }


@router.post("/github/tokens", status_code=201, summary="Store a new PAT")
def create_token(
    body: CreateTokenBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        info = validate_token(body.token.strip())
    except GithubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    tok = GithubToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        label=body.label.strip(),
        scopes=",".join(info.scopes),
        github_login=info.login,
        token_encrypted=encrypt_token(body.token.strip()),
    )
    db.add(tok)
    db.commit()
    return {
        "id": tok.id,
        "label": tok.label,
        "scopes": info.scopes,
        "github_login": info.login,
    }


@router.delete("/github/tokens/{token_id}", status_code=204, summary="Revoke a stored PAT")
def delete_token(
    token_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    tok = db.query(GithubToken).filter(GithubToken.id == token_id, GithubToken.user_id == user.id).first()
    if not tok:
        raise HTTPException(status_code=404, detail="Token not found")
    # Detach from any repos that referenced it
    db.query(ProjectRepo).filter(ProjectRepo.github_token_id == token_id).update({"github_token_id": None})
    db.delete(tok)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Per-Latent repo linkage
# ---------------------------------------------------------------------------


class LinkRepoBody(BaseModel):
    url: str
    default_branch: str | None = None
    github_token_id: str | None = None


@router.get("/projects/{project_id}/repo", summary="Get the linked repo")
def get_repo(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    repo = _repo_for_project(db, project_id)
    if not repo:
        return {"repo": None}
    return {"repo": _repo_summary(repo)}


@router.post("/projects/{project_id}/repo", status_code=201, summary="Link a GitHub repo")
def link_repo(
    project_id: str,
    body: LinkRepoBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    if _repo_for_project(db, project_id):
        raise HTTPException(status_code=409, detail="A repo is already linked. Unlink first.")
    try:
        owner, name = parse_repo_url(body.url)
    except GithubError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token_plain = None
    if body.github_token_id:
        tok_row = db.query(GithubToken).filter(
            GithubToken.id == body.github_token_id,
            GithubToken.user_id == user.id,
        ).first()
        if not tok_row:
            raise HTTPException(status_code=400, detail="github_token_id not owned by user")
        try:
            token_plain = decrypt_token(tok_row.token_encrypted)
        except Exception:
            raise HTTPException(status_code=400, detail="stored token could not be decrypted; rotate it")

    try:
        meta = repo_meta(owner, name, token=token_plain)
    except GithubError as e:
        raise HTTPException(status_code=400, detail=str(e))

    repo = ProjectRepo(
        id=str(uuid.uuid4()),
        project_id=project_id,
        url=canonical_url(owner, name),
        provider="github",
        owner=owner,
        repo_name=name,
        default_branch=(body.default_branch or meta.get("default_branch") or "main"),
        visibility="private" if meta.get("private") else "public",
        github_token_id=body.github_token_id,
        webhook_secret=gen_webhook_secret(),
        created_by=user.id,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)

    # Best-effort initial sync (don't fail the link if GitHub blips).
    try:
        _sync_repo(db, repo, token_plain)
    except GithubError:
        logger.exception("Initial repo sync failed for %s", repo.id)

    return _repo_summary(repo)


class UpdateRepoBody(BaseModel):
    default_branch: str | None = None
    github_token_id: str | None = None


@router.patch("/projects/{project_id}/repo", summary="Update repo settings")
def patch_repo(
    project_id: str,
    body: UpdateRepoBody,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    repo = _repo_for_project(db, project_id)
    if not repo:
        raise HTTPException(status_code=404, detail="No repo linked")
    if body.default_branch is not None:
        repo.default_branch = body.default_branch
    if body.github_token_id is not None:
        if body.github_token_id == "":
            repo.github_token_id = None
        else:
            ok = db.query(GithubToken).filter(
                GithubToken.id == body.github_token_id,
                GithubToken.user_id == user.id,
            ).first()
            if not ok:
                raise HTTPException(status_code=400, detail="github_token_id not owned by user")
            repo.github_token_id = body.github_token_id
    db.commit()
    return _repo_summary(repo)


@router.delete("/projects/{project_id}/repo", status_code=204, summary="Unlink the repo")
def unlink_repo(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    repo = _repo_for_project(db, project_id)
    if not repo:
        raise HTTPException(status_code=404, detail="No repo linked")
    # Clear slot back-references
    db.query(ProjectSlot).filter(ProjectSlot.repo_id == repo.id).update(
        {"repo_id": None, "repo_path": None, "repo_ref": None}
    )
    db.delete(repo)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Sync + manifest
# ---------------------------------------------------------------------------


def _sync_repo(db: Session, repo: ProjectRepo, token_plain: str | None) -> dict:
    """Refresh last_sha, last_synced_at, manifest_json."""
    try:
        sha = head_commit(repo.owner, repo.repo_name, repo.default_branch, token=token_plain)
    except GithubError as e:
        raise

    manifest_json = None
    try:
        f = file_content(repo.owner, repo.repo_name, MANIFEST_PATH, ref=sha, token=token_plain)
        if isinstance(f, dict) and f.get("text"):
            parsed = tomllib.loads(f["text"])
            manifest_json = json.dumps(parsed)
    except GithubError:
        # Manifest is optional; absence is fine.
        manifest_json = None
    except (tomllib.TOMLDecodeError, ValueError) as e:
        logger.warning("Manifest parse failed for repo %s: %s", repo.id, e)
        manifest_json = json.dumps({"_error": f"Manifest parse failed: {e}"})

    repo.last_sha = sha
    repo.last_synced_at = _utcnow()
    repo.manifest_json = manifest_json
    db.commit()
    return {"sha": sha, "has_manifest": bool(manifest_json), "synced_at": repo.last_synced_at.isoformat()}


@router.post("/projects/{project_id}/repo/sync", summary="Re-pull HEAD + manifest")
def sync_endpoint(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    repo = _repo_for_project(db, project_id)
    if not repo:
        raise HTTPException(status_code=404, detail="No repo linked")
    try:
        out = _sync_repo(db, repo, _token_or_none(db, repo))
    except GithubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {**out, "repo": _repo_summary(repo)}


def _manifest_dict(repo: ProjectRepo) -> dict:
    if not repo.manifest_json:
        return {}
    try:
        return json.loads(repo.manifest_json)
    except (ValueError, TypeError):
        return {}


@router.get("/projects/{project_id}/repo/manifest", summary="Parsed manifest preview")
def get_manifest(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    repo = _repo_for_project(db, project_id)
    if not repo:
        raise HTTPException(status_code=404, detail="No repo linked")
    manifest = _manifest_dict(repo)

    # Compute "new tracks": tracks in manifest with positions that don't exist as slots.
    tracks = manifest.get("tracks") or []
    existing_positions = {
        s.position
        for s in db.query(ProjectSlot.position).filter(ProjectSlot.project_id == project_id).all()
    }
    new_tracks = [t for t in tracks if int(t.get("position", 0)) not in existing_positions]
    return {"manifest": manifest, "new_tracks": new_tracks}


class ApplyManifestBody(BaseModel):
    import_new: bool = True
    update_existing_labels: bool = False


@router.post("/projects/{project_id}/repo/manifest/apply", summary="Materialize slots from manifest")
def apply_manifest(
    project_id: str,
    body: ApplyManifestBody = Body(default_factory=lambda: ApplyManifestBody()),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = _project_or_404(db, project_id)
    repo = _repo_for_project(db, project_id)
    if not repo:
        raise HTTPException(status_code=404, detail="No repo linked")
    manifest = _manifest_dict(repo)
    tracks = manifest.get("tracks") or []

    existing = {
        s.position: s
        for s in db.query(ProjectSlot).filter(ProjectSlot.project_id == project_id).all()
    }

    created = 0
    updated = 0
    for t in tracks:
        pos = int(t.get("position") or 0)
        if pos <= 0:
            continue
        label = t.get("label") or f"Track {pos}"
        script_path = t.get("script") or t.get("path")
        cmd = t.get("command")
        if pos in existing:
            slot = existing[pos]
            if body.update_existing_labels and slot.label != label:
                slot.label = label
                updated += 1
            if script_path and not slot.repo_path:
                slot.repo_id = repo.id
                slot.repo_path = script_path
                slot.run_command = cmd
                updated += 1
        else:
            if not body.import_new:
                continue
            slot = ProjectSlot(
                id=str(uuid.uuid4()),
                project_id=project_id,
                position=pos,
                label=label,
                status="forming",
                repo_id=repo.id if script_path else None,
                repo_path=script_path,
                run_command=cmd,
            )
            db.add(slot)
            created += 1
    db.commit()
    return {"created": created, "updated": updated}


# ---------------------------------------------------------------------------
# File content proxy
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/repo/file", summary="Fetch a file's content from the repo")
def get_file(
    project_id: str,
    path: str = Query(..., min_length=1),
    ref: str | None = Query(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    repo = _repo_for_project(db, project_id)
    if not repo:
        raise HTTPException(status_code=404, detail="No repo linked")
    use_ref = ref or repo.last_sha or repo.default_branch
    try:
        data = file_content(repo.owner, repo.repo_name, path, ref=use_ref, token=_token_or_none(db, repo))
    except GithubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if isinstance(data, dict) and data.get("type") == "dir":
        return {
            "type": "dir",
            "entries": [
                {
                    "name": e.get("name"),
                    "type": e.get("type"),
                    "path": e.get("path"),
                    "size": e.get("size"),
                }
                for e in (data.get("entries") or [])
            ],
            "blob_url": blob_url(repo.owner, repo.repo_name, use_ref, path),
        }
    return {
        "type": "file",
        "path": path,
        "ref": use_ref,
        "text": (data.get("text") if isinstance(data, dict) else None),
        "size": (data.get("size") if isinstance(data, dict) else None),
        "blob_url": blob_url(repo.owner, repo.repo_name, use_ref, path),
    }


# ---------------------------------------------------------------------------
# Run trigger + history
# ---------------------------------------------------------------------------


class TriggerRunBody(BaseModel):
    ref: str | None = None
    command: str | None = None


@router.post("/projects/{project_id}/slots/{slot_id}/run", status_code=202, summary="Run the slot's script")
def trigger_slot_run(
    project_id: str,
    slot_id: str,
    body: TriggerRunBody = Body(default_factory=lambda: TriggerRunBody()),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    slot = db.query(ProjectSlot).filter(
        ProjectSlot.id == slot_id, ProjectSlot.project_id == project_id,
    ).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    repo = _repo_for_project(db, project_id)
    if not repo:
        raise HTTPException(status_code=400, detail="Latent has no linked repo")
    if not slot.repo_path:
        raise HTTPException(status_code=400, detail="Slot has no repo_path; nothing to run")

    # Resolve ref + command
    ref = body.ref or slot.repo_ref or repo.last_sha or repo.default_branch
    cmd = body.command or slot.run_command or f"python {slot.repo_path}"

    from server.models import Job
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        app_name="__repo_run__",
        status="pending",
        input_items=json.dumps([]),
        params=json.dumps({
            "repo_id": repo.id,
            "ref": ref,
            "command": cmd,
            "project_id": project_id,
            "slot_id": slot.id,
            "owner": repo.owner,
            "repo_name": repo.repo_name,
            "private": repo.visibility == "private",
            "github_token_id": repo.github_token_id,
        }),
        priority=50,
        created_by=user.id,
    )
    db.add(job)

    run = RepoRun(
        id=str(uuid.uuid4()),
        project_id=project_id,
        slot_id=slot.id,
        repo_id=repo.id,
        job_id=job_id,
        ref=ref,                      # may get refined to a SHA by the worker
        command=cmd,
        triggered_by=user.id,
    )
    db.add(run)
    db.commit()
    return {"job_id": job_id, "repo_run_id": run.id}


@router.get("/projects/{project_id}/slots/{slot_id}/runs", summary="Slot run history")
def slot_runs(
    project_id: str,
    slot_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    rows = (
        db.query(RepoRun)
        .filter(RepoRun.project_id == project_id, RepoRun.slot_id == slot_id)
        .order_by(RepoRun.started_at.desc())
        .limit(50)
        .all()
    )
    return {"runs": [_run_summary(r) for r in rows]}


@router.get("/projects/{project_id}/repo/runs", summary="All repo runs for this Latent")
def project_runs(
    project_id: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _project_or_404(db, project_id)
    rows = (
        db.query(RepoRun)
        .filter(RepoRun.project_id == project_id)
        .order_by(RepoRun.started_at.desc())
        .limit(100)
        .all()
    )
    return {"runs": [_run_summary(r) for r in rows]}


# ---------------------------------------------------------------------------
# Push webhook
# ---------------------------------------------------------------------------


@router.post("/github/webhook", summary="GitHub push event handler", include_in_schema=False)
async def github_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    event = request.headers.get("X-GitHub-Event") or ""
    sig = request.headers.get("X-Hub-Signature-256") or ""
    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Body is not JSON")

    if event == "ping":
        return {"pong": True}

    if event != "push":
        return {"ignored": event}

    full_name = (payload.get("repository") or {}).get("full_name") or ""
    if "/" not in full_name:
        return {"ignored": "no repository"}
    owner, name = full_name.split("/", 1)

    repo = (
        db.query(ProjectRepo)
        .filter(func.lower(ProjectRepo.owner) == owner.lower(), func.lower(ProjectRepo.repo_name) == name.lower())
        .first()
    )
    if not repo:
        return {"ignored": "no matching latent"}

    if not repo.webhook_secret or not verify_webhook(sig, repo.webhook_secret, body):
        raise HTTPException(status_code=401, detail="Invalid signature")

    ref = payload.get("ref") or ""
    branch = ref.rsplit("/", 1)[-1] if "/" in ref else ref
    if branch != repo.default_branch:
        return {"ignored": f"branch {branch} != default {repo.default_branch}"}

    commits = payload.get("commits") or []
    head_sha = (payload.get("after") or "")
    if head_sha:
        repo.last_sha = head_sha
        repo.last_synced_at = _utcnow()
        db.commit()

    # Post a Lemmy thread anchored to the Latent for the latest commit, if a
    # linked admin user is available (the creator of the repo link).
    try:
        from server.lemmy_client import get_user_token, is_configured
        from server.threads_api import _resolve_community_id  # type: ignore
        from server.lemmy_client import create_post as lemmy_create_post
        from server.models import Thread as ThreadModel
    except Exception:
        return {"posted": False, "reason": "lemmy not importable"}

    if not is_configured():
        return {"posted": False, "reason": "lemmy not configured"}
    creator = db.query(User).filter(User.id == repo.created_by).first()
    if not creator or not creator.lemmy_token_encrypted:
        return {"posted": False, "reason": "repo creator not linked to fold"}

    try:
        token = get_user_token(db, creator)
    except Exception:
        return {"posted": False, "reason": "lemmy token unavailable"}

    try:
        community_id = _resolve_community_id(db, "project", repo.project_id, token)
    except HTTPException as e:
        return {"posted": False, "reason": str(e.detail)}

    msg = (commits[-1] or {}).get("message", "") if commits else "(no message)"
    title = msg.split("\n", 1)[0][:140] or "git push"
    url = commit_url(repo.owner, repo.repo_name, head_sha) if head_sha else repo.url
    body_md = "\n\n".join(
        f"`{(c.get('id') or '')[:7]}` {(c.get('message') or '').splitlines()[0]}"
        for c in commits[-5:]
    )
    try:
        post = lemmy_create_post(token, community_id, title, body=body_md or None, url=url)
    except Exception:
        return {"posted": False, "reason": "lemmy post failed"}

    db.add(ThreadModel(
        id=str(uuid.uuid4()),
        anchor_type="project",
        anchor_id=repo.project_id,
        lemmy_post_id=post.id,
        lemmy_community_id=community_id,
        created_by=creator.id,
    ))
    db.commit()
    return {"posted": True, "lemmy_post_id": post.id}
