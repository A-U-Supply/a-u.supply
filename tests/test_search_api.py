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
    with patch("search_api.meili_sync"), patch("search_api.meili_delete"):
        yield


class TestSearchEndpoint:
    """Tests for POST /api/search."""

    def test_search_requires_auth(self, client):
        resp = client.post("/api/search", json={"query": "test"})
        assert resp.status_code == 401

    def test_search_returns_results(self, client, auth_headers):
        with patch("search_client.multi_search", return_value={
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

    def test_upload_unsupported_mime_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/media/upload",
            files={"file": ("doc.pdf", io.BytesIO(b"pdf data"), "application/pdf")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

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


class TestDeleteMedia:
    """Tests for DELETE /api/media/{id}."""

    def test_delete_removes_item(self, client, auth_headers, db_session, tmp_media_dir):
        from models import MediaItem

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
        from models import MediaTag

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
        from models import MediaTag

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
        from models import ExtractionFailure

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
        from models import ApiKey
        from auth import hash_api_key

        raw_key = "read-only-key-test"
        api_key = ApiKey(
            user_id=test_user.id,
            key_hash=hash_api_key(raw_key),
            key_prefix=raw_key[:8],
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
        from models import ApiKey
        from auth import hash_api_key

        raw_key = "write-scope-key-test"
        api_key = ApiKey(
            user_id=test_user.id,
            key_hash=hash_api_key(raw_key),
            key_prefix=raw_key[:8],
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
