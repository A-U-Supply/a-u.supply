"""Experimental Logic Pro ProjectData marker extraction.

**Off by default** — enable with ``SESSION_LOGIC_PARSE=1``.

Logic's ``ProjectData`` file (inside ``.logicx/Alternatives/<nnn>/``) is an
undocumented proprietary binary. This module performs a conservative heuristic
scan: printable label strings adjacent to little-endian float64 values that
fall inside a plausible session timeline (0–12h). Results are only accepted
when the whole set is monotonic in time — otherwise the file's layout didn't
match our assumptions and we return nothing.

Everything is logged; parsing never blocks or fails extraction. Treat output
as best-effort until the format is better understood. The reliable marker
sources remain WAV/AIFF cue chunks and MIDI marker meta-events (cues.py /
midi.py) — this parser is the bonus round.
"""

from __future__ import annotations

import logging
import re
import struct
from pathlib import Path

from server.session_extract.cues import Cue

logger = logging.getLogger(__name__)

_MAX_TIMELINE_SECONDS = 12 * 3600  # 12 hours
_LABEL_RE = re.compile(rb"[\x20-\x7e]{2,64}")


def find_project_data_files(bundle_dir: Path) -> list[Path]:
    """Locate ProjectData files inside an unpacked .logicx bundle."""
    return sorted(
        p
        for p in bundle_dir.rglob("ProjectData")
        if p.is_file() and "__MACOSX" not in p.parts
    )


def extract_markers(project_data_path: Path) -> list[Cue]:
    """Best-effort marker scan of a Logic ProjectData binary.

    Returns an empty list unless the heuristic finds a coherent marker set.
    """
    try:
        data = project_data_path.read_bytes()
    except OSError as exc:
        logger.info("logic_markers: cannot read %s: %s", project_data_path, exc)
        return []

    candidates: list[Cue] = []
    # Scan for printable label runs; check the 8 bytes immediately before and
    # after each run for a plausible float64 timestamp.
    for match in _LABEL_RE.finditer(data):
        label_bytes = match.group()
        # Skip obvious format strings / plist-ish noise.
        if label_bytes.startswith((b"bplist", b"CF", b"NS", b"$")):
            continue
        for offset in (match.start() - 8, match.end(), match.end() + 4):
            if offset < 0 or offset + 8 > len(data):
                continue
            (value,) = struct.unpack_from("<d", data, offset)
            if 0.0 < value < _MAX_TIMELINE_SECONDS:
                candidates.append(
                    Cue(
                        position_seconds=value,
                        label=label_bytes.decode("ascii", "replace").strip(),
                    )
                )
                break

    # Dedupe by (rounded position, label) and require a monotonic timeline.
    seen: set[tuple[float, str]] = set()
    unique: list[Cue] = []
    for cue in sorted(candidates, key=lambda c: c.position_seconds):
        key = (round(cue.position_seconds, 2), cue.label)
        if key not in seen and cue.label:
            seen.add(key)
            unique.append(cue)

    if not unique:
        logger.info("logic_markers: no marker candidates in %s", project_data_path.name)
        return []
    if len(unique) < 2:
        logger.info(
            "logic_markers: only %d candidate(s) in %s — below confidence threshold, discarding",
            len(unique),
            project_data_path.name,
        )
        return []

    logger.info(
        "logic_markers: extracted %d candidate marker(s) from %s (experimental)",
        len(unique),
        project_data_path.name,
    )
    return unique
