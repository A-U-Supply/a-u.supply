"""Fallback-consistency tests across all three thumbnail endpoints.

Every endpoint must use the same resolution chain and return 200 with a
placeholder SVG rather than 404 when no dedicated thumbnail exists.
"""

import os

from PIL import Image

from tests.conftest import make_media_item


def _write_image(media_dir: str, rel_path: str, size=(20, 20)) -> str:
    full = os.path.join(media_dir, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Image.new("RGB", size, color=(60, 120, 200)).save(full, format="PNG")
    return full


def _write_webp_thumb(media_dir: str, rel_path: str) -> str:
    full = os.path.join(media_dir, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Image.new("RGB", (50, 50), color=(200, 100, 60)).save(full, format="WEBP")
    return full


# ---------------------------------------------------------------------------
# Authed /api/media/{id}/thumbnail
# ---------------------------------------------------------------------------


class TestAuthedThumbnailFallback:
    def test_image_with_thumb_webp_serves_thumb(self, client, auth_headers, db_session, tmp_media_dir):
        rel = "image/2026-04/fb_a.png"
        thumb_rel = "image/2026-04/fb_a_thumb.webp"
        _write_image(tmp_media_dir, rel)
        _write_webp_thumb(tmp_media_dir, thumb_rel)
        item = make_media_item(db_session, file_path=rel)

        resp = client.get(f"/api/media/{item.id}/thumbnail", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"

    def test_image_without_thumb_falls_back_to_original(self, client, auth_headers, db_session, tmp_media_dir):
        rel = "image/2026-04/fb_b.png"
        _write_image(tmp_media_dir, rel)
        item = make_media_item(db_session, file_path=rel)

        resp = client.get(f"/api/media/{item.id}/thumbnail", headers=auth_headers)
        assert resp.status_code == 200
        # Served original PNG since there's no _thumb.webp
        assert resp.headers["content-type"].startswith("image/")

    def test_audio_returns_placeholder_svg(self, client, auth_headers, db_session, tmp_media_dir):
        rel = "audio/2026-04/fb_c.wav"
        item = make_media_item(
            db_session,
            media_type="audio",
            mime_type="audio/wav",
            file_path=rel,
            filename="fb_c.wav",
        )

        resp = client.get(f"/api/media/{item.id}/thumbnail", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert b"aria-label=\"Audio\"" in resp.content

    def test_video_without_thumb_returns_placeholder_svg(self, client, auth_headers, db_session, tmp_media_dir):
        rel = "video/2026-04/fb_d.mp4"
        item = make_media_item(
            db_session,
            media_type="video",
            mime_type="video/mp4",
            file_path=rel,
            filename="fb_d.mp4",
        )

        resp = client.get(f"/api/media/{item.id}/thumbnail", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert b"aria-label=\"Video\"" in resp.content


# ---------------------------------------------------------------------------
# Public /api/media/{id}/og-thumb
# ---------------------------------------------------------------------------


class TestOgThumbFallback:
    def test_audio_returns_placeholder(self, client, db_session, tmp_media_dir):
        rel = "audio/2026-04/fb_og_a.wav"
        item = make_media_item(
            db_session,
            media_type="audio",
            mime_type="audio/wav",
            file_path=rel,
            filename="fb_og_a.wav",
        )
        resp = client.get(f"/api/media/{item.id}/og-thumb")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")

    def test_image_with_thumb_webp(self, client, db_session, tmp_media_dir):
        rel = "image/2026-04/fb_og_b.png"
        thumb_rel = "image/2026-04/fb_og_b_thumb.webp"
        _write_image(tmp_media_dir, rel)
        _write_webp_thumb(tmp_media_dir, thumb_rel)
        item = make_media_item(db_session, file_path=rel)

        resp = client.get(f"/api/media/{item.id}/og-thumb")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"


# ---------------------------------------------------------------------------
# Public /api/public/outputs/{id}/thumbnail
# ---------------------------------------------------------------------------


class TestPublicOutputsThumbnailFallback:
    def test_indexed_audio_output_returns_placeholder(self, client, db_session):
        rel = "audio/2026-04/fb_po_a.wav"
        item = make_media_item(
            db_session,
            media_type="audio",
            mime_type="audio/wav",
            file_path=rel,
            filename="fb_po_a.wav",
            output_index="test-outputs",
        )
        resp = client.get(f"/api/public/outputs/{item.id}/thumbnail")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")

    def test_unindexed_input_still_404s(self, client, db_session):
        # Items outside the outputs index must still 404 — placeholder
        # fallback only applies once the item is confirmed as an output.
        item = make_media_item(db_session, media_type="audio", output_index=None)
        resp = client.get(f"/api/public/outputs/{item.id}/thumbnail")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Placeholder resolver unit tests
# ---------------------------------------------------------------------------


class TestResolveThumbnailPath:
    # The `client` fixture is pulled in so SEARCH_MEDIA_DIR is pointed at
    # tmp_media_dir before _get_search_media_dir() is called.

    def test_returns_none_for_audio_without_thumb(self, client, db_session, tmp_media_dir):
        from search_api import _resolve_thumbnail_path

        item = make_media_item(
            db_session,
            media_type="audio",
            mime_type="audio/wav",
            file_path="audio/2026-04/no_thumb.wav",
        )
        assert _resolve_thumbnail_path(item) is None

    def test_prefers_webp_thumb_over_original(self, client, db_session, tmp_media_dir):
        from search_api import _resolve_thumbnail_path

        rel = "image/2026-04/r_a.png"
        thumb_rel = "image/2026-04/r_a_thumb.webp"
        _write_image(tmp_media_dir, rel)
        _write_webp_thumb(tmp_media_dir, thumb_rel)
        item = make_media_item(db_session, file_path=rel)

        result = _resolve_thumbnail_path(item)
        assert result is not None
        path, mime = result
        assert mime == "image/webp"
        assert str(path).endswith("_thumb.webp")

    def test_size_sm_prefers_small_sibling(self, client, db_session, tmp_media_dir):
        from search_api import _resolve_thumbnail_path

        rel = "image/2026-04/r_sm.png"
        sm_rel = "image/2026-04/r_sm_thumb_sm.webp"
        md_rel = "image/2026-04/r_sm_thumb.webp"
        _write_image(tmp_media_dir, rel)
        _write_webp_thumb(tmp_media_dir, sm_rel)
        _write_webp_thumb(tmp_media_dir, md_rel)
        item = make_media_item(db_session, file_path=rel)

        # size="sm" picks the _thumb_sm.webp sibling
        path, _ = _resolve_thumbnail_path(item, size="sm")
        assert str(path).endswith("_thumb_sm.webp")

        # size="md" (default) picks the _thumb.webp sibling
        path, _ = _resolve_thumbnail_path(item, size="md")
        assert str(path).endswith("_thumb.webp")
        assert not str(path).endswith("_thumb_sm.webp")

    def test_size_sm_falls_back_to_md_when_sm_missing(self, client, db_session, tmp_media_dir):
        from search_api import _resolve_thumbnail_path

        rel = "image/2026-04/r_fb.png"
        md_rel = "image/2026-04/r_fb_thumb.webp"
        _write_image(tmp_media_dir, rel)
        _write_webp_thumb(tmp_media_dir, md_rel)  # no _thumb_sm.webp
        item = make_media_item(db_session, file_path=rel)

        path, _ = _resolve_thumbnail_path(item, size="sm")
        assert str(path).endswith("_thumb.webp")


class TestThumbnailSizeParam:
    """?size=sm end-to-end through the endpoints."""

    def test_authed_thumbnail_size_sm(self, client, auth_headers, db_session, tmp_media_dir):
        rel = "image/2026-04/e_a.png"
        sm_rel = "image/2026-04/e_a_thumb_sm.webp"
        md_rel = "image/2026-04/e_a_thumb.webp"
        _write_image(tmp_media_dir, rel)
        _write_webp_thumb(tmp_media_dir, sm_rel)
        _write_webp_thumb(tmp_media_dir, md_rel)
        item = make_media_item(db_session, file_path=rel)

        resp = client.get(f"/api/media/{item.id}/thumbnail?size=sm", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        # No clean way to prove which file was served via status alone,
        # but we can at least confirm the endpoint accepts the param.

    def test_public_output_thumbnail_size_sm(self, client, db_session, tmp_media_dir):
        rel = "image/2026-04/e_b.png"
        sm_rel = "image/2026-04/e_b_thumb_sm.webp"
        md_rel = "image/2026-04/e_b_thumb.webp"
        _write_image(tmp_media_dir, rel)
        _write_webp_thumb(tmp_media_dir, sm_rel)
        _write_webp_thumb(tmp_media_dir, md_rel)
        item = make_media_item(db_session, file_path=rel, output_index="x")

        resp = client.get(f"/api/public/outputs/{item.id}/thumbnail?size=sm")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"

    def test_invalid_size_rejected(self, client, auth_headers, db_session):
        item = make_media_item(db_session)
        resp = client.get(f"/api/media/{item.id}/thumbnail?size=xl", headers=auth_headers)
        assert resp.status_code == 422
