"""Index sample library WAV files into the a-u.supply search engine.

Repeatable workflow that creates MediaItem/MediaSource/MediaTag records in
SQLite, runs the extraction pipeline for audio metadata, and syncs to
Meilisearch (routed to the ``samples-bored`` index via source_type).

Usage:
    uv run python scripts/index_samples.py <zip-path>

    or to re-index with DeepSeek AI tagging for sound effects:
    DEEPSEEK_API_KEY=sk-... uv run python scripts/index_samples.py <zip-path>

Requires a running Meilisearch + env vars (MEILISEARCH_URL, etc.).
Run from the repo root with `uv run python`.
"""

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import uuid
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Allow importing server.* from the repo root
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SEARCH_MEDIA_DIR = os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data")

from server.models import (
    MediaAudioMeta,
    MediaItem,
    MediaSource,
    MediaTag,
    SessionLocal,
)
from server.search_client import SAMPLES_INDEX, configure_indexes, sync_media_item

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

SOURCE_URL = "https://archive.org/details/music-2000-sample-library-44k-wav-rip"
SOURCE_NAME = "Music 2000 Sample library 44k WAV RIP"
SOURCE_CREATOR = "Jester Interactive, Codemasters"
SOURCE_YEAR = 2000

DIR_TAGS = {
    "bass_acoustic":         (["bass", "acoustic"], "bass"),
    "bass_electric notes":   (["bass", "electric", "note"], "bass"),
    "bass_electric riffs":   (["bass", "electric", "riff"], "bass"),
    "bass_synth":            (["bass", "synth"], "bass"),
    "guitar_acoustic notes": (["guitar", "acoustic", "note"], "guitar"),
    "guitar_acoustic riffs": (["guitar", "acoustic", "riff"], "guitar"),
    "guitar_distorted notes":(["guitar", "distorted", "note"], "guitar"),
    "guitar_distorted riffs":(["guitar", "distorted", "riff"], "guitar"),
    "guitar_distorted wah":  (["guitar", "distorted", "wah"], "guitar"),
    "guitar_effects":        (["guitar", "effect"], "guitar"),
    "guitar_electric notes": (["guitar", "electric", "note"], "guitar"),
    "guitar_electric riffs": (["guitar", "electric", "riff"], "guitar"),
    "guitar_electric wah":   (["guitar", "electric", "wah"], "guitar"),
    "melody_acoustic":       (["melody", "acoustic"], "melody"),
    "melody_hardsynth":      (["melody", "synth", "hard"], "synth"),
    "melody_softsynth":      (["melody", "synth", "soft"], "synth"),
    "melody_stab":           (["melody", "stab"], "melody"),
    "organ_acoustic":        (["organ", "acoustic"], "organ"),
    "organ_electric":        (["organ", "electric"], "organ"),
    "pad_acoustic":          (["pad", "acoustic"], "pad"),
    "pad_hardsynth":         (["pad", "synth", "hard"], "synth"),
    "pad_softsynth":         (["pad", "synth", "soft"], "synth"),
    "percussion_cymbal":     (["percussion", "cymbal"], "drums"),
    "percussion_hi hat":     (["percussion", "hi-hat"], "drums"),
    "percussion_human":      (["percussion", "human", "vocal"], "vocal"),
    "percussion_kickdrum":   (["percussion", "kick"], "drums"),
    "percussion_metal":      (["percussion", "metal"], "drums"),
    "percussion_snare":      (["percussion", "snare"], "drums"),
    "percussion_strange":    (["percussion", "experimental"], "drums"),
    "percussion_toms":       (["percussion", "toms"], "drums"),
    "percussion_wooden":     (["percussion", "wooden"], "drums"),
    "percussion_world":      (["percussion", "world"], "drums"),
    "rapping_danny":         (["rap", "vocal", "male"], "vocal"),
    "rapping_mandy":         (["rap", "vocal", "female"], "vocal"),
    "rapping_paul":          (["rap", "vocal", "male"], "vocal"),
    "rapping_sean 1":        (["rap", "vocal", "male"], "vocal"),
    "rapping_sean 2":        (["rap", "vocal", "male"], "vocal"),
    "rapping_shena":         (["rap", "vocal", "female"], "vocal"),
    "rapping_stepz":         (["rap", "vocal", "male"], "vocal"),
    "rapping_steve 1":       (["rap", "vocal", "male"], "vocal"),
    "rapping_steve 2":       (["rap", "vocal", "male"], "vocal"),
    "rapping_steve 3":       (["rap", "vocal", "male"], "vocal"),
    "singing_cathi 1":       (["singing", "vocal", "female"], "vocal"),
    "singing_cathi 2":       (["singing", "vocal", "female"], "vocal"),
    "singing_howard 1":      (["singing", "vocal", "male"], "vocal"),
    "singing_howard 2":      (["singing", "vocal", "male"], "vocal"),
    "singing_jackie 1":      (["singing", "vocal", "female"], "vocal"),
    "singing_jackie 2":      (["singing", "vocal", "female"], "vocal"),
    "singing_mandy":         (["singing", "vocal", "female"], "vocal"),
    "singing_paul":          (["singing", "vocal", "male"], "vocal"),
    "singing_ruby":          (["singing", "vocal", "female"], "vocal"),
    "singing_shena 1":       (["singing", "vocal", "female"], "vocal"),
    "singing_shena 2":       (["singing", "vocal", "female"], "vocal"),
    "singing_spoken_laurel": (["spoken", "vocal", "female"], "vocal"),
    "singing_spoken_ted":    (["spoken", "vocal", "male"], "vocal"),
    "sound effect_animal":   (["sound-effect", "animal", "foley"], "fx"),
    "sound effect_musical":  (["sound-effect", "musical", "foley"], "fx"),
    "sound effect_objects":  (["sound-effect", "foley", "objects"], "fx"),
    "vinyl_crackle":         (["vinyl", "crackle", "noise"], "fx"),
    "vinyl_rocking":         (["vinyl", "rocking", "effect"], "fx"),
    "vinyl_stops":           (["vinyl", "stop", "effect"], "fx"),
}

SOURCE_TOPICS = [
    "music-2000", "mtv-music-generator", "mtvmg", "playstation", "ps1",
    "jester-interactive", "codemasters", "sample-library", "royalty-free",
]


def log(msg):
    print(msg, flush=True)


def extract_audio_metadata(file_path):
    with wave.open(file_path, "rb") as wf:
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


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def storage_path(filename, sha256):
    """Build the on-disk storage path matching the slack_scraper convention."""
    now = datetime.now(timezone.utc)
    month_dir = now.strftime("%Y-%m")
    safe = re.sub(r"[^\w.\-]", "_", filename)[:200]
    final = f"{sha256[:8]}_{safe}"
    return Path(SEARCH_MEDIA_DIR) / "audio" / month_dir / final


def derive_tags(filename, dir_base_tags):
    tags = list(dir_base_tags)
    name = filename.lower().replace(".wav", "")
    notes = re.findall(r'\b([a-g])\b', name)
    tags.extend(notes)
    if "slide" in name or "bend" in name:
        tags.append("slide")
    if "plec" in name or "pick" in name or "picked" in name:
        tags.append("plectrum")
    if "slap" in name:
        tags.append("slap")
    if "mute" in name:
        tags.append("muted")
    if "filter" in name:
        tags.append("filtered")
    if "pluck" in name:
        tags.append("plucked")
    if "buzz" in name:
        tags.append("buzz")
    if "warm" in name or "smooth" in name:
        tags.append("warm")
    if "walk" in name:
        tags.append("walking")
    if "effect" in name or "fx" in name:
        tags.append("effect")
    if "hi" in name and "hat" not in name and "high" not in name:
        tags.append("high")
    if "loop" in name or "riff" in name:
        tags.append("loop")
    if "wah" in name:
        tags.append("wah")
    if "scratch" in name:
        tags.append("scratch")
    if "909" in name:
        tags.append("909")
    seen = set()
    return [t for t in tags if not (t in seen or seen.add(t))]


def batch_tag_dir_via_deepseek(dir_name, filenames, api_key):
    """Batch-tag all files in a directory via DeepSeek. Returns {filename: {description, tags}}."""
    from server.ai_audio import generate_audio_ai_descriptions
    return generate_audio_ai_descriptions(filenames, dir_name=dir_name, api_key=api_key)


def index_samples(zip_path):
    if not os.path.exists(zip_path):
        log(f"ERROR: {zip_path} not found")
        return

    # Ensure Meilisearch indexes exist
    try:
        configure_indexes()
    except Exception as exc:
        log(f"WARNING: configure_indexes failed: {exc}")
        log("(This is expected if Meilisearch isn't running yet)")

    log("Extracting zip...")
    extract_dir = tempfile.mkdtemp(prefix="samples_")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        log(f"Extracted to {extract_dir}")

        samples_root = os.path.join(extract_dir, "Sample library")
        if not os.path.isdir(samples_root):
            log(f"ERROR: 'Sample library/' dir not found in {extract_dir}")
            log("Contents: " + ", ".join(os.listdir(extract_dir)))
            return

        wavs = sorted(Path(samples_root).rglob("*.wav"))
        log(f"Found {len(wavs)} WAV files")

        # Group by directory for AI tagging
        by_dir = {}
        for w in wavs:
            d = w.parent.name
            by_dir.setdefault(d, []).append(w)

        # AI tagging for ALL directories via DeepSeek
        ai_results = {}
        if DEEPSEEK_API_KEY:
            for dir_name in sorted(by_dir):
                fnames = [f"{dir_name}-{w.name}" for w in by_dir[dir_name]]
                log(f"AI tagging {len(fnames)} files in '{dir_name}'...")
                ai_results[dir_name] = batch_tag_dir_via_deepseek(dir_name, fnames, DEEPSEEK_API_KEY)
        else:
            log("No DEEPSEEK_API_KEY set, skipping AI tagging")

        db = SessionLocal()
        try:
            total = len(wavs)
            new_count = 0
            dup_count = 0
            error_count = 0

            for wav_path in wavs:
                dir_name = wav_path.parent.name
                filename = wav_path.name
                renamed = f"{dir_name}-{filename}"

                try:
                    file_hash = sha256_file(str(wav_path))
                    file_size = wav_path.stat().st_size

                    # Dedup by SHA-256
                    existing = db.query(MediaItem).filter(MediaItem.sha256 == file_hash).first()
                    if existing:
                        dup_count += 1
                        continue

                    # Store file in search media dir
                    dest = storage_path(renamed, file_hash)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(wav_path), str(dest))
                    rel_path = str(dest.relative_to(Path(SEARCH_MEDIA_DIR)))

                    # Create MediaItem
                    mime = mimetypes.guess_type(filename)[0] or "audio/wav"
                    media_item = MediaItem(
                        id=str(uuid.uuid4()),
                        sha256=file_hash,
                        filename=renamed,
                        file_path=rel_path,
                        media_type="audio",
                        file_size_bytes=file_size,
                        mime_type=mime,
                        output_index="samples-bored",
                    )
                    db.add(media_item)
                    db.flush()

                    # Create MediaSource with sample_library type → routes to samples-bored index
                    source_meta = {
                        "source_url": SOURCE_URL,
                        "source_name": SOURCE_NAME,
                        "source_creator": SOURCE_CREATOR,
                        "source_year": SOURCE_YEAR,
                        "source_topics": SOURCE_TOPICS,
                        "dir": dir_name,
                        "collection": "music2000",
                    }
                    source = MediaSource(
                        id=str(uuid.uuid4()),
                        media_item_id=media_item.id,
                        source_type="sample_library",
                        source_channel=None,
                        source_url=SOURCE_URL,
                        source_metadata=json.dumps(source_meta),
                    )
                    db.add(source)

                    # Derive and add tags (deduplicated)
                    base_tags, category = DIR_TAGS.get(dir_name, ([dir_name.replace("_", " ")], "other"))
                    derived_tags = derive_tags(filename, base_tags)

                    all_tags = list(dict.fromkeys(
                        derived_tags
                        + SOURCE_TOPICS
                        + [f"dir:{dir_name}"]
                    ))

                    # AI tags + description (from DeepSeek, keyed by renamed filename)
                    ai_info = ai_results.get(dir_name, {}).get(renamed, {})
                    ai_desc = ai_info.get("description", "")
                    ai_tag_list = ai_info.get("tags", [])
                    ai_voice = ai_info.get("voice")
                    ai_instrument = ai_info.get("instrument")

                    desc_parts = [f"Music 2000 sample — {dir_name}"]
                    if ai_desc:
                        desc_parts.append(ai_desc)
                    media_item.description = " | ".join(desc_parts)

                    db.flush()

                    # Run extraction pipeline for ffprobe metadata + whisper
                    full_path = str(dest)
                    try:
                        meta = extract_audio_metadata(full_path)
                        acoustic = {"voice": ai_voice, "instrument": ai_instrument}
                        if ai_tag_list:
                            acoustic["ai_tags"] = ai_tag_list
                        audio_meta = MediaAudioMeta(
                            media_item_id=media_item.id,
                            duration_seconds=meta["duration_seconds"],
                            sample_rate=meta["sample_rate"],
                            channels=meta["channels"],
                            bit_depth=meta["bit_depth"],
                            acoustic_tags=json.dumps(acoustic),
                        )
                        db.add(audio_meta)
                        db.flush()
                    except Exception as exc:
                        log(f"  WARNING: ffprobe failed for {renamed}: {exc}")

                    db.commit()
                    new_count += 1

                    # Sync to Meilisearch (routes to samples-bored via source_type)
                    try:
                        sync_media_item(db, media_item)
                    except Exception as exc:
                        log(f"  WARNING: Meilisearch sync failed for {renamed}: {exc}")

                    if new_count % 200 == 0:
                        log(f"  [{new_count}/{total}] new={new_count} dup={dup_count} err={error_count}")

                except Exception as exc:
                    log(f"  ERROR {renamed}: {exc}")
                    db.rollback()
                    error_count += 1

            log(f"\nDone! New: {new_count}, Duplicates: {dup_count}, Errors: {error_count} / {total}")

        finally:
            db.close()

    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path-to-zip>")
        print()
        print("Indexes WAV files from a Music 2000 sample library zip into")
        print("the a-u.supply search engine (samples-bored Meilisearch index).")
        print()
        print("Set DEEPSEEK_API_KEY=sk-... to enable AI tagging for sound effects.")
        sys.exit(1)

    zip_path = sys.argv[1]
    index_samples(zip_path)


if __name__ == "__main__":
    main()
