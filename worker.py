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
import threading
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


def _param_active(spec: dict, params: dict) -> bool:
    """Check if a param's depends_on condition is met."""
    dep = spec.get("depends_on")
    if not dep:
        return True
    dep_values = dep.get("values", [dep["value"]] if "value" in dep else [])
    return params.get(dep.get("param")) in dep_values


def _build_docker_command(job: Job, manifest: dict, job_dir: Path) -> list[str]:
    """Build the docker run command with proper input files, params, and output flags."""
    image = manifest["image"]
    timeout = manifest.get("timeout_seconds", 600)
    command = manifest.get("command", "")
    input_mode = manifest.get("input_mode", "positional")
    output_flag = manifest.get("output_flag", "")
    params = json.loads(job.params)
    param_specs = manifest.get("params", {})

    # command_map: override command based on a param value
    command_map = manifest.get("command_map")
    if command_map:
        map_param = command_map.get("param", "")
        map_val = params.get(map_param, "")
        command = command_map.get("values", {}).get(map_val, command)

    # The job dir on the host filesystem — this is what Docker sees
    host_job_dir = f"/var/lib/dokku/data/storage/au-supply-jobs/{job.id}"

    cmd = [
        "docker", "run", "--rm",
        "--name", f"job-{job.id[:12]}",
        "--stop-timeout", str(timeout),
        "--memory", "4g",
        "--cpus", "2",
        "-v", f"{host_job_dir}:/work",
    ]

    # Forward environment variables declared in manifest
    for var_name in manifest.get("env", {}).get("pass_through", []):
        val = os.environ.get(var_name)
        if val:
            cmd.extend(["-e", f"{var_name}={val}"])

    cmd.append(image)

    # Subcommand (e.g. "rave", "recipe run")
    if command:
        cmd.extend(command.split())

    # Positional params (inserted between command and input files)
    positional: list[tuple[int, str]] = []
    for pname, spec in param_specs.items():
        if "position" not in spec or not _param_active(spec, params):
            continue
        val = params.get(pname)
        if val is None:
            continue
        tmpl = spec.get("value_template")
        positional.append((spec["position"], tmpl.replace("{}", str(val)) if tmpl else str(val)))
    for _, v in sorted(positional):
        cmd.append(v)

    # Input files
    input_dir = job_dir / "input"
    input_files = sorted(input_dir.iterdir()) if input_dir.is_dir() else []
    if input_mode == "positional":
        for f in input_files:
            cmd.append(f"/work/input/{f.name}")
    elif input_mode == "flag":
        input_flag = manifest.get("input_flag", "--input")
        cmd.append(input_flag)
        for f in input_files:
            cmd.append(f"/work/input/{f.name}")

    # Params mapped to CLI flags
    for param_name, spec in param_specs.items():
        value = params.get(param_name)
        flag = spec.get("flag")
        if "position" in spec or not flag or value is None:
            continue

        # Skip params whose dependency isn't met
        if not _param_active(spec, params):
            continue

        # Skip defaults to keep command clean
        default = spec.get("default")
        if value == default:
            continue

        param_type = spec.get("type", "string")
        if param_type == "bool":
            if value:
                cmd.append(flag)
            # False bools are just omitted
        elif param_type == "multi_select":
            if isinstance(value, list) and value:
                cmd.append(flag)
                cmd.append(",".join(str(v) for v in value))
        else:
            cmd.append(flag)
            cmd.append(str(value))

    # Output flag (e.g. "-o /work/output/output.wav")
    if output_flag:
        cmd.extend(output_flag.split())

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

    # Authenticate with GHCR if credentials are set
    ghcr_user = os.environ.get("GHCR_USER")
    ghcr_token = os.environ.get("GHCR_TOKEN")
    if ghcr_user and ghcr_token:
        login_result = subprocess.run(
            ["docker", "login", "ghcr.io", "-u", ghcr_user, "--password-stdin"],
            input=ghcr_token, capture_output=True, text=True, timeout=30,
        )
        if login_result.returncode != 0:
            logger.warning("GHCR login failed: %s", login_result.stderr.strip())

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

    # Run container — Popen + reader thread so log_tail updates while the
    # container is still running. Polling is what the jobs page already does;
    # this is what makes that polling useful for long jobs.
    cmd = _build_docker_command(job, manifest, job_dir)
    logger.info("Running: %s", " ".join(cmd))

    timeout = manifest.get("timeout_seconds", 600)
    deadline = time.monotonic() + timeout + 30
    log_lines: list[str] = []

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge stderr into stdout for ordered tail
        text=True,
        bufsize=1,
    )

    def _reader():
        for line in proc.stdout:
            log_lines.append(line.rstrip("\n"))

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    last_db_update = 0.0
    LOG_FLUSH_INTERVAL = 3.0  # seconds between log_tail commits while running
    timed_out = False
    while True:
        rc = proc.poll()
        if rc is not None:
            break
        if time.monotonic() > deadline:
            timed_out = True
            subprocess.run(
                ["docker", "kill", f"job-{job.id[:12]}"],
                capture_output=True, timeout=10,
            )
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        now = time.monotonic()
        if now - last_db_update >= LOG_FLUSH_INTERVAL and log_lines:
            job.log_tail = "\n".join(log_lines[-LOG_TAIL_LINES:])
            db.commit()
            last_db_update = now
        time.sleep(0.5)

    reader_thread.join(timeout=2)

    if timed_out:
        logger.error("Job %s timed out after %ds", job.id, timeout)
        job.status = "failed"
        job.error_message = f"Timed out after {timeout} seconds"
        job.log_tail = "\n".join(log_lines[-LOG_TAIL_LINES:])
        job.completed_at = _utcnow()
        db.commit()
        return

    return_code = proc.returncode
    combined_output = "\n".join(log_lines)

    job.log_tail = "\n".join(log_lines[-LOG_TAIL_LINES:])

    log_path = job_dir / "log.txt"
    log_path.write_text(combined_output)

    if return_code == 0:
        logger.info("Job %s completed successfully", job.id)
        _collect_outputs(job, job_dir, db)
        job.status = "completed"
    else:
        # `combined_output` is interleaved stdout+stderr now — use that for the error message.
        error_msg = combined_output.strip() or f"Exit code {return_code}"
        if return_code == 1:
            logger.warning("Job %s failed (expected): %s", job.id, error_msg[:200])
        elif return_code == 2:
            logger.warning("Job %s config error: %s", job.id, error_msg[:200])
        else:
            logger.error("Job %s crashed (exit %d): %s", job.id, return_code, error_msg[:200])
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
