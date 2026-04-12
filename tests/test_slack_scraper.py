"""Tests for the Slack scraper module.

All external calls (Slack API, yt-dlp, HTTP) are mocked.
"""

import hashlib
import json
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_media_item, make_media_source


# ---------------------------------------------------------------------------
# URL extraction tests (pure functions, no mocking needed)
# ---------------------------------------------------------------------------


class TestExtractUrls:
    """Tests for extract_urls — finds supported platform links in message text."""

    def test_finds_youtube_url(self):
        from slack_scraper import extract_urls
        urls = extract_urls("check this out <https://www.youtube.com/watch?v=dQw4w9WgXcQ>")
        assert any("youtube.com" in u for u in urls)

    def test_finds_tiktok_url(self):
        from slack_scraper import extract_urls
        urls = extract_urls("viral <https://www.tiktok.com/@user/video/1234567890>")
        assert any("tiktok.com" in u for u in urls)

    def test_finds_soundcloud_url(self):
        from slack_scraper import extract_urls
        urls = extract_urls("listen to <https://soundcloud.com/artist/track-name>")
        assert any("soundcloud.com" in u for u in urls)

    def test_no_urls_returns_empty(self):
        from slack_scraper import extract_urls
        urls = extract_urls("just a regular message with no links")
        assert urls == [] or len(urls) == 0

    def test_multiple_urls(self):
        from slack_scraper import extract_urls
        text = "check <https://youtube.com/watch?v=abc> and <https://soundcloud.com/x/y>"
        urls = extract_urls(text)
        assert len(urls) >= 2

    def test_slack_url_with_label(self):
        from slack_scraper import extract_urls
        urls = extract_urls("<https://youtube.com/watch?v=abc|my video>")
        assert len(urls) == 1
        assert "youtube.com" in urls[0]


class TestIsDownloadableUrl:
    """Tests for is_downloadable_url — checks if a URL is from a supported platform."""

    def test_youtube_is_downloadable(self):
        from slack_scraper import is_downloadable_url
        assert is_downloadable_url("https://www.youtube.com/watch?v=abc123") is True

    def test_tiktok_is_downloadable(self):
        from slack_scraper import is_downloadable_url
        assert is_downloadable_url("https://www.tiktok.com/@user/video/123") is True

    def test_soundcloud_is_downloadable(self):
        from slack_scraper import is_downloadable_url
        assert is_downloadable_url("https://soundcloud.com/artist/track") is True

    def test_random_url_not_downloadable(self):
        from slack_scraper import is_downloadable_url
        assert is_downloadable_url("https://example.com/page") is False

    def test_google_not_downloadable(self):
        from slack_scraper import is_downloadable_url
        assert is_downloadable_url("https://www.google.com") is False


# ---------------------------------------------------------------------------
# Scrape channel tests (mocked Slack API)
# ---------------------------------------------------------------------------


class TestScrapeChannel:
    """Tests for scrape_channel with mocked Slack API responses."""

    def test_scrape_channel_processes_files(self, db_session):
        """Mocked Slack API returns a message with a file; verify it gets processed."""
        from slack_scraper import scrape_channel

        mock_history = {
            "ok": True,
            "messages": [
                {
                    "ts": "1234567890.000001",
                    "text": "here is a file",
                    "files": [
                        {
                            "id": "F_TEST_001",
                            "name": "photo.jpg",
                            "mimetype": "image/jpeg",
                            "size": 2048,
                            "url_private": "https://files.slack.com/photo.jpg",
                        }
                    ],
                    "reactions": [{"name": "fire", "count": 3}],
                }
            ],
            "response_metadata": {},
        }

        with patch("slack_scraper.get_channel_history", return_value=mock_history), \
             patch("slack_scraper.download_slack_file", return_value=True), \
             patch("slack_scraper._ingest_file", return_value={"status": "new", "media_item_id": "test-id"}), \
             patch("slack_scraper.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"):
            result = scrape_channel("image-gen", "C_TEST")

        assert result["files_found"] >= 1

    def test_dedup_by_slack_file_id(self, db_session):
        """A file with an existing slack_file_id should not be re-downloaded."""
        from slack_scraper import scrape_channel

        # Pre-create a media item with a source that has this slack_file_id
        item = make_media_item(db_session)
        make_media_source(
            db_session,
            item.id,
            source_type="slack_file",
            slack_file_id="F_ALREADY_EXISTS",
            source_channel="image-gen",
        )

        mock_history = {
            "ok": True,
            "messages": [
                {
                    "ts": "1234567890.000002",
                    "text": "duplicate file",
                    "files": [
                        {
                            "id": "F_ALREADY_EXISTS",
                            "name": "photo.jpg",
                            "mimetype": "image/jpeg",
                            "size": 2048,
                            "url_private": "https://files.slack.com/photo.jpg",
                        }
                    ],
                }
            ],
            "response_metadata": {},
        }

        with patch("slack_scraper.get_channel_history", return_value=mock_history), \
             patch("slack_scraper.download_slack_file") as mock_dl, \
             patch("slack_scraper.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"):
            result = scrape_channel("image-gen", "C_TEST")

        # File should have been skipped
        assert result["files_skipped_dedup"] >= 1


class TestReactionRefresh:
    """Tests for refreshing reaction counts."""

    def test_refresh_reactions_updates_counts(self, db_session):
        from slack_scraper import refresh_reactions

        item = make_media_item(db_session)
        source = make_media_source(
            db_session,
            item.id,
            source_type="slack_file",
            slack_message_ts="1234567890.000001",
            source_channel="image-gen",
            reaction_count=2,
        )

        mock_reactions_resp = {
            "ok": True,
            "message": {
                "reactions": [
                    {"name": "fire", "count": 5},
                    {"name": "thumbsup", "count": 3},
                ],
            },
        }

        with patch("slack_scraper.get_reactions", return_value=mock_reactions_resp), \
             patch("slack_scraper.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"):
            result = refresh_reactions(days_back=365)

        db_session.refresh(source)
        assert source.reaction_count == 8


class TestMockYtDlp:
    """Tests for yt-dlp subprocess mocking."""

    def test_download_url_returns_none_on_failure(self):
        from slack_scraper import download_url
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("slack_scraper.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
                result = download_url("https://youtube.com/watch?v=test", Path(tmpdir))
            assert result is None
