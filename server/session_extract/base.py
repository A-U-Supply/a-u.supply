"""Extractor seam for DAW session bundles.

An :class:`Extractor` knows how to recognise one tool's bundle layout
(``detect``) and walk it for user content (``harvest``). Harvested files are
returned as :class:`ExtractedFile` records; the orchestration in ``jobs.py``
registers them as first-class media items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# Audio file extensions harvested from bundles.
AUDIO_EXTS = {".wav", ".aif", ".aiff", ".mp3", ".flac", ".m4a", ".caf"}

# MIDI file extensions harvested from bundles (registered as items in PR 2).
MIDI_EXTS = {".mid", ".midi"}

# macOS metadata directory injected into zips by Finder — never harvested.
_JUNK_DIRS = {"__MACOSX"}


@dataclass
class ExtractedFile:
    """One user-content file found inside a bundle."""

    path: Path  # absolute path inside the (possibly temporary) unpacked bundle
    rel_path: str  # posix path relative to the bundle root
    kind: str  # 'audio' | 'midi'
    size_bytes: int


@dataclass
class Extraction:
    """Everything an extractor found in one bundle."""

    files: list[ExtractedFile] = field(default_factory=list)


class Extractor(Protocol):
    """Detect-and-harvest interface implemented per DAW."""

    tool: str  # matches MediaSessionMeta.tool, e.g. 'logic'

    def detect(self, bundle_dir: Path) -> bool:
        """Return True if this extractor recognises the unpacked bundle layout."""
        ...

    def harvest(self, bundle_dir: Path) -> Extraction:
        """Walk the unpacked bundle and return its user-content files."""
        ...


def is_junk_path(path: Path) -> bool:
    """True for hidden files/dirs and macOS zip metadata — never user content."""
    return any(part.startswith(".") or part in _JUNK_DIRS for part in path.parts)
