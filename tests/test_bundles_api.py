"""Endpoint tests for the multi-part session bundle upload API."""

import io
import json
import wave
import zipfile
from urllib.parse import quote

import pytest
from sqlalchemy.orm import sessionmaker

import server.bundles_api as bundles_api
import server.search_api as search_api
from server.models import MediaAudioMeta, MediaItem
from server.session_extract import jobs


def _wav_bytes(seconds: float = 0.1, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def mock_meili():
    from unittest.mock import patch

    with patch("server.bundles_api.meili_sync"), patch("server.search_api.meili_sync"):
        yield


@pytest.fixture
def sync_extraction(monkeypatch, db_engine):
    """Run session extraction synchronously against the in-memory test DB.

    Replaces the background thread + real ffprobe/whisper/AI pipeline with a
    stub that writes a MediaAudioMeta row, so tests stay hermetic.
    """
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
    sync_run = lambda mid: jobs.run_session_extraction(mid)  # noqa: E731
    monkeypatch.setattr(bundles_api, "run_session_extraction_async", sync_run)
    monkeypatch.setattr(jobs, "run_session_extraction_async", sync_run)
    return TestSession


@pytest.fixture
def project_and_slot(client, auth_headers):
    resp = client.post("/api/projects", json={"name": "Heliotrope", "kind": "album"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    resp = client.post(f"/api/projects/{project_id}/slots", json={}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    slot_id = resp.json()["id"]
    return project_id, slot_id


def _start(client, auth_headers, name="Heliotrope.logicx", **kwargs):
    return client.post("/api/media/bundles", json={"name": name, **kwargs}, headers=auth_headers)


def _put_part(client, auth_headers, bundle_id, path, content):
    return client.post(
        f"/api/media/bundles/{bundle_id}/files",
        content=content,
        headers={**auth_headers, "X-Bundle-Path": quote(path)},
    )


class TestBundleStart:
    def test_requires_auth(self, client):
        resp = client.post("/api/media/bundles", json={"name": "X.logicx"})
        assert resp.status_code in (401, 403)

    def test_rejects_unknown_extension(self, client, auth_headers):
        resp = _start(client, auth_headers, name="NotASession.txt")
        assert resp.status_code == 400

    def test_rejects_slot_without_project(self, client, auth_headers):
        resp = _start(client, auth_headers, slot_id="abc")
        assert resp.status_code == 400

    def test_rejects_missing_project(self, client, auth_headers):
        resp = _start(client, auth_headers, project_id="nope")
        assert resp.status_code == 404

    def test_ok(self, client, auth_headers):
        resp = _start(client, auth_headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["tool"] == "logic"
        assert body["bundle_id"]


class TestBundleParts:
    def test_path_traversal_rejected(self, client, auth_headers):
        bundle_id = _start(client, auth_headers).json()["bundle_id"]
        for bad in ("../evil.wav", "a/../../evil.wav", "/etc/passwd"):
            resp = _put_part(client, auth_headers, bundle_id, bad, b"x")
            assert resp.status_code == 400, bad

    def test_unknown_bundle_404(self, client, auth_headers):
        resp = _put_part(client, auth_headers, "no-such-bundle", "a.wav", b"x")
        assert resp.status_code == 404

    def test_part_and_status(self, client, auth_headers):
        bundle_id = _start(client, auth_headers).json()["bundle_id"]
        wav = _wav_bytes()
        resp = _put_part(client, auth_headers, bundle_id, "Media/Audio Files/kick.wav", wav)
        assert resp.status_code == 200, resp.text
        assert resp.json()["size"] == len(wav)

        resp = client.get(f"/api/media/bundles/{bundle_id}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_count"] == 1
        assert body["total_bytes"] == len(wav)

    def test_abort(self, client, auth_headers):
        bundle_id = _start(client, auth_headers).json()["bundle_id"]
        _put_part(client, auth_headers, bundle_id, "Media/a.wav", _wav_bytes())
        resp = client.delete(f"/api/media/bundles/{bundle_id}", headers=auth_headers)
        assert resp.status_code == 204
        resp = client.get(f"/api/media/bundles/{bundle_id}", headers=auth_headers)
        assert resp.status_code == 404


class TestBundleComplete:
    def _upload_bundle(self, client, auth_headers, project_id=None, slot_id=None):
        start_kwargs = {}
        if project_id:
            start_kwargs["project_id"] = project_id
            start_kwargs["slot_id"] = slot_id
        bundle_id = _start(client, auth_headers, **start_kwargs).json()["bundle_id"]
        _put_part(client, auth_headers, bundle_id, "Media/Audio Files/kick.wav", _wav_bytes())
        _put_part(client, auth_headers, bundle_id, "Media/Audio Files/bass.aif", _wav_bytes(0.2))
        _put_part(client, auth_headers, bundle_id, "Alternatives/000/ProjectData", b"\x00" * 32)
        return bundle_id

    def test_complete_empty_400(self, client, auth_headers):
        bundle_id = _start(client, auth_headers).json()["bundle_id"]
        resp = client.post(f"/api/media/bundles/{bundle_id}/complete", json={}, headers=auth_headers)
        assert resp.status_code == 400

    def test_full_flow(self, client, auth_headers, db_session, sync_extraction, project_and_slot):
        project_id, slot_id = project_and_slot
        bundle_id = self._upload_bundle(client, auth_headers, project_id, slot_id)

        resp = client.post(f"/api/media/bundles/{bundle_id}/complete", json={}, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        item = resp.json()
        assert item["media_type"] == "session"
        assert item["filename"] == "Heliotrope.logicx"
        assert item["session_meta"]["tool"] == "logic"
        # extraction ran synchronously under the test patch
        assert item["session_meta"]["extraction_status"] == "done"
        assert item["session_meta"]["extracted_count"] == 2

        # children endpoint
        resp = client.get(f"/api/media/{item['id']}/children", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        children = resp.json()["children"]
        assert len(children) == 2
        assert {c["filename"] for c in children} == {"kick.wav", "bass.aif"}
        for child in children:
            assert child["media_type"] == "audio"
            assert child["duration_seconds"] == 1.5

        # provenance in the DB
        children_db = (
            db_session.query(MediaItem).filter(MediaItem.parent_media_item_id == item["id"]).all()
        )
        assert len(children_db) == 2

        # everything attached to the same slot
        resp = client.get(f"/api/projects/{project_id}/items", headers=auth_headers)
        assert resp.status_code == 200
        slot_ids = {i["media_item_id"]: i["slot_id"] for i in resp.json()["items"]}
        assert slot_ids.get(item["id"]) == slot_id
        for child in children:
            assert slot_ids.get(child["id"]) == slot_id

    def test_dedup_identical_bundle(self, client, auth_headers, sync_extraction):
        bundle_id = self._upload_bundle(client, auth_headers)
        first = client.post(
            f"/api/media/bundles/{bundle_id}/complete", json={}, headers=auth_headers
        ).json()

        bundle_id2 = self._upload_bundle(client, auth_headers)
        resp = client.post(f"/api/media/bundles/{bundle_id2}/complete", json={}, headers=auth_headers)
        assert resp.status_code == 201
        second = resp.json()
        assert second["id"] == first["id"]
        assert second.get("deduplicated") is True

    def test_zip_download_streams(self, client, auth_headers, sync_extraction):
        bundle_id = self._upload_bundle(client, auth_headers)
        item = client.post(
            f"/api/media/bundles/{bundle_id}/complete", json={}, headers=auth_headers
        ).json()

        resp = client.get(f"/api/media/{item['id']}/file", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert any(n.endswith("Media/Audio Files/kick.wav") for n in names)
        assert any(n.endswith("manifest.json") for n in names)


class TestUploadEndpointIntegration:
    def test_zip_bundle_upload_extracts(self, client, auth_headers, sync_extraction):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Studio.logicx/Media/Audio Files/a.wav", _wav_bytes())
            zf.writestr("Studio.logicx/Alternatives/000/ProjectData", b"\x00" * 16)

        resp = client.post(
            "/api/media/upload",
            files={"file": ("Studio.logicx.zip", buf.getvalue(), "application/zip")},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        item = resp.json()
        assert item["media_type"] == "session"
        assert item["session_meta"]["extraction_status"] == "done"
        assert item["session_meta"]["extracted_count"] == 1

        resp = client.get(f"/api/media/{item['id']}/children", headers=auth_headers)
        assert [c["filename"] for c in resp.json()["children"]] == ["a.wav"]

    def test_audio_upload_triggers_extraction(self, client, auth_headers, monkeypatch):
        calls = []
        import server.extraction as extraction_module

        monkeypatch.setattr(
            extraction_module,
            "run_extraction_async",
            lambda mid, path, mtype: calls.append((mid, path, mtype)),
        )
        resp = client.post(
            "/api/media/upload",
            files={"file": ("take.wav", _wav_bytes(), "audio/wav")},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert len(calls) == 1
        assert calls[0][2] == "audio"
        assert calls[0][0] == resp.json()["id"]

    def test_document_upload_skips_extraction(self, client, auth_headers, monkeypatch):
        calls = []
        import server.extraction as extraction_module

        monkeypatch.setattr(
            extraction_module,
            "run_extraction_async",
            lambda *a: calls.append(a),
        )
        resp = client.post(
            "/api/media/upload",
            files={"file": ("notes.pdf", b"%PDF-1.4 fake", "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert calls == []


class TestReaper:
    def test_reap_stale_bundles(self, tmp_media_dir, monkeypatch):
        import os
        import time

        monkeypatch.setenv("SEARCH_MEDIA_DIR", tmp_media_dir)
        # _get_search_media_dir reads the env var at call time
        staging = bundles_api._staging_dir("old-bundle")
        staging.mkdir(parents=True)
        bundles_api._write_state(staging, {"name": "x.logicx", "files": {}})
        old = time.time() - 25 * 3600
        os.utime(staging, (old, old))

        fresh = bundles_api._staging_dir("fresh-bundle")
        fresh.mkdir(parents=True)
        bundles_api._write_state(fresh, {"name": "y.logicx", "files": {}})

        assert bundles_api.reap_stale_bundles() == 1
        assert not staging.exists()
        assert fresh.exists()
