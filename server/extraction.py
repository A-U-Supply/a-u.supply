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

# Skip speech transcription for media longer than this many seconds. Long
# uploads (jam sessions, mixes) produce useless transcripts at huge CPU cost.
# Set to 0 to transcribe everything regardless of length.
WHISPER_MAX_SECONDS = float(os.environ.get("WHISPER_MAX_SECONDS", "900"))

# Teach Pillow how to open .heic files (iPhone format). Safe no-op if the
# plugin isn't installed; OCR will just log an "cannot identify" error for
# those items as before.
try:
    from pillow_heif import register_heif_opener as _register_heif_opener

    _register_heif_opener()
except ImportError:
    pass

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


# Three sizes per image so <img srcset> picks the right one and the
# image viewer has a high-quality source without hitting the full original:
#   small  (_thumb_sm.webp) — 128px,  mobile grids / narrow strips / OG unfurl
#   medium (_thumb.webp)    — 400px,  default / desktop grid tiles
#   large  (_thumb_lg.webp) — 1600px, image viewer scaled render
IMAGE_THUMBNAIL_MAX = (400, 400)
IMAGE_THUMBNAIL_SM_MAX = (128, 128)
IMAGE_THUMBNAIL_LG_MAX = (1600, 1600)
IMAGE_THUMBNAIL_QUALITY = 85


def _image_thumbnail_path(file_path: str) -> str:
    """Return the conventional ``<stem>_thumb.webp`` sibling path for an image."""
    p = Path(file_path)
    return str(p.with_name(p.stem + "_thumb.webp"))


def _image_thumbnail_sm_path(file_path: str) -> str:
    """Return the ``<stem>_thumb_sm.webp`` sibling path for an image."""
    p = Path(file_path)
    return str(p.with_name(p.stem + "_thumb_sm.webp"))


def _image_thumbnail_lg_path(file_path: str) -> str:
    """Return the ``<stem>_thumb_lg.webp`` sibling path for an image."""
    p = Path(file_path)
    return str(p.with_name(p.stem + "_thumb_lg.webp"))


def _generate_webp(file_path: str, output_path: str, max_size: tuple[int, int]) -> bool:
    """Generate a WEBP at the given max size, preserving aspect, never upscaling."""
    from PIL import Image

    try:
        with Image.open(file_path) as img:
            img.thumbnail(max_size, Image.LANCZOS)
            img.save(output_path, "WEBP", quality=IMAGE_THUMBNAIL_QUALITY, method=6)
        return True
    except Exception as exc:
        logger.error("Image thumbnail generation failed for %s: %s", file_path, exc)
        return False


def generate_image_thumbnail(file_path: str, output_path: str) -> bool:
    """Generate the 400px-max medium thumbnail."""
    return _generate_webp(file_path, output_path, IMAGE_THUMBNAIL_MAX)


def generate_image_thumbnail_sm(file_path: str, output_path: str) -> bool:
    """Generate the 128px-max small thumbnail (for srcset / mobile / OG unfurl)."""
    return _generate_webp(file_path, output_path, IMAGE_THUMBNAIL_SM_MAX)


def generate_image_thumbnail_lg(file_path: str, output_path: str) -> bool:
    """Generate the 1600px-max large thumbnail (for the image viewer)."""
    return _generate_webp(file_path, output_path, IMAGE_THUMBNAIL_LG_MAX)


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


def pick_accent_color(hex_colors: list[str]) -> str | None:
    """Pick a single UI accent color from dominant-color candidates.

    Candidates are scored in HLS as `saturation × (1 − 0.12·rank)` — mild
    dominance weighting, so a vivid minority color beats a dominant
    near-gray background. Near-monochrome palettes (best saturation < 0.12)
    keep their neutral hue rather than inventing one. The winner is clamped
    for legibility: lightness into [0.35, 0.62], saturation raised to ≥ 0.30
    when colored. Returns "#rrggbb" or None if no parseable candidate.
    """
    import colorsys

    best_hls: tuple[float, float, float] | None = None
    best_score = -1.0
    for rank, raw in enumerate(hex_colors or []):
        if not isinstance(raw, str):
            continue
        candidate = raw.strip()
        if len(candidate) != 7 or not candidate.startswith("#"):
            continue
        try:
            value = int(candidate[1:], 16)
        except ValueError:
            continue
        r = ((value >> 16) & 0xFF) / 255.0
        g = ((value >> 8) & 0xFF) / 255.0
        b = (value & 0xFF) / 255.0
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        score = s * (1.0 - 0.12 * rank)
        if score > best_score:
            best_score = score
            best_hls = (h, l, s)
    if best_hls is None:
        return None
    h, l, s = best_hls
    l = min(max(l, 0.35), 0.62)
    if s >= 0.12:
        s = max(s, 0.30)
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


_OCR_CONF_THRESHOLD = 0.3
_OCR_MAX_DIM = 1600
_OCR_IDLE_TIMEOUT = 300  # match Whisper — unload model after 5 min idle

_easyocr_reader = None
_easyocr_timer = None
_easyocr_lock = threading.Lock()


def _get_easyocr_reader():
    """Lazy-load the EasyOCR reader; mirror the Whisper idle-unload pattern."""
    global _easyocr_reader, _easyocr_timer
    with _easyocr_lock:
        if _easyocr_timer:
            _easyocr_timer.cancel()
        if _easyocr_reader is None:
            import easyocr

            cache_dir = os.environ.get("MODEL_CACHE_DIR", "/app/model-cache")
            easyocr_dir = os.path.join(cache_dir, "easyocr")
            os.makedirs(easyocr_dir, exist_ok=True)
            logger.info("Loading EasyOCR reader (en, cpu)...")
            _easyocr_reader = easyocr.Reader(
                ["en"],
                gpu=False,
                model_storage_directory=easyocr_dir,
                user_network_directory=easyocr_dir,
                download_enabled=True,
                verbose=False,
            )
            logger.info("EasyOCR reader loaded.")
        _easyocr_timer = threading.Timer(_OCR_IDLE_TIMEOUT, _unload_easyocr)
        _easyocr_timer.daemon = True
        _easyocr_timer.start()
        return _easyocr_reader


def _unload_easyocr():
    global _easyocr_reader, _easyocr_timer
    with _easyocr_lock:
        logger.info("Unloading EasyOCR reader after idle timeout.")
        _easyocr_reader = None
        _easyocr_timer = None


def extract_text_ocr(file_path: str) -> str | None:
    """Extract text from an image using EasyOCR.

    Returns the extracted text or None if no text is found or
    easyocr is not available. All processing is in-memory; the source
    file on disk is never modified. The model loads on first call and
    unloads after 5 minutes of inactivity (same pattern as Whisper).
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        logger.warning("Pillow/numpy not installed, skipping OCR.")
        return None

    try:
        reader = _get_easyocr_reader()
    except ImportError:
        logger.warning("easyocr not installed, skipping OCR.")
        return None

    with Image.open(file_path) as img:
        img = img.convert("RGB")
        # Downscale huge images — EasyOCR works well at moderate sizes and
        # its detection step is the slow part. 1600px max keeps inference
        # snappy without losing accuracy on incidental-text photos.
        max_dim = max(img.size)
        if max_dim > _OCR_MAX_DIM:
            scale = _OCR_MAX_DIM / max_dim
            img = img.resize(
                (int(img.width * scale), int(img.height * scale)), Image.LANCZOS
            )
        arr = np.array(img)

    results = reader.readtext(arr, detail=1, paragraph=False)
    # results is a list of (bbox, text, confidence) tuples.
    words = [
        text.strip()
        for _, text, conf in results
        if text and text.strip() and conf >= _OCR_CONF_THRESHOLD
    ]
    if not words:
        return None
    return " ".join(words)


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


def record_extraction_failure(
    db, media_item_id: str, extraction_type: str, error: Exception
) -> None:
    """Record (or bump) an unresolved ExtractionFailure row.

    Public helper — callers outside the extraction pipeline (Meilisearch sync
    sites, etc.) use this to register pipeline failures under the unified
    ExtractionFailure table so The Fallen surfaces every ingest-pipeline issue
    in one place.

    Idempotent: if an unresolved row already exists for the same
    (media_item_id, extraction_type), `attempts` and `last_attempt_at` are
    bumped instead of inserting a duplicate. Notification materializers key
    on row id, so retries do not generate new notifications.

    Best-effort: any failure to write is swallowed (rollback) so that the
    caller's main flow is unaffected.
    """
    from server.models import ExtractionFailure

    now = datetime.now(timezone.utc)
    try:
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
    except Exception:
        logger.exception(
            "Failed to record ExtractionFailure (media=%s, type=%s)",
            media_item_id,
            extraction_type,
        )
        try:
            db.rollback()
        except Exception:
            pass


# Backwards-compat alias for internal callers.
_log_failure = record_extraction_failure


# ---------------------------------------------------------------------------
# Meilisearch sync helper
# ---------------------------------------------------------------------------


def _sync_to_search(db, media_item):
    """Attempt to sync a media item to Meilisearch if the client is available."""
    try:
        from server.search_client import sync_media_item

        sync_media_item(db, media_item)
    except ImportError:
        pass  # search_client not available yet
    except Exception as exc:
        logger.warning("Meilisearch sync failed: %s", exc)
        record_extraction_failure(db, media_item.id, "meilisearch_sync", exc)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_extraction(media_item_id: str, file_path: str, media_type: str):
    """Main extraction entry point.

    Runs the appropriate extraction pipeline based on media_type.
    Each step is independent — if one fails, the others still run.
    Failures are logged to the ExtractionFailure table.
    """
    from server.models import (
        MediaAudioMeta,
        MediaImageMeta,
        MediaItem,
        MediaTag,
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

    # Step 3: Thumbnails — sm (128px) + md (400px) + lg (1600px, for the viewer)
    # All three failures share extraction_type="thumbnail" so one retry covers them.
    thumb_path = _image_thumbnail_path(file_path)
    thumb_sm_path = _image_thumbnail_sm_path(file_path)
    thumb_lg_path = _image_thumbnail_lg_path(file_path)
    try:
        ok_md = generate_image_thumbnail(file_path, thumb_path)
        ok_sm = generate_image_thumbnail_sm(file_path, thumb_sm_path)
        ok_lg = generate_image_thumbnail_lg(file_path, thumb_lg_path)
        if not (ok_md and ok_sm and ok_lg):
            raise RuntimeError("thumbnail generation returned False")
    except Exception as exc:
        logger.error("Image thumbnail failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "thumbnail", exc)

    # Step 4: OCR text extraction
    ocr_text: str | None = None
    try:
        ocr_text = extract_text_ocr(file_path)
        meta_kwargs["caption"] = ocr_text or ""
    except Exception as exc:
        logger.error("OCR extraction failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "ocr", exc)

    if not meta_kwargs.get("width"):
        # Can't create meta record without basic dimensions
        return

    # Step 5: AI vision-model enrichment — description + tags + structured
    # attributes (vibe, color mood, content bools). See ai_description.py.
    # Skipped silently if no vision API key is set so local dev still works.
    if os.environ.get("VISION_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"):
        try:
            from server.ai_description import generate_ai_description

            ai = generate_ai_description(file_path, ocr_caption=ocr_text)
            _apply_ai_description(meta_kwargs, ai, ai_overrides=None)
        except Exception as exc:
            logger.error("AI description failed for %s: %s", media_item_id, exc)
            _log_failure(db, media_item_id, "ai_description", exc)

    # Create or update the meta record
    existing = db.query(MediaImageMeta).filter(MediaImageMeta.media_item_id == media_item_id).first()
    if existing:
        for key, val in meta_kwargs.items():
            setattr(existing, key, val)
    else:
        record = MediaImageMeta(media_item_id=media_item_id, **meta_kwargs)
        db.add(record)
    db.commit()


def _apply_ai_description(meta_kwargs: dict, ai: dict, ai_overrides: dict | None) -> None:
    """Merge the normalized AI generation result into a meta_kwargs dict.

    Fields listed in ai_overrides (manual human corrections) are preserved
    untouched — the AI never clobbers what a person has explicitly set.
    """
    overrides = ai_overrides or {}
    now = datetime.now(timezone.utc)

    if "ai_description" not in overrides:
        meta_kwargs["ai_description"] = ai.get("description")
    meta_kwargs["ai_description_model"] = ai.get("model")
    meta_kwargs["ai_description_prompt_v"] = ai.get("prompt_version")
    meta_kwargs["ai_description_generated_at"] = now
    meta_kwargs["ai_description_tokens_in"] = ai.get("tokens_in")
    meta_kwargs["ai_description_tokens_out"] = ai.get("tokens_out")

    if "ai_tags" not in overrides:
        meta_kwargs["ai_tags"] = json.dumps(ai.get("tags") or [])
    if "ai_color_temperature" not in overrides:
        meta_kwargs["ai_color_temperature"] = ai.get("color_temperature")
    if "ai_color_character" not in overrides:
        meta_kwargs["ai_color_character"] = ai.get("color_character")
    if "ai_vibe" not in overrides:
        meta_kwargs["ai_vibe"] = json.dumps(ai.get("vibe") or [])

    flags = ai.get("flags") or {}
    for flag_name, value in flags.items():
        if flag_name in overrides:
            continue
        meta_kwargs[flag_name] = value


_VOICE_PATTERNS: list[tuple[str, str]] = [
    ("kick", "kick"), ("kik", "kick"), ("bd", "kick"), ("bassdrum", "kick"),
    ("snare", "snare"), ("snar", "snare"), ("sn", "snare"), ("snr", "snare"),
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
    ("perc", "percussion"),
    ("bass", "bass"),
    ("guitar", "guitar"),
    ("synth", "synth"), ("pad", "pad"), ("organ", "organ"),
    ("melody", "melody"),
    ("fx", "fx"), ("effect", "fx"),
    ("noise", "noise"),
    ("vocal", "vox"), ("rap", "vox"), ("sing", "vox"), ("spoken", "vox"),
    ("vinyl", "vinyl"), ("crackle", "vinyl"),
]


def _detect_voice_from_filename(filename: str) -> str | None:
    """Heuristic voice type detection from filename keywords."""
    name = filename.lower()
    for pattern, voice in _VOICE_PATTERNS:
        if pattern in name:
            return voice
    return None


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

    # Step 2: Transcription (skipped for long files — a transcript of an
    # hour-long jam is useless for search and very expensive on CPU).
    duration = meta_kwargs.get("duration_seconds")
    if duration and duration > WHISPER_MAX_SECONDS:
        logger.info(
            "Skipping transcription for %s: %.0fs exceeds WHISPER_MAX_SECONDS=%s",
            media_item_id,
            duration,
            WHISPER_MAX_SECONDS,
        )
        meta_kwargs["transcript"] = ""
        meta_kwargs["transcript_confidence"] = 0.0
    else:
        try:
            transcript_result = transcribe_audio(file_path)
            if transcript_result:
                meta_kwargs["transcript"] = transcript_result["transcript"]
                meta_kwargs["transcript_confidence"] = transcript_result["confidence"]
            else:
                meta_kwargs["transcript"] = ""
                meta_kwargs["transcript_confidence"] = 0.0
        except Exception as exc:
            logger.error("Audio transcription failed for %s: %s", media_item_id, exc)
            _log_failure(db, media_item_id, "whisper", exc)

    # Step 3: AI description + tagging from filename context.
    # Derive context from MediaSource.source_metadata.dir when available
    # (sample libraries store drum-machine / sample-category info there),
    # otherwise fall back to the filesystem directory name. Audio attached to
    # a Latent gets the WIP-session prompt (project/slot context) instead of
    # the one-shot-sample prompt.
    try:
        from server.ai_audio import generate_audio_ai_description, generate_wip_description
        from server.models import MediaItem as _MediaItem, MediaSource as _MediaSource, MediaTag as _MediaTag

        filename = os.path.basename(file_path)
        dir_name = None

        # Try to get meaningful context from the MediaSource record
        _source = db.query(_MediaSource).filter(
            _MediaSource.media_item_id == media_item_id,
            _MediaSource.source_type == "sample_library",
        ).first()
        if _source and _source.source_metadata:
            try:
                _meta = json.loads(_source.source_metadata) if isinstance(_source.source_metadata, str) else _source.source_metadata
                dir_name = _meta.get("dir") or _meta.get("machine_name")
            except (json.JSONDecodeError, TypeError):
                pass

        if not dir_name:
            dir_name = os.path.basename(os.path.dirname(file_path))

        wip_context = None
        try:
            from server.models import Project as _Project, ProjectItem as _ProjectItem, ProjectSlot as _ProjectSlot

            _pi = db.query(_ProjectItem).filter(_ProjectItem.media_item_id == media_item_id).first()
            if _pi:
                _proj = db.query(_Project).filter(_Project.id == _pi.project_id).first()
                _slot = (
                    db.query(_ProjectSlot).filter(_ProjectSlot.id == _pi.slot_id).first()
                    if _pi.slot_id
                    else None
                )
                wip_context = {
                    "project_name": _proj.name if _proj else None,
                    "slot_label": _slot.label if _slot else None,
                }
        except Exception:
            wip_context = None

        if wip_context:
            ai = generate_wip_description(
                filename,
                project_name=wip_context["project_name"],
                slot_label=wip_context["slot_label"],
                dir_name=dir_name,
            )
        else:
            ai = generate_audio_ai_description(filename, dir_name=dir_name)
        if ai.get("description"):
            media_item = db.query(_MediaItem).filter(_MediaItem.id == media_item_id).first()
            # Never clobber a human-written description from the upload form.
            if media_item and not media_item.description:
                media_item.description = ai["description"]
        ai_tags = ai.get("tags", [])
        ai_voice = ai.get("voice")
        ai_instrument = ai.get("instrument")

        # Deterministic fallbacks from filename + dir context
        if not ai_voice:
            ai_voice = _detect_voice_from_filename(filename)
        if not ai_instrument and dir_name:
            ai_instrument = dir_name.lower().replace(" ", "-").replace("_", "-")

        if ai_tags or ai_voice or ai_instrument:
            acoustic = {}
            if ai_voice:
                acoustic["voice"] = ai_voice
            if ai_instrument:
                acoustic["instrument"] = ai_instrument
            if ai_tags:
                acoustic["ai_tags"] = ai_tags
            meta_kwargs["acoustic_tags"] = json.dumps(acoustic)
            # Also store as MediaTag records for filtering
            import uuid as _uuid
            for tag in ai_tags:
                existing_tag = (
                    db.query(_MediaTag)
                    .filter(
                        _MediaTag.media_item_id == media_item_id,
                        _MediaTag.tag == tag,
                    )
                    .first()
                )
                if not existing_tag:
                    db.add(_MediaTag(
                        id=str(_uuid.uuid4()),
                        media_item_id=media_item_id,
                        tag=tag,
                    ))
    except ImportError:
        pass  # ai_audio not available
    except Exception as exc:
        logger.error("Audio AI tagging failed for %s: %s", media_item_id, exc)
        _log_failure(db, media_item_id, "ai_audio_tagging", exc)

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
    _duration = meta_kwargs.get("duration_seconds")
    if not _has_audio_stream(file_path):
        logger.info("No audio stream in %s, marking as no audio.", media_item_id)
        meta_kwargs["audio_transcript"] = ""
        meta_kwargs["transcript_confidence"] = 0.0
    elif _duration and _duration > WHISPER_MAX_SECONDS:
        logger.info(
            "Skipping transcription for %s: %.0fs exceeds WHISPER_MAX_SECONDS=%s",
            media_item_id,
            _duration,
            WHISPER_MAX_SECONDS,
        )
        meta_kwargs["audio_transcript"] = ""
        meta_kwargs["transcript_confidence"] = 0.0
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
                        meta_kwargs["audio_transcript"] = ""
                        meta_kwargs["transcript_confidence"] = 0.0
                else:
                    meta_kwargs["audio_transcript"] = ""
                    meta_kwargs["transcript_confidence"] = 0.0
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
    from server.models import (
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
    from server.models import MediaAudioMeta, MediaImageMeta, MediaTag, MediaVideoMeta

    media_item_id = media_item.id

    if extraction_type == "image_metadata":
        img_meta = extract_image_metadata(file_path)
        _upsert_meta(db, MediaImageMeta, media_item_id, img_meta)

    elif extraction_type == "dominant_colors":
        colors = extract_dominant_colors(file_path)
        _upsert_meta(db, MediaImageMeta, media_item_id, {"dominant_colors": json.dumps(colors)})

    elif extraction_type == "ocr":
        ocr_text = extract_text_ocr(file_path)
        _upsert_meta(db, MediaImageMeta, media_item_id, {"caption": ocr_text or ""})

    elif extraction_type == "ai_description":
        from server.ai_description import generate_ai_description

        existing_meta = (
            db.query(MediaImageMeta)
            .filter(MediaImageMeta.media_item_id == media_item_id)
            .first()
        )
        ocr_caption = existing_meta.caption if existing_meta else None
        overrides_json = existing_meta.ai_overrides if existing_meta else None
        try:
            overrides = json.loads(overrides_json) if overrides_json else {}
        except (TypeError, ValueError):
            overrides = {}
        ai = generate_ai_description(file_path, ocr_caption=ocr_caption)
        ai_kwargs: dict = {}
        _apply_ai_description(ai_kwargs, ai, ai_overrides=overrides)
        _upsert_meta(db, MediaImageMeta, media_item_id, ai_kwargs)

    elif extraction_type == "ai_audio_tagging":
        if media_item.media_type == "audio":
            filename = os.path.basename(file_path)
            dir_name = os.path.basename(os.path.dirname(file_path))
            from server.ai_audio import generate_audio_ai_description
            ai = generate_audio_ai_description(filename, dir_name=dir_name)
            if ai.get("description"):
                media_item.description = ai["description"]
            ai_tags = ai.get("tags", [])
            ai_voice = ai.get("voice")
            ai_instrument = ai.get("instrument")
            acoustic = {}
            if ai_voice:
                acoustic["voice"] = ai_voice
            if ai_instrument:
                acoustic["instrument"] = ai_instrument
            if ai_tags:
                acoustic["ai_tags"] = ai_tags
            if acoustic:
                _upsert_meta(db, MediaAudioMeta, media_item_id, {"acoustic_tags": json.dumps(acoustic)})
                for tag in ai_tags:
                    existing_tag = (
                        db.query(MediaTag)
                        .filter(MediaTag.media_item_id == media_item_id, MediaTag.tag == tag)
                        .first()
                    )
                    if not existing_tag:
                        import uuid
                        db.add(MediaTag(id=str(uuid.uuid4()), media_item_id=media_item_id, tag=tag))
                db.commit()
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
    elif extraction_type == "meilisearch_sync":
        from server.search_client import sync_media_item
        sync_media_item(db, media_item)
    else:
        raise ValueError(f"Unknown extraction type: {extraction_type}")


def _run_audio_extraction_batch(
    db,
    items: list[tuple[str, str, str | None]],
    MediaAudioMeta,
) -> None:
    """Run audio extraction on a batch of files, grouping AI calls by context.

    Args:
        db: SQLAlchemy session.
        items: List of (media_item_id, file_path, dir_context) tuples.
               dir_context is the drum-machine / sample-category name, or None.
        MediaAudioMeta: The ORM model class.
    """
    import uuid as _uuid

    # Step 1+2: ffprobe + transcript per file (fast, local)
    for media_item_id, file_path, _dir_ctx in items:
        meta_kwargs: dict = {}
        try:
            audio_meta = extract_audio_metadata(file_path)
            meta_kwargs.update(audio_meta)
        except Exception as exc:
            logger.error("Audio metadata extraction failed for %s: %s", media_item_id, exc)
        try:
            transcript_result = transcribe_audio(file_path)
            if transcript_result:
                meta_kwargs["transcript"] = transcript_result["transcript"]
                meta_kwargs["transcript_confidence"] = transcript_result["confidence"]
        except Exception as exc:
            logger.error("Audio transcription failed for %s: %s", media_item_id, exc)
        if meta_kwargs:
            existing = db.query(MediaAudioMeta).filter(
                MediaAudioMeta.media_item_id == media_item_id
            ).first()
            if existing:
                for key, val in meta_kwargs.items():
                    setattr(existing, key, val)
            else:
                db.add(MediaAudioMeta(media_item_id=media_item_id, **meta_kwargs))
    db.commit()

    # Step 3: Batch AI tagging — group by dir_context, one API call per group
    from server.ai_audio import generate_audio_ai_descriptions
    from server.models import MediaItem as _MI, MediaTag as _MT

    by_context: dict[str, list[tuple[str, str, str]]] = {}
    for media_item_id, file_path, dir_ctx in items:
        ctx_key = dir_ctx or os.path.basename(os.path.dirname(file_path))
        by_context.setdefault(ctx_key, []).append((media_item_id, file_path, ctx_key))

    for dir_context, batch_items in by_context.items():
        try:
            fnames = [os.path.basename(fp) for _, fp, _ in batch_items]
            ai_results = generate_audio_ai_descriptions(fnames, dir_name=dir_context)
        except Exception as exc:
            logger.error("Batch AI tagging failed for %s: %s", dir_context, exc)
            ai_results = {}

        for media_item_id, file_path, _ in batch_items:
            try:
                filename = os.path.basename(file_path)
                ai = ai_results.get(filename, {})
                ai_tags = ai.get("tags", [])
                ai_voice = ai.get("voice")
                ai_instrument = ai.get("instrument")

                if not ai_voice:
                    ai_voice = _detect_voice_from_filename(filename)
                if not ai_instrument and dir_context:
                    ai_instrument = dir_context.lower().replace(" ", "-").replace("_", "-")

                if ai.get("description"):
                    existing_item = db.query(_MI).filter(_MI.id == media_item_id).first()
                    if existing_item:
                        existing_item.description = ai["description"]

                if ai_tags or ai_voice or ai_instrument:
                    acoustic: dict = {}
                    if ai_voice:
                        acoustic["voice"] = ai_voice
                    if ai_instrument:
                        acoustic["instrument"] = ai_instrument
                    if ai_tags:
                        acoustic["ai_tags"] = ai_tags
                    acoustic_json = json.dumps(acoustic)

                    existing_meta = db.query(MediaAudioMeta).filter(
                        MediaAudioMeta.media_item_id == media_item_id
                    ).first()
                    if existing_meta:
                        existing_meta.acoustic_tags = acoustic_json
                    else:
                        db.add(MediaAudioMeta(
                            media_item_id=media_item_id,
                            acoustic_tags=acoustic_json,
                        ))
                    for tag in ai_tags:
                        existing_tag = (
                            db.query(_MT).filter(
                                _MT.media_item_id == media_item_id,
                                _MT.tag == tag,
                            ).first()
                        )
                        if not existing_tag:
                            db.add(_MT(
                                id=str(_uuid.uuid4()),
                                media_item_id=media_item_id,
                                tag=tag,
                            ))
            except Exception as exc:
                logger.error("Batch AI apply failed for %s: %s", media_item_id, exc)
    db.commit()


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
    from server.models import MediaItem, SessionLocal

    db = SessionLocal()
    try:
        items = db.query(MediaItem).filter(MediaItem.id.in_(media_item_ids)).all()
        for item in items:
            logger.info("Queuing re-extraction for %s (%s)", item.id, item.media_type)
            run_extraction_async(item.id, item.file_path, item.media_type)
    finally:
        db.close()
