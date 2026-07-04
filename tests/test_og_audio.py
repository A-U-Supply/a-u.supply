"""Tests for the public /api/media/{id}/og-audio unfurling route.

Sibling to test_thumbnail_cache.py — same shape but for the audio file
route that link-unfurling bots (Slack media unfurl, iMessage, etc.)
fetch.
"""

import os

from PIL import Image

from tests.conftest import make_media_item


def _write_wav(media_dir: str, rel_path: str) -> str:
    full = os.path.join(media_dir, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00")
        f.write(b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
    return full


def _write_png(media_dir: str, rel_path: str) -> str:
    full = os.path.join(media_dir, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Image.new("RGB", (10, 10), color=(0, 0, 0)).save(full, format="PNG")
    return full


class TestPublicOGAudio:
    """GET /api/media/{id}/og-audio serves audio items publicly."""

    def test_serves_audio_item_with_public_cache(self, client, db_session, tmp_media_dir):
        rel = "audio/2026-04/sharetest.wav"
        _write_wav(tmp_media_dir, rel)
        item = make_media_item(
            db_session,
            file_path=rel,
            filename="sharetest.wav",
            media_type="audio",
            mime_type="audio/wav",
        )

        resp = client.get(f"/api/media/{item.id}/og-audio")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "") in ("audio/wav", "audio/wav; charset=utf-8")
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc
        assert "max-age=86400" in cc
        cd = resp.headers.get("Content-Disposition", "")
        assert "inline" in cd
        assert "sharetest.wav" in cd

    def test_404_for_image_items(self, client, db_session, tmp_media_dir):
        rel = "image/2026-04/notaudio.png"
        _write_png(tmp_media_dir, rel)
        item = make_media_item(
            db_session,
            file_path=rel,
            filename="notaudio.png",
            media_type="image",
            mime_type="image/png",
        )

        resp = client.get(f"/api/media/{item.id}/og-audio")
        assert resp.status_code == 404

    def test_404_for_unknown_id(self, client, db_session, tmp_media_dir):
        resp = client.get("/api/media/does-not-exist/og-audio")
        assert resp.status_code == 404

    def test_no_auth_required(self, client, db_session, tmp_media_dir):
        rel = "audio/2026-04/noauth.wav"
        _write_wav(tmp_media_dir, rel)
        item = make_media_item(
            db_session,
            file_path=rel,
            filename="noauth.wav",
            media_type="audio",
            mime_type="audio/wav",
        )

        resp = client.get(f"/api/media/{item.id}/og-audio")
        assert resp.status_code == 200