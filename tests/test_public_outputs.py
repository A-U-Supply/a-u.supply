"""Tests for the public outputs index endpoints (no-auth)."""

import json

from models import MediaImageMeta, MediaVideoMeta
from tests.conftest import make_media_item


class TestPublicOutputsList:
    """Tests for GET /api/public/outputs."""

    def test_no_auth_required(self, client, db_session):
        make_media_item(db_session, output_index="test-outputs")
        resp = client.get("/api/public/outputs")
        assert resp.status_code == 200

    def test_only_returns_indexed_outputs(self, client, db_session):
        make_media_item(db_session, output_index="test-outputs", filename="out.png")
        make_media_item(db_session, output_index=None, filename="in.png")

        resp = client.get("/api/public/outputs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["filename"] == "out.png"

    def test_list_includes_dimensions_and_dominant_colors(self, client, db_session):
        item = make_media_item(db_session, output_index="test-outputs", media_type="image")
        db_session.add(
            MediaImageMeta(
                media_item_id=item.id,
                width=1024,
                height=768,
                format="PNG",
                dominant_colors=json.dumps(["#ff0000", "#00ff00", "#0000ff"]),
            )
        )
        db_session.commit()

        resp = client.get("/api/public/outputs")
        assert resp.status_code == 200
        data = resp.json()["items"][0]
        assert data["width"] == 1024
        assert data["height"] == 768
        assert data["dominant_colors"] == ["#ff0000", "#00ff00", "#0000ff"]

    def test_list_includes_video_dimensions(self, client, db_session):
        item = make_media_item(
            db_session,
            output_index="test-outputs",
            media_type="video",
            filename="clip.mp4",
            mime_type="video/mp4",
            file_path="video/2026-04/abc_clip.mp4",
        )
        db_session.add(
            MediaVideoMeta(
                media_item_id=item.id,
                duration_seconds=12.5,
                width=1920,
                height=1080,
            )
        )
        db_session.commit()

        resp = client.get("/api/public/outputs")
        data = resp.json()["items"][0]
        assert data["width"] == 1920
        assert data["height"] == 1080
        assert data["dominant_colors"] is None

    def test_list_handles_missing_meta(self, client, db_session):
        # Image with no MediaImageMeta row yet
        make_media_item(db_session, output_index="test-outputs", media_type="image")
        resp = client.get("/api/public/outputs")
        data = resp.json()["items"][0]
        assert data["width"] is None
        assert data["height"] is None
        assert data["dominant_colors"] is None

    def test_list_handles_corrupt_dominant_colors_json(self, client, db_session):
        item = make_media_item(db_session, output_index="test-outputs", media_type="image")
        db_session.add(
            MediaImageMeta(
                media_item_id=item.id,
                width=10,
                height=10,
                format="PNG",
                dominant_colors="{{not-json",
            )
        )
        db_session.commit()

        resp = client.get("/api/public/outputs")
        assert resp.status_code == 200
        data = resp.json()["items"][0]
        assert data["dominant_colors"] is None

    def test_filter_by_output_index(self, client, db_session):
        make_media_item(db_session, output_index="alpha")
        make_media_item(db_session, output_index="beta")
        resp = client.get("/api/public/outputs?output_index=alpha")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["output_index"] == "alpha"

    def test_filter_by_media_type(self, client, db_session):
        make_media_item(db_session, output_index="x", media_type="image")
        make_media_item(db_session, output_index="x", media_type="audio", filename="a.wav")
        resp = client.get("/api/public/outputs?media_type=audio")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["media_type"] == "audio"

    def test_list_response_has_cache_header(self, client, db_session):
        make_media_item(db_session, output_index="x")
        resp = client.get("/api/public/outputs")
        assert "Cache-Control" in resp.headers
        assert "max-age=60" in resp.headers["Cache-Control"]
        assert "stale-while-revalidate=300" in resp.headers["Cache-Control"]

    def test_list_urls_point_at_public_endpoints(self, client, db_session):
        item = make_media_item(db_session, output_index="x")
        resp = client.get("/api/public/outputs")
        data = resp.json()["items"][0]
        assert data["file_url"] == f"/api/public/outputs/{item.id}/file"
        assert data["thumbnail_url"] == f"/api/public/outputs/{item.id}/thumbnail"
        assert data["thumbnail_sm_url"] == f"/api/public/outputs/{item.id}/thumbnail?size=sm"
        assert data["thumbnail_lg_url"] == f"/api/public/outputs/{item.id}/thumbnail?size=lg"


class TestPublicOutputFile:
    """Tests for GET /api/public/outputs/{id}/file."""

    def test_404_for_unindexed_input(self, client, db_session):
        item = make_media_item(db_session, output_index=None)
        resp = client.get(f"/api/public/outputs/{item.id}/file")
        assert resp.status_code == 404

    def test_404_for_missing_file(self, client, db_session):
        item = make_media_item(db_session, output_index="x")
        resp = client.get(f"/api/public/outputs/{item.id}/file")
        # File doesn't actually exist on disk in tests; should 404
        assert resp.status_code == 404
