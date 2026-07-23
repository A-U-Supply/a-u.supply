"""Cue-point parsers for audio container formats (pure Python, no deps).

Extracts embedded cue/markers from:

- **WAV** — the `cue ` chunk (positions in samples) with labels from the
  associated `LIST`/`adtl` chunk (`labl` sub-chunks).
- **AIFF/AIFC** — `MARK` chunks (positions in samples, name inline).

Both return positions in **seconds** (sample offset ÷ sample rate), which is
what annotations store. Parsers are defensive: truncated or malformed chunks
abort parsing and return whatever was collected so far.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Cue:
    position_seconds: float
    label: str


def parse_cues(path: Path) -> tuple[list[Cue], str]:
    """Parse cues from a WAV or AIFF file. Returns (cues, source_tag)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".wav":
            return parse_wav_cues(path), "wav_cue"
        if suffix in (".aif", ".aiff", ".aifc"):
            return parse_aiff_cues(path), "aiff_cue"
    except Exception:
        logger.exception("Cue parsing failed for %s", path)
    return [], "wav_cue" if suffix == ".wav" else "aiff_cue"


# ---------------------------------------------------------------------------
# WAV (RIFF, little-endian)
# ---------------------------------------------------------------------------


def _read_riff_chunks(data: bytes):
    """Yield (chunk_id, payload) for each top-level RIFF chunk."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        payload = data[pos + 8 : pos + 8 + size]
        yield chunk_id, payload
        pos += 8 + size + (size & 1)  # chunks are word-aligned


def parse_wav_cues(path: Path) -> list[Cue]:
    data = path.read_bytes()
    sample_rate = 44100
    cue_points: dict[int, int] = {}  # cue id -> sample offset
    labels: dict[int, str] = {}

    for chunk_id, payload in _read_riff_chunks(data) or []:
        if chunk_id == b"fmt " and len(payload) >= 4:
            sample_rate = struct.unpack_from("<I", payload, 4)[0] or 44100
        elif chunk_id == b"cue " and len(payload) >= 4:
            count = struct.unpack_from("<I", payload, 0)[0]
            for i in range(count):
                off = 4 + i * 24
                if off + 24 > len(payload):
                    break
                cue_id, _pos, _fcc, _chunk_start, _block_start, sample_offset = struct.unpack_from(
                    "<IIIIII", payload, off
                )
                cue_points[cue_id] = sample_offset
        elif chunk_id == b"LIST" and len(payload) >= 4 and payload[:4] == b"adtl":
            pos = 4
            while pos + 8 <= len(payload):
                sub_id = payload[pos : pos + 4]
                size = struct.unpack_from("<I", payload, pos + 4)[0]
                sub = payload[pos + 8 : pos + 8 + size]
                if sub_id in (b"labl", b"ltxt") and len(sub) >= 4:
                    cue_id = struct.unpack_from("<I", sub, 0)[0]
                    labels[cue_id] = sub[4:].split(b"\x00")[0].decode("utf-8", "replace").strip()
                pos += 8 + size + (size & 1)

    cues = []
    for cue_id, offset in sorted(cue_points.items(), key=lambda kv: kv[1]):
        label = labels.get(cue_id) or f"WAV cue {cue_id}"
        cues.append(Cue(position_seconds=offset / sample_rate, label=label))
    return cues


# ---------------------------------------------------------------------------
# AIFF (FORM, big-endian)
# ---------------------------------------------------------------------------


def parse_aiff_cues(path: Path) -> list[Cue]:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"FORM" or data[8:12] not in (b"AIFF", b"AIFC"):
        return []

    sample_rate = 44100
    markers: list[tuple[int, str]] = []  # (sample offset, name)

    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        size = struct.unpack_from(">I", data, pos + 4)[0]
        payload = data[pos + 8 : pos + 8 + size]
        if chunk_id == b"COMM" and len(payload) >= 18:
            sample_rate = _extended_to_int(payload[8:18]) or 44100
        elif chunk_id == b"MARK" and len(payload) >= 2:
            count = struct.unpack_from(">H", payload, 0)[0]
            mpos = 2
            for _ in range(count):
                if mpos + 7 > len(payload):
                    break
                _marker_id, position, name_len = struct.unpack_from(">hIB", payload, mpos)
                mpos += 7
                name = payload[mpos : mpos + name_len].decode("utf-8", "replace").strip()
                mpos += name_len + (name_len & 1 ^ 1)  # pstring padded to even
                markers.append((position, name))
        pos += 8 + size + (size & 1)

    return [
        Cue(position_seconds=offset / sample_rate, label=name or "AIFF marker")
        for offset, name in sorted(markers)
    ]


def _extended_to_int(raw: bytes) -> int:
    """Decode an 80-bit IEEE-754 extended float (AIFF sample rate) to int."""
    if len(raw) != 10:
        return 0
    sign = -1 if raw[0] & 0x80 else 1
    exponent = ((raw[0] & 0x7F) << 8 | raw[1]) - 16383
    mantissa = int.from_bytes(raw[2:10], "big")
    if exponent < 0 or exponent > 63:
        return 0
    value = sign * mantissa / (1 << (63 - exponent))
    return int(round(value))
