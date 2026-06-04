# Litany-exp — per-voice sample selection overhaul

**Branch:** `litany-exp` → PR #N
**Status:** in progress

## Goal

Overhaul the per-voice sample selection in Litany: bigger pools (cap 16), multi-pin, visible pool UI with drag-to-reorder, and a search modal for browsing `samples-bored` and adding samples by hand. All new features are additive — default behaviour stays identical.

Built as a **parallel experiment** (`litany-exp`) so the stable Litany stays untouched while we iterate. Merge when ready.

## Approach

Duplicate the entire Litany tree under an `-exp` suffix — page, components, lib, styles. Zero shared files with the original. This avoids regressions and lets us refactor `pool.ts` + `state.ts` (the two most changed files) without backward-compatibility contortions.

The API layer is unchanged — Litany-exp calls the same `GET /api/serve` (for fetching audio) and `POST /api/search` (for the search modal). No new backend endpoints needed.

## Data model

### Voice (state.ts)

```ts
type Rotation = 'every-hit' | 'every-bar' | 'every-4bars';          // drops 'pinned'
type PinnedRotation = 'every-hit' | 'every-bar' | 'every-4bars' | 'fixed';

interface Voice {
  // … all existing fields preserved …
  rotation: Rotation;                         // was Rotation including 'pinned'
  pinned: string[];                           // names of pinned pool entries (serialized)
  pinnedRotation: PinnedRotation;             // cadence for pinned-subset cycling
}
```

`pinnedUrl?: string` is removed. Instead, `pinned: string[]` tracks which entry filenames are pinned. On pool refill, entries matching these names get re-pinned.

### Rotation behaviour

- **No entries pinned**: the full pool rotates per `voice.rotation` (/HIT, /BAR, /4BR) — identical to today.
- **One or more entries pinned**: the pinned subset rotates per `voice.pinnedRotation` (/HIT, /BAR, /4BR, or `fixed` for a single pinned entry). The main `rotation` is ignored.

This subsumes the old `'pinned'` rotation mode — pinning one entry with `pinnedRotation='fixed'` == old PIN behaviour.

### PoolEntry (pool.ts)

```ts
interface PoolEntry {
  buffer: AudioBuffer;
  name: string;                              // filename
  mediaId?: string;                          // Meilisearch id (for dedup)
  source: 'query' | 'manual';               // how it was added
}
```

### SamplePool class

```ts
const MAX_POOL_SIZE = 16;

class SamplePool {
  entries: (PoolEntry | null)[];
  pinnedIndexes: Set<number>;                // indices of pinned entries
  pins: string[];                            // sync'd copy for reactive UI

  // Existing API preserved
  async fill(query: string): Promise<void>;
  next(rotation, pinnedRotation, isBarStart, is4BarStart): AudioBuffer | null;
  previewBuffer(): AudioBuffer | null;
  entryNames: string[];

  // New API
  async addFromSearch(mediaIds: string[]): Promise<void>;   // fetch + decode + add
  async fetchMore(query: string, count: number): Promise<void>;  // append random entries
  togglePin(index: number): void;
  getPinnedNames(): string[];
  removeEntry(index: number): void;
  moveEntry(fromIndex: number, toIndex: number): void;
  getActiveIndex(): number;                  // for UI highlighting
}
```

Key design decisions:
- `fill()` still fetches 8 entries and **replaces** the pool (backward-compatible).
- `addFromSearch()` **appends** entries fetched by media ID.
- `fetchMore()` **appends** random entries from the same query.
- `pinnedIndexes` is derived from `voice.pinned` names matching pool entries at fill/add time.
- `removeEntry()` and `moveEntry()` work on all entries (random or manual).
- Cap at 16 — pool starts at 8, up to 8 more can be added.

## API surface (zero changes)

| Endpoint | Use |
|----------|-----|
| `GET /api/serve?output_index=samples-bored&query=…&sort=random` | Initial pool fill, fetch-more random batches, single-sample fetch by media-id |
| `POST /api/search` with `{"filters":{"output_index":["samples-bored"]},"query":"…","per_page":50}` | Search modal — returns JSON metadata (id, filename, voice, instrument, sample_rate, etc.) |
| `GET /api/media/{id}/file` | Download audio for preview (redirected from `/api/serve`) |

## Component architecture

### Files to create (full duplicate tree)

```
src/pages/admin/atelier/litany-exp.astro           ← page entry
src/styles/atelier/litany-exp.css                   ← styles (start as copy of litany.css)
src/components/LitanyExp.svelte                     ← top-level island
src/components/litany-exp/
  Toolbar.svelte                                    ← (unchanged from litany)
  VoiceCard.svelte                                  ← updated: pool drawer, new rotation UI
  StepGrid.svelte                                   ← (unchanged)
  FXPanel.svelte                                    ← (unchanged)
  EnvPanel.svelte                                   ← (unchanged)
  MasterSection.svelte                              ← (unchanged)
  PoolDrawer.svelte                                 ← NEW: pool grid UI
  SampleSearchModal.svelte                          ← NEW: search modal
src/lib/litany-exp/
  state.ts                                          ← updated types
  pool.ts                                           ← rewritten pool
  audio.ts                                          ← (unchanged)
  scheduler.ts                                      ← minor: pass pinnedRotation to pool.next()
  randomize.ts                                      ← (unchanged)
```

### Files to modify

```
src/layouts/Admin.astro                             ← add sidebar entry for litany-exp
```

### New component: PoolDrawer.svelte

An expandable "POOL" drawer below the query row in VoiceCard, replacing the current `<select>` dropdown:

- **Toggle:** "POOL ▾" button in the meta-row opens/closes the drawer.
- **Grid of chips:** Each pool entry is a chip showing the sample filename (truncated). Grid layout.
- **Active highlight:** The currently-playing entry has a gold border/glow.
- **Per-chip actions:**
  - Drag handle → reorder. Drag-and-drop (`draggable`, `dragstart`/`dragover`/`drop` handlers). Underlying entries array gets reordered.
  - ▶ play button → preview that sample.
  - 📌 pin toggle → pin/unpin.
  - × remove button → remove from pool.
- **Footer row:**
  - "+ Search" button → opens `SampleSearchModal`.
  - "↻ +4 more" button → calls `pool.fetchMore(query, 4)`.
  - Pool count indicator (e.g. "8/16").

### New component: SampleSearchModal.svelte

Full-screen modal for browsing `samples-bored`. Follows the `PullFromIndex.svelte` pattern (overlay + modal + scrollable body + footer).

- **Search bar:** Debounced text input (300ms). Calls `POST /api/search` with `output_index: ["samples-bored"]`.
- **Filters sidebar** (collapsible on narrow screens):
  - Voice: checkboxes for `kick`, `snare`, `hi-hat`, `clap`, `tom`, `percussion`, `fx`, `vox`, `bass`, `chord`, `melody`, `instrument`
  - Instrument: text facet (from Meilisearch facets)
  - Source: `source_name` and `source_creator` facets
  - Sample rate / channels / bit depth: numeric range or preset buttons
- **Results grid:** Cards showing filename, voice badge, instrument, duration. Grid layout matching the litany aesthetic.
- **Per-result actions:**
  - Click card → play a short audio preview (fetches via `/api/serve` and plays). Stops previous preview if one is playing.
  - "+" button → appends to the current voice's pool.
- **Batch add:** Checkbox on each card + "Add selected (N)" footer button.
- **Selected state:** Chips at the bottom showing what's been added, with count.
- **Close:** "Done" button, Escape key, or click overlay. Returns focus to the pool drawer.

### Modified component: VoiceCard.svelte

Changes from the original:
- **Sample select dropdown is removed.** Replaced by the PoolDrawer toggle.
- **Current sample name** stays visible as a small label above the pool drawer.
- **Pin button** is removed from the query row (pin is now per-entry in the drawer).
- **Rotation dropdown** still shows `/HIT`, `/BAR`, `/4BR`. A second small dropdown appears alongside it when entries are pinned, showing `PIN: /HIT`, `PIN: /BAR`, `PIN: /4BR`, `PIN: FIX`.
- **Pool status** (loading/error) is shown on the PoolDrawer toggle button.

### Modified component: LitanyExp.svelte

- **Modal state:** Manages the `SampleSearchModal` instance — one shared instance that gets passed the current voice's context.
- **Pool management:** `addSamplesFromSearch(voiceId, mediaIds)` calls `pool.addFromSearch()`.
- **fetchMore:** `fetchMorePool(voiceId, count)` calls `pool.fetchMore(query, count)`.
- **Toggle pin / remove / reorder:** delegate to pool methods and sync `voice.pinned`.
- All existing state management (undo/redo, play/stop, randomize, chaos) stays identical.

## Implementation order

1. **Scaffold the parallel tree** — Copy all files, rename imports, wire up the page and sidebar entry. Verify "litany-exp" loads and plays identically to litany.
2. **Refactor pool.ts** — Multi-entry support, pin sets, add-from-search, fetch-more, remove, reorder. Keep `fill()` + `next()` API compatible.
3. **Update state.ts** — New types (`PinnedRotation`, drop `'pinned'` from `Rotation`).
4. **Build PoolDrawer.svelte** — Replace the sample select dropdown. Wire up pin toggles, active highlight, preview buttons, drag-to-reorder.
5. **Build SampleSearchModal.svelte** — Search bar, filters, results grid, preview, add-to-pool.
6. **Wire it all together in VoiceCard + LitanyExp** — Connect pool drawer, search modal, multi-pin state.
7. **Update docs in litany-exp.astro** — Reflect new features in the inline docs.
8. **Polish** — Keyboard shortcuts, loading states, edge cases (empty pool, no results, fetch errors).
