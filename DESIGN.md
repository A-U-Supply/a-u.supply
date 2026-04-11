# A-U.SUPPLY — Release Catalog System

## Design Document

```
Document No:  AU-DESIGN-2026-002
Revision:     DRAFT
Date:         2026-04-10
Prepared by:  Engineering Dept.
```

---

## Table of Contents

1. [Overview](#1-overview)
2. [Data Model](#2-data-model)
3. [Product Code Generation](#3-product-code-generation)
4. [API Design](#4-api-design)
5. [File Storage](#5-file-storage)
6. [Admin Upload Interface](#6-admin-upload-interface)
7. [Public Catalog & Release Pages](#7-public-catalog--release-pages)
8. [Persistent Audio Player](#8-persistent-audio-player)
9. [Open Questions](#9-open-questions)

---

## 1. Overview

A release catalog system for A-U.Supply that treats music releases as cataloged raw materials in a scientific warehouse. Product codes resemble ISO document numbers and Library of Congress call numbers. The system provides:

- SQLite-backed catalog with releases, tracks, entities, distribution links, and freeform metadata
- Auto-generated product codes in the existing A-U.Supply convention
- Admin upload interface (Astro pages + FastAPI endpoints)
- Public browsable catalog with release detail pages
- Persistent site-wide audio player that survives page navigation
- Full REST API for all operations

### Existing Stack

- **Frontend**: Astro 5.x (static SSG output, served by FastAPI)
- **Backend**: FastAPI on Python 3.12+, SQLAlchemy ORM, SQLite with WAL
- **Auth**: JWT in httpOnly cookies, role-based (admin/member)
- **Deployment**: Docker multi-stage build, Dokku

All new work extends this stack. No new frameworks.

---

## 2. Data Model

### 2.1 Entity-Relationship Diagram (text)

```
Entity 1---* Release 1---* Track
                |
                |---* DistributionLink
                |---* ReleaseMetadata
```

A release belongs to one entity (the manufacturer). An entity can have many releases. A release has many tracks, distribution links, and freeform metadata pairs.

### 2.2 Tables

#### `entities`

The artist/manufacturer/project names. These are created on the fly during release upload or picked from existing.

| Column       | Type         | Constraints                  | Notes |
|-------------|-------------|------------------------------|-------|
| id          | INTEGER      | PK, autoincrement            |       |
| name        | TEXT         | NOT NULL, UNIQUE             | Display name: "Complete", "BDO", "Level Navi", "Eonnot", etc. |
| slug        | TEXT         | NOT NULL, UNIQUE             | URL-safe: "complete", "bdo", "level-navi" |
| description | TEXT         | NULLABLE                     | Optional short description |
| created_at  | DATETIME     | NOT NULL, default now(utc)   |       |

**Note**: An entity is a project/alias name, not a person. Complete is an entity. BDO is an entity. "Unreliable Metrics, Current Occupant, A-XYZ" would either be one entity with a compound name or three entities linked to the same release — see [Open Question 1](#q1-multi-entity-releases).

#### `releases`

| Column          | Type         | Constraints                      | Notes |
|----------------|-------------|----------------------------------|-------|
| id             | INTEGER      | PK, autoincrement                |       |
| product_code   | TEXT         | NOT NULL, UNIQUE, indexed        | Auto-generated, editable. See §3. |
| title          | TEXT         | NOT NULL                         |       |
| entity_id      | INTEGER      | FK → entities.id, NOT NULL       | The manufacturer |
| release_date   | DATE         | NULLABLE                         | Date of manufacture. Null for drafts without a date yet. |
| cover_art_path | TEXT         | NULLABLE                         | Relative path under media dir |
| status         | TEXT         | NOT NULL, default "draft"        | "draft" or "published" |
| description    | TEXT         | NULLABLE                         | Liner notes, long-form text |
| format_specs   | TEXT         | NULLABLE                         | "Digital (YouTube)", "Digital (Bandcamp, 24-bit/44.1kHz)", etc. |
| created_by     | INTEGER      | FK → users.id, NOT NULL          | Who created it |
| created_at     | DATETIME     | NOT NULL, default now(utc)       |       |
| updated_at     | DATETIME     | NOT NULL, default now(utc), onupdate now(utc) |   |

**Access rule**: Any admin can edit any release regardless of who created it. `created_by` is for attribution/audit, not access control.

#### `tracks`

| Column          | Type         | Constraints                      | Notes |
|----------------|-------------|----------------------------------|-------|
| id             | INTEGER      | PK, autoincrement                |       |
| release_id     | INTEGER      | FK → releases.id, NOT NULL, ON DELETE CASCADE |  |
| title          | TEXT         | NOT NULL                         |       |
| track_number   | INTEGER      | NOT NULL                         | 1-indexed, used for ordering |
| audio_file_path| TEXT         | NULLABLE                         | Relative path under media dir. Null if not yet uploaded. |
| duration_seconds | REAL       | NULLABLE                         | Populated on upload via ffprobe or similar |
| created_at     | DATETIME     | NOT NULL, default now(utc)       |       |

**Unique constraint**: (release_id, track_number) — no duplicate track numbers within a release.

#### `distribution_links`

Per-release links to external distribution channels.

| Column       | Type         | Constraints                      | Notes |
|-------------|-------------|----------------------------------|-------|
| id          | INTEGER      | PK, autoincrement                |       |
| release_id  | INTEGER      | FK → releases.id, NOT NULL, ON DELETE CASCADE |  |
| platform    | TEXT         | NOT NULL                         | "bandcamp", "archive.org", "soundcloud", "youtube", or freeform |
| url         | TEXT         | NOT NULL                         |       |
| label       | TEXT         | NULLABLE                         | Optional display label override |

#### `release_metadata`

Freeform key-value pairs for anything that doesn't fit the schema.

| Column       | Type         | Constraints                      | Notes |
|-------------|-------------|----------------------------------|-------|
| id          | INTEGER      | PK, autoincrement                |       |
| release_id  | INTEGER      | FK → releases.id, NOT NULL, ON DELETE CASCADE |  |
| key         | TEXT         | NOT NULL                         | "credits", "personnel", "equipment", "recording_location", etc. |
| value       | TEXT         | NOT NULL                         |       |
| sort_order  | INTEGER      | NOT NULL, default 0              | For display ordering |

**Unique constraint**: (release_id, key) — one value per key per release. If you need multiple values for the same key, use a single value with newlines or structured text.

### 2.3 SQLAlchemy Models

New models go in `models.py` alongside the existing `User` model. Relationships:

```python
class Entity(Base):
    __tablename__ = "entities"
    # ... columns ...
    releases = relationship("Release", back_populates="entity")

class Release(Base):
    __tablename__ = "releases"
    # ... columns ...
    entity = relationship("Entity", back_populates="releases")
    tracks = relationship("Track", back_populates="release", order_by="Track.track_number", cascade="all, delete-orphan")
    distribution_links = relationship("DistributionLink", back_populates="release", cascade="all, delete-orphan")
    metadata_pairs = relationship("ReleaseMetadata", back_populates="release", cascade="all, delete-orphan")
    creator = relationship("User")

class Track(Base):
    __tablename__ = "tracks"
    # ... columns ...
    release = relationship("Release", back_populates="tracks")

class DistributionLink(Base):
    __tablename__ = "distribution_links"
    # ... columns ...
    release = relationship("Release", back_populates="distribution_links")

class ReleaseMetadata(Base):
    __tablename__ = "release_metadata"
    # ... columns ...
    release = relationship("Release", back_populates="metadata_pairs")
```

### 2.4 Migration Strategy

Since this is a new set of tables (not modifying `users`), we can use `Base.metadata.create_all()` — it's additive and won't touch existing tables. No migration tool needed yet.

---

## 3. Product Code Generation

### 3.1 Existing Codes in the Catalog

Studying the existing catalog reveals three distinct code styles:

| Code | Release | Style |
|------|---------|-------|
| `A-U# 0` | *~~Immelerria~~* | Simple sequential with label prefix |
| `A-U# 01` | *Erkind NOS* | Zero-padded sequential |
| `A-U# M5497.H37` | *How How Things are Made are Made* | Library of Congress call number pastiche |
| `AU-2026-DA-001` | *Law Bale Straw Wonder / Tomato Sink Cloud Tag* | ISO document number with category code |
| `AU-REF-2026-001` | Press kit (company) | Document reference number |
| `AU-PB-2026-001` | Press kit (product bulletin) | Product bulletin number |

The codes are intentionally inconsistent — they look like they emerged from overlapping bureaucratic systems over time. That's the aesthetic.

### 3.2 Generation System

The auto-generator produces codes in the **ISO document style** as the default, since that's the most recent convention, but the code is always editable so the uploader can switch to a different style.

**Default format**: `AU-{YYYY}-{CAT}-{SEQ}`

Where:
- `AU` — fixed prefix (Audio Units)
- `YYYY` — release year
- `CAT` — two-letter category code (see below)
- `SEQ` — three-digit sequence number, zero-padded, per year

**Category codes**:

| Code | Meaning | When to use |
|------|---------|------------|
| `LP` | Long play | Albums (5+ tracks) |
| `EP` | Extended play | EPs (2-4 tracks) |
| `SG` | Single | Singles (1 track) |
| `DA` | Double album | Multi-disc releases |
| `CX` | Compilation | Compilations, mixtapes |
| `AR` | Archive | Reissues, archival material |
| `MX` | Mixed | Anything that defies categorization |

**Sequence logic**: Query `SELECT COUNT(*) FROM releases WHERE product_code LIKE 'AU-{YYYY}-%'` and increment. This means the sequence counts all codes for that year, not per category. If the year already has AU-2026-DA-001 and AU-2026-LP-002, the next one is AU-2026-XX-003.

**Examples of generated codes**:
- `AU-2026-LP-004` — Fourth release of 2026, an album
- `AU-2024-SG-001` — First cataloged 2024 release, a single
- `AU-2020-AR-001` — Archival entry for a 2020 release

**Editability**: The generated code is a suggestion. The uploader can replace it with anything — `A-U# M5497.H37`, a hash fragment, a call number, whatever fits. The only constraint is uniqueness.

### 3.3 Implementation

```python
def generate_product_code(db: Session, year: int, category: str = "MX") -> str:
    """Generate the next product code for the given year and category."""
    pattern = f"AU-{year}-%"
    count = db.query(Release).filter(Release.product_code.like(pattern)).count()
    seq = count + 1
    return f"AU-{year}-{category}-{seq:03d}"
```

The API endpoint for creating a release calls this if no product_code is provided, but accepts a custom one if given.

---

## 4. API Design

All endpoints under `/api`. Write endpoints require auth (JWT cookie). Published releases are publicly readable. Draft releases require auth.

### 4.1 Releases

#### `POST /api/releases`
Create a new release (draft by default).

**Auth**: Required (any authenticated user).

**Request body**:
```json
{
  "title": "Law Bale Straw Wonder / Tomato Sink Cloud Tag",
  "entity_id": 1,
  "product_code": null,
  "release_date": "2026-03-22",
  "description": "Double album. Disc 1: Law Bale Straw Wonder...",
  "format_specs": "Digital (YouTube)",
  "status": "draft",
  "distribution_links": [
    {"platform": "youtube", "url": "https://www.youtube.com/..."}
  ],
  "metadata": [
    {"key": "personnel", "value": "number 4, NoNameSteak, Ancients"},
    {"key": "recording_location", "value": "Minneapolis, MN"}
  ]
}
```

If `product_code` is null/omitted, auto-generate one based on the release date year and track count (to guess category). Return the generated code in the response.

**Response**: `201 Created` with full release object including generated product_code.

#### `GET /api/releases`
List releases with filtering.

**Auth**: Public for published releases. Auth required to see drafts (returns all drafts for any authenticated user, since all admins can see all).

**Query params**:
- `status` — "published", "draft", or "all" (default: "published" for public, "all" for authed)
- `entity` — filter by entity slug
- `year` — filter by release year
- `sort` — "date_desc" (default), "date_asc", "title", "code"
- `page`, `per_page` — pagination (default 50 per page)

**Response**: Paginated list of release summaries (no tracks, no metadata — those are on the detail endpoint).

```json
{
  "releases": [
    {
      "product_code": "AU-2026-DA-001",
      "title": "Law Bale Straw Wonder / Tomato Sink Cloud Tag",
      "entity": {"id": 1, "name": "Complete", "slug": "complete"},
      "release_date": "2026-03-22",
      "cover_art_url": "/api/releases/AU-2026-DA-001/cover",
      "status": "published",
      "track_count": 24,
      "total_duration_seconds": 3738.0
    }
  ],
  "total": 12,
  "page": 1,
  "per_page": 50
}
```

#### `GET /api/releases/{product_code}`
Get full release detail.

**Auth**: Public if published. Auth required if draft.

**Response**: Full release object with tracks, distribution links, and metadata.

```json
{
  "product_code": "AU-2026-DA-001",
  "title": "Law Bale Straw Wonder / Tomato Sink Cloud Tag",
  "entity": {"id": 1, "name": "Complete", "slug": "complete"},
  "release_date": "2026-03-22",
  "cover_art_url": "/api/releases/AU-2026-DA-001/cover",
  "status": "published",
  "description": "...",
  "format_specs": "Digital (YouTube)",
  "created_by": {"id": 1, "name": "tube"},
  "created_at": "2026-04-10T...",
  "updated_at": "2026-04-10T...",
  "tracks": [
    {
      "id": 1,
      "track_number": 1,
      "title": "Heat",
      "duration_seconds": 198.5,
      "stream_url": "/api/releases/AU-2026-DA-001/tracks/1/stream"
    }
  ],
  "distribution_links": [
    {"id": 1, "platform": "youtube", "url": "https://...", "label": null}
  ],
  "metadata": [
    {"id": 1, "key": "personnel", "value": "number 4, NoNameSteak, Ancients"}
  ]
}
```

#### `PUT /api/releases/{product_code}`
Update release metadata (title, description, entity, date, format_specs, product_code, distribution links, freeform metadata).

**Auth**: Required (any admin).

Supports partial updates — only provided fields are changed. Distribution links and metadata are replaced wholesale if provided (send the full list).

Product code changes: if the new code differs from the old one, rename the media directory too.

#### `POST /api/releases/{product_code}/publish`
Set status to "published".

**Auth**: Required (any admin).

#### `POST /api/releases/{product_code}/unpublish`
Set status back to "draft".

**Auth**: Required (any admin).

#### `DELETE /api/releases/{product_code}`
Delete a release and all associated data (tracks, links, metadata, files).

**Auth**: Required (any admin).

### 4.2 Tracks

#### `POST /api/releases/{product_code}/tracks`
Upload one or more audio files. Accepts `multipart/form-data`.

**Auth**: Required (any admin).

**Form fields**:
- `files` — one or more audio files (FLAC, WAV, MP3, OGG, etc.)
- `titles` — JSON array of track titles (optional; defaults to filename without extension)

Tracks are appended after existing tracks. `track_number` is auto-assigned based on upload order. Duration is extracted server-side via `ffprobe`.

**Response**: List of created track objects.

#### `DELETE /api/releases/{product_code}/tracks/{track_id}`
Remove a track and its audio file. Remaining tracks are renumbered.

**Auth**: Required (any admin).

#### `PUT /api/releases/{product_code}/tracks/reorder`
Reorder tracks.

**Auth**: Required (any admin).

**Request body**:
```json
{
  "track_ids": [3, 1, 2, 5, 4]
}
```

The array contains all track IDs in the desired order. `track_number` is reassigned sequentially.

#### `GET /api/releases/{product_code}/tracks/{track_id}/stream`
Stream an audio file.

**Auth**: Public if the release is published. Auth required if draft.

Returns the audio file with appropriate `Content-Type`, `Content-Length`, and `Accept-Ranges` headers for seeking support. Uses `FileResponse` or streaming response for large files.

### 4.3 Cover Art

#### `POST /api/releases/{product_code}/cover`
Upload cover art. Accepts `multipart/form-data` with a single image file.

**Auth**: Required (any admin).

Replaces existing cover art if present. Stores as `cover.{ext}` in the release's media directory.

#### `GET /api/releases/{product_code}/cover`
Serve cover art.

**Auth**: Public if published, auth required if draft.

Returns the image file. Returns 404 if no cover art.

### 4.4 Entities

#### `GET /api/entities`
List all entities, sorted alphabetically.

**Auth**: Public.

**Response**:
```json
[
  {"id": 1, "name": "Complete", "slug": "complete", "release_count": 3},
  {"id": 2, "name": "BDO", "slug": "bdo", "release_count": 1}
]
```

#### `POST /api/entities`
Create a new entity.

**Auth**: Required (any authenticated user).

**Request body**:
```json
{
  "name": "Level Navi",
  "description": null
}
```

Slug is auto-generated from name. Returns 409 if name already exists.

#### `PUT /api/entities/{entity_id}`
Update entity name/description.

**Auth**: Required (any admin).

#### `DELETE /api/entities/{entity_id}`
Delete an entity. Fails if any releases reference it.

**Auth**: Required (any admin).

### 4.5 Product Code Preview

#### `GET /api/releases/next-code?year={YYYY}&category={CAT}`
Preview the next auto-generated product code without creating a release. Useful for the upload form.

**Auth**: Required.

**Response**:
```json
{
  "product_code": "AU-2026-LP-004"
}
```

---

## 5. File Storage

### 5.1 Directory Structure

```
/srv/media/
  releases/
    AU-2026-DA-001/
      cover.jpg
      tracks/
        01-heat.flac
        02-leverage.flac
        03-sikabidi.mp3
        ...
    AU-2020-AR-001/
      cover.png
      tracks/
        01-immelerria.mp3
    ...
```

**Conventions**:
- Release directories named by product code
- Cover art: `cover.{original_extension}`
- Tracks: `{two-digit-number}-{slugified-title}.{original_extension}` — e.g., `01-heat.flac`
- If the product code is changed, the directory is renamed

### 5.2 Configuration

Media root path is configurable via environment variable:

```
MEDIA_DIR=/srv/media
```

Default: `/srv/media` in production, `./data/media` in development.

### 5.3 Serving

All media is served through API endpoints, not as static files. This allows:
- Auth checks on draft releases
- Range request support for audio seeking
- Future CDN integration without changing URLs
- Logging/analytics on plays

The streaming endpoint uses FastAPI's `FileResponse` which supports range requests out of the box.

### 5.4 Accepted Formats

**Audio**: FLAC, WAV, MP3, OGG, AAC, M4A, AIFF
**Images**: JPG, PNG, WEBP, GIF

No transcoding on upload — files stored as-is. The browser handles playback format support. If we need transcoding later (e.g., generating MP3 previews from FLAC), that's a future feature.

### 5.5 Duration Extraction

On track upload, run `ffprobe` to extract duration:

```python
import subprocess, json

def get_duration(path: str) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    return None
```

**Dependency**: Requires `ffprobe` (part of ffmpeg) installed in the Docker image.

---

## 6. Admin Upload Interface

### 6.1 Page Structure

New Astro pages under `/admin/`:

```
src/pages/admin/
  catalog/
    index.astro        — Release list (all drafts + published)
    new.astro          — Multi-step release creation
    [code].astro       — Edit existing release
    [code]/preview.astro — Preview release as it would appear publicly
```

All use the existing `Admin.astro` layout (sidebar nav, auth check).

### 6.2 Multi-Step Upload Flow

The creation form is a single page with collapsible sections, not a wizard with separate URLs. All state is held client-side until final save. Sections:

**Step 1 — Metadata**
- Title (text input)
- Entity (searchable dropdown with "Create new..." option at the bottom)
  - Selecting "Create new..." opens an inline form: name + optional description
  - On save, the new entity is created via API and selected
- Release date (date picker, optional for drafts)
- Format specs (text input, freeform)
- Product code (auto-populated via `/api/releases/next-code`, editable text input)
  - Category dropdown next to it to regenerate the code with a different category prefix
- Description / liner notes (textarea, supports plain text or markdown)

**Step 2 — Cover Art**
- Drag-and-drop zone or click to browse
- Image preview after upload
- Replace button if art already exists

**Step 3 — Audio Files**
- Drag-and-drop zone (accepts multiple files)
- Upload progress bars per file
- After upload, shows track list with:
  - Track number (auto-assigned)
  - Title (editable, defaults to filename)
  - Duration (extracted server-side, displayed as mm:ss)
  - Delete button (X)
- Drag-and-drop reorder on the track list (grab handle on left side)

**Step 4 — Distribution Links**
- List of link rows, each with:
  - Platform dropdown (Bandcamp, Archive.org, SoundCloud, YouTube, Other)
  - URL text input
  - Optional label override
  - Delete button
- "Add link" button at the bottom

**Step 5 — Freeform Metadata**
- List of key-value rows, each with:
  - Key text input
  - Value text input (or textarea for longer values)
  - Delete button
- "Add field" button at the bottom
- Common key suggestions shown as chips/buttons: "credits", "personnel", "equipment", "recording_location", "notes"

**Step 6 — Review & Save**
- Summary of all entered data
- Two buttons: "Save as Draft" and "Publish"

### 6.3 Release List (catalog/index.astro)

Table/list of all releases with:
- Product code
- Title
- Entity name
- Status badge (draft/published)
- Release date
- Track count
- Created by
- Actions: Edit, Preview, Publish/Unpublish, Delete (with confirmation)

Filterable by status (all/draft/published). Sortable by date, title, code.

### 6.4 Edit Page (catalog/[code].astro)

Same layout as the creation form, pre-populated with existing data. Changes are saved via PUT on each section or via a single "Save" button.

### 6.5 Implementation Notes

- All upload/interactive UI is client-side JavaScript (Astro `<script>` blocks or a small framework island if needed)
- File uploads use `fetch()` with `FormData` to the API endpoints
- Drag-and-drop reorder uses the HTML Drag and Drop API or a small library (SortableJS is 10KB gzipped and dependency-free)
- No need for a full SPA framework — vanilla JS with fetch is sufficient for this complexity level

---

## 7. Public Catalog & Release Pages

### 7.1 Page Structure

```
src/pages/
  catalog/
    index.astro        — Browsable grid of published releases
    [code].astro       — Release detail page
```

These are public, no auth required.

### 7.2 Catalog Page (catalog/index.astro)

**Layout**: Grid of release cards. Each card shows:
- Cover art (square, with fallback placeholder if none)
- Product code (monospace, small, above the title)
- Title
- Entity/manufacturer name
- Release date (formatted as industrial date: "2026-03-22")
- Track count and total duration

**Interactions**:
- Click card → release detail page
- Filter bar at top: by entity (dropdown), by year (dropdown)
- Sort: by date (default, newest first), by title, by code

**Aesthetic**: Industrial catalog aesthetic — monospace type, thin borders, amber accents, plenty of whitespace. Each card looks like a product listing in a parts catalog. No rounded corners. No gradients.

**Data loading**: Since Astro builds static pages, the catalog page either:
- (a) Fetches from the API at build time (SSG) — requires rebuild on publish, or
- (b) Fetches client-side on page load (CSR) — always current

**Recommendation**: Client-side fetch. The catalog changes frequently (new releases, publish/unpublish) and we don't want to trigger a full Astro rebuild for every catalog change. The page loads a skeleton, fetches `GET /api/releases`, and renders the grid.

### 7.3 Release Detail Page (catalog/[code].astro)

Also client-side rendered — fetches `GET /api/releases/{code}` on load.

**Layout**: Styled like a product specification sheet / press kit page. Sections:

**Header**:
```
A-U.SUPPLY — AUDIO UNITS DIVISION

PRODUCT SPECIFICATION

Product Code:  AU-2026-DA-001
Product Name:  Law Bale Straw Wonder / Tomato Sink Cloud Tag
Manufacturer:  Complete
Date:          2026-03-22
Format:        Digital (YouTube)
```

**Cover Art**: Large image with thin border, product code caption underneath.

**Track Listing**: Table with columns:
- Item No. (track number)
- Part Name (track title)
- Duration
- Play button (▶) per track

"PLAY ALL" button above the table — sends entire track list to the persistent player.

**Description / Liner Notes**: Rendered below the track listing. Monospace text block with the industrial document feel.

**Distribution Network**: Links displayed as a simple table:
```
DISTRIBUTION CHANNEL     URL
Bandcamp                 https://ausupply.bandcamp.com/...
YouTube                  https://www.youtube.com/...
```

**Additional Specifications**: Freeform metadata displayed as a key-value table:
```
FIELD                    VALUE
Personnel                number 4, NoNameSteak, Ancients
Recording Location       Minneapolis, MN
Equipment                [freeform]
```

**Footer**: Document number, date, classification.

---

## 8. Persistent Audio Player

### 8.1 Architecture

The player is a site-wide component that lives outside Astro's page routing. It must survive page navigation without resetting playback.

**Approach**: Astro View Transitions API.

Astro's View Transitions intercept navigation and swap page content via morphing/animation. Elements with `transition:persist` survive the swap — they are not removed or re-rendered.

The player component:
1. Lives in the base layout (`Base.astro` or a shared layout used by both public and admin pages)
2. Has `transition:persist` so it is not destroyed on navigation
3. Contains an `<audio>` element and all player UI
4. Manages its own state (current queue, current track, playback position) in JavaScript

```astro
<!-- In layout -->
<div id="player" transition:persist>
  <audio id="player-audio"></audio>
  <!-- Player UI -->
</div>
```

### 8.2 Player UI

Fixed bottom bar, always visible when a track is loaded. Hidden initially until the user plays something.

```
┌─────────────────────────────────────────────────────────────────┐
│ [cover] Track Title — Release Title          ◄◄  ▶/❚❚  ►►     │
│         Entity Name                    ───●────────  🔊 ━━━━   │
│                                        0:42 / 3:18             │
└─────────────────────────────────────────────────────────────────┘
```

**Controls**:
- Play/Pause toggle
- Previous / Next track
- Scrubber (range input styled as a thin line)
- Volume control
- Current time / total time display
- Cover art thumbnail (click → navigate to release page)
- Track title + release title
- Shuffle toggle (catalog shuffle mode)

### 8.3 Playback Modes

**Album queue**: When the user clicks "Play All" on a release, the player loads all tracks from that release in order. Clicking a single track on a release also queues the full album starting from that track.

**Catalog shuffle**: Toggle that shuffles all published tracks across the entire catalog. Implementation:
1. Fetch `GET /api/releases?status=published` to get all releases
2. Collect all track stream URLs
3. Shuffle the list
4. Play sequentially through the shuffled list

### 8.4 State Management

Player state is held in a JavaScript singleton (plain object or class) that persists because the player DOM element persists via View Transitions.

```javascript
const playerState = {
  queue: [],          // Array of {track_id, title, release_title, release_code, stream_url, cover_url, duration}
  currentIndex: 0,
  isPlaying: false,
  volume: 1.0,
  shuffleMode: false,
};
```

State does not need to survive a hard page reload (full browser refresh). If the user does a hard refresh, the player resets. This is acceptable — the player is a convenience, not a critical feature.

### 8.5 Integration with Release Pages

Release pages dispatch custom events to communicate with the player:

```javascript
// From a release detail page:
document.dispatchEvent(new CustomEvent('player:queue', {
  detail: {
    tracks: [...],
    startIndex: 0
  }
}));
```

The player listens for these events regardless of which page dispatched them.

### 8.6 Admin Pages

The player should also be available in the admin layout, so admins can preview tracks while editing. This means the player lives in a shared base that both `Base.astro` and `Admin.astro` include (or both layouts are merged into one with conditional sidebar).

---

## 9. Open Questions

### Q1: Multi-Entity Releases

*A Deper[ ]alized Ratio* is credited to "Unreliable Metrics, Current Occupant, A-XYZ" — three separate project names on one release. *A-U Quality Track Supply* is credited to "Various."

**Options**:
- (a) **Single entity_id**: Store compound credits as a single entity name: "Unreliable Metrics, Current Occupant, A-XYZ". Simple. The entity table has weird entries but that matches the catalog's aesthetic.
- (b) **Many-to-many**: A join table `release_entities` linking releases to multiple entities. More correct, but adds complexity for a case that's rare in this catalog.
- (c) **Primary entity + freeform credit**: `entity_id` points to the primary/first entity, and a freeform metadata field "additional_artists" holds the rest.

**Current recommendation**: Option (a). The entity names are already semi-fictional aliases — a compound name like "Unreliable Metrics, Current Occupant, A-XYZ" is no weirder than "Complete Rx" or "Semi-Truck Driver." Keep it simple.

### Q2: Historical Catalog Import

The existing catalog (11 releases from 2020-2026) should be importable. Two options:
- (a) **Manual entry**: Admin uploads each one through the UI. Good for testing the upload flow.
- (b) **Seed script**: A Python script that reads the reference doc data and creates releases via the API or direct DB inserts.

Should we build a seed script, or just import them manually through the UI once it's built?

### Q3: Audio File Source for Historical Releases

The 2020 releases are on Archive.org, the 2022-2024 ones on Bandcamp and YouTube. Do we:
- (a) Download and store the audio files locally on the server?
- (b) Just store distribution links and skip local audio for historical releases?
- (c) Allow releases with no local audio (tracks with null audio_file_path) that link out to external platforms instead?

Option (c) seems most flexible — a release can exist in the catalog with full metadata but point to external platforms for actual playback. The player would only work for releases with local audio.

### Q4: Image Handling

Do we need thumbnails/resized versions of cover art, or serve the original at all sizes? Large album art (the reference doc mentions 8382x8382 AEDAS art) would be heavy for the catalog grid.

**Recommendation**: Generate a thumbnail (e.g., 400x400) on upload and store alongside the original. Serve the thumbnail for catalog grid, original for detail page.

**Dependency**: Requires Pillow or similar in the Docker image.

### Q5: Catalog Page Rendering Strategy

Discussed in §7.2 — client-side rendering vs. SSG. CSR is recommended because:
- No rebuild needed on publish/unpublish
- Filtering and sorting work without page reloads
- The catalog data is small enough that the API call is fast

But this means the catalog page has no SEO content on initial load. Is that a concern? For a label like A-U.Supply, probably not — but worth flagging.

### Q6: Player Framework

The player UI is the most interactive component. Options:
- (a) **Vanilla JS**: No dependencies. Matches the project's anti-bloat aesthetic. More code to write for drag interactions and state management.
- (b) **Preact island**: Tiny (3KB) React-compatible framework. Astro has first-class Preact support. Makes the player's reactive UI easier to build. Adds a dependency but a very small one.
- (c) **Svelte island**: Another Astro-supported option. Compiles away, so zero runtime. Good DX for reactive UI.

**Current recommendation**: Vanilla JS. The player's interactivity is well-bounded (play/pause, next/prev, scrubber, volume) and doesn't need a component framework. It's a single persistent DOM element, not a tree of components.

### Q7: Existing Product Images

The `public/assets/` directory already has cover art images (lbsw.jpg, tsct.jpg, erkind-nos.jpg, etc.) used somewhere in the old site. When we import historical releases, should these images be moved into the new media directory structure, or left where they are?

**Recommendation**: Copy them into `/srv/media/releases/{code}/cover.{ext}` as part of the import. The old paths can stay for backward compatibility with the legacy site.

---

## Appendix A: Sidebar Navigation Updates

Add to the admin sidebar (in `Admin.astro`):

```
Catalog          → /admin/catalog
  New Release    → /admin/catalog/new
Dashboard        → /admin/dashboard        (existing)
Files            → /admin/files            (existing, placeholder)
Settings         → /admin/settings         (existing)
```

## Appendix B: Dependencies to Add

**Python (pyproject.toml)**:
- `python-multipart` — for file upload handling in FastAPI
- `Pillow` — for thumbnail generation (if Q4 is yes)

**System (Dockerfile)**:
- `ffmpeg` / `ffprobe` — for audio duration extraction

**JavaScript (package.json)**:
- `sortablejs` — for drag-and-drop track reordering (optional, could use native DnD)

## Appendix C: Environment Variables

New variables for this feature:

| Variable | Default | Description |
|----------|---------|-------------|
| `MEDIA_DIR` | `/srv/media` (prod), `./data/media` (dev) | Root directory for uploaded media files |

## Appendix D: URL Scheme Summary

**Public pages**:
- `/catalog` — browsable catalog grid
- `/catalog/{product_code}` — release detail page

**Admin pages**:
- `/admin/catalog` — release management list
- `/admin/catalog/new` — create new release
- `/admin/catalog/{product_code}` — edit release

**API endpoints**:
- `GET /api/releases` — list releases
- `POST /api/releases` — create release
- `GET /api/releases/{code}` — get release detail
- `PUT /api/releases/{code}` — update release
- `DELETE /api/releases/{code}` — delete release
- `POST /api/releases/{code}/publish` — publish
- `POST /api/releases/{code}/unpublish` — unpublish
- `POST /api/releases/{code}/tracks` — upload tracks
- `DELETE /api/releases/{code}/tracks/{id}` — delete track
- `PUT /api/releases/{code}/tracks/reorder` — reorder tracks
- `GET /api/releases/{code}/tracks/{id}/stream` — stream audio
- `POST /api/releases/{code}/cover` — upload cover art
- `GET /api/releases/{code}/cover` — serve cover art
- `GET /api/releases/next-code` — preview next product code
- `GET /api/entities` — list entities
- `POST /api/entities` — create entity
- `PUT /api/entities/{id}` — update entity
- `DELETE /api/entities/{id}` — delete entity
