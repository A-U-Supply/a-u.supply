"""Unit tests for cue-point parsers (WAV / AIFF / MIDI) and cue importing."""

import struct

import pytest

from server.models import Annotation
from server.session_extract.cues import parse_aiff_cues, parse_cues, parse_wav_cues
from server.session_extract.jobs import _import_cues
from server.session_extract.midi import parse_midi_file
from tests.conftest import make_media_item


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _wav_with_cues(cues, rate=44100):
    """Build a minimal WAV with a `cue ` chunk and `LIST adtl` labels."""
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    cue_payload = struct.pack("<I", len(cues)) + b"".join(
        struct.pack("<II4sIII", i, 0, b"data", 0, 0, offset)
        for i, (offset, _label) in enumerate(cues, 1)
    )
    adtl_sub = b""
    for i, (_offset, label) in enumerate(cues, 1):
        payload = struct.pack("<I", i) + label.encode() + b"\x00"
        adtl_sub += b"labl" + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            adtl_sub += b"\x00"
    list_payload = b"adtl" + adtl_sub
    data_payload = b"\x00\x00" * 100

    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"cue " + struct.pack("<I", len(cue_payload)) + cue_payload
    body += b"LIST" + struct.pack("<I", len(list_payload)) + list_payload
    if len(list_payload) % 2:
        body += b"\x00"
    body += b"data" + struct.pack("<I", len(data_payload)) + data_payload
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def _ext80(n):
    """Encode an int as an 80-bit IEEE-754 extended float (AIFF sample rate)."""
    exp = n.bit_length() - 1
    return struct.pack(">H", exp + 16383) + (n << (63 - exp)).to_bytes(8, "big")


def _aiff_with_markers(markers, rate=44100):
    """Build a minimal AIFF with a COMM chunk and MARK markers."""
    comm = struct.pack(">hIh", 1, 100, 16) + _ext80(rate)
    mark_payload = struct.pack(">H", len(markers))
    for i, (position, name) in enumerate(markers, 1):
        name_bytes = name.encode()
        mark_payload += struct.pack(">hIB", i, position, len(name_bytes)) + name_bytes
        if len(name_bytes) % 2 == 0:
            mark_payload += b"\x00"  # pstring padded to even length
    body = b"COMM" + struct.pack(">I", len(comm)) + comm
    body += b"MARK" + struct.pack(">I", len(mark_payload)) + mark_payload
    if len(mark_payload) % 2:
        body += b"\x00"
    return b"FORM" + struct.pack(">I", len(body) + 4) + b"AIFF" + body


def _midi_with_markers(path):
    """Write a MIDI file with two marker meta-events via mido."""
    import mido

    mf = mido.MidiFile()
    track = mf.add_track("piano")
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))  # 120 bpm
    track.append(mido.MetaMessage("marker", text="Intro", time=0))
    track.append(mido.Message("note_on", note=60, velocity=64, time=0))
    track.append(mido.Message("note_off", note=60, velocity=64, time=480))
    track.append(mido.MetaMessage("marker", text="Verse", time=0))
    mf.save(str(path))
    return path


# ---------------------------------------------------------------------------
# WAV cues
# ---------------------------------------------------------------------------


class TestWavCues:
    def test_parses_positions_and_labels(self, tmp_path):
        path = tmp_path / "mix.wav"
        path.write_bytes(_wav_with_cues([(0, "Intro"), (22050, "Verse"), (88200, "Chorus")]))
        cues = parse_wav_cues(path)
        assert [(round(c.position_seconds, 2), c.label) for c in cues] == [
            (0.0, "Intro"),
            (0.5, "Verse"),
            (2.0, "Chorus"),
        ]

    def test_missing_label_gets_default(self, tmp_path):
        # cue chunk without a matching LIST adtl entry
        fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
        cue_payload = struct.pack("<I", 1) + struct.pack("<II4sIII", 7, 0, b"data", 0, 0, 44100)
        data_payload = b"\x00\x00" * 10
        body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
        body += b"cue " + struct.pack("<I", len(cue_payload)) + cue_payload
        body += b"data" + struct.pack("<I", len(data_payload)) + data_payload
        path = tmp_path / "plain.wav"
        path.write_bytes(b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body)

        cues = parse_wav_cues(path)
        assert len(cues) == 1
        assert cues[0].label == "WAV cue 7"
        assert cues[0].position_seconds == pytest.approx(1.0)

    def test_garbage_returns_empty(self, tmp_path):
        path = tmp_path / "junk.wav"
        path.write_bytes(b"not a riff file at all")
        assert parse_wav_cues(path) == []


class TestAiffCues:
    def test_parses_markers(self, tmp_path):
        path = tmp_path / "mix.aif"
        path.write_bytes(_aiff_with_markers([(0, "Start"), (44100, "Middle")]))
        cues = parse_aiff_cues(path)
        assert [(round(c.position_seconds, 2), c.label) for c in cues] == [
            (0.0, "Start"),
            (1.0, "Middle"),
        ]

    def test_not_aiff_returns_empty(self, tmp_path):
        path = tmp_path / "junk.aif"
        path.write_bytes(b"NOPE")
        assert parse_aiff_cues(path) == []


class TestParseCuesDispatch:
    def test_wav_dispatch(self, tmp_path):
        path = tmp_path / "x.wav"
        path.write_bytes(_wav_with_cues([(0, "A")]))
        cues, source = parse_cues(path)
        assert source == "wav_cue" and len(cues) == 1

    def test_aiff_dispatch(self, tmp_path):
        path = tmp_path / "x.aiff"
        path.write_bytes(_aiff_with_markers([(0, "A")]))
        cues, source = parse_cues(path)
        assert source == "aiff_cue" and len(cues) == 1

    def test_other_ext_returns_empty(self, tmp_path):
        path = tmp_path / "x.mp3"
        path.write_bytes(b"\xff\xfb")
        cues, _ = parse_cues(path)
        assert cues == []


# ---------------------------------------------------------------------------
# MIDI parsing
# ---------------------------------------------------------------------------


class TestMidiParsing:
    def test_metadata_and_markers(self, tmp_path):
        path = _midi_with_markers(tmp_path / "idea.mid")
        parsed = parse_midi_file(path)
        assert parsed["tempo"] == pytest.approx(120.0)
        assert parsed["note_count"] == 1
        assert parsed["duration_seconds"] == pytest.approx(0.5)
        assert parsed["time_sig"] is None or "/" in parsed["time_sig"]
        labels = [(round(c.position_seconds, 2), c.label) for c in parsed["markers"]]
        assert (0.0, "Intro") in labels
        assert (0.5, "Verse") in labels

    def test_garbage_midi_returns_nulls(self, tmp_path):
        path = tmp_path / "junk.mid"
        path.write_bytes(b"definitely not midi")
        parsed = parse_midi_file(path)
        assert parsed["tempo"] is None
        assert parsed["markers"] == []


# ---------------------------------------------------------------------------
# Cue importing (dedupe + touch protection)
# ---------------------------------------------------------------------------


class TestImportCues:
    def _item(self, db_session):
        return make_media_item(db_session, media_type="audio", mime_type="audio/wav")

    def test_import_and_dedupe(self, db_session):
        from server.session_extract.cues import Cue

        item = self._item(db_session)
        cues = [Cue(0.0, "Intro"), Cue(1.5, "Verse")]
        assert _import_cues(db_session, item.id, cues, "wav_cue") == 2
        # Second identical import adds nothing
        assert _import_cues(db_session, item.id, cues, "wav_cue") == 0
        # One new position imports; the existing one is skipped
        assert _import_cues(db_session, item.id, [Cue(0.0, "Intro"), Cue(3.0, "Chorus")], "wav_cue") == 1

        rows = db_session.query(Annotation).filter_by(media_item_id=item.id).all()
        assert len(rows) == 3
        assert all(a.kind == "cue" and a.source == "wav_cue" for a in rows)

    def test_touched_rows_are_never_modified(self, db_session):
        from server.session_extract.cues import Cue

        item = self._item(db_session)
        _import_cues(db_session, item.id, [Cue(0.0, "Intro")], "midi")
        row = db_session.query(Annotation).filter_by(media_item_id=item.id).one()
        row.label = "Actually the drop"
        row.touched_by_user = True
        db_session.commit()

        # Re-importing the original cue neither reverts the edit nor adds a dupe
        added = _import_cues(db_session, item.id, [Cue(0.0, "Intro")], "midi")
        assert added == 0
        db_session.refresh(row)
        assert row.label == "Actually the drop"
        assert row.touched_by_user is True
        assert db_session.query(Annotation).filter_by(media_item_id=item.id).count() == 1
