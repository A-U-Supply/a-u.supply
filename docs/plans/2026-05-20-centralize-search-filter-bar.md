# Centralize the search filter bar

**Branch:** `feat/centralize-filter-bar` → PR pending
**Status:** plan-only

## Goal

One Svelte component owns every filter control we use to talk to `/api/search`, and both consumers — the Stacks page and the "Pull from index" modal on Latents — render the same bar. Today the modal has a tiny, half-broken substitute (4 pills that conflate media types with the Emulsion index, no other indexes represented) and the Stacks page has the real thing as 180 lines of inline HTML + several hundred lines of vanilla JS scattered through a 3,962-line `.astro` file.

## Problems today

- **Latents → Pull from index modal** (`src/components/PullFromIndex.svelte`):
  - Filter row is `[image] [audio] [video] [emulsion]`. The first three are `media_types`; emulsion is a separate Meilisearch index (`include_emulsion: true`). Visually identical, semantically different.
  - No real index picker. Outputs, app-specific indices (`bullethole`, `collage-frankenstein`, `rottengenizdat`, `sparagmos`) and `__inputs__` are unreachable from the modal even though they're attachable via `/api/search`.
  - Buttons feel inert: plain click does multi-toggle (against [admin UI convention](feedback_ui_buttons_and_multiselect)); clicking "image" when all 4 are on excludes images rather than narrowing to them, so the result set barely changes.
- **Stacks page** (`src/pages/admin/search/index.astro`):
  - All filter UI lives inline. State lives in DOM inputs and is read back via `getFilters()`, mutated by `clearFilter()`, hydrated by `restoreFromUrl()`, populated by `loadFacets()`, and conditionally enabled/disabled by `updateCrossFilterState()` — five overlapping concerns with no clear ownership.
  - The `IndexFilter.svelte` island already exists for one of these controls and talks to the page via `data-selected` + `index-change`/`index-set` CustomEvents. The pattern works; the other 11 filters just haven't been migrated.

Centralizing fixes both: the modal stops being a half-built substitute, and the Stacks page stops being the canonical home for filter logic that should be reusable.

## Approach

### 1. New component `src/components/SearchFilterBar.svelte`

Self-contained Svelte 5 island. Owns:

- Index multi-select (port `IndexFilter` logic inline; the standalone component goes away after migration)
- Media-type switches (`image` / `audio` / `video`) — chunky 2px-border brutalist buttons per [admin UI convention](feedback_ui_buttons_and_multiselect): **plain click = switch to only that type, modifier-click = multi-select**. This is the _one_ substantive UX change from the Stacks page (which currently uses checkboxes — checkbox behavior is preserved under the hood; the UI is just brutalist switches now).
- Source channel multi-select (bits-ui Select multiple)
- Date range (from/to inputs)
- Color group dropdown + URL-preserved multi-color hint (legacy behavior preserved as-is)
- Posted by (single-select dropdown)
- Job app (single-select dropdown; auto-disabled when only `__inputs__` is selected)
- Tags free-text input (comma-separated)
- Min reactions, min tags (number inputs)
- Has-transcript / has-text (auto-disabled by selected media types — same rules as today)
- Sort by (single-select dropdown — kept inside the bar since it lives in the filter panel on Stacks today; modal hides this via a prop)

#### Component contract

```ts
type Props = {
  // Initial filter state. Either provided explicitly (modal) or read from URL (Stacks).
  initial?: Partial<Filters>;
  // When true, the component reads ?q=&output_index=&types=... on mount and
  // writes back to the URL on change. Off in the modal.
  syncUrl?: boolean;
  // Hide sort + filter groups the consumer doesn't want (modal omits sort).
  hide?: (
    | 'sort'
    | 'colors'
    | 'reactions'
    | 'tags-min'
    | 'dates'
    | 'channels'
    | 'posters'
  )[];
  // Two-way binding for the consolidated filter state.
  filters: Filters; // bind:filters
  // Fired on every change.
  onChange?: (next: Filters) => void;
};

type Filters = {
  types: string[]; // ['image','audio','video']
  outputIndexes: string[]; // ['__inputs__'] | ['__emulsion__','outputs', …]
  channels: string[];
  poster: string;
  jobApp: string;
  colorGroups: string[];
  preservedMultiColors: string[]; // ?colorgroup=a,b → kept until user touches the dropdown
  dateFrom: string;
  dateTo: string;
  tagsText: string;
  reactionsMin: number;
  tagsMin: number;
  hasTranscript: '' | 'yes' | 'no';
  hasText: '' | 'yes' | 'no';
  sortBy: string; // 'newest' | 'oldest' | 'random' | …
  // Derived helpers
  includeEmulsion: boolean; // = outputIndexes.includes('__emulsion__')
};
```

For vanilla-JS consumers that can't use `bind:filters` (the Stacks page is `.astro` + inline JS), the component also:

- Writes the JSON-stringified state to `data-filters` on its wrapper element on every change.
- Dispatches a bubbling `filters-change` CustomEvent (`detail: { filters }`) on the wrapper.
- Listens for an inbound `filters-set` CustomEvent (`detail: { patch }`) to allow partial resets from outside (e.g. an active-chip × click).

This mirrors the existing `IndexFilter` pattern (`data-selected` + `index-change` / `index-set`), so the Stacks page integration is uniform.

#### Cross-filter rules (unchanged from today)

- Job App is disabled when the only selected index is `__inputs__` (inputs have no `job_app`).
- Has-transcript is disabled when no audio/video media type is selected.
- Has-text is disabled when image is not selected.
- Index value `[]` snaps back to `['__inputs__']` — never zero (preserved from `IndexFilter`).

#### Facet loading

One `loadFacets()` inside the component, fetches `/api/search/facets` once, populates channel / poster / job-app dropdowns + the dynamic portion of the index list. Same shape the page uses today.

#### Reset semantics

Single public function `reset(key: keyof Filters | 'all')`. Called by:

- Stacks page active-chip × buttons (via the inbound `filters-set` event with a `{ key: null }`-shaped patch, OR a dedicated reset event — see open question 3).
- Stacks `Clear All` button (`reset('all')`).

### 2. Migrate Stacks page (`src/pages/admin/search/index.astro`)

- Delete the inline filter HTML in `filter-body` (lines 60–200) **except** the wrapping `<div class="filter-body" id="filter-body">` + the `filter-toggle` button — both stay so the existing open/close + sessionStorage persistence keeps working without changes.
- Mount `<SearchFilterBar client:load syncUrl={true} />` inside `filter-body`.
- Delete from the inline JS:
  - `getFilters()` — replaced by reading `data-filters` JSON from the bar's wrapper.
  - `loadFacets()` — moved into the component.
  - `updateCrossFilterState()` — moved into the component.
  - The filter-portion of `restoreFromUrl()` — the component reads URL on mount.
  - All the per-input event listeners that wire change → `applyAndSearch` — replaced by one wrapper-level `filters-change` listener.
- Keep:
  - `searchInput` (the `q` field — visually separate, in `.search-row`).
  - `currentPage`, `doSearch()`, `buildSearchBody()`, `applyAndSearch()`, infinite scroll, sort logic that consumes the filter state.
  - `showActiveFilters()` + `__clearFilter()` — rewritten to dispatch `filters-set` on the bar's wrapper instead of mutating DOM directly.
  - `filter-toggle` + sessionStorage persistence.

### 3. Migrate `PullFromIndex.svelte`

- Delete `mediaTypes` state and the `.type-row` block.
- Render `<SearchFilterBar bind:filters={filters} syncUrl={false} hide={['sort','colors','reactions','tags-min','dates','channels','posters']} initial={{ types: ['image','audio','video'], outputIndexes: ['__inputs__'] }} />` inside a collapsible `<details>` that defaults closed. (Collapse owned by the modal — keeps the bar reusable.)
- `runSearch()` consumes `filters` directly. Payload to `/api/search` built the same way `buildSearchBody()` does on Stacks.

The modal explicitly _hides_ sort, colors, reactions, tags-min, dates, channels, posters because they don't fit "find media to attach to a Latent". The bar shows index + media types + job-app + tags + has-text/has-transcript — the filters that meaningfully narrow attach candidates.

### 4. `IndexFilter.svelte` retirement

After both migrations land, `IndexFilter.svelte` has zero callers and gets deleted in the same PR. No back-compat shim — per `feedback_no_compat_shims` / standard `feedback_no_compat_shims`-equivalent practice in this repo, dead components go away.

## Data model / API surface

No backend changes. `/api/search/facets`, `/api/search`, `/api/search/ids` all stay exactly as they are.

URL params owned by the bar when `syncUrl=true`:

| Param          | Maps to                                |
| -------------- | -------------------------------------- |
| `output_index` | `outputIndexes` (comma-joined)         |
| `types`        | `types`                                |
| `channel`      | `channels`                             |
| `poster`       | `poster`                               |
| `app`          | `jobApp`                               |
| `colorgroup`   | `colorGroups` / `preservedMultiColors` |
| `from` / `to`  | `dateFrom` / `dateTo`                  |
| `tag` / `tags` | `tagsText`                             |
| `rxn`          | `reactionsMin`                         |
| `mintags`      | `tagsMin`                              |
| `transcript`   | `hasTranscript`                        |
| `ocr`          | `hasText`                              |
| `sort`         | `sortBy`                               |

Same param names as today — no breakage of existing admin links / bookmarks.

## Open questions

1. **`bind:filters` from a vanilla-JS page?** Svelte 5 components can be mounted via `mount()` and read state via `$state` proxies, but the cleanest cross-framework contract is still the data-attr + CustomEvent pattern (which `IndexFilter` already uses). Plan assumes Stacks consumes via event/dataset; only `PullFromIndex` uses `bind:filters`. Sound?
2. **Sort inside the bar or outside?** Today on Stacks, Sort By is the last control inside the filter panel. The modal doesn't want it. Plan keeps it in the bar with a `hide=['sort']` opt-out. Alternative: lift sort out of the bar entirely and let each consumer render it separately. The cleaner ontology says sort isn't a filter — but the existing UX bundles them, and splitting would change Stacks behavior. Lean toward keeping it bundled.
3. **Active-chip × buttons.** Currently call `__clearFilter('app')` etc., which mutates DOM. After migration the global `__clearFilter` becomes a thin wrapper that dispatches `filters-set` with the relevant patch. Acceptable to keep the global (used by inline `onclick` in chip HTML) or do we want to also rewrite the chip rendering to event-delegate properly? Plan keeps the global for now; chip rewrite is out of scope.
4. **Scope check.** Component + Stacks migration + modal migration + IndexFilter deletion in one PR. Alternatively: ship the component + modal in PR #1 (small blast radius), then Stacks migration in PR #2 (still touches the 3,962-line page but doesn't gate the modal fix). Plan as written does it all in one PR — but I'd like a nod on this before coding.

## Out of scope

- The Stacks `showActiveFilters()` chip rendering. It gets a small rewire (clear via event instead of DOM mutation) but the visual + UX stays identical.
- Sort-related changes other than where it lives.
- Any backend / `/api/search` schema changes.
- The `Insights` panel — it's inside `filter-body` but not part of the filter UI.
