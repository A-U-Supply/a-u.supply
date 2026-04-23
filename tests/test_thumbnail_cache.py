"""Cache-Control header tests for media file and thumbnail endpoints.

Each endpoint needs an actual file on disk under the test's SEARCH_MEDIA_DIR,
so these tests create a real image via Pillow before hitting the route.
"""

import os

from PIL import Image

from tests.conftest import make_media_item


def _write_image(media_dir: str, rel_path: str, size=(20, 20)) -> str:
    full = os.path.join(media_dir, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Image.new("RGB", size, color=(60, 120, 200)).save(full, format="PNG")
    return full


def _write_thumb(media_dir: str, rel_path: str) -> str:
    """Write a WebP sibling thumbnail for a given media path."""
    full = os.path.join(media_dir, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Image.new("RGB", (50, 50), color=(200, 100, 60)).save(full, format="WEBP")
    return full


class TestAuthedMediaFileCache:
    """GET /api/media/{id}/file must emit a private cache header."""

    def test_cache_control_private_long_maxage(self, client, auth_headers, db_session, tmp_media_dir):
        rel = "image/2026-04/cachetest_a.png"
        _write_image(tmp_media_dir, rel)
        item = make_media_item(db_session, file_path=rel)

        resp = client.get(f"/api/media/{item.id}/file", headers=auth_headers)
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "max-age=86400" in cc


class TestAuthedMediaThumbnailCache:
    """GET /api/media/{id}/thumbnail must emit a private cache header."""

    def test_cache_control_with_thumb_file(self, client, auth_headers, db_session, tmp_media_dir):
        rel = "image/2026-04/cachetest_b.png"
        thumb_rel = "image/2026-04/cachetest_b_thumb.webp"
        _write_image(tmp_media_dir, rel)
        _write_thumb(tmp_media_dir, thumb_rel)
        item = make_media_item(db_session, file_path=rel)

        resp = client.get(f"/api/media/{item.id}/thumbnail", headers=auth_headers)
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "max-age=86400" in cc

    def test_cache_control_with_original_fallback(self, client, auth_headers, db_session, tmp_media_dir):
        # No _thumb.webp — endpoint falls back to original image
        rel = "image/2026-04/cachetest_c.png"
        _write_image(tmp_media_dir, rel)
        item = make_media_item(db_session, file_path=rel)

        resp = client.get(f"/api/media/{item.id}/thumbnail", headers=auth_headers)
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "private" in cc
        assert "max-age=86400" in cc


class TestPublicOgThumbCache:
    """GET /api/media/{id}/og-thumb must emit a public cache header."""

    def test_cache_control_public_long_maxage(self, client, db_session, tmp_media_dir):
        rel = "image/2026-04/cachetest_d.png"
        _write_image(tmp_media_dir, rel)
        item = make_media_item(db_session, file_path=rel)

        resp = client.get(f"/api/media/{item.id}/og-thumb")
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "public" in cc
        assert "max-age=86400" in cc
