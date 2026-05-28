# samples-bored: Music 2000 Sample Library Index

## Summary

Index the 2,888 WAV one-shot samples from the Music 2000 (MTV Music Generator) PlayStation game into a standalone Meilisearch index called `samples-bored`, with AI-enriched descriptions for sound-effect directories, and integrate it into the Stacks search UI.

## Status quo

- The `samples-bored` index exists locally with 2,888 documents (created by `scripts/index_samples.py`)
- `server/search_client.py` has been modified to include `SAMPLES_INDEX` in `configure_indexes()` and `multi_search()`
- `SearchFilterBar.svelte` has been modified to add `"sample"` as a media type toggle
- **Not deployed** — the production site at a-u.supply does not have these changes

## Open issues (from user feedback)

### 1. Index not visible in production filters

The filter-bar change (`SearchFilterBar.svelte`) and the search-client changes are only in the local repo. They need to go through the standard workflow: branch → PR → merge to master → auto-deploy via GitHub Actions → Dokku.

### 2. AI tagging scope

DeepSeek was used only for 3 sound-effect directories (59 files: animal, musical, objects). The other 2,829 files get deterministic tags from directory name + filename patterns. This is fine — the instrument/musical directories already have descriptive names that don't benefit from AI.

### 3. Archive.org source metadata

The updated `scripts/index_samples.py` now includes per-document fields:
- `source_url` → `https://archive.org/details/music-2000-sample-library-44k-wav-rip`
- `source_name`, `source_creator`, `source_year`
- `source_topics` (the Archive.org subject tags)
- `royalty_free: true` (there's a "Royalty-Free License.txt" in the zip)
- Improved DeepSeek prompt with the game context (Music 2000 / MTV Music Generator)

These fields need to be added to the shared Meilisearch settings so `configure_indexes()` picks them up.

## TODO

- [ ] Add `source_url`, `source_name`, `source_creator`, `source_year`, `source_topics`, `royalty_free` to `SEARCHABLE_ATTRIBUTES` / `FILTERABLE_ATTRIBUTES` in `server/search_client.py`
- [ ] Re-run `scripts/index_samples.py` (deletes + recreates the index with the new fields + improved AI tags)
- [ ] Create a PR with the three changed files
- [ ] Merge + deploy

## Out of scope (for now)

- Submitting samples directly from the Stacks UI to the samples-bored index
- Audio playback in the grid/feed views for WAV samples
- Download links for individual samples
- Whisper transcription of vocal samples (not useful for one-shots)
