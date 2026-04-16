"""
Async metadata extraction pipeline for the media search engine.

Extracts technical metadata, dominant colors, transcripts, and thumbnails
from ingested media items. Runs as background tasks so ingest is never blocked.
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SEARCH_MEDIA_DIR = os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data")

# ---------------------------------------------------------------------------
# Whisper model lifecycle — load on demand, unload after 5 min idle
# ---------------------------------------------------------------------------

_whisper_model = None
_whisper_timer = None
_whisper_lock = threading.Lock()
_WHISPER_IDLE_TIMEOUT = 300  # 5 minutes


def _get_whisper_model():
    global _whisper_model, _whisper_timer
    with _whisper_lock:
        if _whisper_timer:
            _whisper_timer.cancel()
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            logger.info("Loading faster-whisper model (medium, int8)...")
            _whisper_model = WhisperModel("medium", device="cpu", compute_type="int8")
            logger.info("Whisper model loaded.")
        # Reset idle timer
        _whisper_timer = threading.Timer(_WHISPER_IDLE_TIMEOUT, _unload_whisper)
        _whisper_timer.daemon = True
        _whisper_timer.start()
        return _whisper_model


def _unload_whisper():
    global _whisper_model, _whisper_timer
    with _whisper_lock:
        logger.info("Unloading whisper model after idle timeout.")
        _whisper_model = None
        _whisper_timer = None


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------


def extract_image_metadata(file_path: str) -> dict:
    """Extract width, height, and format from an image using Pillow."""
    from PIL import Image

    with Image.open(file_path) as img:
        width, height = img.size
        fmt = img.format or "UNKNOWN"
    return {"width": width, "height": height, "format": fmt}


def extract_dominant_colors(file_path: str, num_colors: int = 5) -> list[str]:
    """Extract dominant colors from an image via k-means clustering.

    Falls back to Pillow's quantize method if sklearn is unavailable.
    Returns a list of hex color strings like ["#1a1a2e", "#e94560"].
    """
    from PIL import Image

    with Image.open(file_path) as img:
        # Convert to RGB, downsample for speed
        img = img.convert("RGB")
        img = img.resize((100, 100), Image.LANCZOS)

        try:
            return _dominant_colors_kmeans(img, num_colors)
        except ImportError:
            logger.debug("sklearn not available, falling back to Pillow quantize.")
            return _dominant_colors_quantize(img, num_colors)


def _dominant_colors_kmeans(img, num_colors: int) -> list[str]:
    """K-means clustering via sklearn on pixel data."""
    import numpy as np
    from sklearn.cluster import KMeans

    pixels = np.array(img).reshape(-1, 3)
    kmeans = KMeans(n_clusters=num_colors, n_init=10, random_state=42)
    kmeans.fit(pixels)

    # Sort by cluster size (most dominant first)
    _, counts = np.unique(kmeans.labels_, return_counts=True)
    order = counts.argsort()[::-1]
    centers = kmeans.cluster_centers_[order].astype(int)

    return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in centers]


def _dominant_colors_quantize(img, num_colors: int) -> list[str]:
    """Fallback using Pillow's built-in color quantization."""
    quantized = img.quantize(colors=num_colors)
    palette = quantized.getpalette()
    if not palette:
        return []
    # Palette is flat [R, G, B, R, G, B, ...], take first num_colors entries
    colors = []
    for i in range(min(num_colors, len(palette) // 3)):
        r, g, b = palette[i * 3 : i * 3 + 3]
        colors.append(f"#{r:02x}{g:02x}{b:02x}")
    return colors


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------


def extract_audio_metadata(file_path: str) -> dict:
    """Extract audio metadata via ffprobe.

    Returns dict with duration_seconds, sample_rate, channels, bit_depth.
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)

    # Find the audio stream
    audio_stream = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            audio_stream = stream
            break

    if audio_stream is None:
        raise RuntimeError("No audio stream found in file")

    duration = float(data.get("format", {}).get("duration", 0))
    sample_rate = int(audio_stream.get("sample_rate", 0))
    channels = int(audio_stream.get("channels", 0))

    # bit_depth: try bits_per_raw_sample, then bits_per_sample
    bit_depth = None
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        val = audio_stream.get(key)
        if val and str(val).isdigit() and int(val) > 0:
            bit_depth = int(val)
            break

    return {
        "duration_seconds": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "bit_depth": bit_depth,
    }


def transcribe_audio(file_path: str) -> dict | None:
    """Transcribe speech from an audio file using faster-whisper.

    Returns {"transcript": str, "confidence": float} or None if no speech
    is detected or faster-whisper is not available.
    """
    try:
        model = _get_whisper_model()
    except ImportError:
        logger.warning("faster-whisper not installed, skipping transcription.")
        return None

    segments, info = model.transcribe(
        file_path,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    texts = []
    total_confidence = 0.0
    segment_count = 0

    for segment in segments:
        texts.append(segment.text.strip())
        total_confidence += segment.avg_logprob
        segment_count += 1

    if segment_count == 0:
        return None

    transcript = " ".join(texts).strip()
    if not transcript:
        return None

    # avg_logprob is negative (log scale); convert to a 0-1 confidence
    # by taking exp of the average log probability
    import math

    avg_logprob = total_confidence / segment_count
    confidence = round(math.exp(avg_logprob), 4)

    return {"transcript": transcript, "confidence": confidence}


# ---------------------------------------------------------------------------
# Video extraction
# ---------------------------------------------------------------------------


def extract_video_metadata(file_path: str) -> dict:
    """Extract video metadata via ffprobe.

    Returns dict with duration_seconds, width, height, fps.
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)

    # Find the video stream
    video_stream = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if video_stream is None:
        raise RuntimeError("No video stream found in file")

    duration = float(data.get("format", {}).get("duration", 0))
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    # Parse fps from r_frame_rate (e.g. "30/1", "24000/1001")
    fps = None
    r_frame_rate = video_stream.get("r_frame_rate", "")
    if "/" in r_frame_rate:
        num, den = r_frame_rate.split("/")
        if int(den) > 0:
            fps = round(int(num) / int(den), 3)

    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "fps": fps,
    }


def generate_video_thumbnail(file_path: str, output_path: str) -> bool:
    """Generate a WEBP thumbnail from a video frame at ~10% duration.

    Returns True on success, False on failure.
    """
    # First get duration to calculate 10% offset
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                file_path,
            ],
            capture_output=True,
            text=True,
        )
        duration = float(json.loads(result.stdout).get("format", {}).get("duration", 0))
        seek_time = max(0, duration * 0.1)
    except Exception:
        seek_time = 1  # Fallback to 1 second

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss", str(seek_time),
            "-i", file_path,
            "-vframes", "1",
            "-vf", "scale='min(640,iw)':-2",
            "-c:v", "libwebp",
            "-quality", "80",
            output_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Thumbnail generation failed: %s", result.stderr.strip())
        return False
    return True


def _has_audio_stream(video_path: str) -> bool:
    """Check if a video file contains an audio stream."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and "audio" in result.stdout


def _extract_audio_track(video_path: str, output_path: str) -> bool:
    """Extract audio track from a video file to a temporary WAV file."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            output_path,
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Failure logging helper
# ---------------------------------------------------------------------------


def _log_failure(db, media_item_id: str, extraction_type: str, error: Exception):
    """Record an extraction failure in the database."""
    from models import ExtractionFailure

    now = datetime.now(timezone.utc)

    # Check for existing unresolved failure of the same type
    existing = (
        db.query(ExtractionFailure)
        .filter(
            ExtractionFailure.media_item_id == media_item_id,
            ExtractionFailure.extraction_type == extraction_type,
            ExtractionFailure.resolved == False,  # noqa: E712
        )
        .first()
    )
    if existing:
        existing.attempts += 1
        existing.error_message = str(error)
        existing.last_attempt_at = now
    else:
        failure = ExtractionFailure(
            id=str(uuid.uuid4()),
            media_item_id=media_item_id,
            extraction_type=extraction_type,
            error_message=str(error),
            attempts=1,
            last_attempt_at=now,
            resolved=False,
        )
        db.add(failure)
    db.commit()


# ---------------------------------------------------------------------------
# Meilisearch sync helper
# ---------------------------------------------------------------------------


def _sync_to_search(db, media_item):
    """Attempt to sync a media item to Meilisearch if the client is available."""
    try:
        from search_client import sync_media_item

        sync_media_item(db, media_item)
    except ImportError:
        pass  # search_client not available yet
    except Exception as exc:
        logger.warning("Meilisearch sync failed: %s", exc)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_extraction(media_item_id: str, file_path: str, media_type: str):
    """Main extraction entry point.

    Runs the appropriate extraction pipeline based on media_type.
    Each step is independent — if one fails, the others still run.
    Failures are logged to the ExtractionFailure table.
    """
    from models import (
        MediaAudioMeta,
        MediaImageMeta,
        MediaItem,
        MediaVideoMeta,
        SessionLocal,
    )

    db = SessionLocal()
    try:
        media_item = db.query(MediaItem).filter(MediaItem.id == media_item_id).first()
        if media_item is None:
            logger.error("Media item %s not found, skipping extraction.", media_item_id)
            return

        if media_type == "image":
            _run_image_extraction(db, media_item_id, file_path, MediaImageMeta)
        elif media_type == "audio":
            _run_audio_extraction(db, media_item_id, file_path, MediaAudioMeta)
        elif media_type == "video":
            _run_video_extraction(db, media_item_id, file_path, MediaVideoMeta)
        else:
            logger.warning("Unknown media type '%s' for item %s", media_type, media_item_id)
            return

        # Refresh the media item and sync to search
        db.refresh(media_item)
        _sync_to_search(db, media_item)

    except Exception as exc:
        logger.exception("Unexpected error during extraction for %s: %s", media_item_id, exc)
    finally:
        db.close()


def _run_image_extraction(db, media_item_id: str, file_path: str, MediaImageMeta):
    """Run all image extraction steps."""
    meta_kwargs = {}

    # Step 1: Basic image metadata
    try:
        img_meta = extract_image_metadata(file_path)
        meta_kwargs.update(img_meta)
    except Exception as exc:
        logger.error("Image metadata extraction failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "image_metadata", exc)

    # Step 2: Dominant colors
    try:
        colors = extract_dominant_colors(file_path)
        meta_kwargs["dominant_colors"] = json.dumps(colors)
    except Exception as exc:
        logger.error("Dominant color extraction failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "dominant_colors", exc)

    if not meta_kwargs.get("width"):
        # Can't create meta record without basic dimensions
        return

    # Create or update the meta record
    existing = db.query(MediaImageMeta).filter(MediaImageMeta.media_item_id == media_item_id).first()
    if existing:
        for key, val in meta_kwargs.items():
            setattr(existing, key, val)
    else:
        record = MediaImageMeta(media_item_id=media_item_id, **meta_kwargs)
        db.add(record)
    db.commit()


def _run_audio_extraction(db, media_item_id: str, file_path: str, MediaAudioMeta):
    """Run all audio extraction steps."""
    meta_kwargs = {}

    # Step 1: ffprobe metadata
    try:
        audio_meta = extract_audio_metadata(file_path)
        meta_kwargs.update(audio_meta)
    except Exception as exc:
        logger.error("Audio metadata extraction failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "ffprobe", exc)

    # Step 2: Transcription
    try:
        transcript_result = transcribe_audio(file_path)
        if transcript_result:
            meta_kwargs["transcript"] = transcript_result["transcript"]
            meta_kwargs["transcript_confidence"] = transcript_result["confidence"]
    except Exception as exc:
        logger.error("Audio transcription failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "whisper", exc)

    if not meta_kwargs.get("duration_seconds") and "duration_seconds" not in meta_kwargs:
        # Can't create meta record without basic audio info
        return

    # Create or update the meta record
    existing = db.query(MediaAudioMeta).filter(MediaAudioMeta.media_item_id == media_item_id).first()
    if existing:
        for key, val in meta_kwargs.items():
            setattr(existing, key, val)
    else:
        record = MediaAudioMeta(media_item_id=media_item_id, **meta_kwargs)
        db.add(record)
    db.commit()


def _run_video_extraction(db, media_item_id: str, file_path: str, MediaVideoMeta):
    """Run all video extraction steps."""
    meta_kwargs = {}

    # Step 1: ffprobe metadata
    try:
        video_meta = extract_video_metadata(file_path)
        meta_kwargs.update(video_meta)
    except Exception as exc:
        logger.error("Video metadata extraction failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "ffprobe", exc)

    # Step 2: Thumbnail generation
    try:
        basename = Path(file_path).stem
        thumb_path = str(Path(file_path).parent / f"{basename}_thumb.webp")
        if generate_video_thumbnail(file_path, thumb_path):
            meta_kwargs["thumbnail_path"] = thumb_path
        else:
            raise RuntimeError("ffmpeg returned non-zero exit code")
    except Exception as exc:
        logger.error("Thumbnail generation failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "thumbnail", exc)

    # Step 3: Audio transcription from extracted audio track
    if not _has_audio_stream(file_path):
        logger.info("No audio stream in %s, skipping transcription.", media_item_id)
    else:
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_audio_path = tmp.name
            try:
                if _extract_audio_track(file_path, tmp_audio_path):
                    transcript_result = transcribe_audio(tmp_audio_path)
                    if transcript_result:
                        meta_kwargs["audio_transcript"] = transcript_result["transcript"]
                        meta_kwargs["transcript_confidence"] = transcript_result["confidence"]
                else:
                    raise RuntimeError("Failed to extract audio track from video")
            finally:
                if os.path.exists(tmp_audio_path):
                    os.unlink(tmp_audio_path)
        except Exception as exc:
            logger.error("Video transcription failed for %s: %s", media_item_id, exc)
            _log_failure(db, media_item_id, "whisper", exc)

    if not meta_kwargs.get("width"):
        # Can't create meta record without basic video info
        return

    # Create or update the meta record
    existing = db.query(MediaVideoMeta).filter(MediaVideoMeta.media_item_id == media_item_id).first()
    if existing:
        for key, val in meta_kwargs.items():
            setattr(existing, key, val)
    else:
        record = MediaVideoMeta(media_item_id=media_item_id, **meta_kwargs)
        db.add(record)
    db.commit()


# ---------------------------------------------------------------------------
# Async / background execution
# ---------------------------------------------------------------------------


def run_extraction_async(media_item_id: str, file_path: str, media_type: str):
    """Run extraction in a background thread so it doesn't block the response."""
    thread = threading.Thread(
        target=run_extraction,
        args=(media_item_id, file_path, media_type),
        daemon=True,
    )
    thread.start()
    logger.info(
        "Started background extraction for %s (type=%s)",
        media_item_id,
        media_type,
    )


# ---------------------------------------------------------------------------
# Retry & batch operations
# ---------------------------------------------------------------------------


def retry_extraction(failure_id: str):
    """Retry a single failed extraction step.

    Loads the ExtractionFailure record, re-runs just that extraction type,
    marks as resolved on success, increments attempts on failure.
    """
    from models import (
        ExtractionFailure,
        MediaAudioMeta,
        MediaImageMeta,
        MediaItem,
        MediaVideoMeta,
        SessionLocal,
    )

    db = SessionLocal()
    try:
        failure = db.query(ExtractionFailure).filter(ExtractionFailure.id == failure_id).first()
        if failure is None:
            logger.error("ExtractionFailure %s not found.", failure_id)
            return

        media_item = db.query(MediaItem).filter(MediaItem.id == failure.media_item_id).first()
        if media_item is None:
            logger.error("Media item %s not found for failure %s.", failure.media_item_id, failure_id)
            return

        file_path = media_item.file_path
        extraction_type = failure.extraction_type
        now = datetime.now(timezone.utc)

        try:
            _retry_single_step(db, media_item, file_path, extraction_type)
            failure.resolved = True
            failure.last_attempt_at = now
            db.commit()
            logger.info("Retry succeeded for failure %s (type=%s).", failure_id, extraction_type)

            # Sync updated item to search
            db.refresh(media_item)
            _sync_to_search(db, media_item)

        except Exception as exc:
            failure.attempts += 1
            failure.error_message = str(exc)
            failure.last_attempt_at = now
            db.commit()
            logger.error("Retry failed for failure %s: %s", failure_id, exc)

    finally:
        db.close()


def _retry_single_step(db, media_item, file_path: str, extraction_type: str):
    """Re-run a single extraction step by type."""
    from models import MediaAudioMeta, MediaImageMeta, MediaVideoMeta

    media_item_id = media_item.id

    if extraction_type == "image_metadata":
        img_meta = extract_image_metadata(file_path)
        _upsert_meta(db, MediaImageMeta, media_item_id, img_meta)

    elif extraction_type == "dominant_colors":
        colors = extract_dominant_colors(file_path)
        _upsert_meta(db, MediaImageMeta, media_item_id, {"dominant_colors": json.dumps(colors)})

    elif extraction_type == "ffprobe":
        if media_item.media_type == "audio":
            audio_meta = extract_audio_metadata(file_path)
            _upsert_meta(db, MediaAudioMeta, media_item_id, audio_meta)
        elif media_item.media_type == "video":
            video_meta = extract_video_metadata(file_path)
            _upsert_meta(db, MediaVideoMeta, media_item_id, video_meta)

    elif extraction_type == "thumbnail":
        basename = Path(file_path).stem
        thumb_path = str(Path(file_path).parent / f"{basename}_thumb.webp")
        if not generate_video_thumbnail(file_path, thumb_path):
            raise RuntimeError("Thumbnail generation failed")
        _upsert_meta(db, MediaVideoMeta, media_item_id, {"thumbnail_path": thumb_path})

    elif extraction_type == "whisper":
        if media_item.media_type == "audio":
            result = transcribe_audio(file_path)
            if result:
                _upsert_meta(db, MediaAudioMeta, media_item_id, {
                    "transcript": result["transcript"],
                    "transcript_confidence": result["confidence"],
                })
        elif media_item.media_type == "video":
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_audio_path = tmp.name
            try:
                if not _extract_audio_track(file_path, tmp_audio_path):
                    raise RuntimeError("Failed to extract audio track from video")
                result = transcribe_audio(tmp_audio_path)
                if result:
                    _upsert_meta(db, MediaVideoMeta, media_item_id, {
                        "audio_transcript": result["transcript"],
                        "transcript_confidence": result["confidence"],
                    })
            finally:
                if os.path.exists(tmp_audio_path):
                    os.unlink(tmp_audio_path)
    else:
        raise ValueError(f"Unknown extraction type: {extraction_type}")


def _upsert_meta(db, MetaClass, media_item_id: str, updates: dict):
    """Create or update a metadata record."""
    existing = db.query(MetaClass).filter(MetaClass.media_item_id == media_item_id).first()
    if existing:
        for key, val in updates.items():
            setattr(existing, key, val)
    else:
        record = MetaClass(media_item_id=media_item_id, **updates)
        db.add(record)
    db.commit()


def batch_re_extract(media_item_ids: list[str]):
    """Re-run full extraction for multiple media items."""
    from models import MediaItem, SessionLocal

    db = SessionLocal()
    try:
        items = db.query(MediaItem).filter(MediaItem.id.in_(media_item_ids)).all()
        for item in items:
            logger.info("Queuing re-extraction for %s (%s)", item.id, item.media_type)
            run_extraction_async(item.id, item.file_path, item.media_type)
    finally:
        db.close()
