"""MIDI file parsing + preview synthesis for the `midi` media type.

- :func:`parse_midi_file` — tempo, time signature, track names, note count,
  duration, and marker meta-events (the cue source for annotations).
- :func:`render_midi_preview` — pukebox-style sine-synth WAV preview so MIDI
  items are playable in the player (see server/pukebox_synth.py).
- :func:`register_midi_item` — one-shot helper used at ingest: parse, create
  ``media_midi_meta``, render the preview beside the stored file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pretty_midi

from server.session_extract.cues import Cue

logger = logging.getLogger(__name__)

SAMPLE_RATE = 22050
MAX_PREVIEW_SECONDS = 120


def parse_midi_file(path: Path) -> dict:
    """Parse a MIDI file into a metadata dict.

    Returns keys: tempo, time_sig, track_names, note_count, duration_seconds,
    markers (list of Cue). Values are best-effort — missing data is None/[].
    """
    try:
        mid = pretty_midi.PrettyMIDI(str(path))
    except Exception as exc:
        logger.warning("MIDI parse failed for %s: %s", path, exc)
        return {
            "tempo": None,
            "time_sig": None,
            "track_names": [],
            "note_count": None,
            "duration_seconds": None,
            "markers": [],
        }

    tempo_changes = mid.get_tempo_changes()
    tempo = float(tempo_changes[1][0]) if len(tempo_changes[1]) else None

    time_sig = None
    if mid.time_signature_changes:
        ts = mid.time_signature_changes[0]
        time_sig = f"{ts.numerator}/{ts.denominator}"

    track_names = [inst.name for inst in mid.instruments if inst.name]
    note_count = sum(len(inst.notes) for inst in mid.instruments)
    duration = mid.get_end_time()

    markers: list[Cue] = []
    # pretty_midi drops marker meta-events, so read them at the byte level.
    markers = _read_marker_meta_events(path)
    if not markers and mid.lyrics:
        # Fall back to lyrics events as positional labels if no markers exist.
        markers = [Cue(position_seconds=lyric.time, label=lyric.text) for lyric in mid.lyrics]

    return {
        "tempo": tempo,
        "time_sig": time_sig,
        "track_names": track_names,
        "note_count": note_count,
        "duration_seconds": duration if duration > 0 else None,
        "markers": markers,
    }


def _read_marker_meta_events(path: Path) -> list[Cue]:
    """Extract MIDI marker meta-events (0xFF 0x06) with tick→second conversion."""
    try:
        import mido  # not a project dependency — use pretty_midi's mido if vendored
    except ImportError:
        mido = None

    # pretty_midi bundles mido as a hard dependency, so this import is safe in
    # practice; the guard keeps a clear failure mode if that ever changes.
    if mido is None:
        logger.info("mido unavailable — skipping marker meta-event extraction for %s", path)
        return []

    try:
        mf = mido.MidiFile(str(path))
    except Exception as exc:
        logger.warning("mido could not read %s: %s", path, exc)
        return []

    cues: list[Cue] = []
    for track in mf.tracks:
        abs_time = 0.0
        tempo = 500000  # default µs per quarter note
        for msg in track:
            abs_time += mido.tick2second(msg.time, mf.ticks_per_beat, tempo)
            if msg.type == "set_tempo":
                tempo = msg.tempo
            elif msg.type == "marker" and msg.text.strip():
                cues.append(Cue(position_seconds=abs_time, label=msg.text.strip()))
    cues.sort(key=lambda c: c.position_seconds)
    return cues


def render_midi_preview(midi_path: Path, output_path: Path) -> bool:
    """Render a sine-synth WAV preview of a single MIDI file."""
    try:
        mid = pretty_midi.PrettyMIDI(str(midi_path))
    except Exception as exc:
        logger.warning("MIDI preview: parse failed for %s: %s", midi_path, exc)
        return False

    try:
        if mid.get_end_time() <= 0:
            logger.info("MIDI preview: empty file %s", midi_path)
            return False
        audio = mid.synthesize(fs=SAMPLE_RATE)
        audio = np.nan_to_num(audio, nan=0.0)
        max_samples = int(MAX_PREVIEW_SECONDS * SAMPLE_RATE)
        audio = audio[:max_samples]
        peak = np.abs(audio).max()
        if peak > 0:
            audio = audio / peak * 0.9
        from scipy.io import wavfile

        wavfile.write(str(output_path), SAMPLE_RATE, (audio * 32767).astype(np.int16))
        return True
    except Exception as exc:
        logger.warning("MIDI preview: synthesis failed for %s: %s", midi_path, exc)
        return False


def register_midi_item(db, item, file_path: Path, search_media_dir: Path) -> dict:
    """Parse a MIDI file and create its MediaMidiMeta + synthesized preview.

    Called at ingest time (direct upload or bundle extraction). Never raises —
    a MIDI we can't parse still gets a meta row with null fields. Returns the
    parsed metadata dict (including ``markers``) so callers can import cues.
    """
    from server.models import MediaMidiMeta

    parsed = parse_midi_file(file_path)

    preview_rel = None
    preview_abs = file_path.with_suffix(file_path.suffix + ".preview.wav")
    if render_midi_preview(file_path, preview_abs):
        preview_rel = preview_abs.relative_to(search_media_dir).as_posix()

    meta = MediaMidiMeta(
        media_item_id=item.id,
        tempo=parsed["tempo"],
        time_sig=parsed["time_sig"],
        track_names=json.dumps(parsed["track_names"]) if parsed["track_names"] else None,
        note_count=parsed["note_count"],
        duration_seconds=parsed["duration_seconds"],
        preview_path=preview_rel,
    )
    db.add(meta)
    return parsed
