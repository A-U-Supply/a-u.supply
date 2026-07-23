"""Tests for waveform peaks generation and the peaks endpoint."""

import io
import json
import shutil
import wave

import pytest

from server.extraction import generate_peaks, peaks_path_for
from tests.conftest import make_media_item


def _write_wav(path, seconds=0.1, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(rate * seconds))
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
class TestGeneratePeaks:
    def test_generates_bins(self, tmp_path):
        src = _write_wav(tmp_path / "tone.wav")
        assert generate_peaks(str(src), bins=100) is True
        peaks_file = peaks_path_for(src)
        assert peaks_file.exists()
        data = json.loads(peaks_file.read_text())
        assert data["bins"] == 100
        assert len(data["peaks"]) == 100
        assert all(len(p) == 2 and -1.0 <= p[0] <= p[1] <= 1.0 for p in data["peaks"])

    def test_garbage_returns_false(self, tmp_path):
        src = tmp_path / "junk.wav"
        src.write_bytes(b"not audio")
        assert generate_peaks(str(src)) is False


class TestPeaksEndpoint:
    def test_serves_cached_peaks(self, client, auth_headers, db_session, tmp_media_dir, monkeypatch):
        monkeypatch.setenv("SEARCH_MEDIA_DIR", tmp_media_dir)
        from pathlib import Path

        src = _write_wav(Path(tmp_media_dir) / "audio" / "2026-07" / "abcd1234_tone.wav")
        peaks_path_for(src).write_text(json.dumps({"bins": 10, "peaks": [[0, 0.5]] * 10}))
        item = make_media_item(
            db_session,
            filename="tone.wav",
            file_path="audio/2026-07/abcd1234_tone.wav",
            media_type="audio",
            mime_type="audio/wav",
        )

        resp = client.get(f"/api/media/{item.id}/peaks", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["bins"] == 10

    def test_generates_on_demand(self, client, auth_headers, db_session, tmp_media_dir, monkeypatch):
        monkeypatch.setenv("SEARCH_MEDIA_DIR", tmp_media_dir)
        from pathlib import Path

        src = _write_wav(Path(tmp_media_dir) / "audio" / "2026-07" / "abcd1234_tone.wav")
        item = make_media_item(
            db_session,
            filename="tone.wav",
            file_path="audio/2026-07/abcd1234_tone.wav",
            media_type="audio",
            mime_type="audio/wav",
        )

        def fake_generate(file_path, peaks_path=None, **kwargs):
            out = peaks_path or str(peaks_path_for(file_path))
            Path(out).write_text(json.dumps({"bins": 5, "peaks": [[0, 1]] * 5}))
            return True

        import server.extraction as extraction_module

        monkeypatch.setattr(extraction_module, "generate_peaks", fake_generate)
        resp = client.get(f"/api/media/{item.id}/peaks", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["bins"] == 5

    def test_404_for_session_bundle(self, client, auth_headers, db_session, tmp_media_dir, monkeypatch):
        monkeypatch.setenv("SEARCH_MEDIA_DIR", tmp_media_dir)
        item = make_media_item(
            db_session,
            filename="Song.logicx",
            file_path="session/2026-07/abcd1234_Song.logicx",
            media_type="session",
            mime_type="application/octet-stream",
        )
        resp = client.get(f"/api/media/{item.id}/peaks", headers=auth_headers)
        assert resp.status_code == 404
