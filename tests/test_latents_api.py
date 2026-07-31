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


# ---------------------------------------------------------------------------
# Playlists (2026-07-24-latent-playlists): manual file order, slot playlists,
# Latent running orders.
# ---------------------------------------------------------------------------


def make_audio(db_session, name, seconds=None):
    """An audio media item, optionally with a duration in its audio_meta."""
    from server.models import MediaAudioMeta

    item = make_media_item(
        db_session,
        filename=name,
        media_type="audio",
        mime_type="audio/wav",
        file_path=f"audio/2026-07/abcdef12_{name}",
    )
    if seconds is not None:
        db_session.add(MediaAudioMeta(
            media_item_id=item.id, duration_seconds=seconds, sample_rate=44100, channels=2,
        ))
        db_session.commit()
    return item


def list_items(client, auth_headers, project_id, slot_id):
    resp = client.get(
        f"/api/projects/{project_id}/items", params={"slot_id": slot_id}, headers=auth_headers,
    )
    assert resp.status_code == 200
    return resp.json()["items"]


def reorder_items(client, auth_headers, project_id, slot_id, order):
    return client.post(
        f"/api/projects/{project_id}/slots/{slot_id}/items/reorder",
        json={"order": order}, headers=auth_headers,
    )


def get_playlist(client, auth_headers, project_id, slot_id):
    resp = client.get(
        f"/api/projects/{project_id}/slots/{slot_id}/playlist", headers=auth_headers,
    )
    assert resp.status_code == 200
    return resp.json()


def put_playlist(client, auth_headers, project_id, slot_id, order):
    return client.put(
        f"/api/projects/{project_id}/slots/{slot_id}/playlist",
        json={"order": order}, headers=auth_headers,
    )


def attach_audio(client, auth_headers, db_session, project_id, slot_id, names):
    """Attach fresh audio files to a slot in order; return (items, media)."""
    media = [make_audio(db_session, n) for n in names]
    items = [attach_item(client, auth_headers, project_id, m.id, slot_id) for m in media]
    return items, media


# --- Slideshow helpers (2026-07-26-latent-slideshow) -----------------------


def make_image(db_session, name, width=800, height=600):
    """An image media item with image_meta, so _slide_summary has dimensions."""
    from server.models import MediaImageMeta

    item = make_media_item(
        db_session,
        filename=name,
        media_type="image",
        mime_type="image/png",
        file_path=f"images/2026-07/abcdef12_{name}",
    )
    db_session.add(MediaImageMeta(
        media_item_id=item.id, width=width, height=height, format="PNG",
    ))
    db_session.commit()
    return item


def make_video(db_session, name, width=1920, height=1080):
    from server.models import MediaVideoMeta

    item = make_media_item(
        db_session,
        filename=name,
        media_type="video",
        mime_type="video/mp4",
        file_path=f"video/2026-07/abcdef12_{name}",
    )
    db_session.add(MediaVideoMeta(
        media_item_id=item.id, duration_seconds=4.0, width=width, height=height, fps=30.0,
    ))
    db_session.commit()
    return item


def get_slideshow(client, auth_headers, project_id, slot_id):
    resp = client.get(
        f"/api/projects/{project_id}/slots/{slot_id}/slideshow", headers=auth_headers,
    )
    assert resp.status_code == 200
    return resp.json()


def put_slideshow(client, auth_headers, project_id, slot_id, order):
    return client.put(
        f"/api/projects/{project_id}/slots/{slot_id}/slideshow",
        json={"order": order}, headers=auth_headers,
    )


def attach_images(client, auth_headers, db_session, project_id, slot_id, names):
    """Attach fresh images to a slot in order; return (items, media)."""
    media = [make_image(db_session, n) for n in names]
    items = [attach_item(client, auth_headers, project_id, m.id, slot_id) for m in media]
    return items, media


def create_slideshow(client, auth_headers, project_id, name="Show"):
    resp = client.post(
        f"/api/projects/{project_id}/slideshows", json={"name": name}, headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


def add_slides(client, auth_headers, project_id, slideshow_id, media_ids):
    return client.post(
        f"/api/projects/{project_id}/slideshows/{slideshow_id}/items",
        json={"media_item_ids": media_ids}, headers=auth_headers,
    )


class TestItemOrder:
    def test_attach_appends(self, client, auth_headers, db_session, project, slot):
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav", "c.wav"],
        )
        listed = list_items(client, auth_headers, project["id"], slot["id"])
        assert [i["media_item_id"] for i in listed] == [m.id for m in media]
        assert [i["position"] for i in listed] == [1, 2, 3]

    def test_reorder_persists(self, client, auth_headers, db_session, project, slot):
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav", "c.wav"],
        )
        order = [items[2]["id"], items[0]["id"], items[1]["id"]]
        resp = reorder_items(client, auth_headers, project["id"], slot["id"], order)
        assert resp.status_code == 200
        assert [i["id"] for i in resp.json()["items"]] == order
        assert [i["id"] for i in list_items(client, auth_headers, project["id"], slot["id"])] == order

    def test_upload_after_reorder_lands_last(self, client, auth_headers, db_session, project, slot):
        items, _ = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        reorder_items(client, auth_headers, project["id"], slot["id"], [items[1]["id"], items[0]["id"]])
        late = make_audio(db_session, "late.wav")
        attach_item(client, auth_headers, project["id"], late.id, slot["id"])
        listed = list_items(client, auth_headers, project["id"], slot["id"])
        assert listed[-1]["media_item_id"] == late.id

    def test_reorder_rejects_partial_order(self, client, auth_headers, db_session, project, slot):
        items, _ = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        assert reorder_items(
            client, auth_headers, project["id"], slot["id"], [items[0]["id"]],
        ).status_code == 400

    def test_reorder_rejects_duplicates(self, client, auth_headers, db_session, project, slot):
        items, _ = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        assert reorder_items(
            client, auth_headers, project["id"], slot["id"], [items[0]["id"], items[0]["id"]],
        ).status_code == 400

    def test_reorder_rejects_foreign_item(self, client, auth_headers, db_session, project, slot):
        items, _ = attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        loose = make_audio(db_session, "loose.wav")
        loose_item = attach_item(client, auth_headers, project["id"], loose.id)
        assert reorder_items(
            client, auth_headers, project["id"], slot["id"], [items[0]["id"], loose_item["id"]],
        ).status_code == 400

    def test_loose_pile_numbers_independently(self, client, auth_headers, db_session, project, slot):
        attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        loose = make_audio(db_session, "loose.wav")
        item = attach_item(client, auth_headers, project["id"], loose.id)
        assert item["position"] == 1

    def test_reorder_rejects_member(self, client, auth_headers, member_auth_headers, db_session, project, slot):
        items, _ = attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        resp = client.post(
            f"/api/projects/{project['id']}/slots/{slot['id']}/items/reorder",
            json={"order": [items[0]["id"]]}, headers=member_auth_headers,
        )
        assert resp.status_code == 403


class TestSlotPlaylist:
    def test_seeded_from_file_order(self, client, auth_headers, db_session, project, slot):
        _, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [m.id for m in media]

    def test_audio_only(self, client, auth_headers, db_session, project, slot):
        audio = make_audio(db_session, "a.wav")
        image = make_media_item(db_session)
        attach_item(client, auth_headers, project["id"], audio.id, slot["id"])
        attach_item(client, auth_headers, project["id"], image.id, slot["id"])
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [audio.id]

    def test_total_seconds(self, client, auth_headers, db_session, project, slot):
        for name, secs in (("a.wav", 61.5), ("b.wav", 30.0)):
            m = make_audio(db_session, name, seconds=secs)
            attach_item(client, auth_headers, project["id"], m.id, slot["id"])
        assert get_playlist(client, auth_headers, project["id"], slot["id"])["total_seconds"] == 91.5

    def test_untouched_playlist_survives_a_file_reorder(self, client, auth_headers, db_session, project, slot):
        """Dragging files must not move tracks even before the playlist is arranged.

        An untouched playlist mirrors the file order, so the file reorder has
        to pin it first or the tracks come along for the ride.
        """
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav", "c.wav"],
        )
        before = [t["media_item_id"] for t in
                  get_playlist(client, auth_headers, project["id"], slot["id"])["tracks"]]
        reorder_items(client, auth_headers, project["id"], slot["id"],
                      [items[2]["id"], items[1]["id"], items[0]["id"]])
        after = [t["media_item_id"] for t in
                 get_playlist(client, auth_headers, project["id"], slot["id"])["tracks"]]
        assert after == before

    def test_pinned_playlist_still_appends_new_audio(self, client, auth_headers, db_session, project, slot):
        """Pinning on file-reorder must not turn the playlist into a closed set."""
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        reorder_items(client, auth_headers, project["id"], slot["id"],
                      [items[1]["id"], items[0]["id"]])
        late = make_audio(db_session, "late.wav")
        attach_item(client, auth_headers, project["id"], late.id, slot["id"])
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [media[0].id, media[1].id, late.id]

    def test_order_survives_a_file_reorder(self, client, auth_headers, db_session, project, slot):
        """The two orders are independent — dragging files must not move tracks."""
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav", "c.wav"],
        )
        put_playlist(client, auth_headers, project["id"], slot["id"],
                     [media[2].id, media[0].id, media[1].id])
        reorder_items(client, auth_headers, project["id"], slot["id"],
                      [items[1]["id"], items[2]["id"], items[0]["id"]])
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [media[2].id, media[0].id, media[1].id]

    def test_file_order_survives_a_playlist_reorder(self, client, auth_headers, db_session, project, slot):
        """…and the reverse: dragging tracks must not move the file rows."""
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        put_playlist(client, auth_headers, project["id"], slot["id"], [media[1].id, media[0].id])
        listed = list_items(client, auth_headers, project["id"], slot["id"])
        assert [i["id"] for i in listed] == [items[0]["id"], items[1]["id"]]

    def test_new_upload_appends(self, client, auth_headers, db_session, project, slot):
        _, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        put_playlist(client, auth_headers, project["id"], slot["id"], [media[1].id, media[0].id])
        late = make_audio(db_session, "late.wav")
        attach_item(client, auth_headers, project["id"], late.id, slot["id"])
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [media[1].id, media[0].id, late.id]

    def test_detached_file_drops_out(self, client, auth_headers, db_session, project, slot):
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        client.delete(
            f"/api/projects/{project['id']}/items/{items[0]['id']}", headers=auth_headers,
        )
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [media[1].id]

    def test_moved_file_follows_its_slot(self, client, auth_headers, db_session, project, slot):
        items, media = attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        other = client.post(
            f"/api/projects/{project['id']}/slots", json={}, headers=auth_headers,
        ).json()
        client.patch(
            f"/api/projects/{project['id']}/items/{items[0]['id']}",
            json={"slot_id": other["id"]}, headers=auth_headers,
        )
        assert get_playlist(client, auth_headers, project["id"], slot["id"])["tracks"] == []
        moved = get_playlist(client, auth_headers, project["id"], other["id"])
        assert [t["media_item_id"] for t in moved["tracks"]] == [media[0].id]

    def test_returning_file_keeps_its_place(self, client, auth_headers, db_session, project, slot):
        """Stored ids are a hint, not a membership list — a round trip is lossless."""
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav", "c.wav"],
        )
        put_playlist(client, auth_headers, project["id"], slot["id"],
                     [media[2].id, media[1].id, media[0].id])
        client.delete(f"/api/projects/{project['id']}/items/{items[1]['id']}", headers=auth_headers)
        attach_item(client, auth_headers, project["id"], media[1].id, slot["id"])
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [media[2].id, media[1].id, media[0].id]

    def test_put_accepts_partial_order(self, client, auth_headers, db_session, project, slot):
        _, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav", "c.wav"],
        )
        resp = put_playlist(client, auth_headers, project["id"], slot["id"], [media[2].id])
        assert resp.status_code == 200
        assert [t["media_item_id"] for t in resp.json()["tracks"]] == [media[2].id, media[0].id, media[1].id]

    def test_put_rejects_non_audio(self, client, auth_headers, db_session, project, slot):
        image = make_media_item(db_session)
        attach_item(client, auth_headers, project["id"], image.id, slot["id"])
        assert put_playlist(
            client, auth_headers, project["id"], slot["id"], [image.id],
        ).status_code == 400

    def test_put_rejects_foreign_audio(self, client, auth_headers, db_session, project, slot):
        elsewhere = make_audio(db_session, "elsewhere.wav")
        assert put_playlist(
            client, auth_headers, project["id"], slot["id"], [elsewhere.id],
        ).status_code == 400

    def test_put_rejects_duplicates(self, client, auth_headers, db_session, project, slot):
        _, media = attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        assert put_playlist(
            client, auth_headers, project["id"], slot["id"], [media[0].id, media[0].id],
        ).status_code == 400

    def test_corrupt_hint_falls_back_to_file_order(self, client, auth_headers, db_session, project, slot):
        from server.models import ProjectSlot

        _, media = attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        row = db_session.query(ProjectSlot).filter(ProjectSlot.id == slot["id"]).first()
        row.playlist_json = "{not json"
        db_session.commit()
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [media[0].id]

    def test_playlist_rejects_member(self, client, member_auth_headers, project, slot):
        resp = client.get(
            f"/api/projects/{project['id']}/slots/{slot['id']}/playlist", headers=member_auth_headers,
        )
        assert resp.status_code == 403


class TestLatentPlaylists:
    def create(self, client, auth_headers, project_id, name="Album seq"):
        resp = client.post(
            f"/api/projects/{project_id}/playlists", json={"name": name}, headers=auth_headers,
        )
        assert resp.status_code == 201
        return resp.json()

    def add(self, client, auth_headers, project_id, playlist_id, media_ids):
        return client.post(
            f"/api/projects/{project_id}/playlists/{playlist_id}/items",
            json={"media_item_ids": media_ids}, headers=auth_headers,
        )

    def test_create_and_list(self, client, auth_headers, project):
        self.create(client, auth_headers, project["id"], "v1")
        self.create(client, auth_headers, project["id"], "v2")
        resp = client.get(f"/api/projects/{project['id']}/playlists", headers=auth_headers)
        assert [p["name"] for p in resp.json()["playlists"]] == ["v1", "v2"]

    def test_starts_empty(self, client, auth_headers, db_session, project, slot):
        """Curated: nothing enters a running order uninvited."""
        attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        pl = self.create(client, auth_headers, project["id"])
        assert pl["tracks"] == []

    def test_add_tracks_across_slots(self, client, auth_headers, db_session, project, slot):
        other = client.post(
            f"/api/projects/{project['id']}/slots", json={}, headers=auth_headers,
        ).json()
        _, first = attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        _, second = attach_audio(client, auth_headers, db_session, project["id"], other["id"], ["b.wav"])
        pl = self.create(client, auth_headers, project["id"])
        resp = self.add(client, auth_headers, project["id"], pl["id"], [second[0].id, first[0].id])
        assert [t["media_item_id"] for t in resp.json()["tracks"]] == [second[0].id, first[0].id]

    def test_add_skips_dupes_non_audio_and_non_members(self, client, auth_headers, db_session, project, slot):
        _, media = attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        image = make_media_item(db_session)
        attach_item(client, auth_headers, project["id"], image.id, slot["id"])
        stranger = make_audio(db_session, "stranger.wav")
        pl = self.create(client, auth_headers, project["id"])
        self.add(client, auth_headers, project["id"], pl["id"], [media[0].id])
        resp = self.add(
            client, auth_headers, project["id"], pl["id"],
            [media[0].id, image.id, stranger.id, "no-such-id"],
        )
        assert [t["media_item_id"] for t in resp.json()["tracks"]] == [media[0].id]

    def test_reorder(self, client, auth_headers, db_session, project, slot):
        _, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav", "c.wav"],
        )
        pl = self.create(client, auth_headers, project["id"])
        tracks = self.add(
            client, auth_headers, project["id"], pl["id"], [m.id for m in media],
        ).json()["tracks"]
        order = [tracks[2]["playlist_item_id"], tracks[0]["playlist_item_id"], tracks[1]["playlist_item_id"]]
        resp = client.post(
            f"/api/projects/{project['id']}/playlists/{pl['id']}/items/reorder",
            json={"order": order}, headers=auth_headers,
        )
        assert resp.status_code == 200
        assert [t["playlist_item_id"] for t in resp.json()["tracks"]] == order

    def test_reorder_rejects_foreign_row(self, client, auth_headers, db_session, project, slot):
        pl = self.create(client, auth_headers, project["id"])
        resp = client.post(
            f"/api/projects/{project['id']}/playlists/{pl['id']}/items/reorder",
            json={"order": ["nope"]}, headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_remove_track(self, client, auth_headers, db_session, project, slot):
        _, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        pl = self.create(client, auth_headers, project["id"])
        tracks = self.add(
            client, auth_headers, project["id"], pl["id"], [m.id for m in media],
        ).json()["tracks"]
        resp = client.delete(
            f"/api/projects/{project['id']}/playlists/{pl['id']}/items/{tracks[0]['playlist_item_id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert [t["media_item_id"] for t in resp.json()["tracks"]] == [media[1].id]

    def test_detached_track_hidden_then_restored(self, client, auth_headers, db_session, project, slot):
        items, media = attach_audio(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.wav", "b.wav"],
        )
        pl = self.create(client, auth_headers, project["id"])
        self.add(client, auth_headers, project["id"], pl["id"], [m.id for m in media])
        client.delete(f"/api/projects/{project['id']}/items/{items[0]['id']}", headers=auth_headers)

        resp = client.get(f"/api/projects/{project['id']}/playlists", headers=auth_headers)
        assert [t["media_item_id"] for t in resp.json()["playlists"][0]["tracks"]] == [media[1].id]

        attach_item(client, auth_headers, project["id"], media[0].id, slot["id"])
        resp = client.get(f"/api/projects/{project['id']}/playlists", headers=auth_headers)
        assert [t["media_item_id"] for t in resp.json()["playlists"][0]["tracks"]] == [
            media[0].id, media[1].id,
        ]

    def test_deleted_media_cascades_out(self, client, auth_headers, db_session, project, slot):
        from server.models import MediaItem, ProjectPlaylistItem

        _, media = attach_audio(client, auth_headers, db_session, project["id"], slot["id"], ["a.wav"])
        pl = self.create(client, auth_headers, project["id"])
        self.add(client, auth_headers, project["id"], pl["id"], [media[0].id])
        db_session.query(ProjectPlaylistItem).filter(
            ProjectPlaylistItem.media_item_id == media[0].id,
        ).delete(synchronize_session=False)
        db_session.query(MediaItem).filter(MediaItem.id == media[0].id).delete()
        db_session.commit()
        resp = client.get(f"/api/projects/{project['id']}/playlists", headers=auth_headers)
        assert resp.json()["playlists"][0]["tracks"] == []

    def test_rename(self, client, auth_headers, project):
        pl = self.create(client, auth_headers, project["id"], "draft")
        resp = client.patch(
            f"/api/projects/{project['id']}/playlists/{pl['id']}",
            json={"name": "for Tube"}, headers=auth_headers,
        )
        assert resp.json()["name"] == "for Tube"

    def test_rename_rejects_blank(self, client, auth_headers, project):
        pl = self.create(client, auth_headers, project["id"])
        resp = client.patch(
            f"/api/projects/{project['id']}/playlists/{pl['id']}",
            json={"name": "   "}, headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_delete(self, client, auth_headers, project):
        pl = self.create(client, auth_headers, project["id"])
        assert client.delete(
            f"/api/projects/{project['id']}/playlists/{pl['id']}", headers=auth_headers,
        ).status_code == 204
        resp = client.get(f"/api/projects/{project['id']}/playlists", headers=auth_headers)
        assert resp.json()["playlists"] == []

    def test_foreign_playlist_404s(self, client, auth_headers, project):
        other = client.post(
            "/api/projects", json={"name": "Other"}, headers=auth_headers,
        ).json()
        pl = self.create(client, auth_headers, other["id"])
        resp = client.patch(
            f"/api/projects/{project['id']}/playlists/{pl['id']}",
            json={"name": "x"}, headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_rejects_member(self, client, member_auth_headers, project):
        resp = client.get(f"/api/projects/{project['id']}/playlists", headers=member_auth_headers)
        assert resp.status_code == 403


class TestSlotSlideshow:
    def test_seeded_from_file_order(self, client, auth_headers, db_session, project, slot):
        _, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png"],
        )
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [s["media_item_id"] for s in sh["slides"]] == [m.id for m in media]

    def test_images_and_video_only(self, client, auth_headers, db_session, project, slot):
        image = make_image(db_session, "a.png")
        video = make_video(db_session, "b.mp4")
        audio = make_audio(db_session, "c.wav")
        for m in (image, video, audio):
            attach_item(client, auth_headers, project["id"], m.id, slot["id"])
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [s["media_item_id"] for s in sh["slides"]] == [image.id, video.id]

    def test_slide_carries_dimensions(self, client, auth_headers, db_session, project, slot):
        m = make_image(db_session, "a.png", width=700, height=1200)
        attach_item(client, auth_headers, project["id"], m.id, slot["id"])
        slide = get_slideshow(client, auth_headers, project["id"], slot["id"])["slides"][0]
        assert (slide["width"], slide["height"]) == (700, 1200)

    def test_video_dimensions_come_from_video_meta(self, client, auth_headers, db_session, project, slot):
        m = make_video(db_session, "a.mp4", width=640, height=480)
        attach_item(client, auth_headers, project["id"], m.id, slot["id"])
        slide = get_slideshow(client, auth_headers, project["id"], slot["id"])["slides"][0]
        assert (slide["width"], slide["height"]) == (640, 480)

    def test_order_persists(self, client, auth_headers, db_session, project, slot):
        _, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png", "c.png"],
        )
        order = [media[2].id, media[0].id, media[1].id]
        assert put_slideshow(client, auth_headers, project["id"], slot["id"], order).status_code == 200
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [s["media_item_id"] for s in sh["slides"]] == order

    def test_partial_order_appends_the_rest(self, client, auth_headers, db_session, project, slot):
        _, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png", "c.png"],
        )
        put_slideshow(client, auth_headers, project["id"], slot["id"], [media[2].id])
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [s["media_item_id"] for s in sh["slides"]] == [media[2].id, media[0].id, media[1].id]

    def test_new_upload_appends_after_arranging(self, client, auth_headers, db_session, project, slot):
        _, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png"],
        )
        put_slideshow(client, auth_headers, project["id"], slot["id"], [media[1].id, media[0].id])
        late = make_image(db_session, "late.png")
        attach_item(client, auth_headers, project["id"], late.id, slot["id"])
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [s["media_item_id"] for s in sh["slides"]] == [media[1].id, media[0].id, late.id]

    def test_detached_slide_drops_out_but_keeps_its_place(
        self, client, auth_headers, db_session, project, slot
    ):
        items, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png", "c.png"],
        )
        put_slideshow(
            client, auth_headers, project["id"], slot["id"],
            [media[2].id, media[1].id, media[0].id],
        )
        # Detach the middle one — it must vanish from the read...
        assert client.delete(
            f"/api/projects/{project['id']}/items/{items[1]['id']}", headers=auth_headers,
        ).status_code in (200, 204)
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [s["media_item_id"] for s in sh["slides"]] == [media[2].id, media[0].id]
        # ...and come back to the SAME place when reattached, because the
        # stored id was ignored rather than deleted.
        attach_item(client, auth_headers, project["id"], media[1].id, slot["id"])
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [s["media_item_id"] for s in sh["slides"]] == [media[2].id, media[1].id, media[0].id]

    def test_rejects_duplicates(self, client, auth_headers, db_session, project, slot):
        _, media = attach_images(client, auth_headers, db_session, project["id"], slot["id"], ["a.png"])
        assert put_slideshow(
            client, auth_headers, project["id"], slot["id"], [media[0].id, media[0].id],
        ).status_code == 400

    def test_rejects_non_member(self, client, auth_headers, db_session, project, slot):
        attach_images(client, auth_headers, db_session, project["id"], slot["id"], ["a.png"])
        stranger = make_image(db_session, "stranger.png")
        assert put_slideshow(
            client, auth_headers, project["id"], slot["id"], [stranger.id],
        ).status_code == 400

    def test_rejects_audio_in_the_slot(self, client, auth_headers, db_session, project, slot):
        audio = make_audio(db_session, "a.wav")
        attach_item(client, auth_headers, project["id"], audio.id, slot["id"])
        assert put_slideshow(
            client, auth_headers, project["id"], slot["id"], [audio.id],
        ).status_code == 400

    def test_rejects_member(self, client, auth_headers, member_auth_headers, db_session, project, slot):
        attach_images(client, auth_headers, db_session, project["id"], slot["id"], ["a.png"])
        assert client.get(
            f"/api/projects/{project['id']}/slots/{slot['id']}/slideshow",
            headers=member_auth_headers,
        ).status_code == 403


class TestThreeIndependentOrders:
    """File order, slot playlist and slot slideshow never drag each other."""

    def test_file_reorder_leaves_an_untouched_slideshow_alone(
        self, client, auth_headers, db_session, project, slot
    ):
        items, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png", "c.png"],
        )
        before = [s["media_item_id"] for s in
                  get_slideshow(client, auth_headers, project["id"], slot["id"])["slides"]]
        reorder_items(
            client, auth_headers, project["id"], slot["id"],
            [items[2]["id"], items[1]["id"], items[0]["id"]],
        )
        after = [s["media_item_id"] for s in
                 get_slideshow(client, auth_headers, project["id"], slot["id"])["slides"]]
        assert after == before

    def test_pinned_slideshow_still_appends_new_images(
        self, client, auth_headers, db_session, project, slot
    ):
        items, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png"],
        )
        reorder_items(client, auth_headers, project["id"], slot["id"], [items[1]["id"], items[0]["id"]])
        late = make_image(db_session, "late.png")
        attach_item(client, auth_headers, project["id"], late.id, slot["id"])
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [s["media_item_id"] for s in sh["slides"]] == [media[0].id, media[1].id, late.id]

    def test_slideshow_reorder_leaves_the_file_order_alone(
        self, client, auth_headers, db_session, project, slot
    ):
        items, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png", "c.png"],
        )
        put_slideshow(
            client, auth_headers, project["id"], slot["id"],
            [media[2].id, media[0].id, media[1].id],
        )
        listed = list_items(client, auth_headers, project["id"], slot["id"])
        assert [i["id"] for i in listed] == [i["id"] for i in items]

    def test_slideshow_reorder_leaves_the_playlist_alone(
        self, client, auth_headers, db_session, project, slot
    ):
        audio = [make_audio(db_session, n) for n in ("x.wav", "y.wav")]
        for m in audio:
            attach_item(client, auth_headers, project["id"], m.id, slot["id"])
        _, images = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png"],
        )
        put_slideshow(client, auth_headers, project["id"], slot["id"], [images[1].id, images[0].id])
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [m.id for m in audio]

    def test_file_reorder_pins_both_orders_at_once(
        self, client, auth_headers, db_session, project, slot
    ):
        audio = [make_audio(db_session, n) for n in ("x.wav", "y.wav")]
        audio_items = [attach_item(client, auth_headers, project["id"], m.id, slot["id"]) for m in audio]
        img_items, images = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png"],
        )
        every = audio_items + img_items
        reorder_items(
            client, auth_headers, project["id"], slot["id"], [i["id"] for i in reversed(every)],
        )
        pl = get_playlist(client, auth_headers, project["id"], slot["id"])
        sh = get_slideshow(client, auth_headers, project["id"], slot["id"])
        assert [t["media_item_id"] for t in pl["tracks"]] == [m.id for m in audio]
        assert [s["media_item_id"] for s in sh["slides"]] == [m.id for m in images]


class TestLatentSlideshows:
    def test_create_and_list(self, client, auth_headers, project):
        created = create_slideshow(client, auth_headers, project["id"], "Zine flip-through")
        assert created["name"] == "Zine flip-through"
        assert created["slides"] == []
        listed = client.get(
            f"/api/projects/{project['id']}/slideshows", headers=auth_headers,
        ).json()["slideshows"]
        assert [s["id"] for s in listed] == [created["id"]]

    def test_rename(self, client, auth_headers, project):
        sh = create_slideshow(client, auth_headers, project["id"])
        resp = client.patch(
            f"/api/projects/{project['id']}/slideshows/{sh['id']}",
            json={"name": "Renamed"}, headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_rename_rejects_blank(self, client, auth_headers, project):
        sh = create_slideshow(client, auth_headers, project["id"])
        assert client.patch(
            f"/api/projects/{project['id']}/slideshows/{sh['id']}",
            json={"name": "   "}, headers=auth_headers,
        ).status_code == 400

    def test_delete(self, client, auth_headers, project):
        sh = create_slideshow(client, auth_headers, project["id"])
        assert client.delete(
            f"/api/projects/{project['id']}/slideshows/{sh['id']}", headers=auth_headers,
        ).status_code == 204
        listed = client.get(
            f"/api/projects/{project['id']}/slideshows", headers=auth_headers,
        ).json()["slideshows"]
        assert listed == []

    def test_add_slides_in_order(self, client, auth_headers, db_session, project, slot):
        _, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png"],
        )
        sh = create_slideshow(client, auth_headers, project["id"])
        resp = add_slides(client, auth_headers, project["id"], sh["id"], [media[1].id, media[0].id])
        assert resp.status_code == 200
        assert [s["media_item_id"] for s in resp.json()["slides"]] == [media[1].id, media[0].id]

    def test_add_is_forgiving(self, client, auth_headers, db_session, project, slot):
        """Non-members, audio and duplicates are skipped, not 400s."""
        _, media = attach_images(client, auth_headers, db_session, project["id"], slot["id"], ["a.png"])
        audio = make_audio(db_session, "x.wav")
        attach_item(client, auth_headers, project["id"], audio.id, slot["id"])
        stranger = make_image(db_session, "stranger.png")
        sh = create_slideshow(client, auth_headers, project["id"])
        resp = add_slides(
            client, auth_headers, project["id"], sh["id"],
            [media[0].id, audio.id, stranger.id, media[0].id],
        )
        assert resp.status_code == 200
        assert [s["media_item_id"] for s in resp.json()["slides"]] == [media[0].id]

    def test_remove_slide(self, client, auth_headers, db_session, project, slot):
        _, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png"],
        )
        sh = create_slideshow(client, auth_headers, project["id"])
        added = add_slides(
            client, auth_headers, project["id"], sh["id"], [m.id for m in media],
        ).json()
        row = added["slides"][0]["slideshow_item_id"]
        resp = client.delete(
            f"/api/projects/{project['id']}/slideshows/{sh['id']}/items/{row}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert [s["media_item_id"] for s in resp.json()["slides"]] == [media[1].id]

    def test_reorder(self, client, auth_headers, db_session, project, slot):
        _, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png", "c.png"],
        )
        sh = create_slideshow(client, auth_headers, project["id"])
        added = add_slides(client, auth_headers, project["id"], sh["id"], [m.id for m in media]).json()
        rows = [s["slideshow_item_id"] for s in added["slides"]]
        resp = client.post(
            f"/api/projects/{project['id']}/slideshows/{sh['id']}/items/reorder",
            json={"order": [rows[2], rows[0], rows[1]]}, headers=auth_headers,
        )
        assert resp.status_code == 200
        assert [s["media_item_id"] for s in resp.json()["slides"]] == [
            media[2].id, media[0].id, media[1].id,
        ]

    def test_partial_reorder_keeps_the_rest_at_the_end(
        self, client, auth_headers, db_session, project, slot
    ):
        _, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png", "c.png"],
        )
        sh = create_slideshow(client, auth_headers, project["id"])
        added = add_slides(client, auth_headers, project["id"], sh["id"], [m.id for m in media]).json()
        rows = [s["slideshow_item_id"] for s in added["slides"]]
        resp = client.post(
            f"/api/projects/{project['id']}/slideshows/{sh['id']}/items/reorder",
            json={"order": [rows[2]]}, headers=auth_headers,
        )
        assert [s["media_item_id"] for s in resp.json()["slides"]] == [
            media[2].id, media[0].id, media[1].id,
        ]

    def test_departed_slide_is_filtered_not_deleted(
        self, client, auth_headers, db_session, project, slot
    ):
        items, media = attach_images(
            client, auth_headers, db_session, project["id"], slot["id"], ["a.png", "b.png"],
        )
        sh = create_slideshow(client, auth_headers, project["id"])
        add_slides(client, auth_headers, project["id"], sh["id"], [m.id for m in media])
        client.delete(f"/api/projects/{project['id']}/items/{items[0]['id']}", headers=auth_headers)
        listed = client.get(
            f"/api/projects/{project['id']}/slideshows", headers=auth_headers,
        ).json()["slideshows"][0]
        assert [s["media_item_id"] for s in listed["slides"]] == [media[1].id]
        # Reattaching restores it in place.
        attach_item(client, auth_headers, project["id"], media[0].id, slot["id"])
        listed = client.get(
            f"/api/projects/{project['id']}/slideshows", headers=auth_headers,
        ).json()["slideshows"][0]
        assert [s["media_item_id"] for s in listed["slides"]] == [media[0].id, media[1].id]

    def test_404_on_foreign_slideshow(self, client, auth_headers, project):
        other = client.post(
            "/api/projects", json={"name": "Other"}, headers=auth_headers,
        ).json()
        sh = create_slideshow(client, auth_headers, other["id"])
        assert client.patch(
            f"/api/projects/{project['id']}/slideshows/{sh['id']}",
            json={"name": "x"}, headers=auth_headers,
        ).status_code == 404

    def test_rejects_member(self, client, auth_headers, member_auth_headers, project):
        assert client.get(
            f"/api/projects/{project['id']}/slideshows", headers=member_auth_headers,
        ).status_code == 403
        assert client.post(
            f"/api/projects/{project['id']}/slideshows",
            json={"name": "x"}, headers=member_auth_headers,
        ).status_code == 403


class TestSlideshowSectionStyle:
    def test_slideshow_is_a_valid_section_key(self, client, auth_headers, project):
        resp = patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"slideshow": {"accent": "#aabbcc"}}},
        )
        assert resp.status_code == 200
        assert resp.json()["section_styles"]["slideshow"]["accent"] == "#aabbcc"

    def test_unknown_section_still_rejected(self, client, auth_headers, project):
        assert patch_project(
            client, auth_headers, project["id"],
            {"section_styles": {"slideshows": {"accent": "#aabbcc"}}},
        ).status_code == 400


class TestPlaylistsSectionStyle:
    def test_playlists_is_a_styleable_section(self, client, auth_headers, project):
        resp = patch_project(
            client, auth_headers, project["id"], {"section_styles": {"playlists": {"accent": "#ff0000"}}},
        )
        assert resp.status_code == 200
        assert resp.json()["section_styles"]["playlists"]["accent"] == "#ff0000"


# ---------------------------------------------------------------------------
# Manual index order (docs/plans/2026-07-30-latents-manual-order.md)
# ---------------------------------------------------------------------------


def order_ids(client, auth_headers, **params):
    """The index grid's order, as the page would render it."""
    resp = client.get("/api/projects", params=params, headers=auth_headers)
    assert resp.status_code == 200
    return [p["id"] for p in resp.json()["projects"]]


def reorder(client, auth_headers, moved_id, prev_id=None, next_id=None):
    return client.post(
        "/api/projects/reorder",
        json={"moved_id": moved_id, "prev_id": prev_id, "next_id": next_id},
        headers=auth_headers,
    )


@pytest.fixture
def grid(client, auth_headers):
    """Four latents created A→B→C→D.

    Creates prepend, so the grid reads newest-created-first: D, C, B, A.
    Returned keyed by name so the tests can talk about cards, not indices.
    """
    made = {}
    for name in ("A", "B", "C", "D"):
        resp = client.post("/api/projects", json={"name": name}, headers=auth_headers)
        assert resp.status_code == 201
        made[name] = resp.json()["id"]
    return made


def set_status(client, auth_headers, project_id, status):
    resp = patch_project(client, auth_headers, project_id, {"status": status})
    assert resp.status_code == 200


class TestLatentOrderSeed:
    """The default the admins asked to keep: newest created first."""

    def test_new_latents_appear_first(self, client, auth_headers, grid):
        assert order_ids(client, auth_headers) == [grid["D"], grid["C"], grid["B"], grid["A"]]

    def test_position_is_exposed_in_the_summary(self, client, auth_headers, grid):
        resp = client.get("/api/projects", headers=auth_headers)
        positions = [p["position"] for p in resp.json()["projects"]]
        assert positions == sorted(positions), "grid must be sorted by position ascending"

    def test_a_latent_created_later_still_lands_on_top(self, client, auth_headers, grid):
        resp = client.post("/api/projects", json={"name": "E"}, headers=auth_headers)
        assert order_ids(client, auth_headers)[0] == resp.json()["id"]

    def test_backfill_seeds_from_created_at_desc(self, client, auth_headers, db_session, test_user):
        """A pre-existing table (every row position 0) ranks by created_at DESC."""
        from datetime import datetime, timedelta

        from server.latents_api import backfill_project_positions
        from server.models import Project

        base = datetime(2026, 1, 1)
        made = []
        for n, offset in (("old", 0), ("mid", 10), ("new", 20)):
            p = Project(
                slug=f"seed-{n}", name=n, kind="other", status="forming",
                position=0, created_by=test_user.id,
                created_at=base + timedelta(days=offset),
                updated_at=base,
            )
            db_session.add(p)
            made.append(p)
        db_session.commit()

        backfill_project_positions(db_session.connection())
        db_session.commit()

        ordered = order_ids(client, auth_headers)
        by_name = {p.name: p.id for p in made}
        assert ordered == [by_name["new"], by_name["mid"], by_name["old"]]

    def test_backfill_is_idempotent(self, client, auth_headers, db_session, test_user):
        from datetime import datetime, timedelta

        from server.latents_api import backfill_project_positions
        from server.models import Project

        base = datetime(2026, 1, 1)
        for n, offset in (("x", 0), ("y", 5), ("z", 9)):
            db_session.add(Project(
                slug=f"idem-{n}", name=n, kind="other", status="forming",
                position=0, created_by=test_user.id,
                created_at=base + timedelta(days=offset), updated_at=base,
            ))
        db_session.commit()

        backfill_project_positions(db_session.connection())
        db_session.commit()
        once = order_ids(client, auth_headers)
        backfill_project_positions(db_session.connection())
        db_session.commit()
        assert order_ids(client, auth_headers) == once


class TestLatentReorder:
    """Unfiltered drags — the plain case."""

    def test_move_first_to_last(self, client, auth_headers, grid):
        # [D C B A] -> drop D below A
        assert reorder(client, auth_headers, grid["D"], prev_id=grid["A"]).status_code == 200
        assert order_ids(client, auth_headers) == [grid["C"], grid["B"], grid["A"], grid["D"]]

    def test_move_last_to_first(self, client, auth_headers, grid):
        # [D C B A] -> drop A above D
        assert reorder(client, auth_headers, grid["A"], next_id=grid["D"]).status_code == 200
        assert order_ids(client, auth_headers) == [grid["A"], grid["D"], grid["C"], grid["B"]]

    def test_move_into_the_middle(self, client, auth_headers, grid):
        # [D C B A] -> drop A between C and B
        assert reorder(
            client, auth_headers, grid["A"], prev_id=grid["C"], next_id=grid["B"],
        ).status_code == 200
        assert order_ids(client, auth_headers) == [grid["D"], grid["C"], grid["A"], grid["B"]]

    def test_response_echoes_the_full_order(self, client, auth_headers, grid):
        resp = reorder(client, auth_headers, grid["D"], prev_id=grid["A"])
        assert resp.json()["order"] == order_ids(client, auth_headers)

    def test_positions_renormalise_to_dense_rank(self, client, auth_headers, grid):
        """Creates leave positions sparse and negative; a reorder tidies them."""
        reorder(client, auth_headers, grid["D"], prev_id=grid["A"])
        resp = client.get("/api/projects", headers=auth_headers)
        assert [p["position"] for p in resp.json()["projects"]] == [0, 1, 2, 3]

    def test_order_survives_a_second_move(self, client, auth_headers, grid):
        reorder(client, auth_headers, grid["D"], prev_id=grid["A"])          # [C B A D]
        reorder(client, auth_headers, grid["B"], next_id=grid["C"])          # [B C A D]
        assert order_ids(client, auth_headers) == [grid["B"], grid["C"], grid["A"], grid["D"]]

    def test_reorder_does_not_bump_updated_at(self, client, auth_headers, grid):
        """The regression that matters.

        Every index card renders "updated {date}". Project.updated_at carries
        onupdate=_utcnow, so writing positions through the ORM would re-stamp
        every touched latent to today — corrupting the signal on a dozen cards
        for one drag. Positions go through raw SQL precisely to avoid this.
        """
        before = {p["id"]: p["updated_at"] for p in
                  client.get("/api/projects", headers=auth_headers).json()["projects"]}
        assert reorder(client, auth_headers, grid["D"], prev_id=grid["A"]).status_code == 200
        after = {p["id"]: p["updated_at"] for p in
                 client.get("/api/projects", headers=auth_headers).json()["projects"]}
        assert after == before


class TestLatentReorderUnderFilter:
    """Dragging while a status/kind chip is active.

    The invariant under test in all three: hidden cards never change position
    relative to each other, so the result still makes sense once the filter is
    cleared. Grid is [D C B A] throughout.
    """

    def test_hidden_card_between_the_anchors_stays_put(self, client, auth_headers, grid):
        set_status(client, auth_headers, grid["C"], "fixing")   # hidden
        set_status(client, auth_headers, grid["A"], "fixing")   # hidden
        assert order_ids(client, auth_headers, status="forming") == [grid["D"], grid["B"]]

        # Drag D below B in the filtered view.
        assert reorder(client, auth_headers, grid["D"], prev_id=grid["B"]).status_code == 200

        # D landed directly after B. C stayed ahead of B; A stayed last.
        assert order_ids(client, auth_headers) == [grid["C"], grid["B"], grid["D"], grid["A"]]

    def test_dropped_at_top_of_filtered_view_lands_above_first_visible(self, client, auth_headers, grid):
        set_status(client, auth_headers, grid["D"], "fixing")   # hidden, above everything
        set_status(client, auth_headers, grid["C"], "fixing")   # hidden
        assert order_ids(client, auth_headers, status="forming") == [grid["B"], grid["A"]]

        # Drag A to the top of the filtered view.
        assert reorder(client, auth_headers, grid["A"], next_id=grid["B"]).status_code == 200

        # Directly above B — NOT at absolute top. The hidden pair is untouched.
        assert order_ids(client, auth_headers) == [grid["D"], grid["C"], grid["A"], grid["B"]]

    def test_dropped_at_bottom_of_filtered_view_lands_below_last_visible(self, client, auth_headers, grid):
        set_status(client, auth_headers, grid["B"], "fixing")   # hidden, below everything
        set_status(client, auth_headers, grid["A"], "fixing")   # hidden
        assert order_ids(client, auth_headers, status="forming") == [grid["D"], grid["C"]]

        # Drag D to the bottom of the filtered view.
        assert reorder(client, auth_headers, grid["D"], prev_id=grid["C"]).status_code == 200

        # Directly below C — not past the hidden tail.
        assert order_ids(client, auth_headers) == [grid["C"], grid["D"], grid["B"], grid["A"]]

    def test_filtered_view_reflects_the_new_order(self, client, auth_headers, grid):
        set_status(client, auth_headers, grid["C"], "fixing")
        reorder(client, auth_headers, grid["D"], prev_id=grid["B"])
        assert order_ids(client, auth_headers, status="forming") == [grid["B"], grid["D"], grid["A"]]


class TestLatentReorderRobustness:
    def test_unknown_moved_id_is_404(self, client, auth_headers, grid):
        assert reorder(client, auth_headers, "no-such-id", prev_id=grid["A"]).status_code == 404

    def test_prev_anchor_cannot_be_the_moved_card(self, client, auth_headers, grid):
        assert reorder(client, auth_headers, grid["D"], prev_id=grid["D"]).status_code == 400

    def test_next_anchor_cannot_be_the_moved_card(self, client, auth_headers, grid):
        assert reorder(client, auth_headers, grid["D"], next_id=grid["D"]).status_code == 400

    def test_stale_prev_anchor_falls_through_to_next(self, client, auth_headers, grid):
        """Another admin deleted the card you dropped under — not an error."""
        resp = reorder(client, auth_headers, grid["A"], prev_id="deleted-id", next_id=grid["D"])
        assert resp.status_code == 200
        assert order_ids(client, auth_headers) == [grid["A"], grid["D"], grid["C"], grid["B"]]

    def test_both_anchors_unknown_is_a_no_op(self, client, auth_headers, grid):
        before = order_ids(client, auth_headers)
        resp = reorder(client, auth_headers, grid["D"], prev_id="gone", next_id="also-gone")
        assert resp.status_code == 200
        assert order_ids(client, auth_headers) == before

    def test_no_anchors_at_all_is_a_no_op(self, client, auth_headers, grid):
        before = order_ids(client, auth_headers)
        assert reorder(client, auth_headers, grid["D"]).status_code == 200
        assert order_ids(client, auth_headers) == before


class TestLatentReorderAuth:
    def test_reorder_requires_auth(self, client, grid):
        assert client.post(
            "/api/projects/reorder", json={"moved_id": grid["A"]},
        ).status_code == 401

    def test_reorder_rejects_member(self, client, member_auth_headers, grid):
        assert client.post(
            "/api/projects/reorder", json={"moved_id": grid["A"]}, headers=member_auth_headers,
        ).status_code == 403


# ---------------------------------------------------------------------------
# Deleting a latent (docs/plans/2026-07-30-latent-delete.md)
# ---------------------------------------------------------------------------


@pytest.fixture
def furnished(client, auth_headers, db_session, test_user, project):
    """A latent carrying one of everything the cascade is supposed to reach,
    plus a thread, which it is supposed to leave alone."""
    from server.models import Thread

    slot = client.post(
        f"/api/projects/{project['id']}/slots", json={}, headers=auth_headers,
    ).json()
    media = make_media_item(db_session, media_type="audio", mime_type="audio/wav")
    item = attach_item(client, auth_headers, project["id"], media.id, slot_id=slot["id"])
    doc = client.post(
        f"/api/projects/{project['id']}/documents", json={"name": "Liner notes"},
        headers=auth_headers,
    ).json()
    playlist = client.post(
        f"/api/projects/{project['id']}/playlists", json={"name": "Running order"},
        headers=auth_headers,
    ).json()
    slideshow = client.post(
        f"/api/projects/{project['id']}/slideshows", json={"name": "Contact sheet"},
        headers=auth_headers,
    ).json()
    link = client.post(
        f"/api/projects/{project['id']}/links", json={"url": "https://example.com/x"},
        headers=auth_headers,
    ).json()

    thread = Thread(
        anchor_type="project", anchor_id=project["id"],
        lemmy_post_id=4242, lemmy_community_id=7,
        created_by=test_user.id,
    )
    db_session.add(thread)
    db_session.commit()

    return {
        "project": project, "slot": slot, "media": media, "item": item,
        "doc": doc, "playlist": playlist, "slideshow": slideshow,
        "link": link, "thread_id": thread.id,
    }


class TestLatentDelete:
    """David asked to delete two dead latents; Tube's condition was that it
    must not touch the files or the search index."""

    @pytest.fixture(autouse=True)
    def mock_slack(self, monkeypatch):
        calls = []

        def fake_notify(event_type, user, **payload):
            calls.append((event_type, payload))

        import server.slack_notifier

        monkeypatch.setattr(server.slack_notifier, "notify_immediate", fake_notify)
        return calls

    def delete(self, client, auth_headers, project_id):
        return client.delete(f"/api/projects/{project_id}", headers=auth_headers)

    def test_deletes_the_latent(self, client, auth_headers, project):
        assert self.delete(client, auth_headers, project["id"]).status_code == 204
        assert client.get(
            f"/api/projects/{project['id']}", headers=auth_headers,
        ).status_code == 404

    def test_media_survives(self, client, auth_headers, db_session, furnished):
        """THE test. `project_items` is a join row — deleting the latent
        detaches the file, it does not delete it, and the search index is
        never touched. This is Tube's condition on the whole feature."""
        from server.models import MediaItem, ProjectItem

        media_id = furnished["media"].id
        assert self.delete(client, auth_headers, furnished["project"]["id"]).status_code == 204
        db_session.expire_all()

        assert db_session.query(MediaItem).filter(MediaItem.id == media_id).first() is not None
        assert db_session.query(ProjectItem).filter(
            ProjectItem.media_item_id == media_id,
        ).count() == 0

    def test_structure_cascades_away(self, client, auth_headers, db_session, furnished):
        from server.models import (
            ProjectDocument, ProjectLink, ProjectPlaylist, ProjectSlideshow, ProjectSlot,
        )

        pid = furnished["project"]["id"]
        assert self.delete(client, auth_headers, pid).status_code == 204
        db_session.expire_all()

        for model in (ProjectSlot, ProjectDocument, ProjectPlaylist, ProjectSlideshow, ProjectLink):
            assert db_session.query(model).filter(model.project_id == pid).count() == 0, model.__name__

    def test_threads_survive(self, client, auth_headers, db_session, furnished):
        """Deliberate, not an oversight. Threads anchor by (anchor_type,
        anchor_id) with no foreign key, so they don't cascade — and the
        discussion itself lives on the fold as a Lemmy post. Deleting a
        workspace shouldn't reach into it. The dialog says so."""
        from server.models import Thread

        assert self.delete(client, auth_headers, furnished["project"]["id"]).status_code == 204
        db_session.expire_all()
        assert db_session.query(Thread).filter(
            Thread.id == furnished["thread_id"],
        ).first() is not None

    def test_siblings_keep_their_order_and_dates(self, client, auth_headers, grid):
        """`Project.updated_at` carries onupdate=_utcnow and every index card
        renders "updated {date}", so a delete that touched its neighbours
        would re-stamp the grid — the same hazard the manual order fights."""
        before = {
            p["id"]: (p["position"], p["updated_at"])
            for p in client.get("/api/projects", headers=auth_headers).json()["projects"]
        }
        assert self.delete(client, auth_headers, grid["C"]).status_code == 204

        after = {
            p["id"]: (p["position"], p["updated_at"])
            for p in client.get("/api/projects", headers=auth_headers).json()["projects"]
        }
        assert grid["C"] not in after
        assert after == {k: v for k, v in before.items() if k != grid["C"]}

    def test_unknown_id_is_404(self, client, auth_headers):
        assert self.delete(client, auth_headers, "no-such-latent").status_code == 404

    def test_requires_auth(self, client, project):
        assert client.delete(f"/api/projects/{project['id']}").status_code == 401

    def test_rejects_member(self, client, member_auth_headers, project):
        assert client.delete(
            f"/api/projects/{project['id']}", headers=member_auth_headers,
        ).status_code == 403

    def test_announces_in_slack(self, client, auth_headers, mock_slack, furnished):
        assert self.delete(client, auth_headers, furnished["project"]["id"]).status_code == 204
        # Filtered, not indexed: creating the fixture fires latent.created into
        # the same list, and which of the two lands first depends on fixture
        # ordering rather than on anything this test is about.
        deleted = [p for e, p in mock_slack if e == "latent.deleted"]
        assert len(deleted) == 1
        assert deleted[0]["name"] == "Test Latent"
        assert deleted[0]["item_count"] == 1
        # No project link in the message — that URL is a 404 now.
        assert "project_id" not in deleted[0]

    def test_no_announcement_when_nothing_was_deleted(self, client, auth_headers, mock_slack):
        assert self.delete(client, auth_headers, "no-such-latent").status_code == 404
        assert [e for e, _ in mock_slack if e == "latent.deleted"] == []


class TestLatentDeletedSlackMessage:
    def test_names_what_stayed(self):
        from server.slack_notifier import _format_latent_deleted

        text = _format_latent_deleted("brendan", {"name": "Curtis Contribs", "item_count": 14})["text"]
        assert "deleted the latent *Curtis Contribs*" in text
        assert "14 files stayed in Emulsion" in text
        assert "/admin/latents" in text
        assert "detail?id=" not in text, "the latent's own URL is a 404 now"

    def test_singular_file(self):
        from server.slack_notifier import _format_latent_deleted

        assert "1 file stayed" in _format_latent_deleted("b", {"name": "x", "item_count": 1})["text"]

    def test_empty_latent_says_nothing_about_files(self):
        from server.slack_notifier import _format_latent_deleted

        text = _format_latent_deleted("b", {"name": "x", "item_count": 0})["text"]
        assert "stayed" not in text


class TestShippedStatus:
    """A fifth status for latents back-filled from work that already came out.

    `status` is a free String column validated by a Python set, so the whole
    feature is that set plus a colour — these tests pin the set, not a schema.
    """

    @pytest.fixture(autouse=True)
    def mock_slack(self, monkeypatch):
        calls = []

        def fake_notify(event_type, user, **payload):
            calls.append((event_type, payload))

        import server.slack_notifier

        monkeypatch.setattr(server.slack_notifier, "notify_immediate", fake_notify)
        return calls

    def test_accepts_shipped(self, client, auth_headers, project):
        resp = patch_project(client, auth_headers, project["id"], {"status": "shipped"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "shipped"

    def test_it_survives_a_reload(self, client, auth_headers, project):
        set_status(client, auth_headers, project["id"], "shipped")
        resp = client.get(f"/api/projects/{project['id']}", headers=auth_headers)
        assert resp.json()["status"] == "shipped"

    def test_the_index_can_filter_on_it(self, client, auth_headers, grid):
        set_status(client, auth_headers, grid["B"], "shipped")
        resp = client.get("/api/projects?status=shipped", headers=auth_headers)
        assert [p["id"] for p in resp.json()["projects"]] == [grid["B"]]

    def test_the_set_did_not_become_a_pass_through(self, client, auth_headers, project):
        """Adding a value must not turn validation off — the guard that keeps
        a typo in the frontend's status array from writing a status nothing
        renders a colour for."""
        resp = patch_project(client, auth_headers, project["id"], {"status": "shippped"})
        assert resp.status_code == 400

    def test_every_status_the_ui_offers_is_accepted(self, client, auth_headers, project):
        """The buttons in detail.astro and the chips in index.astro are
        hardcoded lists. This is the one place both ends are asserted equal."""
        from server.latents_api import VALID_PROJECT_STATUSES

        assert VALID_PROJECT_STATUSES == {
            "forming",
            "developing",
            "fixing",
            "shipped",
            "abandoned",
        }
        for status in sorted(VALID_PROJECT_STATUSES):
            assert (
                patch_project(client, auth_headers, project["id"], {"status": status}).status_code
                == 200
            )

    def test_announces_itself_in_slack(self, client, auth_headers, project, mock_slack):
        """Shipping is news, not a `prior → next` diff — same treatment as
        abandoning, which is its opposite."""
        set_status(client, auth_headers, project["id"], "shipped")
        shipped = [p for e, p in mock_slack if e == "latent.shipped"]
        assert len(shipped) == 1
        assert shipped[0]["name"] == "Test Latent"
        assert shipped[0]["prior_status"] == "forming"
        assert not [e for e, _ in mock_slack if e == "latent.status_changed"]

    def test_other_statuses_still_use_the_generic_event(
        self, client, auth_headers, project, mock_slack
    ):
        set_status(client, auth_headers, project["id"], "fixing")
        assert [e for e, _ in mock_slack if e.startswith("latent.status")] == [
            "latent.status_changed"
        ]

    def test_abandoning_is_unchanged(self, client, auth_headers, project, mock_slack):
        set_status(client, auth_headers, project["id"], "abandoned")
        assert [e for e, _ in mock_slack if e == "latent.abandoned"]

    def test_shipping_twice_only_announces_once(self, client, auth_headers, project, mock_slack):
        set_status(client, auth_headers, project["id"], "shipped")
        set_status(client, auth_headers, project["id"], "shipped")
        assert len([e for e, _ in mock_slack if e == "latent.shipped"]) == 1


class TestShippedSlackMessage:
    def test_it_reads_as_an_announcement(self):
        from server.slack_notifier import _format_latent_shipped

        text = _format_latent_shipped("brendan", {"name": "Bachelor Sessions", "project_id": "p1"})[
            "text"
        ]
        assert "shipped" in text
        assert "Bachelor Sessions" in text
        # Unlike a deletion, the latent is still there — link to it.
        assert "p1" in text
