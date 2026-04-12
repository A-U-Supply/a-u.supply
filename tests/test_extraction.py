"""Tests for the media extraction pipeline."""

import io
import json
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_media_item


class TestExtractImageMetadata:
    """Tests for extract_image_metadata with real Pillow images."""

    def test_extract_png_metadata(self, tmp_media_dir):
        from PIL import Image
        from extraction import extract_image_metadata

        # Create a small 10x10 red PNG
        img = Image.new("RGB", (10, 10), color=(255, 0, 0))
        path = os.path.join(tmp_media_dir, "test.png")
        img.save(path, format="PNG")

        meta = extract_image_metadata(path)
        assert meta["width"] == 10
        assert meta["height"] == 10
        assert meta["format"] == "PNG"

    def test_extract_jpeg_metadata(self, tmp_media_dir):
        from PIL import Image
        from extraction import extract_image_metadata

        img = Image.new("RGB", (640, 480), color=(0, 128, 255))
        path = os.path.join(tmp_media_dir, "test.jpg")
        img.save(path, format="JPEG")

        meta = extract_image_metadata(path)
        assert meta["width"] == 640
        assert meta["height"] == 480
        assert meta["format"] == "JPEG"


class TestExtractDominantColors:
    """Tests for extract_dominant_colors."""

    def test_returns_hex_color_strings(self, tmp_media_dir):
        from PIL import Image
        from extraction import extract_dominant_colors

        # Create a solid red image
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        path = os.path.join(tmp_media_dir, "red.png")
        img.save(path, format="PNG")

        colors = extract_dominant_colors(path, num_colors=3)
        assert isinstance(colors, list)
        assert len(colors) > 0
        for color in colors:
            assert color.startswith("#")
            assert len(color) == 7  # "#RRGGBB"

    def test_solid_color_image(self, tmp_media_dir):
        from PIL import Image
        from extraction import extract_dominant_colors

        img = Image.new("RGB", (50, 50), color=(0, 0, 255))
        path = os.path.join(tmp_media_dir, "blue.png")
        img.save(path, format="PNG")

        colors = extract_dominant_colors(path, num_colors=1)
        assert len(colors) >= 1
        # The dominant color of a solid blue image should be close to blue
        # Allow some tolerance since quantization may not be exact
        r = int(colors[0][1:3], 16)
        g = int(colors[0][3:5], 16)
        b = int(colors[0][5:7], 16)
        assert b > r and b > g


class TestExtractAudioMetadata:
    """Tests for extract_audio_metadata (mocked ffprobe)."""

    def test_extract_audio_metadata_success(self):
        from extraction import extract_audio_metadata

        ffprobe_output = json.dumps({
            "format": {"duration": "120.5"},
            "streams": [
                {
                    "codec_type": "audio",
                    "sample_rate": "44100",
                    "channels": 2,
                    "bits_per_raw_sample": "16",
                }
            ],
        })

        with patch("extraction.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=ffprobe_output,
                stderr="",
            )
            meta = extract_audio_metadata("/fake/audio.wav")

        assert meta["duration_seconds"] == 120.5
        assert meta["sample_rate"] == 44100
        assert meta["channels"] == 2
        assert meta["bit_depth"] == 16

    def test_extract_audio_metadata_ffprobe_failure(self):
        from extraction import extract_audio_metadata

        with patch("extraction.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="ffprobe error",
            )
            with pytest.raises(RuntimeError, match="ffprobe failed"):
                extract_audio_metadata("/fake/audio.wav")

    def test_extract_audio_no_audio_stream(self):
        from extraction import extract_audio_metadata

        ffprobe_output = json.dumps({
            "format": {"duration": "10.0"},
            "streams": [{"codec_type": "video"}],
        })

        with patch("extraction.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=ffprobe_output,
                stderr="",
            )
            with pytest.raises(RuntimeError, match="No audio stream"):
                extract_audio_metadata("/fake/video.mp4")


class TestTranscribeAudio:
    """Tests for transcribe_audio with mocked whisper."""

    def test_gracefully_handles_missing_whisper(self):
        from extraction import transcribe_audio

        with patch("extraction._get_whisper_model", side_effect=ImportError("no module")):
            result = transcribe_audio("/fake/audio.wav")

        assert result is None

    def test_transcribe_returns_transcript_and_confidence(self):
        from extraction import transcribe_audio

        mock_segment = MagicMock()
        mock_segment.text = " Hello world "
        mock_segment.avg_logprob = -0.3

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([mock_segment]), MagicMock())

        with patch("extraction._get_whisper_model", return_value=mock_model):
            result = transcribe_audio("/fake/audio.wav")

        assert result is not None
        assert result["transcript"] == "Hello world"
        assert 0 < result["confidence"] < 1

    def test_transcribe_no_segments(self):
        from extraction import transcribe_audio

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([]), MagicMock())

        with patch("extraction._get_whisper_model", return_value=mock_model):
            result = transcribe_audio("/fake/audio.wav")

        assert result is None


class TestExtractVideoMetadata:
    """Tests for extract_video_metadata (mocked ffprobe)."""

    def test_extract_video_metadata_success(self):
        from extraction import extract_video_metadata

        ffprobe_output = json.dumps({
            "format": {"duration": "60.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                }
            ],
        })

        with patch("extraction.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=ffprobe_output,
                stderr="",
            )
            meta = extract_video_metadata("/fake/video.mp4")

        assert meta["duration_seconds"] == 60.0
        assert meta["width"] == 1920
        assert meta["height"] == 1080
        assert meta["fps"] == 30.0

    def test_extract_video_fractional_fps(self):
        from extraction import extract_video_metadata

        ffprobe_output = json.dumps({
            "format": {"duration": "120.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1280,
                    "height": 720,
                    "r_frame_rate": "24000/1001",
                }
            ],
        })

        with patch("extraction.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=ffprobe_output,
                stderr="",
            )
            meta = extract_video_metadata("/fake/video.mp4")

        assert abs(meta["fps"] - 23.976) < 0.01


class TestGenerateVideoThumbnail:
    """Tests for generate_video_thumbnail (mocked ffmpeg)."""

    def test_successful_thumbnail_generation(self):
        from extraction import generate_video_thumbnail

        with patch("extraction.subprocess.run") as mock_run:
            # First call is ffprobe for duration, second is ffmpeg
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout='{"format":{"duration":"60.0"}}', stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]
            result = generate_video_thumbnail("/fake/video.mp4", "/fake/thumb.webp")

        assert result is True

    def test_failed_thumbnail_generation(self):
        from extraction import generate_video_thumbnail

        with patch("extraction.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout='{"format":{"duration":"60.0"}}', stderr=""),
                MagicMock(returncode=1, stdout="", stderr="ffmpeg error"),
            ]
            result = generate_video_thumbnail("/fake/video.mp4", "/fake/thumb.webp")

        assert result is False


class TestRunExtraction:
    """Tests for the orchestration function run_extraction."""

    def test_run_extraction_creates_image_meta(self, db_session, tmp_media_dir):
        from PIL import Image
        from models import MediaImageMeta, SessionLocal

        item = make_media_item(db_session, media_type="image")

        # Create an actual image file
        img = Image.new("RGB", (20, 20), color=(100, 200, 50))
        file_path = os.path.join(tmp_media_dir, "test.png")
        img.save(file_path, format="PNG")

        # Patch SessionLocal to return our test session
        with patch("models.SessionLocal", return_value=db_session):
            # Prevent session.close() from actually closing our test session
            with patch.object(db_session, "close"):
                from extraction import run_extraction

                run_extraction(item.id, file_path, "image")

        meta = db_session.query(MediaImageMeta).filter(
            MediaImageMeta.media_item_id == item.id
        ).first()
        assert meta is not None
        assert meta.width == 20
        assert meta.height == 20

    def test_run_extraction_nonexistent_item(self, db_session):
        """Extraction for a nonexistent item should not raise."""
        with patch("models.SessionLocal", return_value=db_session):
            with patch.object(db_session, "close"):
                from extraction import run_extraction

                run_extraction("nonexistent-id", "/fake/path", "image")


class TestFailureLogging:
    """Tests for extraction failure recording."""

    def test_log_failure_creates_record(self, db_session):
        from extraction import _log_failure
        from models import ExtractionFailure

        item = make_media_item(db_session)
        error = RuntimeError("Pillow crash")

        _log_failure(db_session, item.id, "image_metadata", error)

        failure = db_session.query(ExtractionFailure).filter(
            ExtractionFailure.media_item_id == item.id
        ).first()
        assert failure is not None
        assert failure.extraction_type == "image_metadata"
        assert "Pillow crash" in failure.error_message
        assert failure.attempts == 1
        assert failure.resolved is False

    def test_log_failure_increments_attempts_on_existing(self, db_session):
        from extraction import _log_failure
        from models import ExtractionFailure

        item = make_media_item(db_session)

        _log_failure(db_session, item.id, "ffprobe", RuntimeError("first"))
        _log_failure(db_session, item.id, "ffprobe", RuntimeError("second"))

        failures = db_session.query(ExtractionFailure).filter(
            ExtractionFailure.media_item_id == item.id,
            ExtractionFailure.extraction_type == "ffprobe",
        ).all()
        assert len(failures) == 1
        assert failures[0].attempts == 2
        assert "second" in failures[0].error_message


class TestPartialFailure:
    """Tests that partial extraction failures don't block other steps."""

    def test_image_extraction_partial_failure(self, db_session, tmp_media_dir):
        """If dominant color extraction fails, image metadata should still be saved."""
        from PIL import Image
        from models import MediaImageMeta, ExtractionFailure

        item = make_media_item(db_session, media_type="image")

        img = Image.new("RGB", (15, 15), color=(50, 50, 50))
        file_path = os.path.join(tmp_media_dir, "partial.png")
        img.save(file_path, format="PNG")

        with patch("models.SessionLocal", return_value=db_session):
            with patch.object(db_session, "close"):
                with patch("extraction.extract_dominant_colors", side_effect=RuntimeError("color crash")):
                    from extraction import run_extraction

                    run_extraction(item.id, file_path, "image")

        # Image metadata should still be created despite color extraction failure
        meta = db_session.query(MediaImageMeta).filter(
            MediaImageMeta.media_item_id == item.id
        ).first()
        assert meta is not None
        assert meta.width == 15

        # A failure record should exist for dominant_colors
        failure = db_session.query(ExtractionFailure).filter(
            ExtractionFailure.media_item_id == item.id,
            ExtractionFailure.extraction_type == "dominant_colors",
        ).first()
        assert failure is not None


class TestRetryExtraction:
    """Tests for retry_extraction."""

    def test_retry_increments_attempts_on_failure(self, db_session):
        from models import ExtractionFailure
        from extraction import retry_extraction

        item = make_media_item(db_session, media_type="image")
        failure = ExtractionFailure(
            id=str(uuid.uuid4()),
            media_item_id=item.id,
            extraction_type="image_metadata",
            error_message="original error",
            attempts=1,
            resolved=False,
        )
        db_session.add(failure)
        db_session.commit()

        with patch("models.SessionLocal", return_value=db_session):
            with patch.object(db_session, "close"):
                with patch("extraction._retry_single_step", side_effect=RuntimeError("still broken")):
                    retry_extraction(failure.id)

        db_session.refresh(failure)
        assert failure.attempts == 2
        assert failure.resolved is False
        assert "still broken" in failure.error_message

    def test_retry_marks_resolved_on_success(self, db_session):
        from models import ExtractionFailure
        from extraction import retry_extraction

        item = make_media_item(db_session, media_type="image")
        failure = ExtractionFailure(
            id=str(uuid.uuid4()),
            media_item_id=item.id,
            extraction_type="image_metadata",
            error_message="original error",
            attempts=1,
            resolved=False,
        )
        db_session.add(failure)
        db_session.commit()

        with patch("models.SessionLocal", return_value=db_session):
            with patch.object(db_session, "close"):
                with patch("extraction._retry_single_step"):
                    retry_extraction(failure.id)

        db_session.refresh(failure)
        assert failure.resolved is True
