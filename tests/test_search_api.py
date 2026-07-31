"""Tests for the media search engine API endpoints."""

import hashlib
import io
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_media_item, make_media_source


# All endpoints require auth. We mock Meilisearch sync calls globally.
@pytest.fixture(autouse=True)
def mock_meilisearch():
    with patch("server.search_api.meili_sync"), patch("server.search_api.meili_delete"):
        yield


class TestSearchEndpoint:
    """Tests for POST /api/search."""

    def test_search_requires_auth(self, client):
        resp = client.post("/api/search", json={"query": "test"})
        assert resp.status_code == 401

    def test_search_returns_results(self, client, auth_headers):
        with patch("server.search_client.multi_search", return_value={
            "hits": [{"id": "abc", "filename": "test.png"}],
            "total": 1,
            "facets": {},
            "page": 1,
            "per_page": 20,
        }):
            resp = client.post(
                "/api/search",
                json={"query": "test"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "hits" in data
        assert "total" in data


class TestGetMedia:
    """Tests for GET /api/media/{id}."""

    def test_get_media_returns_metadata(self, client, auth_headers, db_session):
        from sqlalchemy.orm import sessionmaker

        # Create item through the test session that the app will use
        item = make_media_item(db_session)
        make_media_source(db_session, item.id)

        resp = client.get(f"/api/media/{item.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == item.id
        assert data["filename"] == "test.png"
        assert "tags" in data
        assert "sources" in data

    def test_get_media_not_found(self, client, auth_headers):
        resp = client.get("/api/media/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404


class TestUploadMedia:
    """Tests for POST /api/media/upload."""

    def test_upload_creates_item(self, client, auth_headers, tmp_media_dir):
        content = b"fake image data for testing"
        resp = client.post(
            "/api/media/upload",
            files={"file": ("test.png", io.BytesIO(content), "image/png")},
            data={"tags": "drums, bass", "description": "A test upload"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "test.png"
        assert data["mime_type"] == "image/png"
        expected_sha = hashlib.sha256(content).hexdigest()
        assert data["sha256"] == expected_sha
        assert "drums" in data["tags"]
        assert "bass" in data["tags"]

    def test_upload_empty_file_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/media/upload",
            files={"file": ("empty.png", io.BytesIO(b""), "image/png")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_upload_unknown_mime_accepted_as_document(self, client, auth_headers, tmp_media_dir):
        """PDFs (and any other non-image/audio/video type) are accepted, not rejected.

        We never block a file based on its MIME type — unknown types roll up
        into the generic ``document`` media_type so PDFs, text, archives, etc.
        all land in the Emulsion index alongside other user uploads.
        """
        content = b"%%PDF-1.4 fake pdf body"
        resp = client.post(
            "/api/media/upload",
            files={"file": ("doc.pdf", io.BytesIO(content), "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["media_type"] == "document"
        assert data["mime_type"] == "application/pdf"
        assert data["filename"] == "doc.pdf"
        assert data["file_path"].startswith("document/")
        assert os.path.exists(os.path.join(tmp_media_dir, data["file_path"]))

    def test_upload_dedup_same_content(self, client, auth_headers, db_session):
        """Uploading the same file twice should create one MediaItem and two sources."""
        content = b"identical content for dedup test"

        # First upload
        resp1 = client.post(
            "/api/media/upload",
            files={"file": ("first.png", io.BytesIO(content), "image/png")},
            headers=auth_headers,
        )
        assert resp1.status_code == 201
        item_id_1 = resp1.json()["id"]

        # Second upload of same content
        resp2 = client.post(
            "/api/media/upload",
            files={"file": ("second.png", io.BytesIO(content), "image/png")},
            headers=auth_headers,
        )
        assert resp2.status_code == 201
        item_id_2 = resp2.json()["id"]

        # Same item
        assert item_id_1 == item_id_2

        # Should have two sources now
        sources = resp2.json()["sources"]
        assert len(sources) == 2


class TestUpdateMedia:
    """Tests for PUT /api/media/{id}."""

    def test_update_description(self, client, auth_headers, db_session):
        item = make_media_item(db_session, description="old desc")

        resp = client.put(
            f"/api/media/{item.id}",
            json={"description": "new description"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "new description"

    def test_update_nonexistent_item(self, client, auth_headers):
        resp = client.put(
            "/api/media/fake-id",
            json={"description": "nope"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestRenameMedia:
    """Tests for PUT /api/media/{id} with `filename` (rename)."""

    def _write(self, tmp_media_dir, rel_path, data=b"data"):
        full = os.path.join(tmp_media_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return full

    def test_rename_updates_db_and_moves_file(self, client, auth_headers, db_session, tmp_media_dir):
        item = make_media_item(db_session, filename="test.png", file_path="image/2026-04/abcdef12_test.png")
        self._write(tmp_media_dir, item.file_path)

        resp = client.put(
            f"/api/media/{item.id}",
            json={"filename": "new-name.png"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "new-name.png"

        db_session.refresh(item)
        assert item.file_path == "image/2026-04/abcdef12_new-name.png"
        assert not os.path.exists(os.path.join(tmp_media_dir, "image/2026-04/abcdef12_test.png"))
        assert os.path.exists(os.path.join(tmp_media_dir, "image/2026-04/abcdef12_new-name.png"))

    def test_rename_preserves_extension_ignoring_typed_extension(self, client, auth_headers, db_session, tmp_media_dir):
        item = make_media_item(db_session, filename="test.png", file_path="image/2026-04/abcdef12_test.png")
        self._write(tmp_media_dir, item.file_path)

        resp = client.put(
            f"/api/media/{item.id}",
            json={"filename": "new-name.jpg"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "new-name.png"

    def test_rename_moves_thumbnail_siblings(self, client, auth_headers, db_session, tmp_media_dir):
        item = make_media_item(db_session, filename="test.png", file_path="image/2026-04/abcdef12_test.png")
        self._write(tmp_media_dir, item.file_path)
        self._write(tmp_media_dir, "image/2026-04/abcdef12_test_thumb.webp")
        self._write(tmp_media_dir, "image/2026-04/abcdef12_test_thumb_sm.webp")
        self._write(tmp_media_dir, "image/2026-04/abcdef12_test_thumb_lg.webp")

        resp = client.put(
            f"/api/media/{item.id}",
            json={"filename": "renamed.png"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        for suffix in ("_thumb.webp", "_thumb_sm.webp", "_thumb_lg.webp"):
            assert not os.path.exists(
                os.path.join(tmp_media_dir, f"image/2026-04/abcdef12_test{suffix}")
            )
            assert os.path.exists(
                os.path.join(tmp_media_dir, f"image/2026-04/abcdef12_renamed{suffix}")
            )

    def test_rename_video_thumbnail_path_updated(self, client, auth_headers, db_session, tmp_media_dir):
        from server.models import MediaVideoMeta

        item = make_media_item(
            db_session,
            filename="clip.mp4",
            file_path="video/2026-04/abcdef12_clip.mp4",
            media_type="video",
            mime_type="video/mp4",
        )
        self._write(tmp_media_dir, item.file_path)
        old_thumb_abs = self._write(tmp_media_dir, "video/2026-04/abcdef12_clip_thumb.webp")
        db_session.add(MediaVideoMeta(
            media_item_id=item.id,
            duration_seconds=1.0,
            width=100,
            height=100,
            thumbnail_path=old_thumb_abs,
        ))
        db_session.commit()

        resp = client.put(
            f"/api/media/{item.id}",
            json={"filename": "renamed.mp4"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        db_session.refresh(item)
        new_thumb_abs = os.path.join(tmp_media_dir, "video/2026-04/abcdef12_renamed_thumb.webp")
        assert item.video_meta.thumbnail_path == new_thumb_abs
        assert not os.path.exists(old_thumb_abs)
        assert os.path.exists(new_thumb_abs)

    def test_rename_collision_returns_409(self, client, auth_headers, db_session, tmp_media_dir):
        item = make_media_item(db_session, filename="test.png", file_path="image/2026-04/abcdef12_test.png")
        self._write(tmp_media_dir, item.file_path)
        self._write(tmp_media_dir, "image/2026-04/abcdef12_taken.png")

        resp = client.put(
            f"/api/media/{item.id}",
            json={"filename": "taken.png"},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_rename_rejects_empty_name(self, client, auth_headers, db_session):
        item = make_media_item(db_session)
        resp = client.put(f"/api/media/{item.id}", json={"filename": "   "}, headers=auth_headers)
        assert resp.status_code == 400

    def test_rename_rejects_path_separators(self, client, auth_headers, db_session):
        item = make_media_item(db_session)
        resp = client.put(f"/api/media/{item.id}", json={"filename": "a/b"}, headers=auth_headers)
        assert resp.status_code == 400

    def test_rename_requires_admin_scope(self, client, member_auth_headers, db_session, tmp_media_dir):
        item = make_media_item(db_session, filename="test.png", file_path="image/2026-04/abcdef12_test.png")
        self._write(tmp_media_dir, item.file_path)

        resp = client.put(
            f"/api/media/{item.id}",
            json={"filename": "new-name.png"},
            headers=member_auth_headers,
        )
        assert resp.status_code == 403

    def test_description_only_edit_still_allows_write_scope(self, client, member_auth_headers, db_session):
        item = make_media_item(db_session, description="old")
        resp = client.put(
            f"/api/media/{item.id}",
            json={"description": "new"},
            headers=member_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "new"

    def test_meili_sync_failure_does_not_fail_the_rename(self, client, auth_headers, db_session, tmp_media_dir):
        item = make_media_item(db_session, filename="test.png", file_path="image/2026-04/abcdef12_test.png")
        self._write(tmp_media_dir, item.file_path)

        with patch("server.search_api.meili_sync", side_effect=RuntimeError("meili down")):
            resp = client.put(
                f"/api/media/{item.id}",
                json={"filename": "new-name.png"},
                headers=auth_headers,
            )
        assert resp.status_code == 200

        from server.models import ExtractionFailure

        failure = (
            db_session.query(ExtractionFailure)
            .filter(ExtractionFailure.media_item_id == item.id)
            .first()
        )
        assert failure is not None
        assert failure.extraction_type == "meilisearch_sync"


class TestDeleteMedia:
    """Tests for DELETE /api/media/{id}."""

    def test_delete_removes_item(self, client, auth_headers, db_session, tmp_media_dir):
        from server.models import MediaItem

        item = make_media_item(db_session)

        # Create the file on disk so delete doesn't error
        file_path = os.path.join(tmp_media_dir, item.file_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(b"data")

        resp = client.delete(f"/api/media/{item.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify item is gone
        assert db_session.query(MediaItem).filter(MediaItem.id == item.id).first() is None

    def test_delete_nonexistent_item(self, client, auth_headers):
        resp = client.delete("/api/media/fake-id", headers=auth_headers)
        assert resp.status_code == 404


class TestTagCRUD:
    """Tests for tag add/remove/batch endpoints."""

    def test_add_tags(self, client, auth_headers, db_session):
        item = make_media_item(db_session)

        resp = client.post(
            f"/api/media/{item.id}/tags",
            json={"tags": ["drums", "BASS", "  synth  "]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "drums" in data["added"]
        assert "bass" in data["added"]
        assert "synth" in data["added"]

    def test_add_duplicate_tag_silently_skipped(self, client, auth_headers, db_session):
        from server.models import MediaTag

        item = make_media_item(db_session)
        db_session.add(MediaTag(media_item_id=item.id, tag="drums"))
        db_session.commit()

        resp = client.post(
            f"/api/media/{item.id}/tags",
            json={"tags": ["drums", "bass"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # "drums" was already there, only "bass" should be added
        assert "drums" not in resp.json()["added"]
        assert "bass" in resp.json()["added"]

    def test_remove_tag(self, client, auth_headers, db_session):
        from server.models import MediaTag

        item = make_media_item(db_session)
        db_session.add(MediaTag(media_item_id=item.id, tag="drums"))
        db_session.commit()

        resp = client.delete(
            f"/api/media/{item.id}/tags/drums",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_remove_nonexistent_tag(self, client, auth_headers, db_session):
        item = make_media_item(db_session)

        resp = client.delete(
            f"/api/media/{item.id}/tags/nonexistent",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_batch_add_tags(self, client, auth_headers, db_session):
        items = [make_media_item(db_session) for _ in range(2)]

        resp = client.post(
            "/api/media/batch/tags",
            json={
                "media_ids": [item.id for item in items],
                "tags": ["batch-tag"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        for item in items:
            assert "batch-tag" in results[item.id]["added"]


class TestBatchDelete:
    """Tests for batch delete endpoint."""

    def test_batch_delete(self, client, auth_headers, db_session, tmp_media_dir):
        items = [make_media_item(db_session) for _ in range(2)]

        # Create files on disk
        for item in items:
            file_path = os.path.join(tmp_media_dir, item.file_path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(b"data")

        resp = client.post(
            "/api/media/batch/delete",
            json={"media_ids": [item.id for item in items]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()["deleted"]) == 2


class TestApiKeyCRUD:
    """Tests for API key management endpoints."""

    def test_create_api_key_returns_key(self, client, auth_headers):
        resp = client.post(
            "/api/keys",
            json={"label": "test key", "scope": "read"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "key" in data  # Raw key returned only once
        assert data["label"] == "test key"
        assert data["scope"] == "read"
        assert data["key_prefix"].startswith("au_")

    def test_list_api_keys_shows_prefix(self, client, auth_headers):
        # Create a key first
        client.post(
            "/api/keys",
            json={"label": "listable", "scope": "write"},
            headers=auth_headers,
        )

        resp = client.get("/api/keys", headers=auth_headers)
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) >= 1
        assert "key_prefix" in keys[0]
        # Raw key should NOT be in list response
        assert "key" not in keys[0] or keys[0].get("key") is None

    def test_revoke_api_key(self, client, auth_headers):
        create_resp = client.post(
            "/api/keys",
            json={"label": "to-revoke", "scope": "read"},
            headers=auth_headers,
        )
        key_id = create_resp.json()["id"]

        resp = client.delete(f"/api/keys/{key_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Key should no longer appear in list
        list_resp = client.get("/api/keys", headers=auth_headers)
        key_ids = [k["id"] for k in list_resp.json()]
        assert key_id not in key_ids


class TestExtractionFailures:
    """Tests for GET /api/extraction-failures."""

    def test_list_extraction_failures(self, client, auth_headers, db_session):
        from server.models import ExtractionFailure

        item = make_media_item(db_session)
        db_session.add(
            ExtractionFailure(
                media_item_id=item.id,
                extraction_type="ffprobe",
                error_message="ffprobe not found",
            )
        )
        db_session.commit()

        resp = client.get("/api/extraction-failures", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["failures"][0]["extraction_type"] == "ffprobe"


class TestScopeEnforcement:
    """Tests that read-scope keys cannot access write/admin endpoints."""

    def test_read_scope_cannot_upload(self, client, db_session, test_user):
        from server.models import ApiKey
        from server.auth import hash_api_key

        raw_key = "read-only-key-test"
        api_key = ApiKey(
            user_id=test_user.id,
            key_hash=hash_api_key(raw_key),
            key_prefix=raw_key[:11],
            label="read only",
            scope="read",
        )
        db_session.add(api_key)
        db_session.commit()

        resp = client.post(
            "/api/media/upload",
            files={"file": ("test.png", io.BytesIO(b"data"), "image/png")},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 403

    def test_write_scope_cannot_delete(self, client, db_session, test_user):
        from server.models import ApiKey
        from server.auth import hash_api_key

        raw_key = "write-scope-key-test"
        api_key = ApiKey(
            user_id=test_user.id,
            key_hash=hash_api_key(raw_key),
            key_prefix=raw_key[:11],
            label="write only",
            scope="write",
        )
        db_session.add(api_key)
        db_session.commit()

        resp = client.delete(
            "/api/media/some-id",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        # Should be 403 (scope) not 404 (not found) because scope check happens first
        assert resp.status_code == 403


class TestUploadFailureReport:
    """Tests for POST /api/media/upload/report-failure.

    The browser is the only witness to a transfer that dies in flight — a
    dropped connection or an aborted request never reaches this process — so
    the client reports them and the server relays to Slack.
    """

    @pytest.fixture(autouse=True)
    def mock_slack(self, monkeypatch):
        calls = []

        def fake_notify(event_type, user, **payload):
            calls.append((event_type, payload))

        import server.slack_notifier

        monkeypatch.setattr(server.slack_notifier, "notify_immediate", fake_notify)
        return calls

    def report(self, client, headers, failures):
        return client.post(
            "/api/media/upload/report-failure",
            json={"failures": failures},
            headers=headers,
        )

    def test_requires_auth(self, client):
        resp = self.report(client, {}, [{"name": "a.wav", "message": "boom"}])
        assert resp.status_code in (401, 403)

    def test_reports_one_failure(self, client, auth_headers, mock_slack):
        resp = self.report(
            client, auth_headers, [{"name": "kick_04.wav", "message": "Network error"}]
        )
        assert resp.status_code == 204
        events = [p for e, p in mock_slack if e == "upload.failed"]
        assert len(events) == 1
        assert events[0]["count"] == 1
        assert events[0]["names"] == ["kick_04.wav"]
        assert events[0]["first_message"] == "Network error"

    def test_a_whole_queue_is_one_message(self, client, auth_headers, mock_slack):
        """The client batches on purpose: one dead uplink fails everything
        queued behind it, and a post per file would be a wall of red."""
        failures = [{"name": f"f{i}.wav", "message": "Network error"} for i in range(12)]
        assert self.report(client, auth_headers, failures).status_code == 204
        events = [p for e, p in mock_slack if e == "upload.failed"]
        assert len(events) == 1
        assert events[0]["count"] == 12
        # Names are capped so a big batch doesn't paste a directory listing.
        assert len(events[0]["names"]) == 5

    def test_empty_report_says_nothing(self, client, auth_headers, mock_slack):
        assert self.report(client, auth_headers, []).status_code == 204
        assert [e for e, _ in mock_slack if e == "upload.failed"] == []

    def test_slack_outage_is_not_an_upload_failure(
        self, client, auth_headers, monkeypatch
    ):
        """Reporting a failure must never itself surface as one."""

        def boom(*a, **k):
            raise RuntimeError("slack is down")

        import server.slack_notifier

        monkeypatch.setattr(server.slack_notifier, "notify_immediate", boom)
        resp = self.report(client, auth_headers, [{"name": "a.wav", "message": "x"}])
        assert resp.status_code == 204


class TestUploadFailedSlackMessage:
    def test_singular_names_the_file(self):
        from server.slack_notifier import _format_upload_failed

        text = _format_upload_failed(
            "brendan", {"count": 1, "names": ["kick_04.wav"], "first_message": "413 too large"}
        )["text"]
        assert "kick_04.wav" in text
        assert "413 too large" in text
        assert "1 of" not in text  # reads as a sentence, not a tally

    def test_plural_counts_and_truncates(self):
        from server.slack_notifier import _format_upload_failed

        text = _format_upload_failed(
            "brendan", {"count": 12, "names": [f"f{i}.wav" for i in range(5)]}
        )["text"]
        assert "12 of" in text
        assert "+7 more" in text
