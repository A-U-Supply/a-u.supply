"""Logic Pro (.logicx) bundle extractor.

A .logicx is a macOS package directory. Layout varies slightly across Logic
versions, so harvesting is deliberately inclusive: every audio/MIDI file
anywhere in the package is collected (hidden files and Finder metadata
excluded). Typical locations:

- ``Media/Audio Files/*.aif|*.wav`` — recorded and bounced audio
- ``Alternatives/<nnn>/ProjectData`` — project state (proprietary binary;
  marker parsing lives in logic_markers.py, PR 2)
- ``*.mid`` anywhere — imported/exported MIDI the user keeps in the package
"""

from __future__ import annotations

import logging
from pathlib import Path

from server.session_extract.base import (
    AUDIO_EXTS,
    MIDI_EXTS,
    ExtractedFile,
    Extraction,
    is_junk_path,
)

logger = logging.getLogger(__name__)


class LogicExtractor:
    tool = "logic"

    def detect(self, bundle_dir: Path) -> bool:
        return bundle_dir.is_dir() and bundle_dir.name.lower().endswith(".logicx")

    def harvest(self, bundle_dir: Path) -> Extraction:
        files: list[ExtractedFile] = []
        for path in sorted(bundle_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(bundle_dir)
            if is_junk_path(rel):
                continue
            ext = path.suffix.lower()
            if ext in AUDIO_EXTS:
                kind = "audio"
            elif ext in MIDI_EXTS:
                kind = "midi"
            else:
                continue
            size = path.stat().st_size
            if size == 0:
                logger.info("Skipping zero-byte bundle file: %s", rel)
                continue
            files.append(
                ExtractedFile(
                    path=path,
                    rel_path=rel.as_posix(),
                    kind=kind,
                    size_bytes=size,
                )
            )
        return Extraction(files=files)
