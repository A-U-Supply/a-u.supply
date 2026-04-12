"""Tests for SHA-256 deduplication behavior across upload and scrape sources."""

import hashlib
import io
import os
import uuid
from unittest.mock import patch

import pytest

from tests.conftest import make_media_item, make_media_source


class TestSha256Consistency:
    """Tests that SHA-256 hashing is consistent."""

    def test_same_bytes_same_hash(self):
        data = b"consistent content for hashing"
        h1 = hashlib.sha256(data).hexdigest()
        h2 = hashlib.sha256(data).hexdigest()
        assert h1 == h2

    def test_different_bytes_different_hash(self):
        h1 = hashlib.sha256(b"content A").hexdigest()
        h2 = hashlib.sha256(b"content B").hexdigest()
        assert h1 != h2

    def test_hash_is_64_char_hex(self):
        h = hashlib.sha256(b"test").hexdigest()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


@pytest.fixture(autouse=True)
def mock_meilisearch():
    with patch("search_api.meili_sync"), patch("search_api.meili_delete"):
        yield


class TestUploadDedup:
    """Tests for deduplication via the upload endpoint."""

    def test_duplicate_upload_returns_same_item(self, client, auth_headers, tmp_media_dir):
        content = b"unique content for dedup testing 12345"

        # First upload
        resp1 = client.post(
            "/api/media/upload",
            files={"file": ("file1.png", io.BytesIO(content), "image/png")},
            headers=auth_headers,
        )
        assert resp1.status_code == 201
        data1 = resp1.json()

        # Second upload with same content but different filename
        resp2 = client.post(
            "/api/media/upload",
            files={"file": ("file2.png", io.BytesIO(content), "image/png")},
            headers=auth_headers,
        )
        assert resp2.status_code == 201
        data2 = resp2.json()

        # Same MediaItem
        assert data1["id"] == data2["id"]
        assert data1["sha256"] == data2["sha256"]

        # Two sources
        assert len(data2["sources"]) == 2

    def test_different_content_creates_separate_items(self, client, auth_headers, tmp_media_dir):
        resp1 = client.post(
            "/api/media/upload",
            files={"file": ("a.png", io.BytesIO(b"content A"), "image/png")},
            headers=auth_headers,
        )
        resp2 = client.post(
            "/api/media/upload",
            files={"file": ("b.png", io.BytesIO(b"content B"), "image/png")},
            headers=auth_headers,
        )

        assert resp1.json()["id"] != resp2.json()["id"]
        assert resp1.json()["sha256"] != resp2.json()["sha256"]


class TestDatabaseDedup:
    """Tests for dedup at the database level."""

    def test_sha256_unique_constraint_prevents_duplicate_items(self, db_session):
        from models import MediaItem
        from sqlalchemy.exc import IntegrityError

        sha = hashlib.sha256(b"same content").hexdigest()

        item1 = MediaItem(
            id=str(uuid.uuid4()),
            sha256=sha,
            filename="first.png",
            file_path="image/2026-04/first.png",
            media_type="image",
            file_size_bytes=100,
            mime_type="image/png",
        )
        db_session.add(item1)
        db_session.commit()

        item2 = MediaItem(
            id=str(uuid.uuid4()),
            sha256=sha,
            filename="second.png",
            file_path="image/2026-04/second.png",
            media_type="image",
            file_size_bytes=100,
            mime_type="image/png",
        )
        db_session.add(item2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_multiple_sources_for_same_item(self, db_session):
        """Same content from different sources should have one item with multiple sources."""
        from models import MediaSource

        item = make_media_item(db_session)

        # Source 1: manual upload
        make_media_source(db_session, item.id, source_type="manual_upload")

        # Source 2: slack scrape
        make_media_source(
            db_session,
            item.id,
            source_type="slack_file",
            slack_file_id="F_FROM_SLACK",
            source_channel="#media",
        )

        # Source 3: link scrape
        make_media_source(
            db_session,
            item.id,
            source_type="slack_link",
            source_url="https://youtube.com/watch?v=xyz",
        )

        db_session.refresh(item)
        assert len(item.sources) == 3
        source_types = {s.source_type for s in item.sources}
        assert source_types == {"manual_upload", "slack_file", "slack_link"}


class TestCrossSourceDedup:
    """Tests for dedup across manual upload and Slack scrape."""

    def test_upload_then_slack_scrape_same_content(self, db_session):
        """If a file is uploaded manually and then scraped from Slack,
        only one MediaItem should exist with two sources."""
        from models import MediaItem, MediaSource

        sha = hashlib.sha256(b"cross source content").hexdigest()

        # Simulate manual upload creating the item
        item = make_media_item(db_session, sha256=sha)
        make_media_source(db_session, item.id, source_type="manual_upload")

        # Simulate slack scraper finding the same content by SHA-256
        existing = db_session.query(MediaItem).filter(MediaItem.sha256 == sha).first()
        assert existing is not None
        assert existing.id == item.id

        # Add slack source to existing item (what the scraper should do)
        slack_source = MediaSource(
            media_item_id=existing.id,
            source_type="slack_file",
            slack_file_id="F_CROSS_SOURCE",
            source_channel="#media",
        )
        db_session.add(slack_source)
        db_session.commit()

        # Verify single item, two sources
        items = db_session.query(MediaItem).filter(MediaItem.sha256 == sha).all()
        assert len(items) == 1

        db_session.refresh(item)
        assert len(item.sources) == 2

    def test_slack_scrape_then_upload_same_content(self, db_session):
        """If a file is scraped from Slack first and then manually uploaded,
        the upload should detect the duplicate and add a new source."""
        from models import MediaItem, MediaSource

        sha = hashlib.sha256(b"reverse order content").hexdigest()

        # Simulate slack scraper creating the item first
        item = make_media_item(db_session, sha256=sha)
        make_media_source(
            db_session,
            item.id,
            source_type="slack_file",
            slack_file_id="F_FIRST_FROM_SLACK",
        )

        # Simulate upload finding duplicate by SHA-256
        existing = db_session.query(MediaItem).filter(MediaItem.sha256 == sha).first()
        assert existing is not None

        # Upload adds a new source
        upload_source = MediaSource(
            media_item_id=existing.id,
            source_type="manual_upload",
        )
        db_session.add(upload_source)
        db_session.commit()

        db_session.refresh(item)
        assert len(item.sources) == 2
        source_types = {s.source_type for s in item.sources}
        assert "slack_file" in source_types
        assert "manual_upload" in source_types
