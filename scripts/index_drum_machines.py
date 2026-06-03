"""Index classic drum machine sample WAVs into the a-u.supply search engine.

Downloads individual drum-machine zips from the Internet Archive
``drum-machines-collection``, extracts WAVs, runs AI tagging per machine,
and creates MediaItem/MediaSource/MediaAudioMeta/MediaTag records routed to
the ``samples-bored`` Meilisearch index via source_type.

Usage:
    uv run python scripts/index_drum_machines.py [--download-dir <path>] [--limit N]

    With AI tagging:
    DEEPSEEK_API_KEY=sk-... uv run python scripts/index_drum_machines.py

    Resume / skip already-downloaded zips:
    uv run python scripts/index_drum_machines.py --download-dir /tmp/dm-zips

Requires a running Meilisearch + env vars (MEILISEARCH_URL, etc.).
Run from the repo root with ``uv run python``.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SEARCH_MEDIA_DIR = os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data")

from server.models import (  # noqa: E402
    MediaAudioMeta,
    MediaItem,
    MediaSource,
    MediaTag,
    SessionLocal,
)
from server.search_client import SAMPLES_INDEX, configure_indexes, sync_media_item  # noqa: E402

COLLECTION_ID = "drum-machines-collection"
ARCHIVE_METADATA_URL = f"https://archive.org/metadata/{COLLECTION_ID}"
ARCHIVE_DOWNLOAD_BASE = f"https://archive.org/download/{COLLECTION_ID}"
DETAILS_URL = f"https://archive.org/details/{COLLECTION_ID}"

SOURCE_NAME = "Drum Machine Samples Collection"
SOURCE_CREATOR = "Internet Archive (Various Uploaders)"
SOURCE_YEAR = 2023
SOURCE_TOPICS = [
    "drum-machines", "samples", "wav", "classic", "royalty-free",
    "one-shot", "percussion",
]

# Common drum machine manufacturer/brand tags added to every sample
BRAND_TAGS_BY_PREFIX: dict[str, list[str]] = {
    "4-In-The-Floor": ["4-in-the-floor"],
    "Access":   ["access", "access-virus"],
    "Acetone":  ["acetone"],
    "AcidLab":  ["acidlab"],
    "Akai":     ["akai"],
    "Alesis":   ["alesis"],
    "Amdek":    ["amdek"],
    "Analog-Solutions": ["analog-solutions"],
    "ARP":      ["arp"],
    "Austin":   ["austin"],
    "BME":      ["bme"],
    "Bontempi": ["bontempi"],
    "Boss":     ["boss", "roland"],
    "Casio":    ["casio"],
    "Cheetah":  ["cheetah"],
    "Clavia":   ["clavia", "nord"],
    "Conn":     ["conn"],
    "Coron":    ["coron"],
    "CRB":      ["crb"],
    "Cwejman":  ["cwejman"],
    "Dave Smith Instruments": ["dave-smith-instruments", "dsi", "sequential"],
    "Daytone":  ["daytone"],
    "DeepSky":  ["deepsky"],
    "Denon":    ["denon"],
    "DigiTech": ["digitech"],
    "Doepfer":  ["doepfer"],
    "DrBöhm":   ["dr-bohm", "bohm"],
    "Drumfire": ["drumfire"],
    "EKO":      ["eko"],
    "ELI":      ["eli"],
    "Electro-Harmonix": ["electro-harmonix", "ehx"],
    "Electron": ["electron"],
    "Electronica": ["electronica"],
    "Elektron": ["elektron"],
    "Elgam":    ["elgam"],
    "Elka":     ["elka"],
    "Eminent-Solina": ["eminent", "solina"],
    "EMU":      ["emu", "e-mu"],
    "Ensoniq":  ["ensoniq"],
    "Estradin": ["estradin"],
    "Fairlight": ["fairlight"],
    "Farfisa":  ["farfisa"],
    "Forat":    ["forat"],
    "Formanta": ["formanta"],
    "Fricke":   ["fricke", "mfb"],
    "FutureRetro": ["future-retro"],
    "GEM":      ["gem"],
    "Gulbransen": ["gulbransen"],
    "Hammond":  ["hammond"],
    "Hing-Hon": ["hing-hon"],
    "Hohner":   ["hohner"],
    "Jen":      ["jen"],
    "Jomox":    ["jomox"],
    "Kawai":    ["kawai"],
    "Kay":      ["kay"],
    "Keio":     ["keio"],
    "Kent":     ["kent"],
    "Ketron":   ["ketron"],
    "Keytek":   ["keytek"],
    "Kinsman":  ["kinsman"],
    "Klone":    ["klone"],
    "Korg":     ["korg"],
    "Linn":     ["linn", "linn-electronics"],
    "MFB":      ["mfb", "fricke"],
    "MPC":      ["mpc", "akai"],
    "Oberheim": ["oberheim"],
    "Roland":   ["roland"],
    "SCI":      ["sequential-circuits", "sci"],
    "Sequential": ["sequential", "sequential-circuits"],
    "Yamaha":   ["yamaha"],
    "Zoom":     ["zoom"],
}

# Drum voice detection by filename keywords for deterministic fallback
VOICE_PATTERNS: list[tuple[str, str]] = [
    ("kick", "kick"), ("kik", "kick"), ("bd", "kick"), ("bassdrum", "kick"),
    ("snare", "snare"), ("sn", "snare"), ("snar", "snare"), ("snr", "snare"),
    ("sd", "snare"),
    ("hihat", "hi-hat"), ("hi hat", "hi-hat"), ("hh", "hi-hat"), ("hat", "hi-hat"),
    ("chh", "hi-hat"), ("ohh", "hi-hat"), ("hi-hat", "hi-hat"),
    ("tom", "tom"), ("tom-tom", "tom"),
    ("cymbal", "cymbal"), ("cym", "cymbal"), ("crash", "cymbal"), ("ride", "cymbal"),
    ("clap", "clap"), ("clp", "clap"),
    ("rim", "percussion"), ("rimshot", "percussion"), ("rim shot", "percussion"),
    ("conga", "percussion"), ("bongo", "percussion"),
    ("shaker", "percussion"), ("tamb", "percussion"), ("tambourine", "percussion"),
    ("cowbell", "percussion"), ("cow", "percussion"),
    ("maraca", "percussion"), ("clave", "percussion"), ("guiro", "percussion"),
    ("triangle", "percussion"),
    ("perc", "percussion"), ("percussion", "percussion"),
    ("bass", "bass"),
    ("synth", "synth"),
    ("fx", "fx"), ("effect", "fx"),
    ("noise", "noise"),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def storage_path(filename: str, sha256: str) -> Path:
    """Build on-disk storage path matching the slack_scraper convention."""
    now = datetime.now(timezone.utc)
    month_dir = now.strftime("%Y-%m")
    safe = re.sub(r"[^\w.\-]", "_", filename)[:200]
    final = f"{sha256[:8]}_{safe}"
    return Path(SEARCH_MEDIA_DIR) / "audio" / month_dir / final


def detect_voice(filename: str) -> str | None:
    """Detect drum voice type from filename heuristics."""
    name = filename.lower()
    for pattern, voice in VOICE_PATTERNS:
        if pattern in name:
            return voice
    return None


def detect_instrument_from_machine(machine_name: str) -> str | None:
    """Derive a clean instrument key from the drum machine name."""
    return machine_name.lower().replace(" ", "-").replace("_", "-")


def brand_tags(machine_name: str) -> list[str]:
    """Derive brand/manufacturer tags from the machine name prefix."""
    tags: list[str] = []
    for prefix, taglist in BRAND_TAGS_BY_PREFIX.items():
        if machine_name.lower().startswith(prefix.lower()):
            tags.extend(taglist)
            break
    return tags


def derive_tags(filename: str, machine_name: str) -> list[str]:
    """Derive deterministic tags from filename + machine name."""
    tags: list[str] = []
    name = filename.lower().replace(".wav", "")

    # Brand tags
    tags.extend(brand_tags(machine_name))

    # Machine name as tag
    machine_tag = machine_name.lower().replace(" ", "-")
    tags.append(machine_tag)

    # Voice-based tags
    voice = detect_voice(filename)
    if voice:
        tags.append(voice)

    # Note detection
    notes = re.findall(r"\b([a-g])\b", name)
    tags.extend(notes)

    # Technique / character tags
    technique_map = {
        "dist": "distorted", "distorted": "distorted",
        "clean": "clean",
        "dry": "dry",
        "wet": "wet",
        "long": "long",
        "short": "short",
        "hard": "hard",
        "soft": "soft",
        "deep": "deep",
        "punch": "punchy",
        "vintage": "vintage",
        "analog": "analog",
        "digital": "digital",
        "acoustic": "acoustic",
        "electro": "electronic",
        "lo-fi": "lofi", "lofi": "lofi", "lo_fi": "lofi",
        "hi-fi": "hifi", "hifi": "hifi",
        "pitched": "pitched",
        "low": "low",
        "high": "high",
        "mid": "mid",
        "layer": "layered",
        "slide": "slide",
        "bend": "bend",
        "mute": "muted",
        "open": "open",
        "close": "closed",
        "pedal": "pedal",
        "reverse": "reversed",
        "gate": "gated",
        "verb": "reverb", "reverb": "reverb",
        "delay": "delay",
        "flange": "flanger",
        "chorus": "chorus",
        "phaser": "phaser",
        "filter": "filtered",
    }
    for key, tag in technique_map.items():
        if key in name:
            tags.append(tag)

    # Drum machine model detection in filename
    model_patterns = [
        "808", "909", "606", "707", "727", "505", "626", "303",
        "1200", "sp12", "sp-12", "sp1200", "sp-1200",
        "dmx", "drumtraks", "drumulator",
        "linn", "linndrum", "lm-1", "lm1",
        "mpc", "mpc60", "mpc3000", "mpc2000",
    ]
    name_clean = name.replace("-", "").replace("_", "").replace(" ", "")
    for mp in model_patterns:
        if mp.replace("-", "") in name_clean:
            tags.append(mp)

    seen: set[str] = set()
    return [t for t in tags if not (t in seen or seen.add(t))]


def extract_audio_metadata(file_path: str | Path) -> dict[str, Any]:
    import wave
    with wave.open(str(file_path), "rb") as wf:
        frames = wf.getnframes()
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        duration = frames / sample_rate if sample_rate > 0 else 0
        bit_depth = sampwidth * 8
    return {
        "duration_seconds": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "bit_depth": bit_depth,
    }


def fetch_archive_files() -> list[dict]:
    """Fetch the file listing from archive.org metadata API."""
    log(f"Fetching file list from {ARCHIVE_METADATA_URL} ...")
    resp = httpx.get(ARCHIVE_METADATA_URL, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    files = [f for f in data.get("files", []) if f.get("name", "").endswith(".zip")]
    files.sort(key=lambda f: f.get("name", "").lower())
    log(f"Found {len(files)} drum machine zips")
    return files


def download_zip(file_info: dict, download_dir: Path) -> Path | None:
    """Download a single zip from archive.org. Returns local path or None."""
    name = file_info["name"]
    local_path = download_dir / name
    if local_path.exists():
        remote_size = int(file_info.get("size", 0))
        local_size = local_path.stat().st_size
        if local_size == remote_size:
            return local_path
    url = f"{ARCHIVE_DOWNLOAD_BASE}/{quote(name)}"
    try:
        with httpx.stream("GET", url, timeout=300, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_bytes(65536):
                    f.write(chunk)
        return local_path
    except Exception as exc:
        log(f"  Download failed for {name}: {exc}")
        if local_path.exists():
            local_path.unlink()
        return None


def find_wavs_in_zip(zip_path: Path) -> list[tuple[str, str]]:
    """Return list of (top_dir_name, wav_entry_path) tuples from zip."""
    result: list[tuple[str, str]] = []
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        entries = [n for n in zf.namelist() if n.lower().endswith(".wav")]
        if not entries:
            return result
        # Determine top-level directory (the drum machine name)
        top_dir = None
        for entry in zf.namelist():
            if entry.endswith("/") and "/" not in entry.rstrip("/"):
                top_dir = entry.rstrip("/")
                break
            parts = entry.split("/")
            if len(parts) > 1:
                top_dir = parts[0]
                break
        if top_dir is None:
            top_dir = zip_path.stem
        for entry in entries:
            result.append((top_dir, entry))
    return result


def process_drum_machine(
    zip_path: Path,
    file_info: dict,
    db,
    stats: dict,
) -> None:
    """Process one drum machine zip: extract, create records, run extraction pipeline."""
    machine_name = zip_path.stem
    wav_entries = find_wavs_in_zip(zip_path)
    if not wav_entries:
        log(f"  {machine_name}: no WAVs found, skipping")
        stats["skipped_empty"] += 1
        return

    wav_count = len(wav_entries)
    log(f"  {machine_name}: {wav_count} WAVs")

    extract_dir = tempfile.mkdtemp(prefix=f"dm_{machine_name[:20]}_")
    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            zf.extractall(extract_dir)

        # Build mapping of zip entry path → local file path
        local_wavs: list[tuple[str, str, Path]] = []
        for top_dir, entry in wav_entries:
            local_path = Path(extract_dir) / entry
            if local_path.exists() and local_path.is_file():
                local_wavs.append((top_dir, entry, local_path))

        if not local_wavs:
            log(f"  {machine_name}: WAVs failed to extract")
            stats["errors"] += 1
            return

        # Pass 1: ingest all files, collect for batch extraction
        batch_items: list[tuple[str, str, str]] = []  # (media_item_id, file_path, dir_ctx)
        for top_dir, entry, local_path in local_wavs:
            try:
                filename = Path(entry).name
                renamed = f"{machine_name}-{filename}"
                file_hash = sha256_file(str(local_path))

                existing = db.query(MediaItem).filter(MediaItem.sha256 == file_hash).first()
                if existing:
                    stats["duplicates"] += 1
                    continue

                file_size = local_path.stat().st_size
                dest = storage_path(renamed, file_hash)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(local_path), str(dest))
                rel_path = str(dest.relative_to(Path(SEARCH_MEDIA_DIR)))

                mime = mimetypes.guess_type(filename)[0] or "audio/wav"
                media_item = MediaItem(
                    id=str(uuid.uuid4()),
                    sha256=file_hash,
                    filename=renamed,
                    file_path=rel_path,
                    media_type="audio",
                    file_size_bytes=file_size,
                    mime_type=mime,
                    description=f"{machine_name} drum machine sample",
                    output_index="samples-bored",
                )
                db.add(media_item)
                db.flush()

                source_meta = {
                    "source_url": DETAILS_URL,
                    "source_name": SOURCE_NAME,
                    "source_creator": SOURCE_CREATOR,
                    "source_year": SOURCE_YEAR,
                    "source_topics": SOURCE_TOPICS,
                    "dir": machine_name,
                    "collection": "drum-machines",
                    "archive_identifier": COLLECTION_ID,
                    "machine_name": machine_name,
                }
                source = MediaSource(
                    id=str(uuid.uuid4()),
                    media_item_id=media_item.id,
                    source_type="sample_library",
                    source_channel=None,
                    source_url=DETAILS_URL,
                    source_metadata=json.dumps(source_meta),
                )
                db.add(source)

                det_tags = derive_tags(filename, machine_name)
                all_tags = list(dict.fromkeys(
                    det_tags + SOURCE_TOPICS + [f"dir:{machine_name}"]
                ))
                for tag in all_tags:
                    db.add(MediaTag(media_item_id=media_item.id, tag=tag))

                db.commit()
                stats["new"] += 1
                batch_items.append((media_item.id, str(dest), machine_name))

            except Exception as exc:
                log(f"    ERROR {entry}: {exc}")
                db.rollback()
                stats["errors"] += 1

        # AI tagging runs separately via manage.py backfill-audio-ai-tags
        # (avoids holding DB session during slow API calls)

        # Sync to Meilisearch
        for media_item_id, _, _ in batch_items:
            try:
                item = db.query(MediaItem).filter(MediaItem.id == media_item_id).first()
                if item:
                    sync_media_item(db, item)
            except Exception as exc:
                log(f"    WARNING Meilisearch sync for {media_item_id}: {exc}")

    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def index_drum_machines(download_dir: str | None = None, limit: int | None = None) -> None:
    """Main entry point: download and index all drum machine zips."""
    # Resolve download directory
    if download_dir:
        dl_dir = Path(download_dir)
    else:
        dl_dir = Path(tempfile.gettempdir()) / "drum-machines-zips"
    dl_dir.mkdir(parents=True, exist_ok=True)

    # Ensure Meilisearch indexes exist
    try:
        configure_indexes()
    except Exception as exc:
        log(f"WARNING: configure_indexes failed: {exc}")

    # Fetch file list
    files = fetch_archive_files()
    if limit:
        files = files[:limit]
        log(f"Limited to {len(files)} machines")

    total_machines = len(files)

    # Pre-download phase (concurrent)
    log(f"\nDownloading {total_machines} zips to {dl_dir} ...")
    download_start = time.monotonic()
    zip_paths: dict[str, Path] = {}
    download_failures = 0

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(download_zip, fi, dl_dir): fi for fi in files}
        for i, future in enumerate(as_completed(futures), 1):
            fi = futures[future]
            name = fi["name"]
            try:
                result = future.result()
                if result:
                    zip_paths[name] = result
                    size_mb = int(fi.get("size", 0)) / 1024 / 1024
                    log(f"  [{i}/{total_machines}] {name} ({size_mb:.1f} MB)")
                else:
                    download_failures += 1
                    log(f"  [{i}/{total_machines}] {name} FAILED")
            except Exception as exc:
                download_failures += 1
                log(f"  [{i}/{total_machines}] {name} ERROR: {exc}")

    elapsed = time.monotonic() - download_start
    log(f"Downloaded {len(zip_paths)}/{total_machines} zips in {elapsed:.0f}s "
        f"({download_failures} failures)")

    if not zip_paths:
        log("No zips downloaded, aborting.")
        return

    # Processing phase (sequential — SQLite is single-writer)
    log(f"\nProcessing {len(zip_paths)} drum machines ...")
    stats = {"new": 0, "duplicates": 0, "errors": 0, "skipped_empty": 0}
    db = SessionLocal()
    try:
        for i, (name, zip_path) in enumerate(sorted(zip_paths.items()), 1):
            fi_dict = {"name": name, "size": str(zip_path.stat().st_size)}
            log(f"\n[{i}/{len(zip_paths)}] {zip_path.stem}")
            try:
                process_drum_machine(zip_path, fi_dict, db, stats)
            except Exception as exc:
                log(f"  FAILED: {exc}")
                stats["errors"] += 1

            # Periodic summary
            if i % 20 == 0:
                log(f"\n--- Progress: {i}/{len(zip_paths)} machines | "
                    f"New={stats['new']} Dup={stats['duplicates']} "
                    f"Err={stats['errors']} Empty={stats['skipped_empty']} ---")
    finally:
        db.close()

    log(f"\n{'='*60}")
    log(f"DONE. New samples: {stats['new']}, Duplicates: {stats['duplicates']}, "
        f"Errors: {stats['errors']}, Empty: {stats['skipped_empty']}")
    log(f"Total machines processed: {len(zip_paths)}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Index classic drum machine samples into samples-bored search index",
    )
    parser.add_argument(
        "--download-dir",
        default=None,
        help="Directory to cache downloaded zips (default: system temp dir)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process first N drum machines (for testing)",
    )
    args = parser.parse_args()

    index_drum_machines(download_dir=args.download_dir, limit=args.limit)


if __name__ == "__main__":
    main()
