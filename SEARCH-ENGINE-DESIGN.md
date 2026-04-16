# Media Search Engine — Architecture & Design

> **Note:** This is the original design document written before implementation. Some details may differ from the final implementation. See CLAUDE.md for current architecture. The code is the source of truth.

## Overview

A media search engine for A-U.Supply that indexes images, audio, and video collected by bots (Slack scrapers, yt-dlp) and uploaded by members. Supports rich metadata, automatic feature extraction, content-based search, and a manual tagging workflow.

All access requires authentication. No public endpoints.

---

## 1. Infrastructure

### Meilisearch

- **Installation**: Single binary on the host, managed by systemd
- **Binding**: `127.0.0.1:7700` — unreachable from the internet
- **Master key**: Environment variable, read only by FastAPI
- **Role**: Search view over SQLite data. SQLite is the source of truth. If the Meilisearch index is lost or corrupted, it can be fully rebuilt from SQLite + files on disk
- **Indexes**: Separate index per media type (`images`, `audio`, `video`), extensible for future types (e.g. `fonts`, `3d-models`, `presets`)
- **Cross-type search**: Uses Meilisearch's `multi-search` endpoint to query multiple indexes in a single request

### File Storage

- **Mount**: Separate Dokku persistent storage volume, isolated from release catalog media
  - Host: `/var/lib/dokku/data/storage/au-supply-search` → Container: `/app/search-data`
- **Layout**: `/app/search-data/{media_type}/{YYYY-MM}/{8char-sha256}_{filename}`
  - 8-character SHA-256 prefix prevents filename collisions within the same month
- **Thumbnails**: Stored alongside originals as `{basename}_thumb.webp`
- **Resizing**: If storage grows beyond disk capacity, attach a Hetzner volume, move data, update the Dokku storage mapping. No app changes required.

### Meilisearch Data Persistence

- Meilisearch data directory: `/var/lib/meilisearch/data`
- Backed up separately from app data, but considered disposable — can be rebuilt from SQLite

---

## 2. Data Model

### SQLite (Source of Truth)

#### `media_item` table

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| sha256 | TEXT | Content hash, unique. Used for deduplication |
| filename | TEXT | Original filename |
| file_path | TEXT | Path on disk relative to search-data root |
| media_type | TEXT | `image`, `audio`, `video` |
| file_size_bytes | INTEGER | |
| mime_type | TEXT | |
| description | TEXT | Freeform notes |
| created_at | DATETIME | When ingested |
| updated_at | DATETIME | |

#### `media_source` table

Multiple sources can reference the same `media_item` (dedup by SHA-256).

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| media_item_id | UUID | FK → media_item |
| source_type | TEXT | `slack_file`, `slack_link`, `manual_upload` |
| source_channel | TEXT | e.g. `sample-sale`, `image-gen` |
| uploader_id | UUID | FK → user (or bot identifier) |
| slack_file_id | TEXT | For dedup on re-scrape |
| slack_message_ts | TEXT | Slack message timestamp |
| slack_message_text | TEXT | Searchable context |
| slack_reactions | JSON | `{"🔥": 3, "👍": 1}` |
| reaction_count | INTEGER | Total reactions, filterable/sortable |
| source_url | TEXT | Original URL (YouTube, TikTok, etc.) |
| source_metadata | JSON | yt-dlp extracted metadata (title, description, uploader, channel) |
| created_at | DATETIME | When this source was recorded |

#### `media_image_meta` table

| Column | Type | Notes |
|---|---|---|
| media_item_id | UUID | FK → media_item, unique |
| width | INTEGER | |
| height | INTEGER | |
| format | TEXT | e.g. `JPEG`, `PNG`, `WEBP` |
| dominant_colors | JSON | Array of hex colors, extracted via k-means |
| caption | TEXT | Auto-generated (future — via external API) |

#### `media_audio_meta` table

| Column | Type | Notes |
|---|---|---|
| media_item_id | UUID | FK → media_item, unique |
| duration_seconds | FLOAT | |
| sample_rate | INTEGER | |
| channels | INTEGER | |
| bit_depth | INTEGER | |
| transcript | TEXT | Whisper output |
| transcript_confidence | FLOAT | Average confidence score |
| acoustic_tags | JSON | Future — automated classification |

#### `media_video_meta` table

| Column | Type | Notes |
|---|---|---|
| media_item_id | UUID | FK → media_item, unique |
| duration_seconds | FLOAT | |
| width | INTEGER | |
| height | INTEGER | |
| fps | FLOAT | |
| thumbnail_path | TEXT | Auto-generated frame |
| audio_transcript | TEXT | Whisper output from extracted audio track |
| transcript_confidence | FLOAT | |

#### `media_tag` table

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| media_item_id | UUID | FK → media_item |
| tag | TEXT | Normalized: lowercase, trimmed |
| tagged_by | UUID | FK → user |
| created_at | DATETIME | |
| Unique constraint | | `(media_item_id, tag)` |

#### `tag_vocabulary` table

Tracks all known tags for autocomplete suggestions.

| Column | Type | Notes |
|---|---|---|
| tag | TEXT | Primary key, normalized |
| usage_count | INTEGER | Number of media items using this tag |
| created_at | DATETIME | When first used |

#### `api_key` table

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID | FK → user |
| key_hash | TEXT | bcrypt hash of the API key |
| key_prefix | TEXT | First 8 chars, for identification in UI |
| label | TEXT | User-defined (e.g. "slack-bot", "laptop") |
| scope | TEXT | `read`, `write`, `admin` |
| created_at | DATETIME | |
| last_used_at | DATETIME | |
| revoked_at | DATETIME | Null if active |

#### `extraction_failure` table

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| media_item_id | UUID | FK → media_item |
| extraction_type | TEXT | `whisper`, `dominant_colors`, `ffprobe`, `thumbnail`, `yt-dlp` |
| error_message | TEXT | |
| attempts | INTEGER | |
| last_attempt_at | DATETIME | |
| resolved | BOOLEAN | Default false |

### Meilisearch Index Schemas

Each index mirrors its SQLite data, flattened for search.

#### `images` index

```json
{
  "id": "uuid",
  "filename": "photo.jpg",
  "media_type": "image",
  "file_size_bytes": 204800,
  "mime_type": "image/jpeg",
  "description": "freeform notes",
  "tags": ["texture", "dark", "abstract"],
  "tag_count": 3,
  "width": 1920,
  "height": 1080,
  "format": "JPEG",
  "dominant_colors": ["#1a1a2e", "#e94560"],
  "caption": null,
  "sources": [
    {
      "source_type": "slack_file",
      "source_channel": "image-gen",
      "uploader": "username",
      "message_text": "new texture pack output",
      "reaction_count": 5,
      "reactions": {"🔥": 3, "👍": 2}
    }
  ],
  "total_reaction_count": 5,
  "source_channels": ["image-gen"],
  "created_at": 1700000000,
  "updated_at": 1700000000
}
```

#### `audio` index

```json
{
  "id": "uuid",
  "filename": "vocal-chop.wav",
  "media_type": "audio",
  "file_size_bytes": 1048576,
  "mime_type": "audio/wav",
  "description": "freeform notes",
  "tags": ["vocal", "chop", "rnb"],
  "tag_count": 3,
  "duration_seconds": 12.5,
  "sample_rate": 44100,
  "channels": 2,
  "bit_depth": 16,
  "transcript": "transcribed speech content",
  "acoustic_tags": [],
  "sources": [
    {
      "source_type": "slack_link",
      "source_channel": "sample-sale",
      "uploader": "username",
      "message_text": "fire vocal from this tiktok",
      "source_url": "https://tiktok.com/...",
      "source_title": "Original TikTok Title",
      "reaction_count": 8
    }
  ],
  "total_reaction_count": 8,
  "source_channels": ["sample-sale"],
  "created_at": 1700000000,
  "updated_at": 1700000000
}
```

#### `video` index

```json
{
  "id": "uuid",
  "filename": "tutorial.mp4",
  "media_type": "video",
  "file_size_bytes": 52428800,
  "mime_type": "video/mp4",
  "description": "freeform notes",
  "tags": ["tutorial", "synthesis"],
  "tag_count": 3,
  "duration_seconds": 180.0,
  "width": 1920,
  "height": 1080,
  "fps": 30.0,
  "audio_transcript": "transcribed speech from video",
  "sources": [
    {
      "source_type": "slack_link",
      "source_channel": "sample-sale",
      "uploader": "username",
      "message_text": "check this out",
      "source_url": "https://youtube.com/...",
      "source_title": "Original YouTube Title",
      "reaction_count": 2
    }
  ],
  "total_reaction_count": 2,
  "source_channels": ["sample-sale"],
  "created_at": 1700000000,
  "updated_at": 1700000000
}
```

#### Index Configuration (all indexes)

- **Searchable attributes** (priority order): `tags`, `description`, `sources.message_text`, `transcript`/`audio_transcript`, `caption`, `filename`, `sources.source_title`
- **Filterable attributes**: `media_type`, `tags`, `tag_count`, `source_channels`, `total_reaction_count`, `created_at`, `width`, `height`, `duration_seconds`, `format`, `mime_type`
- **Sortable attributes**: `created_at`, `updated_at`, `total_reaction_count`, `file_size_bytes`, `duration_seconds`, `tag_count`
- **Facets**: `tags`, `source_channels`, `format`, `mime_type`

---

## 3. Authentication & API Keys

### Web Authentication

Existing JWT-in-httpOnly-cookie system. No changes needed.

### API Key Authentication

- Keys are generated server-side, displayed once at creation, then only the `key_prefix` is shown
- Stored as bcrypt hashes in `api_key` table (same approach as passwords)
- Sent via `Authorization: Bearer <key>` header
- `last_used_at` updated on each use (debounced — not on every single request)
- Revocation is immediate: delete/mark the row, key stops working on next request

### Permission Scopes

| Scope | Capabilities |
|---|---|
| `read` | Search, view metadata, stream/download files |
| `write` | Everything in `read` + upload, tag, edit metadata |
| `admin` | Everything in `write` + delete, manage API keys, trigger scrapes |

### Auth Middleware

FastAPI dependency that checks:
1. JWT cookie (web sessions), OR
2. `Authorization: Bearer <key>` header (API keys)

Both resolve to a user with a permission scope. Endpoint decorators specify required scope.

---

## 4. Metadata Extraction Pipeline

Runs asynchronously after ingest. Media items are indexed immediately with basic file metadata; extracted features are added as they complete.

### Image Extraction (v1)

- **Pillow**: width, height, format, file size
- **Dominant colors**: k-means clustering on downsampled pixel data (3-5 colors)
- **Auto-captioning**: Not implemented in v1. Schema supports it. Future implementation via external API (Claude Vision or similar).

### Audio Extraction (v1)

- **ffprobe**: duration, sample rate, channels, bit depth
- **faster-whisper**: Speech transcription using CTranslate2 backend, INT8 quantization
  - **Model**: `medium` — source material is unprocessed (speech over music, background noise, variable quality)
  - **VAD filter**: Silero VAD enabled — skip files with no detected speech, saves CPU
  - **Lifecycle**: Load model on demand, unload after 5 minutes idle. ~2GB RAM during inference.
  - **Runs as background task** — never blocks ingest
- **Acoustic tagging**: Not implemented in v1. Schema supports it. Future implementation via PANNs or librosa-based feature extraction.

### Video Extraction (v1)

- **ffprobe**: duration, width, height, fps
- **ffmpeg**: Thumbnail generation — grab frame at ~10% into video, save as WEBP
- **Audio track extraction**: Extract audio → run through same faster-whisper pipeline (with VAD)
- **Frame sampling for visual captioning**: Not implemented in v1. Noted for future.

### Failure Handling

- If any extraction step fails, the media item is still indexed with whatever metadata succeeded
- Failure is logged in `extraction_failure` table with error details
- Failed extractions are surfaced in admin UI for manual review
- Retry mechanism: batch re-extract from admin UI (single item or batch selection)

---

## 5. Slack Scraping

### Setup

- Slack bot/app with scopes: `files:read`, `channels:history`, `channels:read`
- Bot token stored as environment variable

### Channels (v1)

- `#image-gen` — AI-generated images
- `#sample-sale` — Unprocessed audio: raw YouTube clips, TikTok pulls, voice recordings, misc sounds

### Scraping Flow

1. **Scheduled run** (cron, configurable interval) or manual trigger via API
2. Pull messages since last scrape timestamp (stored in SQLite) using `conversations.history`
3. For each message:
   - **File attachments**: Download via Slack API, store on disk
   - **URLs** (YouTube, TikTok, SoundCloud, etc.): Download via yt-dlp with default quality settings
   - Store message text, user, timestamp, reactions
4. SHA-256 hash each file, check for duplicates:
   - **Duplicate found**: Add new `media_source` record pointing to existing `media_item`
   - **New file**: Create `media_item`, `media_source`, trigger extraction pipeline
5. Dedup by Slack file ID to prevent re-processing on re-scrape
6. Index in Meilisearch

### yt-dlp

- **Quality**: Default settings (no cap)
- **Updates**: yt-dlp updated on every deploy to keep up with YouTube/TikTok extractor changes
- **Failures**: Logged in `extraction_failure` table, flagged for manual review. Don't block the rest of the scrape.
- **Source metadata**: Title, description, uploader, channel name, duration from the source platform — stored in `media_source.source_metadata` and indexed in Meilisearch

### Reaction Refresh

- Separate scheduled job, runs weekly
- Sweeps media items ingested in the last 60 days
- Calls `reactions.get` per Slack message, updates `slack_reactions` and `reaction_count`
- Older posts' reactions are considered settled

### Historical Backfill

- First run pulls entire channel history for configured channels
- Dry-run mode available: scrape and calculate total file sizes before downloading anything

---

## 6. Content Deduplication

- **Hash**: SHA-256 of file contents, computed on ingest
- **Unique constraint**: `sha256` column on `media_item` is unique
- **Behavior**: If a file with the same hash already exists:
  - Do not store a second copy on disk
  - Create a new `media_source` record linking the new source context to the existing `media_item`
  - Update Meilisearch document with the additional source
- **Multiple source contexts**: A single media item can have sources from Slack, manual upload, different channels, etc. All source metadata is preserved and searchable.

---

## 7. API Endpoints

All endpoints require authentication (JWT cookie or API key).

### Search

| Method | Path | Scope | Description |
|---|---|---|---|
| POST | `/api/search` | read | Multi-index search with filters, facets, pagination |
| GET | `/api/tags` | read | List all tags with usage counts |

#### `POST /api/search` request body

```json
{
  "query": "drums",
  "media_types": ["audio", "video"],
  "filters": {
    "tags": ["percussive"],
    "source_channels": ["sample-sale"],
    "date_range": {"from": "2024-01-01", "to": "2024-12-31"},
    "reaction_count": {"min": 1},
    "tag_count": {"min": 0}
  },
  "sort": "created_at:desc",
  "page": 1,
  "per_page": 20
}
```

### Media CRUD

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/media/{id}` | read | Full metadata for a media item |
| GET | `/api/media/{id}/file` | read | Stream/download the file |
| GET | `/api/media/{id}/thumbnail` | read | Get thumbnail (images and video) |
| POST | `/api/media/upload` | write | Upload with metadata |
| PUT | `/api/media/{id}` | write | Update description/notes |
| DELETE | `/api/media/{id}` | admin | Delete media item and file |

### Tagging

| Method | Path | Scope | Description |
|---|---|---|---|
| POST | `/api/media/{id}/tags` | write | Add tags to a media item |
| DELETE | `/api/media/{id}/tags/{tag}` | write | Remove a tag |
| POST | `/api/media/batch/tags` | write | Add tags to multiple items |
| GET | `/api/tags/suggest` | read | Autocomplete suggestions from vocabulary |

### Batch Operations

| Method | Path | Scope | Description |
|---|---|---|---|
| POST | `/api/media/batch/delete` | admin | Delete multiple items |
| POST | `/api/media/batch/re-extract` | admin | Re-run extraction pipeline on selected items |
| POST | `/api/media/batch/export` | read | Generate zip of selected files for download |

### Ingest

| Method | Path | Scope | Description |
|---|---|---|---|
| POST | `/api/ingest/slack` | admin | Trigger Slack scrape (manual) |
| GET | `/api/ingest/slack/status` | admin | Check scrape status / last run |
| POST | `/api/ingest/slack/dry-run` | admin | Calculate sizes without downloading |

### API Keys

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/keys` | write | List your active API keys (prefix, label, dates) |
| POST | `/api/keys` | write | Generate new key with label and scope |
| DELETE | `/api/keys/{id}` | write | Revoke a key |

### Extraction Failures

| Method | Path | Scope | Description |
|---|---|---|---|
| GET | `/api/extraction-failures` | admin | List failed extractions for review |
| POST | `/api/extraction-failures/{id}/retry` | admin | Retry a specific failure |

---

## 8. Admin UI Pages

### `/admin/search` — Search Interface

- Search bar with typo-tolerant full-text search
- Filter sidebar: media type, source channel, date range, tags, reaction count, tag count
- Faceted results: counts per media type, per source channel, per tag
- Grid view (thumbnails) and list view (detail rows)
- Click-through to detail view
- Batch selection with checkboxes: tag, delete, re-extract, export

### `/admin/search/{id}` — Media Detail

- Full metadata display
- Inline preview: `<audio>` element for audio, `<video>` for video, `<img>` for images
- All source contexts listed (where it came from, who posted, message text, reactions)
- Tag editor with autocomplete from vocabulary
- Extraction status and any failure details
- Delete button

### `/admin/search/triage` — Tagging Triage Queue

- Surfaces untagged or low-tag-count items
- Filterable by media type, source channel
- Shows one item at a time with preview
- **Desktop**: Keyboard-driven workflow
  - Spacebar: play/pause audio and video
  - Arrow keys: navigate between items
  - Number keys / letter shortcuts: apply common tags
  - Enter: submit tags and advance to next item
  - Tab: skip without tagging
- **Mobile**: Touch-friendly interface
  - Swipe to navigate between items
  - Tag suggestion bar with tap-to-apply
  - Large touch targets
- Progress indicator: "47 of 312 untagged items reviewed"
- Skip and come back later — progress is per-user

### `/admin/api-keys` — API Key Management

- List active keys: label, prefix, scope, created_at, last_used_at
- Generate new key: label input, scope selector, displays key once
- Revoke button per key (immediate effect)

### `/admin/search/upload` — Manual Upload

- Drag-and-drop or file picker
- Multi-file upload
- Add tags and description during upload
- Shows extraction progress after upload

### `/admin/search/failures` — Extraction Failures

- List of failed extractions with error details
- Filter by extraction type (whisper, ffprobe, yt-dlp, etc.)
- Retry individual or batch retry
- Mark as resolved

---

## 9. Tag System

### Normalization

- All tags are lowercased and trimmed on save
- "Drums", "drums", "DRUMS" all resolve to `drums`
- Leading/trailing whitespace stripped
- Duplicate tags on the same item are silently ignored

### Vocabulary

- `tag_vocabulary` table tracks all known tags with usage counts
- New tags are added to the vocabulary automatically on first use
- Autocomplete suggests from existing vocabulary as user types
- No strict enforcement — members can create any tag

### Autocomplete Behavior

- Triggered after 1+ characters typed
- Sorted by usage count (most popular first)
- Shows usage count next to each suggestion
- Pressing Enter on a non-matching string creates a new tag

---

## 10. Testing Strategy

### Unit Tests

- Tag normalization and dedup logic
- SHA-256 content hashing and dedup behavior
- API key generation, hashing, verification
- Permission scope checks
- Meilisearch document building from SQLite records
- yt-dlp URL detection and metadata extraction parsing

### Integration Tests

- Full ingest pipeline: upload → extract → index → search
- Slack scraping: mock Slack API responses, verify dedup, source linking
- API key auth flow: create, use, revoke, verify revoked key fails
- Search queries: text search, filters, facets, multi-index
- Batch operations: tag, delete, re-extract
- Extraction failure handling: simulate failures, verify flagging, retry

### End-to-End Tests

- Upload a file via API → search for it → verify result
- Tag via triage UI → verify tag appears in search
- Revoke API key → verify subsequent requests fail

---

## 11. Future Enhancements (Not in v1)

- **Auto-captioning for images**: Via external API (Claude Vision or similar). Schema ready, `caption` field exists.
- **Acoustic tagging**: Automated classification of audio character (percussive, ambient, tonal, vocal, noisy). Via PANNs or librosa feature extraction. Schema ready, `acoustic_tags` field exists.
- **More Slack channels**: Scraper supports multiple channels, just add channel IDs to config.
- **Image similarity search**: Perceptual hashing or embedding-based similarity. Would require a vector store or Meilisearch vector search (experimental).
- **Video frame search**: Sample frames at intervals, run through image captioning pipeline. `thumbnail_path` exists, extend to frame gallery.
- **Advanced audio classification**: Genre detection, BPM estimation, key detection. Useful for sample-sale material.
- **Embedding-based search**: Store CLIP embeddings for images, audio embeddings for sound. Enable "find similar" functionality.
- **Slack real-time events**: Replace polling with `reaction_added`/`reaction_removed` webhooks for instant reaction updates. Requires publicly reachable endpoint.
- **Auto-tagging via LLM**: Use transcript/caption + source context to suggest tags automatically.

---

## 12. Deployment

### Meilisearch

```bash
# Install
curl -L https://install.meilisearch.com | sh
sudo mv meilisearch /usr/local/bin/

# Systemd unit at /etc/systemd/system/meilisearch.service
# Binds to 127.0.0.1:7700
# Master key via environment file at /etc/meilisearch.env
# Data directory: /var/lib/meilisearch/data

sudo systemctl enable meilisearch
sudo systemctl start meilisearch
```

### Dokku Storage Mount

```bash
# Create search media storage directory
sudo mkdir -p /var/lib/dokku/data/storage/au-supply-search

# Mount into container
dokku storage:mount au-supply /var/lib/dokku/data/storage/au-supply-search:/app/search-data
```

### Environment Variables (new)

| Variable | Description |
|---|---|
| `MEILISEARCH_URL` | `http://127.0.0.1:7700` |
| `MEILISEARCH_MASTER_KEY` | Master API key for Meilisearch |
| `SLACK_BOT_TOKEN` | Slack bot OAuth token |
| `SEARCH_MEDIA_DIR` | Path to search media storage (default: `/app/search-data`) |

### Docker Changes

- Add `faster-whisper` and dependencies to `pyproject.toml`
- Add `yt-dlp` to Dockerfile (install via pip, updated on each build)
- Add `librosa` or `scikit-learn` for dominant color extraction
- Ensure `ffmpeg` and `ffprobe` are available (already installed)

### Networking

FastAPI inside Docker needs to reach Meilisearch on the host's localhost. Options:
- Set `MEILISEARCH_URL` to `http://host.docker.internal:7700` (Docker Desktop)
- Or use `--network host` in Dokku config
- Or use the host gateway IP from within the container

Dokku approach: `dokku docker-options:add au-supply deploy "--add-host=host.docker.internal:host-gateway"`
