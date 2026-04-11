#!/usr/bin/env python3
"""Seed the release catalog with 3 Archive.org releases.

Usage: .venv/bin/python seed_catalog.py

Idempotent — deletes existing seed records and recreates them with correct data.
Downloads MP3 derivatives and cover art from Archive.org.
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

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "./data/media"))
ADMIN_USER_ID = 1

SEED_RELEASES = [
    {
        "product_code": "A-U# 0",
        "title": "Immelerria",
        "entity_name": "Level Navi",
        "release_date": "2020-01-01",
        "archive_id": "au_immelerria",
        "cover_file": "au_immelerria.jpg",
        "description": None,
        "format_specs": "Digital (Archive.org, WAV)",
    },
    {
        "product_code": "A-U# 01",
        "title": "Erkind NOS",
        "entity_name": "Eonnot",
        "release_date": "2020-01-01",
        "archive_id": "au_erkind-nos",
        "cover_file": "au_erkind-nos.jpg",
        "description": None,
        "format_specs": "Digital (Archive.org, WAV)",
    },
    {
        "product_code": "A-U# M5497.H37",
        "title": "How How Things are Made are Made",
        "entity_name": "Complete Rx",
        "release_date": "2020-01-01",
        "archive_id": "au_how-how-things-are-made-are-made",
        "cover_file": "au_how-how-things-are-made-are-made.jpg",
        "description": "A 13-track audio album exploring how materials and compounds form the basis of manufactured goods.",
        "format_specs": "Digital (Archive.org, WAV)",
    },
]


def slugify(name):
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


def get_duration(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return float(json.loads(result.stdout)["format"]["duration"])
    except (FileNotFoundError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass
    return None


def download(url, dest, label=""):
    if dest.exists() and dest.stat().st_size > 1000:
        print(f"  [skip] {label or dest.name} already downloaded")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [download] {label or dest.name} ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "a-u.supply seed/2.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            while chunk := resp.read(65536):
                f.write(chunk)
        size = dest.stat().st_size
        print(f"             {size:,} bytes")
        return size > 1000
    except (urllib.error.URLError, OSError) as e:
        print(f"  [error] {e}")
        if dest.exists():
            dest.unlink()
        return False


def fetch_original_audio(archive_id):
    """Fetch Archive.org metadata and return list of original audio files."""
    url = f"https://archive.org/metadata/{archive_id}"
    print(f"  [fetch] metadata for {archive_id} ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "a-u.supply seed/2.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  [warn] {e}")
        return []

    audio_exts = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac", ".aiff"}
    files = []
    for f in meta.get("files", []):
        name = f.get("name", "")
        ext = Path(name).suffix.lower()
        if ext in audio_exts and f.get("source") == "original":
            files.append({"name": name, "ext": ext, "title": Path(name).stem})
    files.sort(key=lambda x: x["name"])
    return files


def seed():
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        print("=" * 60)
        print("  a-u.supply — Catalog Seed Script v2")
        print("=" * 60)
        print()

        # Delete old bad seed data
        old_codes = ["AU-2020-AR-001", "AU-2020-AR-002", "AU-2020-AR-003"]
        for code in old_codes:
            old = db.query(Release).filter(Release.product_code == code).first()
            if old:
                print(f"  [delete] old release {code}")
                db.delete(old)
        db.commit()

        # Create entities
        print("[1/3] Entities ...")
        entities = {}
        for r in SEED_RELEASES:
            name = r["entity_name"]
            slug = slugify(name)
            existing = db.query(Entity).filter(Entity.slug == slug).first()
            if existing:
                print(f"  [exists] {name} (id={existing.id})")
                entities[name] = existing
            else:
                e = Entity(name=name, slug=slug)
                db.add(e)
                db.flush()
                print(f"  [created] {name} (id={e.id})")
                entities[name] = e
        db.commit()
        print()

        # Create releases
        print("[2/3] Releases ...")
        for i, r in enumerate(SEED_RELEASES, 1):
            code = r["product_code"]
            print(f"\n--- {i}/{len(SEED_RELEASES)}: {code} — {r['title']} ---")

            existing = db.query(Release).filter(Release.product_code == code).first()
            if existing:
                print(f"  [exists] id={existing.id}")
                continue

            archive_id = r["archive_id"]
            rdir = MEDIA_DIR / "releases" / code
            tracks_dir = rdir / "tracks"
            tracks_dir.mkdir(parents=True, exist_ok=True)

            # Download cover art
            cover_path = None
            cover_url = f"https://archive.org/download/{archive_id}/{urllib.request.quote(r['cover_file'])}"
            cover_dest = rdir / f"cover{Path(r['cover_file']).suffix}"
            if download(cover_url, cover_dest, "cover art"):
                cover_path = str(cover_dest.relative_to(MEDIA_DIR))

            # Create release
            release = Release(
                product_code=code,
                title=r["title"],
                release_date=date.fromisoformat(r["release_date"]),
                cover_art_path=cover_path,
                status="published",
                description=r.get("description"),
                format_specs=r.get("format_specs"),
                created_by=ADMIN_USER_ID,
            )
            db.add(release)
            db.flush()
            print(f"  [created] release id={release.id}")

            # Link entity
            entity = entities[r["entity_name"]]
            db.execute(insert(release_entities).values(
                release_id=release.id, entity_id=entity.id, position=0,
            ))

            # Download original audio tracks
            audio_files = fetch_original_audio(archive_id)
            print(f"  [info] {len(audio_files)} original audio files found")
            for j, af in enumerate(audio_files, 1):
                slug = slugify(af["title"])
                filename = f"{j:02d}-{slug}{af['ext']}"
                dest = tracks_dir / filename
                dl_url = f"https://archive.org/download/{archive_id}/{urllib.request.quote(af['name'])}"

                if download(dl_url, dest, filename):
                    dur = get_duration(dest)
                    if dur:
                        print(f"             duration: {dur:.1f}s")
                    track = Track(
                        release_id=release.id,
                        title=af["title"],
                        track_number=j,
                        audio_file_path=str(dest.relative_to(MEDIA_DIR)),
                        duration_seconds=dur,
                    )
                    db.add(track)

            # Distribution link
            db.add(DistributionLink(
                release_id=release.id,
                platform="archive.org",
                url=f"https://archive.org/details/{archive_id}",
            ))

            db.commit()

        # Summary
        print(f"\n[3/3] Summary")
        print(f"  Entities: {db.query(Entity).count()}")
        print(f"  Releases: {db.query(Release).count()}")
        print(f"  Tracks:   {db.query(Track).count()}")
        print(f"  Links:    {db.query(DistributionLink).count()}")
        print("\nDone.")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
