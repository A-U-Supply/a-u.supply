# Latents — session bundles, extraction & Marginalia (timestamped comments/markers)

**Branch:** `latents-sessions-marginalia` (plan PR #555) → implementation PRs linked below
**Status:** shipped — PR 1 [#556](https://github.com/A-U-Supply/a-u.supply/pull/556), PR 2 [#557](https://github.com/A-U-Supply/a-u.supply/pull/557)+[#559](https://github.com/A-U-Supply/a-u.supply/pull/559), PR 3 [#558](https://github.com/A-U-Supply/a-u.supply/pull/558), all live 2026-07-23. Deferred: AI listening [#554](https://github.com/A-U-Supply/a-u.supply/issues/554).

Implementation PRs (in order):

1. **PR 1** — Session bundle upload + WAV extraction + text-pass AI tagging
2. **PR 2** — MIDI/cue harvesting + `annotations` store + `marginalia` index
3. **PR 3** — Timestamped comments + player waveform UI
4. Deferred — AI listening enrichment (see "Deferred: AI listening" + [#554](https://github.com/A-U-Supply/a-u.supply/issues/554))

Related: [`2026-05-15-latents.md`](2026-05-15-latents.md) (Latents v1).

## Goal

Make Logic Pro sessions first-class in Latents: drag a `.logicx` bundle onto a slot, have its audio/MIDI/markers harvested into playable, taggable, searchable peers — and give every audio file SoundCloud-style timestamped comments and markers that render in the persistent player.

## Naming

| Thing | Name |
|---|---|
| Feature + new Meilisearch index (7th) | **Marginalia** — notes in the margin of the timeline |
| DB table | `annotations` |
| Annotation kinds | `comment` (human, has body) · `cue` (imported/AI marker, label only) |
| Annotation sources | `user` · `wav_cue` · `aiff_cue` · `midi` · `logic` · `ai_listen` (reserved) |
| UI copy | plain language — "Comments", "Markers" (same style as Emulsion vs "upload") |
| Uploaded DAW bundle | a **bundle** (session media type, existing) |

Glossary entries land with PR 2.

## Decisions (locked with the user)

1. **Upload UX.** A `.logicx` is a macOS directory bundle — Finder shows one file; the filesystem (and browsers) see a directory. The user experience must treat it as one file: drag the `.logicx` onto the uploader and everything else is automatic. Client-side, the uploader walks the dropped bundle via `webkitGetAsEntry()` and uploads each contained file as a separate streamed request (parallel, per-file retry, one aggregate progress bar). No client-side zipping (multi-GB zip in a browser tab is a memory bomb). Pre-zipped bundles (`.logicx.zip` etc.) remain accepted on the normal upload path and are unpacked server-side.
2. **Extracted files are peers.** Every harvested audio/MIDI file becomes a full Emulsion media item attached to the **same slot/Latent** as the parent bundle: playable, taggable, searchable, star-able, downloadable. Session tile shows "N extracted files"; children show "from X.logicx".
3. **Bidirectional provenance.** `media_items.parent_media_item_id` links children → parent; parent → children via query. Re-uploading a new bundle version creates a new, independent extraction set (never mutates the old one).
4. **Assume multi-GB.** All new upload paths stream to disk. `MAX_UPLOAD_PART_BYTES` env cap (default 2 GB) per part. Dokku nginx `client_max_body_size` bump documented in deployment notes.
5. **Markers: solid sources + experimental Logic parse.** WAV `cue `/`LIST adtl` chunks, AIFF `MARK` chunks, and MIDI marker meta-events are parsed with standard tooling. A best-effort Logic `ProjectData` parser ships behind `SESSION_LOGIC_PARSE=1`, failures logged, never blocks upload.
6. **MIDI is a 5th media type.** `media_type='midi'`, Emulsion-routed, with `media_midi_meta` (tempo, time sig, track names, note count, duration, preview path) and an OGG preview synthesized via the existing `pukebox_synth.py` so MIDIs play in the player.
7. **Imported cues are editable.** One table for human comments and imported cues. Any edit sets `touched_by_user`; re-extraction only adds missing imports and never modifies or deletes touched rows.
8. **Storage architecture.** SQLite is the system of record for annotations; a new `marginalia` Meilisearch index holds a rebuildable projection (same pattern as votes). `manage.py reindex` covers it. Lemmy is not involved.
9. **Comments are robust.** Flat comments + one-level replies, resolve/archive, author edits own (admin deletes any), position editing, Slack notification on create, `[mm:ss]` seek-links in bodies.
10. **Player UI.** Expandable now-playing panel: server-generated peaks → waveform canvas + annotation avatars, click-to-seek, comment composer at current time. Collapsed bar shows ticks on the seek slider. `player:queue` gains optional `start_time`.
11. **AI: text pass now, listening deferred.** DeepSeek text tagging (WIP-bounce prompt, ~$0.0001/file) runs on upload for Latents audio. Every-stem audio-LLM listening is deferred — full design below, tracked in a dedicated issue; the schema reserves `source='ai_listen'`.
12. **Mobile-first + accessible.** All new UI designed at ≤640px first: ≥44px targets, bottom-sheet threads, no hover-only affordances. Keyboard navigation and ARIA throughout. Known gaps in touched components get fixed (Uploader dropzone keyboard, player transport aria-labels, loose-files play button).

## Background: what exists today (survey)

- `session` media type + `.logicx` extension detection (`SESSION_EXT_TO_TOOL`) + `media_session_meta` + Emulsion routing already exist (Latents v1).
- **No extraction runs at upload time** — uploaded audio has no duration/metadata until a manual batch re-extract. PR 1 wires `run_extraction_async` into the upload path.
- Uploads buffer whole files in memory with no size cap — new bundle endpoints stream; the legacy single-file endpoint keeps its behavior.
- `pretty_midi` + `server/pukebox_synth.py` (numpy/scipy MIDI→audio) already in-repo.
- Waveform peaks exist only client-side in Ossuary (`src/lib/ossuary/source.ts` `computePeaks`); the player has no waveform and no marker concept.
- Comments = Lemmy threads anchored to project/slot/media_item — no time anchoring, not reused here.
- Consistency fixes folded into PR 1/PR 3: extraction-on-upload (durations for Latents audio), Uploader dropzone keyboard operation, `LatentLooseFiles` play button, player transport aria-labels.

## Data model

### PR 1 — bundles & extraction

`media_items` gains:

```
parent_media_item_id   String FK media_items.id ON DELETE SET NULL, indexed
```

`media_session_meta` gains:

```
extraction_status   String  — 'pending' | 'processing' | 'done' | 'failed' | NULL (zip-legacy)
extracted_count     Integer DEFAULT 0
extraction_error    Text NULL
```

Bundle storage on disk: `{SEARCH_MEDIA_DIR}/session/YYYY-MM/<sha8>_<name>.logicx/` (unpacked tree) + `manifest.json`:

```json
{ "name": "Heliotrope.logicx", "total_bytes": 123, "files": [
  {"path": "Media/Audio Files/a.wav", "size": 456, "sha256": "…"} ],
  "manifest_sha256": "…" }
```

`manifest_sha256` (hash of sorted path+size+sha256 rows) becomes the `MediaItem.sha256` — re-uploading the identical bundle dedups exactly like today. Downloads: `GET /api/media/{id}/file` streams a zip of the tree on the fly for session items stored as directories.

### PR 2 — annotations & marginalia

```
annotations
  id                 String PK (uuid)
  media_item_id      String FK media_items.id ON DELETE CASCADE, indexed
  parent_id          String FK annotations.id ON DELETE CASCADE NULL  — one-level replies
  kind               String  — 'comment' | 'cue'
  source             String  — 'user' | 'wav_cue' | 'aiff_cue' | 'midi' | 'logic' | 'ai_listen'
  position_seconds   Float
  label              String NULL  — cue text ("Verse", "WAV cue 3")
  body               Text NULL    — comment markdown
  author_id          Integer FK users.id NULL  — null for imported/AI
  resolved_at        DateTime NULL
  resolved_by        Integer FK users.id NULL
  touched_by_user    Boolean DEFAULT FALSE
  created_at / updated_at
  INDEX (media_item_id, position_seconds)
```

`media_midi_meta`:

```
media_item_id   String PK FK media_items.id ON DELETE CASCADE
tempo           Float NULL      time_sig String NULL
track_names     Text NULL (JSON array)
note_count      Integer NULL    duration_seconds Float NULL
preview_path    String NULL     — OGG preview relative to SEARCH_MEDIA_DIR
```

`marginalia` Meilisearch index — one doc per annotation:

```json
{ "id", "media_item_id", "project_ids": [], "parent_id",
  "kind", "source", "position_seconds", "label", "body",
  "author_id", "author_name", "resolved": false,
  "media_type", "filename", "created_at", "updated_at" }
```

Searchable: `label`, `body`, `filename`. Filterable: `media_item_id`, `project_ids`, `kind`, `source`, `author_id`, `resolved`, `media_type`, `created_at`. Sortable: `created_at`, `position_seconds`. Index registration + settings go through `configure_indexes()`; `manage.py reindex` rebuilds from SQLite.

### Marker inheritance

Cues parsed from a session bundle's MIDI/Logic sources anchor to the session item. When playing an extracted child audio item, the player can additionally show the parent session's cues (toggle, default on) — queried via `parent_media_item_id`. Cues from a WAV's own cue chunks anchor to that WAV only.

## API surface

All new endpoints are `require_admin` (matches Latents). Tags registered in `TAGS_METADATA` and rows added to `docs/api.md`.

### PR 1 — bundles (tag: `Bundles`)

```
POST   /api/media/bundles                      → start { name } → { bundle_id }
POST   /api/media/bundles/{id}/files           → one part: headers X-Bundle-Path, raw body, streamed
GET    /api/media/bundles/{id}                 → status { received: n, bytes, state }
POST   /api/media/bundles/{id}/complete        → validate (.logicx present) → create MediaItem(type=session)
                                                 + MediaSessionMeta, attach (project_id/slot_id), enqueue extraction
DELETE /api/media/bundles/{id}                 → abort + delete staging dir
```

Staging: `{SEARCH_MEDIA_DIR}/.bundles/{uuid}/` with a `.bundle.json` state file; abandoned bundles reaped after 24h (startup sweep).

`POST /api/media/upload` (existing): gains server-side unzip for zipped bundles (extension match `.logicx.zip` etc.) → same extraction path; also now enqueues `run_extraction_async` for audio/video uploads so durations/metadata land without manual re-extract.

### PR 2 — extraction + annotations read (tags: `Bundles`, `Marginalia`)

```
GET    /api/media/{id}/annotations             → list (flat, ordered by position) incl. inherited
GET    /api/media/annotations/counts?media_ids=a,b,c
                                                 → badge counts for slot rows / grids
GET    /api/media/{id}/children                → extracted children of a session item
GET    /api/media/{id}/audio                   → extended: serves synth OGG preview for midi items
```

### PR 3 — comments write + peaks (tag: `Marginalia`)

```
POST   /api/media/{id}/annotations             → create { kind, position_seconds, body?|label?, parent_id? }
PATCH  /api/annotations/{id}                   → edit body/label/position (author; sets touched_by_user)
POST   /api/annotations/{id}/resolve           → toggle resolved (any admin)
DELETE /api/annotations/{id}                   → author or admin
GET    /api/annotations                        → search via marginalia index
                                                 (q, media_item_id, project_id, author_id, kind, source,
                                                  resolved, sort, page)
GET    /api/media/{id}/peaks                   → waveform peaks JSON (~1500 min/max bins)
```

Write path: SQLite commit → sync single `marginalia` doc (same immediate-upsert pattern as votes). Slack event `latent.annotation_created` (immediate tier) with Latent + `[mm:ss]` link.

## Extraction pipeline (server/session_extract/)

```
server/session_extract/
  __init__.py
  base.py        — ExtractedFile / ExtractedCue dataclasses; Extractor protocol:
                   detect(bundle_dir) -> bool; harvest(bundle_dir) -> Extraction
  logic.py       — .logicx: Media/Audio Files/*.{wav,aif,aiff,mp3,flac}, *.mid anywhere,
                   WAV/AIFF cue chunks, ProjectData heuristic (flag-gated)
  cues.py        — RIFF cue /LIST adtl parser, AIFF MARK parser (pure Python, struct)
  midi.py        — pretty_midi wrapper: tempo, time sig, track names, note count,
                   marker meta-events, duration
  logic_markers.py — experimental ProjectData scan (SESSION_LOGIC_PARSE=1)
  jobs.py        — orchestration: create child items, attach to slot, cues → annotations,
                   MIDI previews, status bookkeeping on media_session_meta
```

Orchestration runs via the existing `run_extraction_async` thread pattern. Per-child failures log to `extraction_failures` (existing table) and don't fail the set; terminal status lands on `media_session_meta.extraction_status`.

MIDI preview: `pukebox_synth`-style render per .mid (single-file variant of `synthesize_preview`), stored beside the item as `<file>.preview.ogg`, served by `/api/media/{id}/audio` when `media_type='midi'`.

Peaks (PR 3, but generation wired in extraction for all audio/video): ffmpeg PCM → numpy → ~1500 min/max bins → `<file>.peaks.json` beside the media file; endpoint serves it (404 until generated; Ossuary-style client fallback is out of scope).

## Frontend

### PR 1 — `Uploader.svelte`

- Detect dropped directories/bundles via `webkitGetAsEntry()` recursive walk; group as one bundle entry with aggregate progress (bytes across parts), 3 parallel part uploads, per-part retry; cancel aborts server-side.
- `.logicx` file-picker selection via `webkitdirectory` fallback note (drag is primary).
- Fix keyboard operability of the dropzone (Enter/Space opens picker).
- Session tile: extraction progress state → "N extracted files" expandable list; child rows show "from {bundle}" chip (PR 2 data, PR 3 polish).

### PR 3 — player + surfaces

- `Player.svelte`: expandable now-playing panel (waveform canvas from peaks; annotation avatars at `position/duration`; click seeks; composer posts at `currentTime`; reply/resolve in popover). Collapsed seek bar gains tick marks. New optional `start_time` on `player:queue`. Keyboard: `[` / `]` prev/next marker, `c` comment at current time; `aria-live` polite announcements on add/resolve; transport buttons get `aria-label`s; volume/seek get `aria-valuetext`.
- Mobile: panel becomes a bottom sheet; comment thread full-width; ≥44px targets; no hover-only affordances.
- `LatentSlots.svelte` / `LatentLooseFiles.svelte`: 💬 count badges via counts endpoint; marker popover lists with seek-links (`player:queue` + `start_time`); loose files gain the play button.
- `src/pages/admin/search/detail.astro`: Marginalia section for any media item.
- Latent activity strip: annotation events merged in.

## Deferred: AI listening enrichment ([#554](https://github.com/A-U-Supply/a-u.supply/issues/554))

Every-stem listening is held, per user direction — revisit after PRs 1–3 ship and real session volumes are known, or when a clearly better/cheaper model lands. Design (ready to implement, additive only):

- `server/ai_listen.py`: provider seam — `AI_LISTEN_PROVIDER` (default `gemini`), `AI_LISTEN_MODEL` (default `gemini-3.5-flash-lite`), `AI_LISTEN_API_KEY`, `AI_LISTEN_BASE_URL`. ffmpeg → 192k MP3 → provider Files API → structured JSON.
- **Scope: every extracted stem**, not just primaries.
- Output: file-level metadata (description/tags/vibe/instrumentation/production notes, refining the text pass with provenance) **+ timeline observations** `[{start_sec, end_sec?, kind: vibe_shift|section|instrument|event, label, detail}]` → written as `source='ai_listen'` cue annotations — clickable seek markers in the player.
- Guards: `AI_LISTEN_AUTO`, `AI_LISTEN_MAX_FILE_MINUTES` (default 90), per-run minute cap; per-file "Listen / Re-listen" button; `manage.py backfill-listen --limit N`.
- Cost (verified 2026-07-22, Vertex list prices; ~115k audio tokens/hour at 32 tok/s): Gemini 3.5 Flash-Lite ≈ **$0.04/hour** (~$0.20 per 5-hour session, every stem); Gemini 3.6 Flash ≈ $0.20/hour; Qwen3.5-Omni-Flash (Alibaba) ≈ $0.35/hour. DeepSeek/SiliconFlow have no audio-understanding models.
- Schema seam already in place: `annotations.source='ai_listen'` reserved; AI fields follow the existing image `ai-fields` override pattern.

## Testing

Latents currently has near-zero endpoint tests; these PRs establish the base.

- **PR 1** (`tests/test_bundles_api.py`, `test_session_extract.py`): bundle start/parts/complete/abort; path traversal rejection; staging reaper; synthetic `.logicx` fixture (tmp dir + tiny generated WAVs) → extraction creates children, attaches to slot, sets provenance; zipped-bundle path; manifest-hash dedup; extraction-on-upload enqueued.
- **PR 2** (`tests/test_cues.py`, `test_annotations_api.py`): crafted RIFF `cue ` chunk + AIFF `MARK` fixtures; pretty_midi-generated .mid with markers; inheritance query; counts endpoint; marginalia doc build + sync (mocked Meili, conftest pattern).
- **PR 3** (`tests/test_annotations_write.py`, `test_peaks.py`): CRUD, permissions (author vs admin), resolve, replies (one level enforced), peaks generation (tiny WAV fixture), search endpoint filters.
- Per PR: `npm run format`, `uv run pytest`.

## Docs updates

| PR | Files |
|---|---|
| 0 | this plan |
| 1 | `docs/api.md` (Bundles group), `main.py` `TAGS_METADATA`, `docs/operations.md` (bundle reaper note, nginx body-size), `docs/deployment.md` (nginx `client_max_body_size`) |
| 2 | `docs/glossary.md` (Marginalia, bundle, cue), `docs/api.md` (Marginalia group), `docs/architecture.md` (7th index, annotations table, session_extract) |
| 3 | `docs/player.md` (`start_time`, annotation UI, keyboard), `docs/frontend.md` (player panel, event bus) |
| issue | AI-listening design section above |

`AGENTS.md` needs no changes (no rules altered).

## Risks / mitigations

- **Multi-GB uploads through Dokku/nginx** → per-file streaming parts; `client_max_body_size` + `proxy_read_timeout` documented; client retry per part.
- **Disk usage** (unpacked 5 GB bundles + extracted peers) → zip-on-download avoids storing both; volume sizing note in deployment docs; orphan GC remains separately deferred (v1 plan).
- **ProjectData parser brittleness** → flag-gated, log-only, zero blocking.
- **Waveform perf on mobile** → cached peaks JSON, DPR-aware canvas, no client decode.
- **Marginalia index drift** → SQLite source of truth; `reindex` rebuilds; write-path immediate sync like votes.

## Out of scope

- Ableton/other DAW extractors (seam exists; Logic only ships).
- AI listening (deferred per above; issue-tracked).
- Orphan-file garbage collection (deferred in v1 plan).
- Public-facing comments; Marginalia is admin-only.
- Real-time collaboration, notifications inbox integration (Slack only).
