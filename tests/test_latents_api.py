"""Tests for the Latents API — hero card fields (style, accent, image-only hero)
and section/slot styles (backgrounds + color coding).

Covers the surfaces added in the latent-hero-cards and latent-section-styles
plans.
"""

import json
from datetime import timedelta

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


# ---------------------------------------------------------------------------
# Section / slot styles (latent-section-styles plan)
# ---------------------------------------------------------------------------


BAD_HEX = [
    "abcdef",            # missing #
    "#abcd",             # short form not allowed
    "#abcdefg",          # too long
    "#abcdeg",           # non-hex digit
    "#abcdef; }",        # style-injection attempt
    "#abcdef}body{",     # style-injection attempt
    "red",
    "rgb(1,2,3)",
]


@pytest.fixture
def slot(client, auth_headers, project):
    resp = client.post(f"/api/projects/{project['id']}/slots", json={}, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()


def patch_slot(client, auth_headers, project_id, slot_id, payload):
    return client.patch(
        f"/api/projects/{project_id}/slots/{slot_id}", json=payload, headers=auth_headers,
    )


def attach_item(client, auth_headers, project_id, media_id, slot_id=None):
    resp = client.post(
        f"/api/projects/{project_id}/items",
        json={"media_item_ids": [media_id], "slot_id": slot_id},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["items"][0]


def star_item(client, auth_headers, project_id, item_id, on=True):
    return client.put(
        f"/api/projects/{project_id}/items/{item_id}/primary",
        json={"is_primary": on},
        headers=auth_headers,
    )


def get_slot(client, auth_headers, project_id, slot_id):
    resp = client.get(f"/api/projects/{project_id}", headers=auth_headers)
    assert resp.status_code == 200
    return next(s for s in resp.json()["slots"] if s["id"] == slot_id)


class TestSectionStyles:
    def test_summary_default_empty(self, project):
        assert project["section_styles"] == {}

    def test_set_accent_normalizes(self, client, auth_headers, project):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"accent": "#AbCdEf"}}},
        )
        assert resp.status_code == 200
        assert resp.json()["section_styles"]["docs"]["accent"] == "#abcdef"

    def test_unknown_section_rejected(self, client, auth_headers, project):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"header": {"accent": "#abcdef"}}},
        )
        assert resp.status_code == 400

    def test_unknown_subkey_rejected(self, client, auth_headers, project):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"glow": "#abcdef"}}},
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize("bad", BAD_HEX)
    def test_rejects_non_hex(self, client, auth_headers, project, bad):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"accent": bad}}},
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize("mode", ["image", "solid", "none"])
    def test_valid_bg_modes(self, client, auth_headers, project, mode):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"loose": {"bg_mode": mode}}},
        )
        assert resp.status_code == 200
        assert resp.json()["section_styles"]["loose"]["bg_mode"] == mode

    @pytest.mark.parametrize("mode", ["auto", "vignette", "solid; }"])
    def test_invalid_bg_modes(self, client, auth_headers, project, mode):
        # `auto` is slot-only — sections have no starred image to inherit.
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"loose": {"bg_mode": mode}}},
        )
        assert resp.status_code == 400

    def test_bg_media_missing_404(self, client, auth_headers, project):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"threads": {"bg_media_item_id": "nope-no-such-id"}}},
        )
        assert resp.status_code == 404

    def test_bg_media_must_be_image(self, client, auth_headers, db_session, project):
        audio = make_media_item(
            db_session, media_type="audio", filename="test.mp3", mime_type="audio/mpeg",
        )
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"threads": {"bg_media_item_id": audio.id}}},
        )
        assert resp.status_code == 400

    def test_bg_media_image_accepted(self, client, auth_headers, db_session, project):
        item = make_image_with_colors(db_session, ["#ff0000"])
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"threads": {"bg_media_item_id": item.id, "bg_mode": "image"}}},
        )
        assert resp.status_code == 200
        assert resp.json()["section_styles"]["threads"]["bg_media_item_id"] == item.id

    def test_empty_string_deletes_subkey(self, client, auth_headers, project):
        patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"accent": "#abcdef"}}},
        )
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"accent": ""}}},
        )
        assert resp.status_code == 200
        # last key removed -> whole section entry drops away
        assert "docs" not in resp.json()["section_styles"]

    def test_merge_preserves_other_sections_and_keys(self, client, auth_headers, project):
        patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"accent": "#111111", "text": "#222222"}, "links": {"accent": "#333333"}}},
        )
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"accent": "#444444"}}},
        )
        styles = resp.json()["section_styles"]
        assert styles["docs"] == {"accent": "#444444", "text": "#222222"}
        assert styles["links"] == {"accent": "#333333"}

    def test_empty_section_object_deletes_entry(self, client, auth_headers, project):
        patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"accent": "#abcdef"}, "links": {"accent": "#333333"}}},
        )
        resp = patch_project(
            client, auth_headers, project["id"], {"section_styles": {"docs": {}}},
        )
        styles = resp.json()["section_styles"]
        assert "docs" not in styles
        assert styles["links"] == {"accent": "#333333"}

    def test_empty_dict_clears_all(self, client, auth_headers, project):
        patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"accent": "#abcdef"}}},
        )
        resp = patch_project(client, auth_headers, project["id"], {"section_styles": {}})
        assert resp.status_code == 200
        assert resp.json()["section_styles"] == {}

    def test_member_403(self, client, auth_headers, member_auth_headers, project):
        resp = client.patch(
            f"/api/projects/{project['id']}",
            json={"section_styles": {"docs": {"accent": "#abcdef"}}},
            headers=member_auth_headers,
        )
        assert resp.status_code == 403


class TestSlotStyle:
    def test_summary_defaults(self, slot):
        assert slot["style"] == {}
        assert slot["accent_auto"] is None
        assert slot["accent"] is None
        assert slot["primary_image_media_id"] is None

    @pytest.mark.parametrize("key", ["accent", "bg_color", "border", "text"])
    def test_hex_keys_accept_and_normalize(self, client, auth_headers, project, slot, key):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {key: "#AbCdEf"}},
        )
        assert resp.status_code == 200
        assert resp.json()["style"][key] == "#abcdef"

    @pytest.mark.parametrize("bad", BAD_HEX)
    def test_rejects_non_hex(self, client, auth_headers, project, slot, bad):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"accent": bad}},
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize("mode", ["auto", "image", "solid", "none"])
    def test_valid_bg_modes(self, client, auth_headers, project, slot, mode):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"bg_mode": mode}},
        )
        assert resp.status_code == 200
        assert resp.json()["style"]["bg_mode"] == mode

    def test_invalid_bg_mode(self, client, auth_headers, project, slot):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"bg_mode": "vignette"}},
        )
        assert resp.status_code == 400

    def test_bg_media_missing_404(self, client, auth_headers, project, slot):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_media_item_id": "nope-no-such-id"}},
        )
        assert resp.status_code == 404

    def test_bg_media_must_be_image(self, client, auth_headers, db_session, project, slot):
        audio = make_media_item(
            db_session, media_type="audio", filename="test.mp3", mime_type="audio/mpeg",
        )
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_media_item_id": audio.id}},
        )
        assert resp.status_code == 400

    def test_unknown_key_rejected(self, client, auth_headers, project, slot):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"glow": "#abcdef"}},
        )
        assert resp.status_code == 400

    def test_partial_merge_and_per_key_reset(self, client, auth_headers, project, slot):
        patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"accent": "#111111", "border": "#222222"}},
        )
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"accent": ""}},
        )
        assert resp.json()["style"] == {"border": "#222222"}

    def test_style_survives_unrelated_patch(self, client, auth_headers, project, slot):
        patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"accent": "#111111"}},
        )
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"label": "Renamed", "status": "developing"},
        )
        assert resp.json()["style"] == {"accent": "#111111"}

    def test_reset_all_clears_style(self, client, auth_headers, project, slot):
        patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"accent": "#111111", "bg_mode": "solid", "bg_color": "#222222"}},
        )
        resp = patch_slot(client, auth_headers, project["id"], slot["id"], {"style": {}})
        assert resp.status_code == 200
        assert resp.json()["style"] == {}


class TestFaceStyle:
    """bg_style treatments + head_tint retirement (latent-faces revision)."""

    @pytest.mark.parametrize("treatment", ["scrim", "plate", "treat"])
    def test_slot_bg_style_accepted(self, client, auth_headers, project, slot, treatment):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_style": treatment}},
        )
        assert resp.status_code == 200
        assert resp.json()["style"]["bg_style"] == treatment

    @pytest.mark.parametrize("treatment", ["scrim", "plate", "treat"])
    def test_section_bg_style_accepted(self, client, auth_headers, project, treatment):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"bg_style": treatment}}},
        )
        assert resp.status_code == 200
        assert resp.json()["section_styles"]["docs"]["bg_style"] == treatment

    @pytest.mark.parametrize("bad", ["vignette", "SCRIM", "scrim; }", "treat}body{"])
    def test_bg_style_rejected(self, client, auth_headers, project, slot, bad):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_style": bad}},
        )
        assert resp.status_code == 400

    def test_bg_style_empty_string_deletes(self, client, auth_headers, project, slot):
        patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_style": "treat", "accent": "#111111"}},
        )
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"bg_style": ""}},
        )
        assert resp.json()["style"] == {"accent": "#111111"}

    def test_bg_style_legal_before_image_picked(self, client, auth_headers, project, slot):
        # Single-key PATCHes stay order-independent: treatment before image.
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_style": "plate"}},
        )
        assert resp.status_code == 200
        assert "bg_media_item_id" not in resp.json()["style"]

    def test_head_tint_rejected_for_slots(self, client, auth_headers, project, slot):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"head_tint": "#abcdef"}},
        )
        assert resp.status_code == 400

    def test_head_tint_rejected_for_sections(self, client, auth_headers, project):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"docs": {"head_tint": "#abcdef"}}},
        )
        assert resp.status_code == 400

    def test_legacy_head_tint_scrubbed_on_write(self, client, auth_headers, db_session, project, slot):
        # Pre-faces styles may hold head_tint; any style write scrubs it.
        from server.models import ProjectSlot

        db_session.query(ProjectSlot).filter(ProjectSlot.id == slot["id"]).update(
            {"style_json": json.dumps({"accent": "#111111", "head_tint": "#222222"})}
        )
        db_session.commit()
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"text": "#333333"}},
        )
        assert resp.json()["style"] == {"accent": "#111111", "text": "#333333"}


class TestSolidFaceAccent:
    """Effective accent: manual override > solid face color > extracted."""

    def test_solid_color_becomes_accent(self, client, auth_headers, project, slot):
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_mode": "solid", "bg_color": "#123456"}},
        )
        assert resp.json()["accent"] == "#123456"

    def test_solid_wins_over_extracted(self, client, auth_headers, db_session, project, slot):
        item = make_image_with_colors(db_session, ["#ff0000"])
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        star_item(client, auth_headers, project["id"], pi["id"])
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_mode": "solid", "bg_color": "#123456"}},
        )
        assert resp.json()["accent_auto"] == "#ff0000"
        assert resp.json()["accent"] == "#123456"

    def test_override_wins_over_solid(self, client, auth_headers, project, slot):
        patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_mode": "solid", "bg_color": "#123456"}},
        )
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"accent": "#654321"}},
        )
        assert resp.json()["accent"] == "#654321"

        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"accent": ""}},
        )
        assert resp.json()["accent"] == "#123456"  # falls back to the face color

    def test_non_solid_bg_color_not_accent(self, client, auth_headers, project, slot):
        # bg_color only feeds the accent while the face is actually solid.
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"],
            {"style": {"bg_color": "#123456", "bg_mode": "none"}},
        )
        assert resp.json()["accent"] is None


class TestSlotCountsInResponses:
    """Slot summaries from mutation endpoints must carry real counts —
    the client replaces/merges slot objects from these responses."""

    def _slot_with_items(self, client, auth_headers, db_session, project, slot, n=2):
        for _ in range(n):
            item = make_image_with_colors(db_session, ["#ff0000"])
            attach_item(client, auth_headers, project["id"], item.id, slot["id"])

    def test_label_patch_keeps_counts(self, client, auth_headers, db_session, project, slot):
        self._slot_with_items(client, auth_headers, db_session, project, slot)
        resp = patch_slot(client, auth_headers, project["id"], slot["id"], {"label": "Renamed"})
        assert resp.json()["item_count"] == 2

    def test_style_patch_keeps_counts(self, client, auth_headers, db_session, project, slot):
        # The exact clobber path from the field report: a style edit zeroed
        # the visible file count.
        self._slot_with_items(client, auth_headers, db_session, project, slot)
        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"accent": "#111111"}},
        )
        assert resp.json()["item_count"] == 2

    def test_reorder_keeps_counts(self, client, auth_headers, db_session, project, slot):
        self._slot_with_items(client, auth_headers, db_session, project, slot)
        other = client.post(
            f"/api/projects/{project['id']}/slots", json={}, headers=auth_headers,
        ).json()
        resp = client.post(
            f"/api/projects/{project['id']}/slots/reorder",
            json={"order": [other["id"], slot["id"]]},
            headers=auth_headers,
        )
        rows = {s["id"]: s for s in resp.json()["slots"]}
        assert rows[slot["id"]]["item_count"] == 2
        assert rows[other["id"]]["item_count"] == 0

    def test_create_slot_counts_zero(self, client, auth_headers, project):
        resp = client.post(
            f"/api/projects/{project['id']}/slots", json={}, headers=auth_headers,
        )
        assert resp.json()["item_count"] == 0
        assert resp.json()["thread_count"] == 0


class TestSlotAccentAuto:
    def test_star_image_computes_accent(self, client, auth_headers, db_session, project, slot):
        item = make_image_with_colors(db_session, ["#808080", "#ff0000"])
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        resp = star_item(client, auth_headers, project["id"], pi["id"])
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_primary"] is True
        # fresh slot summary rides along for instant repaint
        assert data["slot"]["accent_auto"] == "#ff0000"
        assert data["slot"]["accent"] == "#ff0000"
        assert data["slot"]["primary_image_media_id"] == item.id
        assert get_slot(client, auth_headers, project["id"], slot["id"])["accent_auto"] == "#ff0000"

    def test_unstar_clears_accent(self, client, auth_headers, db_session, project, slot):
        item = make_image_with_colors(db_session, ["#ff0000"])
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        star_item(client, auth_headers, project["id"], pi["id"])
        resp = star_item(client, auth_headers, project["id"], pi["id"], on=False)
        assert resp.json()["slot"]["accent_auto"] is None
        assert resp.json()["slot"]["primary_image_media_id"] is None

    def test_star_audio_is_noop(self, client, auth_headers, db_session, project, slot):
        audio = make_media_item(
            db_session, media_type="audio", filename="test.mp3", mime_type="audio/mpeg",
        )
        pi = attach_item(client, auth_headers, project["id"], audio.id, slot["id"])
        resp = star_item(client, auth_headers, project["id"], pi["id"])
        assert resp.status_code == 200
        assert resp.json()["slot"]["accent_auto"] is None

    def test_override_wins_and_resets_to_auto(self, client, auth_headers, db_session, project, slot):
        item = make_image_with_colors(db_session, ["#ff0000"])
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        star_item(client, auth_headers, project["id"], pi["id"])

        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"accent": "#123456"}},
        )
        assert resp.json()["accent"] == "#123456"
        assert resp.json()["accent_auto"] == "#ff0000"

        resp = patch_slot(
            client, auth_headers, project["id"], slot["id"], {"style": {"accent": ""}},
        )
        assert resp.json()["accent"] == "#ff0000"

    def test_detach_starred_recomputes(self, client, auth_headers, db_session, project, slot):
        item = make_image_with_colors(db_session, ["#ff0000"])
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        star_item(client, auth_headers, project["id"], pi["id"])

        resp = client.delete(
            f"/api/projects/{project['id']}/items/{pi['id']}", headers=auth_headers,
        )
        assert resp.status_code == 204
        assert get_slot(client, auth_headers, project["id"], slot["id"])["accent_auto"] is None

    def test_move_starred_recomputes_both_slots(self, client, auth_headers, db_session, project, slot):
        other = client.post(
            f"/api/projects/{project['id']}/slots", json={}, headers=auth_headers,
        ).json()
        item = make_image_with_colors(db_session, ["#ff0000"])
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        star_item(client, auth_headers, project["id"], pi["id"])

        resp = client.patch(
            f"/api/projects/{project['id']}/items/{pi['id']}",
            json={"slot_id": other["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert get_slot(client, auth_headers, project["id"], slot["id"])["accent_auto"] is None
        moved_to = get_slot(client, auth_headers, project["id"], other["id"])
        assert moved_to["accent_auto"] == "#ff0000"
        assert moved_to["primary_image_media_id"] == item.id

    def test_clear_slot_items_nulls_accent(self, client, auth_headers, db_session, project, slot):
        item = make_image_with_colors(db_session, ["#ff0000"])
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        star_item(client, auth_headers, project["id"], pi["id"])

        resp = client.delete(
            f"/api/projects/{project['id']}/slots/{slot['id']}/items", headers=auth_headers,
        )
        assert resp.status_code == 200
        assert get_slot(client, auth_headers, project["id"], slot["id"])["accent_auto"] is None

    def test_two_starred_latest_added_wins(self, client, auth_headers, db_session, project, slot):
        from server.models import ProjectItem, _utcnow

        red = make_image_with_colors(db_session, ["#ff0000"])
        blue = make_image_with_colors(db_session, ["#0000ff"])
        pi_red = attach_item(client, auth_headers, project["id"], red.id, slot["id"])
        pi_blue = attach_item(client, auth_headers, project["id"], blue.id, slot["id"])
        # Pin added_at explicitly so "latest wins" is deterministic under test speed.
        now = _utcnow()
        db_session.query(ProjectItem).filter(ProjectItem.id == pi_red["id"]).update(
            {"added_at": now - timedelta(minutes=1)}
        )
        db_session.query(ProjectItem).filter(ProjectItem.id == pi_blue["id"]).update(
            {"added_at": now}
        )
        db_session.commit()

        star_item(client, auth_headers, project["id"], pi_red["id"])
        resp = star_item(client, auth_headers, project["id"], pi_blue["id"])
        data = resp.json()["slot"]
        assert data["accent_auto"] == "#0000ff"
        assert data["primary_image_media_id"] == blue.id

    def test_extraction_failure_never_blocks_star(self, client, auth_headers, db_session, project, slot):
        # No image_meta and no file on disk — extraction yields None, star succeeds.
        item = make_media_item(db_session, file_path="image/2026-07/does_not_exist.png")
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        resp = star_item(client, auth_headers, project["id"], pi["id"])
        assert resp.status_code == 200
        assert resp.json()["slot"]["accent_auto"] is None
        assert resp.json()["slot"]["primary_image_media_id"] == item.id

    def test_primary_image_in_list_slots(self, client, auth_headers, db_session, project, slot):
        item = make_image_with_colors(db_session, ["#ff0000"])
        pi = attach_item(client, auth_headers, project["id"], item.id, slot["id"])
        star_item(client, auth_headers, project["id"], pi["id"])

        resp = client.get(f"/api/projects/{project['id']}/slots", headers=auth_headers)
        assert resp.status_code == 200
        row = next(s for s in resp.json()["slots"] if s["id"] == slot["id"])
        assert row["primary_image_media_id"] == item.id
        assert row["accent_auto"] == "#ff0000"
