"""Tests for the Puke Box manifest endpoint and MIDI stem serving."""

import json

from server.models import MediaAudioMeta, MediaPukeBoxMeta, MediaSource
from tests.conftest import make_media_item


def _make_pukebox_entry(db_session, entry_id="2026-06-15-193727", **overrides):
    """Create a complete Puke Box entry (MediaItem + source + metas)."""
    item = make_media_item(
        db_session,
        output_index="puke-box",
        media_type="audio",
        filename=f"{entry_id}-preview.ogg",
        file_path=f"audio/2026-06/{entry_id}/preview.ogg",
        mime_type="audio/ogg",
        description="A test MIDI entry",
    )
    db_session.add(MediaSource(
        media_item_id=item.id,
        source_type="slack_file",
        source_channel="midieval",
        slack_message_ts="1718479000.000001",
    ))
    db_session.add(MediaAudioMeta(
        media_item_id=item.id,
        duration_seconds=28.5,
        sample_rate=22050,
        channels=1,
    ))
    meta_defaults = {
        "entry_id": entry_id,
        "scale": "Lydian",
        "root": "C#",
        "tempo": 120,
        "chords": json.dumps(["Cmaj7", "D7", "Em7", "F#m7b5"]),
        "description": "A shimmering lydian adventure",
        "melody_instrument": 0,
        "temperature": 1.0,
        "midi_paths": json.dumps({
            "melody": f"audio/2026-06/{entry_id}/melody.mid",
            "drums": f"audio/2026-06/{entry_id}/drums.mid",
            "bass": f"audio/2026-06/{entry_id}/bass.mid",
            "chords": f"audio/2026-06/{entry_id}/chords.mid",
        }),
    }
    meta_defaults.update(overrides)
    db_session.add(MediaPukeBoxMeta(media_item_id=item.id, **meta_defaults))
    db_session.commit()
    db_session.refresh(item)
    return item


class TestPukeboxManifest:
    """Tests for GET /api/pukebox/manifest."""

    def test_no_auth_required(self, client, db_session):
        _make_pukebox_entry(db_session)
        resp = client.get("/api/pukebox/manifest")
        assert resp.status_code == 200

    def test_returns_entries_with_musical_metadata(self, client, db_session):
        _make_pukebox_entry(db_session, entry_id="2026-06-15-193727")
        resp = client.get("/api/pukebox/manifest")
        data = resp.json()
        assert data["total"] == 1
        entry = data["entries"][0]
        assert entry["entry_id"] == "2026-06-15-193727"
        assert entry["date"] == "2026-06-15"
        assert entry["scale"] == "Lydian"
        assert entry["root"] == "C#"
        assert entry["tempo"] == 120
        assert entry["description"] == "A shimmering lydian adventure"
        assert entry["chords"] == ["Cmaj7", "D7", "Em7", "F#m7b5"]
        assert entry["melody_instrument"] == 0
        assert entry["temperature"] == 1.0

    def test_preview_url_points_to_public_outputs(self, client, db_session):
        item = _make_pukebox_entry(db_session)
        resp = client.get("/api/pukebox/manifest")
        entry = resp.json()["entries"][0]
        assert entry["preview_url"] == f"/api/public/outputs/{item.id}/file"

    def test_midi_urls_only_for_available_stems(self, client, db_session):
        _make_pukebox_entry(
            db_session,
            midi_paths=json.dumps({"melody": "audio/2026-06/x/melody.mid"}),
        )
        resp = client.get("/api/pukebox/manifest")
        entry = resp.json()["entries"][0]
        assert "melody" in entry["midi_urls"]
        assert "drums" not in entry["midi_urls"]
        assert "bass" not in entry["midi_urls"]
        assert "chords" not in entry["midi_urls"]

    def test_ordered_newest_first(self, client, db_session):
        _make_pukebox_entry(db_session, entry_id="2026-06-01-100000")
        _make_pukebox_entry(db_session, entry_id="2026-06-15-200000")
        _make_pukebox_entry(db_session, entry_id="2026-06-10-150000")
        resp = client.get("/api/pukebox/manifest")
        entries = resp.json()["entries"]
        assert len(entries) == 3
        assert entries[0]["entry_id"] == "2026-06-15-200000"
        assert entries[1]["entry_id"] == "2026-06-10-150000"
        assert entries[2]["entry_id"] == "2026-06-01-100000"

    def test_only_returns_puke_box_entries(self, client, db_session):
        _make_pukebox_entry(db_session, entry_id="2026-06-15-193727")
        make_media_item(db_session, output_index="samples-bored", media_type="audio")
        make_media_item(db_session, output_index=None, media_type="audio")
        resp = client.get("/api/pukebox/manifest")
        assert resp.json()["total"] == 1

    def test_cache_header_present(self, client, db_session):
        _make_pukebox_entry(db_session)
        resp = client.get("/api/pukebox/manifest")
        assert "Cache-Control" in resp.headers
        assert "max-age=60" in resp.headers["Cache-Control"]

    def test_empty_manifest_returns_zero(self, client, db_session):
        resp = client.get("/api/pukebox/manifest")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        assert resp.json()["entries"] == []


class TestPukeboxMidiStem:
    """Tests for GET /api/pukebox/midi/{media_id}/{stem}."""

    def test_invalid_stem_name_returns_400(self, client, db_session):
        item = _make_pukebox_entry(db_session)
        resp = client.get(f"/api/pukebox/midi/{item.id}/piano")
        assert resp.status_code == 400

    def test_404_for_non_pukebox_item(self, client, db_session):
        item = make_media_item(db_session, output_index="samples-bored", media_type="audio")
        resp = client.get(f"/api/pukebox/midi/{item.id}/melody")
        assert resp.status_code == 404

    def test_404_for_missing_stem(self, client, db_session):
        item = _make_pukebox_entry(
            db_session,
            midi_paths=json.dumps({"melody": "audio/2026-06/x/melody.mid"}),
        )
        resp = client.get(f"/api/pukebox/midi/{item.id}/drums")
        assert resp.status_code == 404

    def test_404_for_missing_file_on_disk(self, client, db_session):
        item = _make_pukebox_entry(db_session)
        resp = client.get(f"/api/pukebox/midi/{item.id}/melody")
        assert resp.status_code == 404  # file doesn't exist in test media dir


class TestPukeboxMessageParser:
    """Tests for server.pukebox_ingest.parse_midi_message."""

    def test_parses_valid_message(self):
        from server.pukebox_ingest import parse_midi_message

        text = (
            ":musical_note: *Daily MIDI* — Lydian in C# (120 BPM)\n"
            "_A shimmering lydian adventure_\n"
            ":musical_keyboard: Melody — ImprovRNN, Acoustic Grand Piano (MIDI 0), temperature 1.0\n"
            ":musical_score: Chords — Cmaj7 D7 Em7 F#m7b5\n"
        )
        result = parse_midi_message(text)
        assert result is not None
        assert result["scale"] == "Lydian"
        assert result["root"] == "C#"
        assert result["tempo"] == 120
        assert result["description"] == "A shimmering lydian adventure"
        assert result["chords"] == ["Cmaj7", "D7", "Em7", "F#m7b5"]
        assert result["melody_instrument"] == 0
        assert result["temperature"] == 1.0

    def test_returns_none_for_non_midi_message(self):
        from server.pukebox_ingest import parse_midi_message

        assert parse_midi_message("just a regular chat message") is None
        assert parse_midi_message("") is None

    def test_handles_flat_key(self):
        from server.pukebox_ingest import parse_midi_message

        text = "*Daily MIDI* — Blues in Bb (90 BPM)\n_A slow blues_\n"
        result = parse_midi_message(text)
        assert result is not None
        assert result["root"] == "Bb"
        assert result["tempo"] == 90
