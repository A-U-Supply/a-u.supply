#!/usr/bin/env python3
"""Seed the release catalog with 3 historical Archive.org releases (2020 era).

Usage:
    uv run python seed_catalog.py

Idempotent — skips records that already exist.
Downloads cover art and audio files from Archive.org into the media directory.
Extracts track durations via ffprobe if available, otherwise sets to None.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from sqlalchemy import insert

from models import (
    Base,
    DistributionLink,
    Entity,
    Release,
    ReleaseMetadata,
    SessionLocal,
    Track,
    engine,
    release_entities,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "./data/media"))
ADMIN_USER_ID = 1  # first admin user — must exist before running

# ---------------------------------------------------------------------------
# Seed data — 3 Archive.org releases from the 2020 era
# ---------------------------------------------------------------------------

SEED_RELEASES = [
    {
        "product_code": "AU-2020-AR-001",
        "title": "Immelerria",
        "entity_name": "Complete",
        "release_date": "2020-01-01",
        "status": "published",
        "description": None,
        "format_specs": "Digital (Archive.org)",
        "archive_url": "https://archive.org/details/immelerria",
        "archive_cover": "https://archive.org/services/img/immelerria",
        "cover_ext": "jpg",
        "tracks": [
            # Archive.org item files — update filenames to match the actual item
            # Format: (track_number, title, filename_on_archive)
            # The download URL will be: https://archive.org/download/{item_id}/{filename}
        ],
        "metadata": [],
    },
    {
        "product_code": "AU-2020-AR-002",
        "title": "Erkind NOS",
        "entity_name": "Complete",
        "release_date": "2020-01-01",
        "status": "published",
        "description": None,
        "format_specs": "Digital (Archive.org)",
        "archive_url": "https://archive.org/details/erkind-nos",
        "archive_cover": "https://archive.org/services/img/erkind-nos",
        "cover_ext": "jpg",
        "tracks": [],
        "metadata": [],
    },
    {
        "product_code": "AU-2020-AR-003",
        "title": "Depersonalized Ratio",
        "entity_name": "Complete",
        "release_date": "2020-01-01",
        "status": "published",
        "description": None,
        "format_specs": "Digital (Archive.org)",
        "archive_url": "https://archive.org/details/depersonalized-ratio",
        "archive_cover": "https://archive.org/services/img/depersonalized-ratio",
        "cover_ext": "jpg",
        "tracks": [],
        "metadata": [],
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def track_filename(track_number: int, title: str, ext: str) -> str:
    """Generate the canonical track filename: NN-slug.ext"""
    return f"{track_number:02d}-{slugify(title)}.{ext}"


def get_duration(path: str) -> float | None:
    """Extract audio duration in seconds via ffprobe. Returns None if unavailable."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
    except (FileNotFoundError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass
    return None


def download_file(url: str, dest: Path, label: str = "") -> bool:
    """Download a file from url to dest. Returns True on success."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [skip] {label or dest.name} already exists")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    display = label or dest.name
    print(f"  [download] {display} ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "a-u.supply seed/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            total = resp.headers.get("Content-Length")
            downloaded = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
            if total:
                print(f"           {downloaded} / {total} bytes")
            else:
                print(f"           {downloaded} bytes")
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"  [error] Failed to download {display}: {exc}")
        # Remove partial file
        if dest.exists():
            dest.unlink()
        return False


def archive_item_id(archive_url: str) -> str:
    """Extract the item identifier from an Archive.org details URL."""
    # https://archive.org/details/immelerria -> immelerria
    return archive_url.rstrip("/").split("/")[-1]


def fetch_archive_metadata(item_id: str) -> dict | None:
    """Fetch Archive.org metadata JSON for an item. Returns None on failure."""
    url = f"https://archive.org/metadata/{item_id}"
    print(f"  [fetch] Archive.org metadata for '{item_id}' ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "a-u.supply seed/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        print(f"  [warn] Could not fetch metadata for {item_id}: {exc}")
        return None


def audio_files_from_metadata(meta: dict) -> list[dict]:
    """Extract original audio files from Archive.org metadata, sorted by name."""
    if not meta or "files" not in meta:
        return []
    audio_exts = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".aiff"}
    files = []
    for f in meta["files"]:
        name = f.get("name", "")
        ext = Path(name).suffix.lower()
        source = f.get("source", "")
        if ext in audio_exts and source == "original":
            files.append({
                "name": name,
                "ext": ext.lstrip("."),
                "title": Path(name).stem,
                "size": f.get("size"),
            })
    # Sort by filename for consistent track ordering
    files.sort(key=lambda x: x["name"])
    return files


# ---------------------------------------------------------------------------
# Main seed logic
# ---------------------------------------------------------------------------


def seed():
    # Ensure tables exist
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        _seed_all(db)
    finally:
        db.close()


def _seed_all(db):
    print("=" * 60)
    print("  a-u.supply — Catalog Seed Script")
    print("=" * 60)
    print()

    # Collect unique entity names
    entity_names = sorted({r["entity_name"] for r in SEED_RELEASES})

    # --- Step 1: Create entities ---
    print("[1/3] Creating entities ...")
    entities = {}
    for name in entity_names:
        slug = slugify(name)
        existing = db.query(Entity).filter(Entity.slug == slug).first()
        if existing:
            print(f"  [skip] Entity '{name}' already exists (id={existing.id})")
            entities[name] = existing
        else:
            entity = Entity(name=name, slug=slug)
            db.add(entity)
            db.flush()
            print(f"  [created] Entity '{name}' (id={entity.id}, slug='{slug}')")
            entities[name] = entity
    db.commit()
    print()

    # --- Step 2: Create releases, download files, create tracks ---
    print("[2/3] Creating releases and downloading media ...")
    print()

    for idx, seed_rel in enumerate(SEED_RELEASES, 1):
        code = seed_rel["product_code"]
        print(f"--- Release {idx}/{len(SEED_RELEASES)}: {code} ---")

        # Check if release already exists
        existing_release = (
            db.query(Release).filter(Release.product_code == code).first()
        )
        if existing_release:
            print(f"  [skip] Release '{code}' already exists (id={existing_release.id})")
            print()
            continue

        entity = entities[seed_rel["entity_name"]]
        item_id = archive_item_id(seed_rel["archive_url"])
        release_dir = MEDIA_DIR / "releases" / code
        tracks_dir = release_dir / "tracks"
        tracks_dir.mkdir(parents=True, exist_ok=True)

        # Download cover art
        cover_path = None
        cover_file = release_dir / f"cover.{seed_rel['cover_ext']}"
        if download_file(seed_rel["archive_cover"], cover_file, label="cover art"):
            cover_path = str(cover_file.relative_to(MEDIA_DIR))

        # Fetch Archive.org metadata to discover audio files
        track_entries = seed_rel["tracks"]
        if not track_entries:
            meta = fetch_archive_metadata(item_id)
            audio_files = audio_files_from_metadata(meta)
            if audio_files:
                print(f"  [info] Found {len(audio_files)} audio files in archive")
                track_entries = [
                    (i + 1, af["title"], af["name"])
                    for i, af in enumerate(audio_files)
                ]
            else:
                print("  [info] No original audio files found in archive metadata")

        # Parse release date
        rel_date = None
        if seed_rel["release_date"]:
            rel_date = date.fromisoformat(seed_rel["release_date"])

        # Create the Release record
        release = Release(
            product_code=code,
            title=seed_rel["title"],
            release_date=rel_date,
            cover_art_path=cover_path,
            status=seed_rel.get("status", "published"),
            description=seed_rel.get("description"),
            format_specs=seed_rel.get("format_specs"),
            created_by=ADMIN_USER_ID,
        )
        db.add(release)
        db.flush()  # get release.id
        print(f"  [created] Release '{code}' — {seed_rel['title']} (id={release.id})")

        # Link entity via join table
        db.execute(
            insert(release_entities).values(
                release_id=release.id,
                entity_id=entity.id,
                position=0,
                role=None,
            )
        )

        # Download tracks and create Track records
        for track_num, title, archive_filename in track_entries:
            ext = Path(archive_filename).suffix.lstrip(".")
            local_name = track_filename(track_num, title, ext)
            local_path = tracks_dir / local_name
            download_url = f"https://archive.org/download/{item_id}/{urllib.request.quote(archive_filename)}"

            downloaded = download_file(download_url, local_path, label=local_name)

            duration = None
            if downloaded and local_path.exists():
                duration = get_duration(str(local_path))
                if duration is not None:
                    print(f"           duration: {duration:.1f}s")

            audio_path = str(local_path.relative_to(MEDIA_DIR)) if downloaded else None

            track = Track(
                release_id=release.id,
                title=title,
                track_number=track_num,
                audio_file_path=audio_path,
                duration_seconds=duration,
            )
            db.add(track)

        # Create distribution link for Archive.org
        dist_link = DistributionLink(
            release_id=release.id,
            platform="archive.org",
            url=seed_rel["archive_url"],
            label=None,
        )
        db.add(dist_link)

        # Create any metadata pairs
        for sort_idx, meta_pair in enumerate(seed_rel.get("metadata", [])):
            md = ReleaseMetadata(
                release_id=release.id,
                key=meta_pair["key"],
                value=meta_pair["value"],
                sort_order=sort_idx,
            )
            db.add(md)

        db.commit()
        print()

    # --- Step 3: Summary ---
    print("[3/3] Summary")
    print(f"  Entities: {db.query(Entity).count()}")
    print(f"  Releases: {db.query(Release).count()}")
    print(f"  Tracks:   {db.query(Track).count()}")
    print(f"  Links:    {db.query(DistributionLink).count()}")
    print()
    print("Done.")


if __name__ == "__main__":
    seed()
