"""Slack channel scraper for the media search engine.

Pulls media files and URLs from configured Slack channels,
downloads them, deduplicates by SHA-256, and stores them as
MediaItem/MediaSource records in the database.
"""

import hashlib
import json
import logging
import mimetypes
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from models import MediaItem, MediaSource, SessionLocal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SEARCH_MEDIA_DIR = Path(os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data"))

SCRAPE_CHANNELS: dict[str, str] = {
    "image-gen": os.environ.get("SLACK_CHANNEL_IMAGE_GEN", "C_IMAGE_GEN_ID"),
    "sample-sale": os.environ.get("SLACK_CHANNEL_SAMPLE_SALE", "C_SAMPLE_SALE_ID"),
}

SLACK_API_BASE = "https://slack.com/api/"

# Cache of Slack user ID → display name
_user_cache: dict[str, str] = {}


def _get_slack_username(user_id: str) -> str:
    """Resolve a Slack user ID to a display name. Caches results."""
    if not user_id:
        return ""
    if user_id in _user_cache:
        return _user_cache[user_id]
    # Lazy-load full user list on first call
    if not _user_cache:
        _load_slack_users()
    return _user_cache.get(user_id, user_id)


def _load_slack_users():
    """Fetch all workspace users and cache their display names."""
    global _user_cache
    cursor = None
    while True:
        params: dict = {"limit": "200"}
        if cursor:
            params["cursor"] = cursor
        resp = slack_api("users.list", params)
        if not resp.get("ok"):
            logger.error("Failed to fetch users list: %s", resp.get("error"))
            break
        for member in resp.get("members", []):
            uid = member.get("id", "")
            name = (
                member.get("profile", {}).get("display_name")
                or member.get("profile", {}).get("real_name")
                or member.get("real_name")
                or member.get("name")
                or uid
            )
            _user_cache[uid] = name
        meta = resp.get("response_metadata", {})
        next_cursor = meta.get("next_cursor", "")
        if not next_cursor:
            break
        cursor = next_cursor
    logger.info("Loaded %d Slack users into cache", len(_user_cache))

# URL patterns that yt-dlp is likely to handle
_DOWNLOADABLE_DOMAINS = {
    "youtube.com", "youtu.be", "www.youtube.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com",
    "soundcloud.com", "www.soundcloud.com",
    "vimeo.com", "www.vimeo.com",
    "instagram.com", "www.instagram.com",
    "twitter.com", "x.com", "www.twitter.com",
    "facebook.com", "www.facebook.com", "fb.watch",
    "twitch.tv", "www.twitch.tv", "clips.twitch.tv",
    "dailymotion.com", "www.dailymotion.com",
    "bandcamp.com",
    "streamable.com",
    "reddit.com", "www.reddit.com", "v.redd.it",
}

# MIME prefix -> media_type folder
_MIME_TO_MEDIA_TYPE = {
    "image": "image",
    "audio": "audio",
    "video": "video",
}

# ---------------------------------------------------------------------------
# Status tracking (module-level, read by search_api.py)
# ---------------------------------------------------------------------------

_scrape_status: dict = {
    "running": False,
    "last_run": None,
    "last_result": None,
    "current_channel": None,
}
_status_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Slack API helpers
# ---------------------------------------------------------------------------


def slack_api(method: str, params: dict | None = None) -> dict:
    """Call a Slack Web API method and return the parsed JSON response."""
    url = f"{SLACK_API_BASE}{method}"
    if params:
        url = f"{url}?{urlencode(params)}"

    req = Request(url, headers={
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    })

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except (HTTPError, URLError) as exc:
        logger.error("Slack API error calling %s: %s", method, exc)
        return {"ok": False, "error": str(exc)}

    if not data.get("ok"):
        logger.warning("Slack API %s returned error: %s", method, data.get("error"))

    return data


def get_channel_history(
    channel_id: str,
    oldest: str | None = None,
    cursor: str | None = None,
) -> dict:
    """Fetch channel messages via conversations.history with pagination."""
    params: dict = {"channel": channel_id, "limit": "200"}
    if oldest:
        params["oldest"] = oldest
    if cursor:
        params["cursor"] = cursor
    return slack_api("conversations.history", params)


def get_reactions(channel_id: str, timestamp: str) -> dict:
    """Fetch reactions on a specific message."""
    return slack_api("reactions.get", {
        "channel": channel_id,
        "timestamp": timestamp,
        "full": "true",
    })


def download_slack_file(url: str, dest_path: Path) -> bool:
    """Download a Slack-hosted file (url_private) with auth headers."""
    req = Request(url, headers={
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
    })
    try:
        with urlopen(req, timeout=120) as resp:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        return True
    except (HTTPError, URLError) as exc:
        logger.error("Failed to download Slack file %s: %s", url, exc)
        return False


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------

_URL_RE = re.compile(
    r"https?://[^\s<>\[\]|\"'`,;)}\]]+",
    re.IGNORECASE,
)


def extract_urls(text: str) -> list[str]:
    """Extract HTTP(S) URLs from message text."""
    if not text:
        return []
    # Slack wraps URLs in <url> or <url|label> format
    slack_urls = re.findall(r"<(https?://[^|>]+)(?:\|[^>]*)?>", text)
    if slack_urls:
        return slack_urls
    return _URL_RE.findall(text)


def is_downloadable_url(url: str) -> bool:
    """Check if a URL is likely downloadable by yt-dlp."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Strip port if present
        domain = domain.split(":")[0]
        return any(domain == d or domain.endswith(f".{d}") for d in _DOWNLOADABLE_DOMAINS)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# yt-dlp integration
# ---------------------------------------------------------------------------


def download_url(url: str, dest_dir: Path) -> Path | None:
    """Download a URL via yt-dlp subprocess. Returns path to the file or None."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(dest_dir / "%(title)s.%(ext)s")

    # Resolve yt-dlp binary: check venv first, then system PATH
    import shutil
    ytdlp_bin = shutil.which("yt-dlp") or "/app/.venv/bin/yt-dlp"

    try:
        result = subprocess.run(
            [
                ytdlp_bin,
                "--no-playlist",
                "-o", output_template,
                "--write-info-json",
                "--no-overwrites",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            logger.error("yt-dlp failed for %s: %s", url, result.stderr[:500])
            return None

        # Find the downloaded file by looking for new files (not .info.json)
        # yt-dlp prints the filename — parse it from stdout
        for line in result.stdout.splitlines():
            # Matches: [download] Destination: /path/to/file.ext
            # or: [download] /path/to/file.ext has already been downloaded
            match = re.search(r"\[download\]\s+(?:Destination:\s+)?(.+\.(?!info\.json)\S+)", line)
            if match:
                candidate = Path(match.group(1).strip())
                if candidate.exists() and not candidate.name.endswith(".info.json"):
                    return candidate

            # Matches: [Merger] Merging formats into "/path/to/file.ext"
            match = re.search(r'\[Merger\]\s+Merging formats into "(.+?)"', line)
            if match:
                candidate = Path(match.group(1).strip())
                if candidate.exists():
                    return candidate

        # Fallback: find the most recently created non-json file in dest_dir
        candidates = [
            f for f in dest_dir.iterdir()
            if f.is_file() and not f.name.endswith(".info.json")
        ]
        if candidates:
            return max(candidates, key=lambda f: f.stat().st_mtime)

        logger.warning("yt-dlp completed but no output file found for %s", url)
        return None

    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timed out for %s", url)
        return None
    except FileNotFoundError:
        logger.error("yt-dlp not found — is it installed?")
        return None


def _parse_ytdlp_info(downloaded_path: Path) -> dict | None:
    """Parse the .info.json sidecar file written by yt-dlp."""
    info_path = downloaded_path.with_suffix(".info.json")
    # yt-dlp sometimes names it differently
    if not info_path.exists():
        stem = downloaded_path.stem
        for candidate in downloaded_path.parent.glob(f"{stem}*.info.json"):
            info_path = candidate
            break

    if not info_path.exists():
        return None

    try:
        with open(info_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "title": data.get("title"),
            "description": data.get("description"),
            "uploader": data.get("uploader"),
            "channel": data.get("channel"),
            "duration": data.get("duration"),
            "webpage_url": data.get("webpage_url"),
            "extractor": data.get("extractor"),
        }
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse yt-dlp info.json at %s: %s", info_path, exc)
        return None


# ---------------------------------------------------------------------------
# File hashing & media type detection
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest for a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(131072)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _detect_media_type(filename: str) -> str | None:
    """Return 'image', 'audio', or 'video' based on MIME type guess, or None."""
    mime, _ = mimetypes.guess_type(filename)
    if not mime:
        return None
    prefix = mime.split("/")[0]
    return _MIME_TO_MEDIA_TYPE.get(prefix)


def _detect_mime_type(filename: str) -> str:
    """Return MIME type string or a fallback."""
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


# ---------------------------------------------------------------------------
# File storage
# ---------------------------------------------------------------------------


def _storage_path(media_type: str, sha256: str, filename: str) -> Path:
    """Build the on-disk storage path for a media file.

    Layout: {SEARCH_MEDIA_DIR}/{media_type}/{YYYY-MM}/{8char-sha256}_{filename}
    """
    now = datetime.now(timezone.utc)
    month_dir = now.strftime("%Y-%m")
    safe_filename = re.sub(r"[^\w.\-]", "_", filename)[:200]
    final_name = f"{sha256[:8]}_{safe_filename}"
    return SEARCH_MEDIA_DIR / media_type / month_dir / final_name


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_last_scrape_ts(db, channel_name: str) -> str | None:
    """Get the newest Slack message timestamp scraped for a channel.

    Used for incremental scrapes — only fetch messages newer than this.
    Returns None if no messages have been scraped (triggers full history).

    Note: only used when we're confident the full history has been scraped.
    For the initial backfill, pass None to get everything.
    """
    latest = (
        db.query(MediaSource.slack_message_ts)
        .filter(
            MediaSource.source_channel == channel_name,
            MediaSource.slack_message_ts.isnot(None),
        )
        .order_by(MediaSource.slack_message_ts.desc())
        .first()
    )
    return latest[0] if latest else None


def _slack_file_already_scraped(db, slack_file_id: str) -> bool:
    """Check if a Slack file ID has already been ingested."""
    return (
        db.query(MediaSource.id)
        .filter(MediaSource.slack_file_id == slack_file_id)
        .first()
    ) is not None


def _source_url_already_scraped(db, source_url: str, channel_name: str) -> bool:
    """Check if a URL has already been scraped from this channel."""
    return (
        db.query(MediaSource.id)
        .filter(
            MediaSource.source_url == source_url,
            MediaSource.source_channel == channel_name,
        )
        .first()
    ) is not None


def _ingest_file(
    db,
    file_path: Path,
    filename: str,
    *,
    source_type: str,
    source_channel: str | None = None,
    slack_file_id: str | None = None,
    slack_message_ts: str | None = None,
    slack_message_text: str | None = None,
    slack_reactions: dict | None = None,
    reaction_count: int = 0,
    source_url: str | None = None,
    source_metadata: dict | None = None,
) -> dict:
    """Hash a downloaded file, dedup, create MediaItem + MediaSource.

    Returns {"status": "new"|"duplicate"|"skipped", "media_item_id": ...}
    """
    media_type = _detect_media_type(filename)
    if not media_type:
        logger.info("Skipping non-media file: %s", filename)
        return {"status": "skipped", "media_item_id": None}

    sha256 = _sha256_file(file_path)
    mime_type = _detect_mime_type(filename)
    file_size = file_path.stat().st_size

    # Check for existing item with same hash
    existing = db.query(MediaItem).filter(MediaItem.sha256 == sha256).first()

    if existing:
        # Duplicate content — add a new source pointing to the existing item
        media_item = existing
        status = "duplicate"
    else:
        # New file — move to permanent storage
        dest = _storage_path(media_type, sha256, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Copy file to storage location (source may be in a temp dir)
        import shutil
        shutil.copy2(str(file_path), str(dest))

        rel_path = str(dest.relative_to(SEARCH_MEDIA_DIR))

        media_item = MediaItem(
            id=str(uuid.uuid4()),
            sha256=sha256,
            filename=filename,
            file_path=rel_path,
            media_type=media_type,
            file_size_bytes=file_size,
            mime_type=mime_type,
        )
        db.add(media_item)
        db.flush()
        status = "new"

    # Create source record
    source = MediaSource(
        id=str(uuid.uuid4()),
        media_item_id=media_item.id,
        source_type=source_type,
        source_channel=source_channel,
        slack_file_id=slack_file_id,
        slack_message_ts=slack_message_ts,
        slack_message_text=slack_message_text,
        slack_reactions=json.dumps(slack_reactions) if slack_reactions else None,
        reaction_count=reaction_count,
        source_url=source_url,
        source_metadata=json.dumps(source_metadata) if source_metadata else None,
    )
    db.add(source)
    db.flush()

    # NOTE: extraction is NOT triggered here — the item hasn't been committed yet
    # and background threads can't see uncommitted data. Extraction runs as a
    # batch after the scrape completes via _run_post_scrape_extraction().

    # Sync to Meilisearch
    try:
        from search_client import sync_media_item
        sync_media_item(db, media_item)
    except ImportError:
        pass

    return {"status": status, "media_item_id": media_item.id}


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------


def _extract_reactions_from_message(message: dict) -> tuple[dict, int]:
    """Extract a {emoji: count} dict and total count from a Slack message."""
    reactions_list = message.get("reactions", [])
    if not reactions_list:
        return {}, 0
    reactions = {r["name"]: r["count"] for r in reactions_list}
    total = sum(reactions.values())
    return reactions, total


def _process_message_files(
    db,
    message: dict,
    channel_name: str,
    stats: dict,
    dry_run: bool = False,
) -> None:
    """Process file attachments in a Slack message."""
    files = message.get("files", [])
    ts = message.get("ts", "")
    text = message.get("text", "")
    user_id = message.get("user", "")
    poster = _get_slack_username(user_id) if user_id else ""
    reactions, reaction_count = _extract_reactions_from_message(message)

    for file_info in files:
        file_id = file_info.get("id", "")
        url_private = file_info.get("url_private", "")
        filename = file_info.get("name", "unknown")
        file_size = file_info.get("size", 0)
        mimetype = file_info.get("mimetype", "")

        if not url_private:
            continue

        stats["files_found"] += 1

        # Determine media type from MIME
        prefix = mimetype.split("/")[0] if mimetype else ""
        media_type = _MIME_TO_MEDIA_TYPE.get(prefix)
        if not media_type:
            # Try from filename
            media_type = _detect_media_type(filename)
        if not media_type:
            logger.debug("Skipping non-media file: %s (%s)", filename, mimetype)
            continue

        if dry_run:
            stats["total_size_bytes"] += file_size
            by_type = stats.setdefault("by_type", {"image": 0, "audio": 0, "video": 0})
            by_type[media_type] = by_type.get(media_type, 0) + 1
            stats["total_files"] = stats.get("total_files", 0) + 1
            continue

        # Dedup by slack_file_id
        if file_id and _slack_file_already_scraped(db, file_id):
            stats["files_skipped_dedup"] += 1
            continue

        # Download to temp location
        tmp_dir = SEARCH_MEDIA_DIR / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{uuid.uuid4().hex}_{filename}"

        if not download_slack_file(url_private, tmp_path):
            stats["errors"] += 1
            continue

        try:
            result = _ingest_file(
                db,
                tmp_path,
                filename,
                source_type="slack_file",
                source_channel=channel_name,
                slack_file_id=file_id,
                slack_message_ts=ts,
                slack_message_text=text,
                slack_reactions=reactions,
                reaction_count=reaction_count,
                source_metadata={"poster": poster, "slack_user_id": user_id} if poster else None,
            )
            if result["status"] == "skipped":
                stats["files_skipped_dedup"] += 1
            else:
                stats["files_downloaded"] += 1
        except Exception as exc:
            logger.error("Error ingesting file %s: %s", filename, exc)
            stats["errors"] += 1
        finally:
            # Clean up temp file
            if tmp_path.exists():
                tmp_path.unlink()


def _process_message_urls(
    db,
    message: dict,
    channel_name: str,
    stats: dict,
    dry_run: bool = False,
    skip_ytdlp: bool = False,
) -> None:
    """Extract and download URLs from a Slack message via yt-dlp."""
    if skip_ytdlp and not dry_run:
        return

    text = message.get("text", "")
    ts = message.get("ts", "")
    reactions, reaction_count = _extract_reactions_from_message(message)

    urls = extract_urls(text)
    downloadable = [u for u in urls if is_downloadable_url(u)]

    for url in downloadable:
        stats["files_found"] += 1

        if dry_run:
            by_type = stats.setdefault("by_type", {"image": 0, "audio": 0, "video": 0})
            # Can't know type/size without downloading in dry run — count as unknown
            stats["total_files"] = stats.get("total_files", 0) + 1
            continue

        # Dedup by source URL
        if _source_url_already_scraped(db, url, channel_name):
            stats["files_skipped_dedup"] += 1
            continue

        # Download via yt-dlp to temp dir
        tmp_dir = SEARCH_MEDIA_DIR / "_tmp" / uuid.uuid4().hex
        tmp_dir.mkdir(parents=True, exist_ok=True)

        downloaded_path = download_url(url, tmp_dir)
        if not downloaded_path:
            stats["errors"] += 1
            # Clean up temp dir
            import shutil
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
            continue

        # Parse yt-dlp metadata
        ytdlp_meta = _parse_ytdlp_info(downloaded_path)

        try:
            result = _ingest_file(
                db,
                downloaded_path,
                downloaded_path.name,
                source_type="slack_link",
                source_channel=channel_name,
                slack_message_ts=ts,
                slack_message_text=text,
                slack_reactions=reactions,
                reaction_count=reaction_count,
                source_url=url,
                source_metadata=ytdlp_meta,
            )
            if result["status"] == "skipped":
                stats["files_skipped_dedup"] += 1
            else:
                stats["files_downloaded"] += 1
        except Exception as exc:
            logger.error("Error ingesting URL %s: %s", url, exc)
            stats["errors"] += 1
        finally:
            # Clean up temp dir
            import shutil
            shutil.rmtree(str(tmp_dir), ignore_errors=True)


# ---------------------------------------------------------------------------
# Channel scraping
# ---------------------------------------------------------------------------


def scrape_channel(
    channel_name: str,
    channel_id: str,
    dry_run: bool = False,
    incremental: bool = False,
) -> dict:
    """Scrape a single Slack channel for media.

    When incremental=True, only fetches messages newer than the last scraped
    timestamp for this channel. Much faster for periodic auto-sync.

    Returns stats dict with counts of files found, downloaded, skipped, errors.
    """
    db = SessionLocal()
    stats: dict = {
        "channel": channel_name,
        "files_found": 0,
        "files_downloaded": 0,
        "files_skipped_dedup": 0,
        "errors": 0,
    }

    if dry_run:
        stats["total_files"] = 0
        stats["total_size_bytes"] = 0
        stats["by_type"] = {"image": 0, "audio": 0, "video": 0}

    try:
        oldest = None
        if incremental:
            oldest = _get_last_scrape_ts(db, channel_name)
            if oldest:
                logger.info("Incremental scrape for %s: oldest=%s", channel_name, oldest)

        cursor = None

        page = 0
        while True:
            page += 1
            resp = get_channel_history(channel_id, oldest=oldest, cursor=cursor)
            if not resp.get("ok"):
                logger.error(
                    "Failed to fetch history for %s (page %d): %s",
                    channel_name, page, resp.get("error"),
                )
                stats["errors"] += 1
                break

            messages = resp.get("messages", [])
            logger.info(
                "Scraping %s page %d: %d messages (cursor=%s)",
                channel_name, page, len(messages), cursor or "none",
            )

            if not messages:
                break

            for message in messages:
                _process_message_files(db, message, channel_name, stats, dry_run=dry_run)
                _process_message_urls(db, message, channel_name, stats, dry_run=dry_run, skip_ytdlp=True)

            if not dry_run:
                db.commit()

            # Pagination
            meta = resp.get("response_metadata", {})
            next_cursor = meta.get("next_cursor", "")
            if not next_cursor:
                break
            cursor = next_cursor

    except Exception as exc:
        logger.exception("Error scraping channel %s: %s", channel_name, exc)
        stats["errors"] += 1
        db.rollback()
    finally:
        db.close()

    return stats


# ---------------------------------------------------------------------------
# Reaction refresh
# ---------------------------------------------------------------------------


def refresh_reactions(days_back: int = 60) -> dict:
    """Refresh reaction counts for media ingested in the last N days."""
    db = SessionLocal()
    updated = 0
    errors = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    try:
        sources = (
            db.query(MediaSource)
            .filter(
                MediaSource.created_at >= cutoff,
                MediaSource.slack_message_ts.isnot(None),
                MediaSource.source_channel.isnot(None),
            )
            .all()
        )

        for source in sources:
            channel_id = SCRAPE_CHANNELS.get(source.source_channel)
            if not channel_id:
                continue

            resp = get_reactions(channel_id, source.slack_message_ts)
            if not resp.get("ok"):
                # "no_item_found" is normal for deleted messages
                if resp.get("error") != "no_item_found":
                    errors += 1
                continue

            msg = resp.get("message", {})
            reactions, total = _extract_reactions_from_message(msg)

            source.slack_reactions = json.dumps(reactions) if reactions else None
            source.reaction_count = total
            updated += 1

        db.commit()
    except Exception as exc:
        logger.exception("Error refreshing reactions: %s", exc)
        db.rollback()
        errors += 1
    finally:
        db.close()

    return {"updated": updated, "errors": errors}


# ---------------------------------------------------------------------------
# Entry points (called by search_api.py)
# ---------------------------------------------------------------------------


def _run_scrape(channels: dict[str, str], incremental: bool = False) -> dict:
    """Internal: run the scrape synchronously, updating _scrape_status."""
    results = {}
    with _status_lock:
        _scrape_status["running"] = True
        _scrape_status["last_run"] = datetime.now(timezone.utc).isoformat()
        _scrape_status["last_result"] = None

    try:
        for name, cid in channels.items():
            with _status_lock:
                _scrape_status["current_channel"] = name
            results[name] = scrape_channel(name, cid, incremental=incremental)

        # Run extraction and Meilisearch sync for all items missing metadata
        with _status_lock:
            _scrape_status["current_channel"] = "_extraction"
        _run_post_scrape_extraction()

    finally:
        with _status_lock:
            _scrape_status["running"] = False
            _scrape_status["current_channel"] = None
            _scrape_status["last_result"] = results

    return results


def _run_post_scrape_extraction():
    """Run metadata extraction and Meilisearch sync for items missing metadata."""
    from models import MediaImageMeta, MediaAudioMeta, MediaVideoMeta

    db = SessionLocal()
    try:
        # Find images without metadata
        images_without_meta = (
            db.query(MediaItem)
            .filter(
                MediaItem.media_type == "image",
                ~MediaItem.id.in_(db.query(MediaImageMeta.media_item_id)),
            )
            .all()
        )
        logger.info("Post-scrape: %d images need extraction", len(images_without_meta))
        for item in images_without_meta:
            try:
                from extraction import run_extraction
                full_path = str(SEARCH_MEDIA_DIR / item.file_path)
                run_extraction(item.id, full_path, item.media_type)
            except Exception as exc:
                logger.error("Extraction failed for %s: %s", item.id, exc)

        # Find audio without metadata
        audio_without_meta = (
            db.query(MediaItem)
            .filter(
                MediaItem.media_type == "audio",
                ~MediaItem.id.in_(db.query(MediaAudioMeta.media_item_id)),
            )
            .all()
        )
        logger.info("Post-scrape: %d audio files need extraction", len(audio_without_meta))
        for item in audio_without_meta:
            try:
                from extraction import run_extraction
                full_path = str(SEARCH_MEDIA_DIR / item.file_path)
                run_extraction(item.id, full_path, item.media_type)
            except Exception as exc:
                logger.error("Extraction failed for %s: %s", item.id, exc)

        # Find videos without metadata
        videos_without_meta = (
            db.query(MediaItem)
            .filter(
                MediaItem.media_type == "video",
                ~MediaItem.id.in_(db.query(MediaVideoMeta.media_item_id)),
            )
            .all()
        )
        logger.info("Post-scrape: %d videos need extraction", len(videos_without_meta))
        for item in videos_without_meta:
            try:
                from extraction import run_extraction
                full_path = str(SEARCH_MEDIA_DIR / item.file_path)
                run_extraction(item.id, full_path, item.media_type)
            except Exception as exc:
                logger.error("Extraction failed for %s: %s", item.id, exc)

        # Rebuild Meilisearch index for all items
        logger.info("Post-scrape: syncing all items to Meilisearch")
        try:
            from search_client import sync_media_item, configure_indexes
            configure_indexes()
            all_items = db.query(MediaItem).all()
            for item in all_items:
                try:
                    db.refresh(item)
                    sync_media_item(db, item)
                except Exception as exc:
                    logger.error("Meilisearch sync failed for %s: %s", item.id, exc)
        except ImportError:
            pass

        logger.info("Post-scrape extraction and sync complete")
    finally:
        db.close()


def trigger_scrape(channels: list[str] | None = None) -> dict:
    """Trigger a scrape of specified channels (or all configured).

    Runs in a background thread. Returns immediate status.
    """
    with _status_lock:
        if _scrape_status["running"]:
            return {"status": "already_running", "current_channel": _scrape_status["current_channel"]}

    target = {}
    if channels:
        for name in channels:
            if name in SCRAPE_CHANNELS:
                target[name] = SCRAPE_CHANNELS[name]
            else:
                logger.warning("Unknown channel: %s", name)
    else:
        target = dict(SCRAPE_CHANNELS)

    if not target:
        return {"status": "error", "message": "No valid channels specified"}

    thread = threading.Thread(target=_run_scrape, args=(target,), daemon=True)
    thread.start()

    return {"status": "started", "channels": list(target.keys())}


def trigger_dry_run(channels: list[str] | None = None) -> dict:
    """Run a dry-run scan of specified channels (or all configured).

    Synchronous — returns scan results immediately.
    """
    target = {}
    if channels:
        for name in channels:
            if name in SCRAPE_CHANNELS:
                target[name] = SCRAPE_CHANNELS[name]
    else:
        target = dict(SCRAPE_CHANNELS)

    if not target:
        return {"status": "error", "message": "No valid channels specified"}

    results = {}
    for name, cid in target.items():
        results[name] = scrape_channel(name, cid, dry_run=True)

    return {"status": "complete", "results": results}


def backfill_posters() -> dict:
    """Backfill poster display names for all existing MediaSource records.

    Re-reads channel history, matches sources by slack_message_ts,
    and stores the poster name in source_metadata.
    """
    db = SessionLocal()
    updated = 0
    errors = 0

    try:
        # Load Slack user cache
        _load_slack_users()
        logger.info("Backfilling posters for %d cached users", len(_user_cache))

        for channel_name, channel_id in SCRAPE_CHANNELS.items():
            logger.info("Backfilling posters for channel: %s", channel_name)
            cursor = None
            while True:
                resp = get_channel_history(channel_id, oldest=None, cursor=cursor)
                if not resp.get("ok"):
                    errors += 1
                    break

                for message in resp.get("messages", []):
                    ts = message.get("ts", "")
                    user_id = message.get("user", "")
                    if not ts or not user_id:
                        continue

                    poster_name = _get_slack_username(user_id)

                    # Find all sources with this message timestamp in this channel
                    sources = (
                        db.query(MediaSource)
                        .filter(
                            MediaSource.slack_message_ts == ts,
                            MediaSource.source_channel == channel_name,
                        )
                        .all()
                    )
                    for source in sources:
                        # Merge poster into source_metadata
                        try:
                            meta = json.loads(source.source_metadata) if source.source_metadata else {}
                        except (json.JSONDecodeError, TypeError):
                            meta = {}
                        if meta.get("poster") == poster_name:
                            continue
                        meta["poster"] = poster_name
                        meta["slack_user_id"] = user_id
                        source.source_metadata = json.dumps(meta)
                        updated += 1

                db.commit()

                next_cursor = resp.get("response_metadata", {}).get("next_cursor", "")
                if not next_cursor:
                    break
                cursor = next_cursor

        # Resync all items to Meilisearch with updated poster data
        logger.info("Resyncing %d updated sources to Meilisearch", updated)
        try:
            from search_client import sync_media_item, configure_indexes
            configure_indexes()
            items = db.query(MediaItem).all()
            for item in items:
                try:
                    db.refresh(item)
                    sync_media_item(db, item)
                except Exception:
                    pass
        except ImportError:
            pass

    except Exception as exc:
        logger.exception("Error backfilling posters: %s", exc)
        db.rollback()
        errors += 1
    finally:
        db.close()

    return {"updated": updated, "errors": errors}


def trigger_incremental_scrape() -> dict:
    """Trigger an incremental scrape (only new messages since last run).

    Used by the background auto-sync scheduler. Much faster than a full scrape.
    Runs in a background thread. Returns immediate status.
    """
    with _status_lock:
        if _scrape_status["running"]:
            return {"status": "already_running", "current_channel": _scrape_status["current_channel"]}

    target = dict(SCRAPE_CHANNELS)
    if not target:
        return {"status": "error", "message": "No channels configured"}

    thread = threading.Thread(
        target=_run_scrape, args=(target,), kwargs={"incremental": True}, daemon=True,
    )
    thread.start()

    return {"status": "started", "channels": list(target.keys()), "mode": "incremental"}


def trigger_reaction_refresh(days_back: int = 7) -> dict:
    """Trigger a reaction refresh and sync updated counts to Meilisearch.

    Runs synchronously (fast — just API calls + DB updates).
    """
    result = refresh_reactions(days_back=days_back)

    # Sync updated items to Meilisearch
    if result["updated"] > 0:
        db = SessionLocal()
        try:
            from search_client import sync_media_item
            from models import MediaItem

            # Re-sync items whose sources were updated
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
            sources = (
                db.query(MediaSource)
                .filter(
                    MediaSource.created_at >= cutoff,
                    MediaSource.slack_message_ts.isnot(None),
                )
                .all()
            )
            item_ids = {s.media_item_id for s in sources}
            for item_id in item_ids:
                item = db.query(MediaItem).filter(MediaItem.id == item_id).first()
                if item:
                    try:
                        sync_media_item(db, item)
                    except Exception as exc:
                        logger.error("Meilisearch sync failed for %s: %s", item_id, exc)
        except ImportError:
            pass
        finally:
            db.close()

    return result


def get_scrape_status() -> dict:
    """Return the current/last scrape status."""
    with _status_lock:
        return dict(_scrape_status)
