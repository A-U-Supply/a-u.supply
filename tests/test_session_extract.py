"""Unit tests for the session bundle extractor seam + orchestration jobs."""

import io
import wave
import zipfile
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from server.bundles_api import _sanitize_rel_path
from server.models import (
    MediaAudioMeta,
    MediaItem,
    MediaSessionMeta,
    MediaSource,
    Project,
    ProjectItem,
    ProjectSlot,
)
from server.session_extract import jobs
from server.session_extract.jobs import _find_bundle_root
from server.session_extract.logic import LogicExtractor
from tests.conftest import make_media_item


def _wav_bytes(seconds: float = 0.1, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _make_bundle_dir(root: Path, name: str = "Heliotrope.logicx") -> Path:
    """Create a synthetic .logicx bundle tree on disk."""
    bundle = root / name
    (bundle / "Media" / "Audio Files").mkdir(parents=True)
    (bundle / "Alternatives" / "000").mkdir(parents=True)
    (bundle / "Media" / "Audio Files" / "kick.wav").write_bytes(_wav_bytes())
    (bundle / "Media" / "Audio Files" / "bass.aif").write_bytes(_wav_bytes(0.2))
    (bundle / "Alternatives" / "000" / "ProjectData").write_bytes(b"\x00" * 64)
    return bundle


@pytest.fixture
def patched_jobs(monkeypatch, db_engine, tmp_media_dir):
    """Run session extraction synchronously against the in-memory test DB."""
    monkeypatch.setenv("SEARCH_MEDIA_DIR", tmp_media_dir)
    TestSession = sessionmaker(bind=db_engine)
    monkeypatch.setattr(jobs, "SessionLocal", TestSession)

    def stub_run_extraction(media_item_id, file_path, media_type):
        db = TestSession()
        try:
            if media_type == "audio":
                db.add(
                    MediaAudioMeta(
                        media_item_id=media_item_id,
                        duration_seconds=1.5,
                        sample_rate=44100,
                        channels=2,
                        bit_depth=16,
                    )
                )
                db.commit()
        finally:
            db.close()

    monkeypatch.setattr(jobs, "run_extraction", stub_run_extraction)
    monkeypatch.setattr("server.extraction._sync_to_search", lambda db, item: None)
    return TestSession


# ---------------------------------------------------------------------------
# Extractor units
# ---------------------------------------------------------------------------


class TestLogicExtractor:
    def test_detect(self, tmp_path):
        ex = LogicExtractor()
        logicx = tmp_path / "Song.logicx"
        logicx.mkdir()
        als = tmp_path / "Song.als"
        als.mkdir()
        assert ex.detect(logicx)
        assert not ex.detect(als)
        assert not ex.detect(tmp_path / "missing.logicx")

    def test_harvest_collects_audio_and_midi_only(self, tmp_path):
        bundle = _make_bundle_dir(tmp_path)
        (bundle / "notes.mid").write_bytes(b"MThd" + b"\x00" * 20)
        (bundle / "Media" / "Audio Files" / ".hidden.wav").write_bytes(_wav_bytes())
        (bundle / "Media" / "Audio Files" / "empty.wav").write_bytes(b"")
        (bundle / "README.txt").write_text("hello")

        result = ex_files = LogicExtractor().harvest(bundle)
        by_name = {f.path.name: f for f in result.files}

        assert set(by_name) == {"kick.wav", "bass.aif", "notes.mid"}
        assert by_name["kick.wav"].kind == "audio"
        assert by_name["notes.mid"].kind == "midi"
        assert by_name["kick.wav"].rel_path == "Media/Audio Files/kick.wav"
        assert all(f.size_bytes > 0 for f in ex_files.files)


class TestFindBundleRoot:
    def test_single_top_level_dir(self, tmp_path):
        (tmp_path / "Song.logicx").mkdir()
        (tmp_path / "Song.logicx" / "f.wav").write_bytes(b"x")
        assert _find_bundle_root(tmp_path) == tmp_path / "Song.logicx"

    def test_contents_directly(self, tmp_path):
        (tmp_path / "Media").mkdir()
        (tmp_path / "Media" / "f.wav").write_bytes(b"x")
        (tmp_path / "Alternatives").mkdir()
        assert _find_bundle_root(tmp_path) == tmp_path

    def test_skips_macosx(self, tmp_path):
        (tmp_path / "__MACOSX").mkdir()
        (tmp_path / "Song.logicx").mkdir()
        assert _find_bundle_root(tmp_path) == tmp_path / "Song.logicx"


class TestSanitizeRelPath:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Media/Audio Files/a.wav", "Media/Audio Files/a.wav"),
            ("Song.logicx/Media/a.wav", "Media/a.wav"),  # bundle root stripped
            ("./Media/a.wav", "Media/a.wav"),
        ],
    )
    def test_valid(self, raw, expected):
        assert _sanitize_rel_path(raw) == expected

    @pytest.mark.parametrize("raw", ["../evil", "a/../../evil", "/etc/passwd", "", "a\x00b"])
    def test_rejected(self, raw):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _sanitize_rel_path(raw)
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _make_session_item(db_session, file_path: str, filename: str = "Heliotrope.logicx") -> MediaItem:
    item = make_media_item(
        db_session,
        filename=filename,
        file_path=file_path,
        media_type="session",
        mime_type="application/octet-stream",
        file_size_bytes=100,
    )
    db_session.add(MediaSource(media_item_id=item.id, source_type="manual_upload"))
    db_session.add(
        MediaSessionMeta(
            media_item_id=item.id,
            tool="logic",
            original_bundle_name=filename,
            bundle_size_bytes=100,
            extraction_status="pending",
            extracted_count=0,
        )
    )
    db_session.commit()
    return item


def _make_project_with_slot(db_session, user_id: int = 1) -> tuple[Project, ProjectSlot]:
    project = Project(name="Heliotrope", slug="heliotrope", kind="album", created_by=user_id)
    db_session.add(project)
    db_session.flush()
    slot = ProjectSlot(project_id=project.id, position=1, label="Track 1")
    db_session.add(slot)
    db_session.commit()
    return project, slot


class TestRunSessionExtraction:
    def test_end_to_end_directory_bundle(self, db_session, tmp_media_dir, patched_jobs, test_user):
        bundle = _make_bundle_dir(Path(tmp_media_dir) / "session" / "2026-07")
        rel = bundle.relative_to(tmp_media_dir).as_posix()
        parent = _make_session_item(db_session, rel)
        project, slot = _make_project_with_slot(db_session, user_id=test_user.id)
        db_session.add(
            ProjectItem(project_id=project.id, slot_id=slot.id, media_item_id=parent.id, added_by=test_user.id)
        )
        db_session.commit()

        jobs.run_session_extraction(parent.id)

        db_session.expire_all()
        meta = db_session.query(MediaSessionMeta).filter_by(media_item_id=parent.id).one()
        assert meta.extraction_status == "done"
        assert meta.extracted_count == 2  # kick.wav + bass.aif; notes.mid not in fixture bundle

        children = db_session.query(MediaItem).filter_by(parent_media_item_id=parent.id).all()
        assert len(children) == 2
        for child in children:
            assert child.media_type == "audio"
            assert (Path(tmp_media_dir) / child.file_path).exists()
            # attached to the same project + slot as the parent
            attach = (
                db_session.query(ProjectItem)
                .filter_by(project_id=project.id, slot_id=slot.id, media_item_id=child.id)
                .one_or_none()
            )
            assert attach is not None
            # stubbed audio extraction wrote durations
            assert child.audio_meta is not None
            assert child.audio_meta.duration_seconds == 1.5
            # routed to emulsion via the session_extract source
            source_types = {s.source_type for s in child.sources}
            assert "session_extract" in source_types

    def test_zip_backed_bundle(self, db_session, tmp_media_dir, patched_jobs):
        # Build a zipped .logicx on disk (legacy single-file upload shape)
        zip_rel = "session/2026-07/abcd1234_Studio.logicx.zip"
        zip_abs = Path(tmp_media_dir) / zip_rel
        zip_abs.parent.mkdir(parents=True)
        with zipfile.ZipFile(zip_abs, "w") as zf:
            zf.writestr("Studio.logicx/Media/Audio Files/a.wav", _wav_bytes())
            zf.writestr("Studio.logicx/Alternatives/000/ProjectData", b"\x00" * 32)
            zf.writestr("__MACOSX/._junk", b"junk")

        parent = _make_session_item(db_session, zip_rel, filename="Studio.logicx.zip")
        jobs.run_session_extraction(parent.id)

        db_session.expire_all()
        meta = db_session.query(MediaSessionMeta).filter_by(media_item_id=parent.id).one()
        assert meta.extraction_status == "done"
        assert meta.extracted_count == 1
        child = db_session.query(MediaItem).filter_by(parent_media_item_id=parent.id).one()
        assert child.filename == "a.wav"

    def test_dedup_reuses_existing_item(self, db_session, tmp_media_dir, patched_jobs):
        bundle = _make_bundle_dir(Path(tmp_media_dir) / "session" / "2026-07")
        rel = bundle.relative_to(tmp_media_dir).as_posix()

        # Pre-create an item whose content matches kick.wav exactly
        import hashlib

        kick_sha = hashlib.sha256((bundle / "Media" / "Audio Files" / "kick.wav").read_bytes()).hexdigest()
        existing = make_media_item(
            db_session,
            sha256=kick_sha,
            filename="kick.wav",
            file_path="audio/2026-01/deadbeef_kick.wav",
            media_type="audio",
            mime_type="audio/wav",
        )

        parent = _make_session_item(db_session, rel)
        jobs.run_session_extraction(parent.id)

        db_session.expire_all()
        meta = db_session.query(MediaSessionMeta).filter_by(media_item_id=parent.id).one()
        assert meta.extraction_status == "done"
        assert meta.extracted_count == 1  # only bass.aif was newly created
        db_session.refresh(existing)
        assert existing.parent_media_item_id == parent.id
        children = db_session.query(MediaItem).filter_by(parent_media_item_id=parent.id).all()
        assert len(children) == 2  # existing kick + new bass

    def test_midi_child_end_to_end(self, db_session, tmp_media_dir, patched_jobs, test_user):
        from tests.test_cues import _midi_with_markers

        bundle = _make_bundle_dir(Path(tmp_media_dir) / "session" / "2026-07")
        _midi_with_markers(bundle / "notes.mid")
        rel = bundle.relative_to(tmp_media_dir).as_posix()
        parent = _make_session_item(db_session, rel)
        project, slot = _make_project_with_slot(db_session, user_id=test_user.id)
        db_session.add(
            ProjectItem(project_id=project.id, slot_id=slot.id, media_item_id=parent.id, added_by=test_user.id)
        )
        db_session.commit()

        jobs.run_session_extraction(parent.id)

        db_session.expire_all()
        meta = db_session.query(MediaSessionMeta).filter_by(media_item_id=parent.id).one()
        assert meta.extraction_status == "done"
        assert meta.extracted_count == 3  # 2 audio + 1 midi

        midi_child = (
            db_session.query(MediaItem)
            .filter_by(parent_media_item_id=parent.id, media_type="midi")
            .one()
        )
        assert midi_child.filename == "notes.mid"
        assert midi_child.midi_meta is not None
        assert midi_child.midi_meta.tempo == pytest.approx(120.0)
        assert midi_child.midi_meta.note_count == 1
        assert midi_child.midi_meta.duration_seconds == pytest.approx(0.5)
        assert midi_child.midi_meta.preview_path
        assert (Path(tmp_media_dir) / midi_child.midi_meta.preview_path).exists()
        # attached to the same slot as the parent
        attach = (
            db_session.query(ProjectItem)
            .filter_by(project_id=project.id, slot_id=slot.id, media_item_id=midi_child.id)
            .one_or_none()
        )
        assert attach is not None

        # MIDI markers anchored to the SESSION item as cue annotations
        from server.models import Annotation

        annos = (
            db_session.query(Annotation)
            .filter_by(media_item_id=parent.id, source="midi")
            .order_by(Annotation.position_seconds)
            .all()
        )
        assert [a.label for a in annos] == ["Intro", "Verse"]
        assert all(a.kind == "cue" for a in annos)

    def test_missing_bundle_marks_failed(self, db_session, tmp_media_dir, patched_jobs):
        parent = _make_session_item(db_session, "session/2026-07/nope.logicx")
        jobs.run_session_extraction(parent.id)

        db_session.expire_all()
        meta = db_session.query(MediaSessionMeta).filter_by(media_item_id=parent.id).one()
        assert meta.extraction_status == "failed"
        assert meta.extraction_error

        from server.models import ExtractionFailure

        failure = (
            db_session.query(ExtractionFailure)
            .filter_by(media_item_id=parent.id, extraction_type="session_extract")
            .one_or_none()
        )
        assert failure is not None
