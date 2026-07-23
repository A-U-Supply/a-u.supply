# Session Bundles, Extraction & Marginalia

Feature guide for the Latents upload pipeline (shipped 2026-07-23). The full design rationale and locked decisions live in [`plans/2026-07-22-latents-sessions-marginalia.md`](plans/2026-07-22-latents-sessions-marginalia.md); the deferred AI-listening pass is tracked in [issue #554](https://github.com/A-U-Supply/a-u.supply/issues/554). Vocabulary: [`glossary.md`](glossary.md) (Marginalia, bundle, cue).

Shipped as: PR [#555](https://github.com/A-U-Supply/a-u.supply/pull/555) (plan), [#556](https://github.com/A-U-Supply/a-u.supply/pull/556) (bundles), [#557](https://github.com/A-U-Supply/a-u.supply/pull/557)+[#559](https://github.com/A-U-Supply/a-u.supply/pull/559) (MIDI/cues/store), [#558](https://github.com/A-U-Supply/a-u.supply/pull/558) (comments + player UI), [#562](https://github.com/A-U-Supply/a-u.supply/pull/562) (DeepSeek endpoint fix).

## What it does

1. **Session bundle upload.** Drag a `.logicx` onto a Latent uploader — the browser walks the package and uploads each file as a streamed, parallel, retryable part (one aggregate progress bar). Zipped bundles (`.logicx.zip`) work on the normal upload path. The server harvests every audio file into a first-class Emulsion item attached to the **same slot**, linked to the parent session via `media_items.parent_media_item_id`. Session downloads stream a zip built on the fly (no double storage).
2. **MIDI + cue harvesting.** MIDI files are a real media type (`midi`) with parsed metadata (tempo, time sig, track names, notes, duration) and a synthesized WAV preview, so they play in the player. Markers are harvested from WAV/AIFF cue chunks, MIDI marker meta-events, and an experimental Logic `ProjectData` parser (`SESSION_LOGIC_PARSE=1`, off by default).
3. **Marginalia — timestamped comments + markers.** SoundCloud-style comments at playback positions, rendered on a server-generated waveform in the persistent player. One-level replies, resolve/archive, edit/delete. Imported cues (WAV/AIFF/MIDI/Logic, and future AI) share the same table. Session-level markers inherit to extracted children.
4. **Auto metadata at upload.** Every upload (Tribute or Latents) now runs extraction immediately: ffprobe durations, thumbnails, whisper transcript (≤15 min), waveform peaks, and AI text tagging. Latent-attached audio gets a WIP-bounce prompt with project/slot context. AI never overwrites human-written descriptions.

## How to use it

- **Upload a session**: drag the `.logicx` (or its zip) onto a slot's upload zone → "Extracting audio…" → expand **N extracted files** on the session row. Children appear as peers with "from session" chips that scroll to the parent.
- **Comment at a timestamp**: play any Latents/Stacks audio → chevron in the player bar opens the waveform → click to seek, 💬 to comment at that moment, ◆ for a text-less marker. `[`/`]` jump between markers, `c` composes, `Esc` closes. `[mm:ss]` links everywhere seek the player (`player:queue` + `start_time`, or bare `player:seek` if the item is already playing).
- **Mix-note workflow**: comment → **resolve** when fixed. Slot/loose badges show `💬 n` with unresolved highlighted. Search detail has a full Comments & markers section; the Latent page has a "Latest comments & markers" strip.
- **Get Logic markers in**: bounce a reference mix with markers (WAV cue chunks) and/or keep an exported `.mid` in the project folder — both import automatically. The direct `ProjectData` parse is opt-in (`SESSION_LOGIC_PARSE=1`) and best-effort.

## Architecture pointers (for agents)

| Area | Where |
|---|---|
| Bundle API (start/parts/status/complete/abort, staging, reaper) | `server/bundles_api.py` |
| Extractor seam (Logic first; add DAWs to `EXTRACTORS`) | `server/session_extract/{base,logic,jobs}.py` |
| Cue parsers (WAV RIFF, AIFF MARK) | `server/session_extract/cues.py` |
| MIDI parse + synth preview | `server/session_extract/midi.py` |
| Experimental Logic ProjectData scan | `server/session_extract/logic_markers.py` |
| Marginalia API (comments, resolve, search, counts) | `server/marginalia_api.py` |
| Marginalia Meili projection + doc builder | `server/search_client.py` (`sync_annotation`, `MARGINALIA_INDEX`) |
| Models | `annotations`, `media_midi_meta`, `media_items.parent_media_item_id`, `media_session_meta.extraction_*` |
| Peaks | `server/extraction.py` (`generate_peaks`), `GET /api/media/{id}/peaks` |
| Player panel + events (`start_time`, `player:seek`, `player:time-request`) | `src/components/Player.svelte`, [`player.md`](player.md) |
| Shared annotation UI helpers | `src/components/marginalia.ts`, `Marginalia{List,Badge,Recent}.svelte` |
| Tests | `tests/test_{bundles_api,session_extract,cues,annotations_api,annotations_write,peaks,ai_audio_config}.py` |

## Operations

- **Limits** (applied 2026-07-23): nginx `client_max_body_size 20g`, `proxy_read_timeout 900`; app `MAX_UPLOAD_PART_BYTES=21474836480`. The cap is an accident/disk guard, not a trust one. See [`deployment.md`](deployment.md#nginx).
- **Env vars** (`operations.md` has the full table): `MAX_UPLOAD_PART_BYTES`, `BUNDLE_STALE_HOURS` (24h staging TTL, reaped at startup), `WHISPER_MAX_SECONDS` (900), `SESSION_LOGIC_PARSE`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`.
- **Reindex**: `manage.py reindex` rebuilds all Meili indices including `marginalia` from SQLite.
- **Bundle staging**: `{SEARCH_MEDIA_DIR}/.bundles/` — reaped on startup; safe to delete by hand.

## Known quirks & history

- **DeepSeek 401 (fixed)**: audio text tagging silently no-opped in prod because the direct `api.deepseek.com` key was invalid; per-batch error swallowing hid it. Now routed through SiliconFlow (`deepseek-ai/DeepSeek-V4-Flash`) via `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`; the dead `DEEPSEEK_API_KEY` was unset so the `VISION_API_KEY` fallback engages. If tags stop appearing, check this first.
- **Legacy `.mid`-as-`audio` items** (`drums.mid`, `melody.mid`, `chords.mid`, `bass (1).mid`): uploaded before the `midi` type existed; the audio pipeline can't process them. Re-upload to get proper playable MIDI items with previews.
- **`gh pr edit --base` silently no-ops** on this repo (GraphQL projects deprecation). Use `gh api -X PATCH repos/A-U-Supply/a-u.supply/pulls/N -f base=master` and verify. PR #557 was recovered this way via #559.
- **Dokku deploy lock**: rapid consecutive merges can fail-fast the deploy workflow ("deploy lock in place"). Re-run the failed run (`gh run rerun`) once the current deploy finishes.
- **Pre-existing test failure**: `tests/test_notifications.py::test_midden_picks_up_discarded_outputs` fails on clean master (unrelated to this work).

## Deferred

AI **listening** enrichment (every stem, timeline observations as `source='ai_listen'` annotations): full design, provider/cost table (~$0.04/hour on Gemini Flash-Lite), and acceptance criteria in [issue #554](https://github.com/A-U-Supply/a-u.supply/issues/554). The schema and provider seam are already in place; the future PR is purely additive.
