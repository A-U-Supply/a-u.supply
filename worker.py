"""Job worker: polls for pending jobs and runs them in Docker containers.

Usage:
    .venv/bin/python worker.py

Environment variables:
    JOB_DATA_DIR        — where job input/output files live (default: /app/job-data)
    WORKER_POLL_INTERVAL — seconds between polls for new jobs (default: 2)
    WORKER_CONCURRENCY   — max concurrent jobs, currently only 1 supported (default: 1)
    DOCKER_HOST          — Docker socket path (default: unix:///var/run/docker.sock)
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from models import AppDefinition, Job, JobOutput, MediaItem, SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("worker")

JOB_DATA_DIR = Path(os.environ.get("JOB_DATA_DIR", "/app/job-data"))
SEARCH_MEDIA_DIR = Path(os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data"))
POLL_INTERVAL = int(os.environ.get("WORKER_POLL_INTERVAL", "2"))
LOG_TAIL_LINES = 50

# Graceful shutdown
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down after current job...", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _utcnow():
    return datetime.now(timezone.utc)


def _infer_media_type(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    type_map = {
        ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
        ".gif": "image", ".bmp": "image", ".tiff": "image", ".svg": "image",
        ".wav": "audio", ".mp3": "audio", ".flac": "audio", ".ogg": "audio",
        ".aac": "audio", ".m4a": "audio", ".aiff": "audio", ".opus": "audio",
        ".mp4": "video", ".webm": "video", ".mkv": "video", ".avi": "video",
        ".mov": "video", ".wmv": "video", ".flv": "video",
    }
    return type_map.get(ext)


def _pick_job(db: Session) -> Job | None:
    """Atomically pick the highest-priority pending job."""
    job = (
        db.query(Job)
        .filter(Job.status == "pending")
        .order_by(Job.priority, Job.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )
    if job:
        job.status = "running"
        job.started_at = _utcnow()
        db.commit()
    return job


def _prepare_input(job: Job, manifest: dict, db: Session) -> Path:
    """Copy input media files into the job's input directory and write job.json."""
    job_dir = JOB_DATA_DIR / job.id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    item_ids = json.loads(job.input_items)
    items = db.query(MediaItem).filter(MediaItem.id.in_(item_ids)).all()
    items_by_id = {i.id: i for i in items}

    input_files = []
    for mid in item_ids:
        item = items_by_id.get(mid)
        if not item:
            logger.warning("Media item %s not found, skipping", mid)
            continue

        src = Path(item.file_path)
        if not src.is_absolute():
            src = SEARCH_MEDIA_DIR / src
        if not src.is_file():
            logger.warning("File %s not found for item %s, skipping", src, mid)
            continue

        dest = input_dir / item.filename
        # Handle duplicate filenames
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = input_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.copy2(src, dest)
        input_files.append({
            "filename": dest.name,
            "media_type": item.media_type,
            "media_item_id": item.id,
        })

    # Write job.json
    job_manifest = {
        "job_id": job.id,
        "params": json.loads(job.params),
        "input_files": input_files,
    }
    (job_dir / "job.json").write_text(json.dumps(job_manifest, indent=2))

    return job_dir


def _build_docker_command(job: Job, manifest: dict, job_dir: Path) -> list[str]:
    """Build the docker run command."""
    image = manifest["image"]
    timeout = manifest.get("timeout_seconds", 600)
    command = manifest.get("command", "")

    # The job dir on the host filesystem — this is what Docker sees
    # Since we mount /var/lib/dokku/data/storage/au-supply-jobs as /app/job-data,
    # the host path is the storage path + job_id
    host_job_dir = f"/var/lib/dokku/data/storage/au-supply-jobs/{job.id}"

    cmd = [
        "docker", "run", "--rm",
        "--name", f"job-{job.id[:12]}",
        "--stop-timeout", str(timeout),
        "--memory", "4g",
        "--cpus", "2",
        "-v", f"{host_job_dir}:/work",
        image,
    ]

    if command:
        cmd.extend(command.split())

    return cmd


def _collect_outputs(job: Job, job_dir: Path, db: Session):
    """Scan the output directory and create JobOutput records."""
    output_dir = job_dir / "output"
    if not output_dir.is_dir():
        return

    # Check for output manifest
    output_manifest_path = output_dir / "manifest.json"
    output_manifest = {}
    if output_manifest_path.is_file():
        try:
            data = json.loads(output_manifest_path.read_text())
            output_manifest = {o["filename"]: o for o in data.get("outputs", [])}
        except Exception:
            logger.warning("Failed to parse output manifest.json for job %s", job.id)

    for f in sorted(output_dir.iterdir()):
        if f.name == "manifest.json" or not f.is_file():
            continue

        manifest_entry = output_manifest.get(f.name, {})
        media_type = manifest_entry.get("media_type") or _infer_media_type(f.name)

        output = JobOutput(
            job_id=job.id,
            filename=f.name,
            file_path=f.name,  # Relative to output dir
            media_type=media_type,
            file_size_bytes=f.stat().st_size,
        )
        db.add(output)

    db.commit()


def _run_job(job: Job, db: Session):
    """Execute a single job."""
    logger.info("Running job %s (app=%s)", job.id, job.app_name)

    # Load app manifest
    app_def = db.query(AppDefinition).filter(AppDefinition.name == job.app_name).first()
    if not app_def:
        job.status = "failed"
        job.error_message = f"App '{job.app_name}' not found"
        job.completed_at = _utcnow()
        db.commit()
        return

    manifest = tomllib.loads(app_def.manifest)

    # Prepare input files
    try:
        job_dir = _prepare_input(job, manifest, db)
    except Exception as e:
        logger.exception("Failed to prepare input for job %s", job.id)
        job.status = "failed"
        job.error_message = f"Input preparation failed: {e}"
        job.completed_at = _utcnow()
        db.commit()
        return

    # Pull image
    image = manifest["image"]
    logger.info("Pulling image %s", image)
    pull_result = subprocess.run(
        ["docker", "pull", image],
        capture_output=True, text=True, timeout=300,
    )
    if pull_result.returncode != 0:
        logger.warning("Docker pull failed (may use cached image): %s", pull_result.stderr.strip())

    # Check for cancellation before running
    db.refresh(job)
    if job.status == "cancelled":
        logger.info("Job %s was cancelled before execution", job.id)
        return

    # Run container
    cmd = _build_docker_command(job, manifest, job_dir)
    logger.info("Running: %s", " ".join(cmd))

    timeout = manifest.get("timeout_seconds", 600)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 30,  # Grace period beyond container stop-timeout
        )
    except subprocess.TimeoutExpired:
        logger.error("Job %s timed out after %ds", job.id, timeout)
        # Kill the container
        subprocess.run(
            ["docker", "kill", f"job-{job.id[:12]}"],
            capture_output=True, timeout=10,
        )
        job.status = "failed"
        job.error_message = f"Timed out after {timeout} seconds"
        job.completed_at = _utcnow()
        db.commit()
        return

    # Capture logs
    combined_output = result.stdout + result.stderr
    log_lines = combined_output.strip().split("\n")
    job.log_tail = "\n".join(log_lines[-LOG_TAIL_LINES:])

    # Write full log
    log_path = job_dir / "log.txt"
    log_path.write_text(combined_output)

    if result.returncode == 0:
        logger.info("Job %s completed successfully", job.id)
        _collect_outputs(job, job_dir, db)
        job.status = "completed"
    else:
        error_msg = result.stderr.strip() or result.stdout.strip() or f"Exit code {result.returncode}"
        if result.returncode == 1:
            logger.warning("Job %s failed (expected): %s", job.id, error_msg[:200])
        elif result.returncode == 2:
            logger.warning("Job %s config error: %s", job.id, error_msg[:200])
        else:
            logger.error("Job %s crashed (exit %d): %s", job.id, result.returncode, error_msg[:200])
        job.status = "failed"
        job.error_message = error_msg[:2000]
        job.retry_count += 1

    job.completed_at = _utcnow()
    db.commit()


def main():
    """Main worker loop."""
    JOB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Worker started. Polling every %ds. Job data: %s", POLL_INTERVAL, JOB_DATA_DIR)

    while not _shutdown:
        db = SessionLocal()
        job = None
        try:
            job = _pick_job(db)
            if job:
                _run_job(job, db)
            else:
                time.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.exception("Worker loop error")
            if job and job.status == "running":
                job.status = "failed"
                job.error_message = f"Worker crash: {e}"
                job.completed_at = _utcnow()
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            time.sleep(POLL_INTERVAL)
        finally:
            db.close()

    logger.info("Worker shut down cleanly.")


if __name__ == "__main__":
    main()
