"""Puke Box ingest: scrape Slack #midieval for Daily MIDI entries and archive them.

Each Daily MIDI message from the #midieval Slack channel contains 4 MIDI file
attachments (melody, drums, bass, chords) plus structured musical metadata in
the message text. This module:

1. Parses the message text to extract scale, root, tempo, description, chords,
   melody instrument, and temperature.
2. Downloads the 4 MIDI stems from the Slack thread.
3. Synthesizes an OGG preview (sine-wave mix via pukebox_synth + ffmpeg).
4. Creates a MediaItem (audio/OGG) + MediaSource + MediaAudioMeta +
   MediaPukeBoxMeta in the database.
5. Syncs to the dedicated `puke-box` Meilisearch index.

Deduplication: skips entries whose Slack thread_ts already has a MediaSource.

Ported from ausupply.github.io/puke-box/scrape_midieval.py (proven over 138+
entries) but writes to SQLite + Meilisearch instead of committing to git.
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from server.models import (
    MediaAudioMeta,
    MediaItem,
    MediaPukeBoxMeta,
    MediaSource,
    SessionLocal,
)
from server.slack_scraper import download_slack_file, slack_api

logger = logging.getLogger(__name__)

CHANNEL_NAME = "midieval"
MIDI_FILENAMES = {"melody.mid", "drums.mid", "bass.mid", "chords.mid"}
OUTPUT_INDEX = "puke-box"

SEARCH_MEDIA_DIR = Path(os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data"))


# ---------------------------------------------------------------------------
# Message parsing (ported from scrape_midieval.py)
# ---------------------------------------------------------------------------


def parse_midi_message(text: str) -> dict | None:
    """Parse a Daily MIDI message into structured metadata. Returns None if not a match."""
    header = re.search(
        r'\*Daily MIDI\*\s*—\s*(.+?)\s+in\s+(\w[#b]?)\s+\((\d+)\s*BPM\)', text
    )
    if not header:
        return None

    scale, root, tempo = header.group(1), header.group(2), int(header.group(3))

    # Search for description only AFTER the *Daily MIDI* header
    # (song title on first line may contain underscores)
    text_after_header = text[header.end():]
    desc_match = re.search(r'_(.+?)_', text_after_header)
    description = desc_match.group(1) if desc_match else ""

    chords_match = re.search(r':musical_score: Chords\s*—\s*(.+)', text)
    chords = chords_match.group(1).split() if chords_match else []

    melody_match = re.search(r'Melody.*?MIDI\s+(\d+)', text)
    melody_instrument = int(melody_match.group(1)) if melody_match else 0

    temp_match = re.search(r'temperature\s+([\d.]+)', text)
    temperature = float(temp_match.group(1)) if temp_match else 1.0

    return {
        "scale": scale,
        "root": root,
        "tempo": tempo,
        "description": description,
        "chords": chords,
        "melody_instrument": melody_instrument,
        "temperature": temperature,
    }


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------


def _find_channel_id(client_token: str) -> str:
    """Find the #midieval channel ID by paginating through conversations.list.

    Uses the raw slack_api helper (same as slack_scraper) so we don't need
    a slack_sdk WebClient.
    """
    cursor = None
    while True:
        params: dict = {"types": "public_channel", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = slack_api("conversations.list", params)
        if not resp.get("ok"):
            raise RuntimeError(f"conversations.list failed: {resp.get('error')}")
        for ch in resp.get("channels", []):
            if ch.get("name") == CHANNEL_NAME:
                return ch["id"]
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    raise ValueError(f"Channel #{CHANNEL_NAME} not found")


def _download_with_auth(url: str, dest: Path) -> bool:
    """Download a Slack file URL to dest. Wraps download_slack_file."""
    return download_slack_file(url, dest)


def _fetch_midi_messages(channel_id: str) -> list[dict]:
    """Fetch all Daily MIDI messages from channel history.

    Uses cursor-based pagination. Each message is parsed with
    parse_midi_message(); non-matching messages are skipped.
    Returns a list of parsed metadata dicts with entry_id + thread_ts.
    """
    results = []
    cursor = None
    while True:
        params: dict = {"channel": channel_id, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = slack_api("conversations.history", params)
        if not resp.get("ok"):
            raise RuntimeError(f"conversations.history failed: {resp.get('error')}")
        for msg in resp.get("messages", []):
            text = msg.get("text", "")
            parsed = parse_midi_message(text)
            if parsed is None:
                continue
            ts = msg["ts"]
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            parsed["entry_id"] = dt.strftime("%Y-%m-%d-%H%M%S")
            parsed["thread_ts"] = ts
            results.append(parsed)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    logger.info("Found %d Daily MIDI messages in #%s", len(results), CHANNEL_NAME)
    return results


def _download_thread_midi_files(
    channel_id: str,
    thread_ts: str,
    output_dir: Path,
) -> dict[str, str]:
    """Download MIDI files from a thread's replies.

    Returns a dict mapping stem name (melody/drums/bass/chords) to the
    absolute path of the downloaded .mid file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, str] = {}

    cursor = None
    while True:
        params: dict = {"channel": channel_id, "ts": thread_ts, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = slack_api("conversations.replies", params)
        if not resp.get("ok"):
            logger.warning("conversations.replies failed for %s: %s", thread_ts, resp.get("error"))
            break
        for msg in resp.get("messages", []):
            for f in msg.get("files", []):
                name = f.get("name", "")
                if name not in MIDI_FILENAMES:
                    continue
                url = f.get("url_private_download") or f.get("url_private")
                if not url:
                    continue
                dest = output_dir / name
                if _download_with_auth(url, dest):
                    # Validate it's a real MIDI file
                    data = dest.read_bytes()
                    if data.startswith(b"MThd"):
                        stem = name.replace(".mid", "")
                        downloaded[stem] = str(dest)
                        logger.info("Downloaded %s (%d bytes)", name, len(data))
                    else:
                        logger.warning("%s: not a valid MIDI file, skipping", name)
                        dest.unlink(missing_ok=True)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return downloaded


# ---------------------------------------------------------------------------
# OGG synthesis
# ---------------------------------------------------------------------------


def _synthesize_ogg(midi_dir: Path, ogg_path: Path) -> bool:
    """Synthesize MIDI files to OGG via WAV intermediate.

    Uses pukebox_synth to generate a WAV preview, then converts to OGG
    with ffmpeg for smaller file size.
    """
    from server.pukebox_synth import synthesize_preview

    wav_path = ogg_path.with_suffix(".wav")
    try:
        if not synthesize_preview(midi_dir, wav_path):
            logger.error("Synthesizer returned failure")
            return False
    except Exception as e:
        logger.error(f"Synthesis failed: {e}")
        return False

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path), "-b:a", "64k", str(ogg_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error(f"ffmpeg failed: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"ffmpeg conversion failed: {e}")
        return False
    finally:
        if wav_path.exists():
            wav_path.unlink()

    return True


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _storage_dir_for_entry(entry_id: str) -> Path:
    """Return the storage directory for a puke-box entry: audio/YYYY-MM/<entry_id>/"""
    month = entry_id[:7]  # YYYY-MM
    return SEARCH_MEDIA_DIR / "audio" / month / entry_id


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def _existing_entry_ids(db) -> set[str]:
    """Return set of entry_ids already ingested (via MediaPukeBoxMeta)."""
    rows = db.query(MediaPukeBoxMeta.entry_id).all()
    return {r[0] for r in rows}


def _ingest_entry(
    db,
    msg: dict,
    channel_id: str,
    tmp_dir: Path,
) -> bool:
    """Ingest a single Daily MIDI message. Returns True on success."""
    entry_id = msg["entry_id"]
    entry_dir = tmp_dir / entry_id
    entry_dir.mkdir(parents=True, exist_ok=True)

    # Download MIDI stems
    midi_paths = _download_thread_midi_files(channel_id, msg["thread_ts"], entry_dir)
    if len(midi_paths) < 4:
        logger.warning("%s: only %d MIDI files downloaded (expected 4)", entry_id, len(midi_paths))

    if not midi_paths:
        logger.warning("%s: no MIDI files, skipping", entry_id)
        return False

    # Synthesize OGG preview
    ogg_tmp = entry_dir / "preview.ogg"
    if not _synthesize_ogg(entry_dir, ogg_tmp):
        logger.warning("%s: OGG synthesis failed, skipping", entry_id)
        return False

    # Move files to permanent storage
    store_dir = _storage_dir_for_entry(entry_id)
    store_dir.mkdir(parents=True, exist_ok=True)

    ogg_dest = store_dir / "preview.ogg"
    ogg_dest.write_bytes(ogg_tmp.read_bytes())

    midi_rel: dict[str, str] = {}
    for stem, src_path in midi_paths.items():
        src = Path(src_path)
        midi_dest = store_dir / f"{stem}.mid"
        midi_dest.write_bytes(src.read_bytes())
        midi_rel[stem] = str(midi_dest.relative_to(SEARCH_MEDIA_DIR))

    ogg_rel = str(ogg_dest.relative_to(SEARCH_MEDIA_DIR))
    sha256 = _sha256_file(ogg_dest)
    file_size = ogg_dest.stat().st_size

    # Get audio duration via ffprobe
    duration = 0.0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(ogg_dest)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            duration = float(result.stdout.strip())
    except Exception:
        pass

    # Create MediaItem
    media_item_id = str(uuid.uuid4())
    media_item = MediaItem(
        id=media_item_id,
        sha256=sha256,
        filename=f"{entry_id}-preview.ogg",
        file_path=ogg_rel,
        media_type="audio",
        file_size_bytes=file_size,
        mime_type="audio/ogg",
        output_index=OUTPUT_INDEX,
        description=msg["description"],
    )
    db.add(media_item)
    db.flush()

    # MediaSource (Slack)
    source = MediaSource(
        id=str(uuid.uuid4()),
        media_item_id=media_item_id,
        source_type="slack_file",
        source_channel=CHANNEL_NAME,
        slack_message_ts=msg["thread_ts"],
        slack_message_text=None,
    )
    db.add(source)
    db.flush()

    # MediaAudioMeta
    audio_meta = MediaAudioMeta(
        media_item_id=media_item_id,
        duration_seconds=duration,
        sample_rate=22050,
        channels=1,
    )
    db.add(audio_meta)

    # MediaPukeBoxMeta
    pbm = MediaPukeBoxMeta(
        media_item_id=media_item_id,
        entry_id=entry_id,
        scale=msg["scale"],
        root=msg["root"],
        tempo=msg["tempo"],
        chords=json.dumps(msg["chords"]),
        description=msg["description"],
        melody_instrument=msg["melody_instrument"],
        temperature=msg["temperature"],
        midi_paths=json.dumps(midi_rel),
    )
    db.add(pbm)

    # Sync to Meilisearch
    try:
        from server.search_client import sync_media_item
        sync_media_item(db, media_item)
    except Exception as e:
        logger.error("Meilisearch sync failed for %s: %s", entry_id, e)

    logger.info("Ingested %s — %s in %s (%d BPM)", entry_id, msg["scale"], msg["root"], msg["tempo"])
    return True


def run_ingest() -> dict:
    """Main orchestration. Returns a stats dict."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        logger.error("SLACK_BOT_TOKEN environment variable is required")
        return {"error": "no token", "ingested": 0, "skipped": 0, "errors": 0}

    db = SessionLocal()
    try:
        channel_id = _find_channel_id(token)
        messages = _fetch_midi_messages(channel_id)

        existing = _existing_entry_ids(db)
        new_messages = [m for m in messages if m["entry_id"] not in existing]
        logger.info("Found %d new entries to process", len(new_messages))

        ingested = 0
        errors = 0
        with tempfile.TemporaryDirectory(prefix="pukebox_") as tmp:
            tmp_dir = Path(tmp)
            for msg in new_messages:
                try:
                    if _ingest_entry(db, msg, channel_id, tmp_dir):
                        ingested += 1
                    db.commit()
                except Exception as e:
                    db.rollback()
                    logger.error("Error ingesting %s: %s", msg["entry_id"], e)
                    errors += 1

        return {
            "total_messages": len(messages),
            "new": len(new_messages),
            "ingested": ingested,
            "errors": errors,
        }
    finally:
        db.close()


def backfill_from_legacy(legacy_dir: Path | None = None) -> dict:
    """Backfill from the existing /srv/legacy-site/puke-box/ directory.

    Reads the on-disk entry directories (each with preview.ogg + 4 MIDI stems
    + meta.json) and creates MediaItem/MediaSource/MediaAudioMeta/
    MediaPukeBoxMeta rows without re-downloading from Slack.

    This avoids re-scraping Slack (some old file attachments are gone) and
    is the fastest path to getting the existing 138+ entries into the search
    engine.
    """
    import shutil

    if legacy_dir is None:
        legacy_dir = Path(os.environ.get("LEGACY_SITE_DIR", "/srv/legacy-site")) / "puke-box"

    if not legacy_dir.is_dir():
        return {"error": f"legacy dir not found: {legacy_dir}", "ingested": 0, "errors": 0}

    db = SessionLocal()
    try:
        existing = _existing_entry_ids(db)
        ingested = 0
        errors = 0
        skipped = 0

        # Find all entry directories matching YYYY-MM-DD(-HHMMSS)?
        entry_dirs = sorted(
            d for d in legacy_dir.iterdir()
            if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}(-\d{6})?$", d.name)
        )

        for entry_dir in entry_dirs:
            entry_id = entry_dir.name
            if entry_id in existing:
                skipped += 1
                continue

            ogg_src = entry_dir / "preview.ogg"
            if not ogg_src.exists():
                logger.warning("%s: no preview.ogg, skipping", entry_id)
                errors += 1
                continue

            # Read meta.json if present
            meta_path = entry_dir / "meta.json"
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass

            # Move files to permanent storage
            store_dir = _storage_dir_for_entry(entry_id)
            store_dir.mkdir(parents=True, exist_ok=True)

            ogg_dest = store_dir / "preview.ogg"
            shutil.copy2(str(ogg_src), str(ogg_dest))

            midi_rel: dict[str, str] = {}
            for stem in ("melody", "drums", "bass", "chords"):
                midi_src = entry_dir / f"{stem}.mid"
                if midi_src.exists():
                    midi_dest = store_dir / f"{stem}.mid"
                    shutil.copy2(str(midi_src), str(midi_dest))
                    midi_rel[stem] = str(midi_dest.relative_to(SEARCH_MEDIA_DIR))

            ogg_rel = str(ogg_dest.relative_to(SEARCH_MEDIA_DIR))
            sha256 = _sha256_file(ogg_dest)
            file_size = ogg_dest.stat().st_size

            # Get audio duration
            duration = 0.0
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(ogg_dest)],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    duration = float(result.stdout.strip())
            except Exception:
                pass

            media_item_id = str(uuid.uuid4())
            media_item = MediaItem(
                id=media_item_id,
                sha256=sha256,
                filename=f"{entry_id}-preview.ogg",
                file_path=ogg_rel,
                media_type="audio",
                file_size_bytes=file_size,
                mime_type="audio/ogg",
                output_index=OUTPUT_INDEX,
                description=meta.get("description", ""),
            )
            db.add(media_item)
            db.flush()

            source = MediaSource(
                id=str(uuid.uuid4()),
                media_item_id=media_item_id,
                source_type="slack_file",
                source_channel=CHANNEL_NAME,
            )
            db.add(source)

            audio_meta = MediaAudioMeta(
                media_item_id=media_item_id,
                duration_seconds=duration,
                sample_rate=22050,
                channels=1,
            )
            db.add(audio_meta)

            pbm = MediaPukeBoxMeta(
                media_item_id=media_item_id,
                entry_id=entry_id,
                scale=meta.get("scale"),
                root=meta.get("root"),
                tempo=meta.get("tempo"),
                chords=json.dumps(meta.get("chords", [])),
                description=meta.get("description", ""),
                melody_instrument=meta.get("melody_instrument"),
                temperature=meta.get("temperature"),
                midi_paths=json.dumps(midi_rel) if midi_rel else None,
            )
            db.add(pbm)

            try:
                from server.search_client import sync_media_item
                sync_media_item(db, media_item)
            except Exception as e:
                logger.error("Meilisearch sync failed for %s: %s", entry_id, e)

            db.commit()
            ingested += 1
            logger.info("Backfilled %s", entry_id)

        return {
            "total_dirs": len(entry_dirs),
            "ingested": ingested,
            "skipped": skipped,
            "errors": errors,
        }
    except Exception as e:
        db.rollback()
        logger.error("Backfill failed: %s", e)
        return {"error": str(e), "ingested": 0, "errors": 1}
    finally:
        db.close()
