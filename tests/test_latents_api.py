"""Tests for the Latents API — hero card fields (style, accent, image-only hero).

First test coverage for server/latents_api.py; scoped to the hero-card
surface added in the latent-hero-cards plan.
"""

import json

import pytest

from tests.conftest import make_media_item


@pytest.fixture
def project(client, auth_headers):
    """Create a fresh Latent via the API."""
    resp = client.post("/api/projects", json={"name": "Test Latent"}, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()


def make_image_with_colors(db_session, colors, **kwargs):
    """Create an image media item whose image_meta carries dominant_colors."""
    from server.models import MediaImageMeta

    item = make_media_item(db_session, **kwargs)
    meta = MediaImageMeta(
        media_item_id=item.id,
        width=100,
        height=100,
        format="PNG",
        dominant_colors=json.dumps(colors),
    )
    db_session.add(meta)
    db_session.commit()
    return item


def patch_project(client, auth_headers, project_id, payload):
    return client.patch(f"/api/projects/{project_id}", json=payload, headers=auth_headers)


class TestLatentsAuth:
    """Admin-only regression — hero fields must not loosen access."""

    def test_list_requires_auth(self, client):
        assert client.get("/api/projects").status_code == 401

    def test_list_rejects_member(self, client, member_auth_headers):
        assert client.get("/api/projects", headers=member_auth_headers).status_code == 403

    def test_patch_rejects_member(self, client, auth_headers, member_auth_headers, project):
        resp = client.patch(
            f"/api/projects/{project['id']}",
            json={"hero_style": "plate"},
            headers=member_auth_headers,
        )
        assert resp.status_code == 403


class TestHeroImage:
    def test_summary_defaults(self, project):
        assert project["hero_media_item_id"] is None
        assert project["hero_style"] == "scrim"
        assert project["hero_accent"] is None
        assert project["hero_accent_auto"] is None
        assert project["hero_accent_override"] is None

    def test_set_hero_image_extracts_accent(self, client, auth_headers, db_session, project):
        # Vivid red should beat the dominant near-gray.
        item = make_image_with_colors(db_session, ["#808080", "#ff0000"])
        resp = patch_project(client, auth_headers, project["id"], {"hero_media_item_id": item.id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["hero_media_item_id"] == item.id
        assert data["hero_accent_auto"] == "#ff0000"
        assert data["hero_accent"] == "#ff0000"

    def test_hero_must_be_image(self, client, auth_headers, db_session, project):
        audio = make_media_item(
            db_session, media_type="audio", filename="test.mp3", mime_type="audio/mpeg",
        )
        resp = patch_project(client, auth_headers, project["id"], {"hero_media_item_id": audio.id})
        assert resp.status_code == 400
        assert "image" in resp.json()["detail"].lower()

    def test_hero_missing_item_404(self, client, auth_headers, project):
        resp = patch_project(
            client, auth_headers, project["id"], {"hero_media_item_id": "nope-no-such-id"},
        )
        assert resp.status_code == 404

    def test_hero_change_recomputes_auto_keeps_override(self, client, auth_headers, db_session, project):
        red = make_image_with_colors(db_session, ["#ff0000"])
        blue = make_image_with_colors(db_session, ["#0000ff"])
        patch_project(client, auth_headers, project["id"], {"hero_media_item_id": red.id})
        patch_project(client, auth_headers, project["id"], {"hero_accent_override": "#123456"})

        resp = patch_project(client, auth_headers, project["id"], {"hero_media_item_id": blue.id})
        data = resp.json()
        assert data["hero_accent_auto"] != "#ff0000"       # recomputed for the new image
        assert data["hero_accent_override"] == "#123456"   # survived the swap
        assert data["hero_accent"] == "#123456"            # override still wins

    def test_hero_clear_nulls_auto_keeps_override(self, client, auth_headers, db_session, project):
        item = make_image_with_colors(db_session, ["#ff0000"])
        patch_project(client, auth_headers, project["id"], {"hero_media_item_id": item.id})
        patch_project(client, auth_headers, project["id"], {"hero_accent_override": "#123456"})

        resp = patch_project(client, auth_headers, project["id"], {"hero_media_item_id": ""})
        data = resp.json()
        assert data["hero_media_item_id"] is None
        assert data["hero_accent_auto"] is None
        assert data["hero_accent_override"] == "#123456"

    def test_accent_extraction_failure_never_blocks_patch(self, client, auth_headers, db_session, project):
        # No image_meta and no file on disk — extraction yields None, PATCH succeeds.
        item = make_media_item(db_session, file_path="image/2026-07/does_not_exist.png")
        resp = patch_project(client, auth_headers, project["id"], {"hero_media_item_id": item.id})
        assert resp.status_code == 200
        assert resp.json()["hero_accent_auto"] is None


class TestHeroStyle:
    @pytest.mark.parametrize("style", ["scrim", "plate", "treat"])
    def test_valid_styles_accepted(self, client, auth_headers, project, style):
        resp = patch_project(client, auth_headers, project["id"], {"hero_style": style})
        assert resp.status_code == 200
        assert resp.json()["hero_style"] == style

    @pytest.mark.parametrize("style", ["vignette", "SCRIM", "", "scrim; }"])
    def test_invalid_styles_rejected(self, client, auth_headers, project, style):
        resp = patch_project(client, auth_headers, project["id"], {"hero_style": style})
        assert resp.status_code == 400


class TestHeroAccentOverride:
    def test_accepts_and_normalizes_hex(self, client, auth_headers, project):
        resp = patch_project(client, auth_headers, project["id"], {"hero_accent_override": "#AbCdEf"})
        assert resp.status_code == 200
        assert resp.json()["hero_accent_override"] == "#abcdef"
        assert resp.json()["hero_accent"] == "#abcdef"

    @pytest.mark.parametrize(
        "bad",
        [
            "abcdef",            # missing #
            "#abcd",             # short form not allowed
            "#abcdefg",          # too long
            "#abcdeg",           # non-hex digit
            "#abcdef; }",        # style-injection attempt
            "#abcdef}body{",     # style-injection attempt
            "red",
            "rgb(1,2,3)",
        ],
    )
    def test_rejects_non_hex(self, client, auth_headers, project, bad):
        resp = patch_project(client, auth_headers, project["id"], {"hero_accent_override": bad})
        assert resp.status_code == 400

    def test_empty_string_resets_to_auto(self, client, auth_headers, db_session, project):
        item = make_image_with_colors(db_session, ["#ff0000"])
        patch_project(client, auth_headers, project["id"], {"hero_media_item_id": item.id})
        patch_project(client, auth_headers, project["id"], {"hero_accent_override": "#123456"})

        resp = patch_project(client, auth_headers, project["id"], {"hero_accent_override": ""})
        data = resp.json()
        assert data["hero_accent_override"] is None
        assert data["hero_accent"] == data["hero_accent_auto"] == "#ff0000"
