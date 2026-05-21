## Admin design polish — wide consistency pass

**Branch:** `polish/admin-consistency` → PR TBD
**Status:** in progress

### Goal

Bring every admin page (outside The Atelier — Punctum / Photism / Bullethole / Spectralize stay as they are) onto a single brutalist visual language: chunky 2px-bordered cards with 4px hard shadows, brutalist controls with hover/active feedback, design-token-driven colors that survive dark mode, and windowed pagination. Fix the catalog/new dark-mode bug along the way. No API/business-logic changes — this is a design pass.

### Non-goals

- The Atelier section (Punctum, Photism, Bullethole, Spectralize). They are intentionally off-house.
- `/admin/search` (The Stacks) and `/admin/search/detail`. These are the blueprint.
- Renaming anything, changing routes, or touching server endpoints.
- Adding features that aren't already user-facing requirements (see "Functional additions" — limited to a small list).

### Survey findings (summary)

A four-agent parallel audit found ~200 concrete inconsistencies across 16 admin pages. The patterns repeat across files; we can solve most of them with shared primitives.

**Hot list (the worst):**

1. **Catalog dark-mode bug** — `catalog/new.astro` and `catalog/edit.astro` define `.collapsible__heading { background: #f8f8f8; }` in scoped styles (`new.astro:260`, `edit.astro:285`). In dark mode `--color-fg` flips to `#ececec` while the heading background stays light gray → near-white on white. Plus `#c00` reds and `#f8f6f0` cream drop zones with no dark-mode fallback.
2. **Hardcoded hex everywhere** — `#111` thumbnail backgrounds, `#c00` errors, `#080` success greens, `#f4f4f4` chip backgrounds, `rgba(0,0,0,0.6)` overlays. Anywhere a page bypasses the `var(--color-*)` system, dark mode breaks.
3. **Stock `<select>` elements** — nomenclator (5×), failures, slop, midden, workspace, hecatomb (4×), settings, jobs all use raw browser dropdowns. Search has a brutalist `.sort-select` + chevron pattern that nobody else copied.
4. **Mixed card languages** — most pages use ad-hoc 1px-bordered panels. Search/detail uses `.brutal-card`. We have the chrome; pages don't reach for it.
5. **Pagination drift** — search uses a windowed brutalist pagination with hard-shadowed page buttons. Bookmarks uses prev/next + a page-jump input. Jobs uses prev/next only. Midden uses bare `.action-btn`. No two pages agree.
6. **Custom button salads** — `.btn-sm`, `.btn-save`, `.btn-save--publish`, `.toggle-btn`, `.triage-btn--submit`, `.upload-btn`, `.hec-...`-prefixed buttons. None inherit the `.action-btn`/`.btn-primary` system.
7. **Dashboard ledger** — current "Ledger" widget is just a `<ol class="feed">` of recent activity, redundant with the existing activity feed. User wants it gone and replaced with something more useful (quick-links, optional user-pinned shortcuts, possibly grid/feed/list quick toggles).
8. **The Fallen, The Queue** — wide tables that horizontal-scroll on every screen below ~1200px. Need to skim columns and/or switch to vertical cards on narrow viewports.
9. **Hecatomb** — flat form with no card grouping; filters buried in a `<details>` so most users never use them; no preview of "how many items match" before submitting.
10. **Slop / Midden** — grids of soft 1px-bordered cards (not `.brutal-card`); hardcoded status-badge backgrounds that don't flip in dark mode; select-all UX diverges from the search blueprint's tri-state pattern.

Per-page punch lists live in the appendix at the bottom of this file.

### Approach

The shape of the solution is **shared primitives, then page-by-page adoption**. Concretely:

1. **One foundation commit** that adds the missing reusable pieces (`BrutalSelect.astro` partial, `BrutalPagination.astro` partial, a couple of admin.css additions for "form-card" + "config-card" patterns, a `--status-ok` / `--status-fail` / `--status-warn` token trio so badges stop hardcoding `#080`/`#c00`).
2. **One commit per affected page-group** that rewrites the markup to use those primitives + replaces hardcoded hex with tokens. No JS logic touched unless it directly serves the redesign.
3. **One governance commit** at the end that:
   - Adds a `scripts/lint-design.mjs` script flagging hardcoded hex in `src/pages/admin/**/*.astro` and `src/components/**/*.svelte` (excluding atelier).
   - Wires it into `npm run format:check` and the pre-commit hook.
   - Extends `docs/frontend.md` with a "New admin page checklist" and links the partials.

All commits go on `polish/admin-consistency`; one PR; squash-merged commits keep the history readable.

### Functional additions (small)

Three small things that aren't strictly visual but the user requested as part of this pass:

- **URL-driven view mode** on Stacks/grid pages — `?view=grid|feed|list` survives reload and is shareable. Today it's localStorage-only.
- **Dashboard ledger → quick-links** — replace ledger panel with a quick-links board: built-in shortcuts (Random search, Latest tributes, Hecatomb a random batch, etc.) plus a user-editable list stored in `localStorage` (no server change).
- **Hecatomb candidate preview** — debounced count of matching items above the submit button. Reuses existing `/api/search` count endpoint. No new API.

If these turn out to be more invasive than expected I'll drop them out of this PR and file a follow-up.

### Primitives we're adding

#### `BrutalSelect.astro`

Astro partial wrapping a `<select>` with the search-page chevron overlay + 2px border + hover lift. Drop-in replacement for `<select class="filter-select">`. Same `name`, `id`, `value`, and `<option>` slot. Server-rendered, no JS island needed.

```astro
<BrutalSelect id="status" name="status" value={current}>
  <option value="all">All</option>
  <option value="pending">Pending</option>
</BrutalSelect>
```

#### `BrutalPagination.astro`

Astro partial rendering the windowed search-blueprint pagination shape from a `{ current, total, hrefFor(page) }` prop. Used wherever we render server-paged lists. Client islands that want to drive it without navigation can swap to the dynamic version (a small JS helper in `src/lib/pagination.ts`) — same DOM shape, JS sets `hrefFor` to `#` and dispatches a `pagination:go` CustomEvent.

#### `BrutalCard.astro`

Astro partial that emits the `.brutal-card` / `.brutal-card__head` / `.brutal-card__body` triple with a `title`, optional `action` slot (e.g. a `<button>` for the header) and a default slot for body content.

```astro
<BrutalCard title="Filters">
  <Fragment slot="action"><button>Clear</button></Fragment>
  <slot/>
</BrutalCard>
```

#### Token additions in `tailwind.css`

```css
@theme {
  --color-status-ok: #1e7a1e;       /* readable on both #fff and #111 */
  --color-status-fail: #c4453a;     /* warm red — current activity-feed red */
  --color-status-warn: #b8860b;     /* matches --color-accent */
  --color-status-pending: var(--color-muted);
}
html[data-theme='dark'] {
  --color-status-ok: #5ec25e;
  --color-status-fail: #f0746a;
  --color-status-warn: #e0a020;
}
```

Plus a `--color-overlay` token for the `rgba(0,0,0,0.5)` family overlays we have everywhere; it stays semi-opaque black in light mode and shifts to semi-opaque white-on-very-dark in dark mode (so glyph badges over media tiles stay readable).

#### admin.css additions

- `.brutal-card__head--inverted` — header bar that paints `var(--color-fg)` background with `var(--color-bg)` text, for selection/batch banners.
- `.config-card` extends `.brutal-card` with a labeled-grid body, used by Workspace's "Configure batch" and Hecatomb's main form.
- `.empty-state--card` variant that wraps the dashed empty state in a `.brutal-card` for consistency.

### Commit plan

Eight commits, all on `polish/admin-consistency`, all in one PR.

| # | Subject | Files |
|---|---------|-------|
| 1 | `feat(admin): shared brutalist primitives + status color tokens` | `src/styles/tailwind.css`, `src/styles/admin.css`, `src/components/BrutalCard.astro` (new), `src/components/BrutalSelect.astro` (new), `src/components/BrutalPagination.astro` (new), `src/lib/pagination.ts` (new), `docs/frontend.md` |
| 2 | `fix(catalog): dark-mode + collapsible cards on new/edit forms` | `catalog/new.astro`, `catalog/edit.astro`, `catalog/index.astro` |
| 3 | `feat(winnowing): card chrome + brutalist controls on failures/slop/midden/nomenclator` | `search/failures.astro`, `search/slop.astro`, `search/midden.astro`, `search/nomenclator.astro` |
| 4 | `feat(altar): workspace + tribute + jobs polish` | `search/workspace.astro`, `search/upload.astro`, `jobs/index.astro`, `jobs/detail.astro` |
| 5 | `feat(hecatomb): card-grouped form + candidate preview + brutalist controls` | `hecatomb.astro` |
| 6 | `feat(dashboard): drop ledger, brutalist cards, quick-links board` | `dashboard.astro` |
| 7 | `feat(dog-ears): windowed pagination + brutalist filters + tile polish` + URL-driven view mode | `bookmarks.astro`, `src/lib/view-mode.ts` (new), apply to search/grid pages that use view toggle |
| 8 | `feat(darkroom+sacristy): latents/api-keys/settings polish + design lint` | `latents/*.astro`, `api-keys.astro`, `settings.astro`, `scripts/lint-design.mjs` (new), `.claude/hooks/precommit.sh` (extend), `package.json` |

Each commit is fully buildable and passes `npm run format:check` and `pytest`. Commits #2–#8 share the foundation from #1.

### Per-page changes (high level)

#### Catalog (commit #2)

- **Dark-mode fix is mandatory.** All hardcoded hex (`#f8f8f8`, `#c00`, `#080`, `#a00`, `#9a7209`, `#f8f6f0`) replaced with `var(--color-surface)`, `var(--color-status-fail)`, `var(--color-status-ok)`, `var(--color-accent)`, etc.
- `.collapsible` sections rewritten to `.brutal-card` + `.brutal-card__head` (with a chevron action button on the right). Same accordion behavior, brutalist chrome.
- `.btn-sm`, `.btn-save`, `.btn-save--publish` consolidated to `.btn-primary` / `.action-btn` / `.action-btn--publish`.
- `.entity-select` and the inline tag picker get 2px borders + `.brutalist-control` hover states.
- Catalog list table wrapped in a `.brutal-card` with the status tabs becoming `.brutal-card__head` actions. Tabs themselves get the brutalist hover/active treatment.

#### Winnowing — Failures / Slop / Midden / Nomenclator (commit #3)

- **Failures (The Fallen):** drop the 6-column wide table. New layout: vertical list of fat rows (one card per item, image thumb + error message + retry/triage actions on a single line on desktop, stacked on mobile). Brutalist selection bar at top, copied from search.
- **Slop / Midden:** wrap each tile in `.brutal-card`; status badges use the new tokens; select-all matches the search blueprint's tri-state pattern (checkbox + "N selected" + "select all M matching" link, all in `.brutal-card__head--inverted`). Add a stats strip ("117 items, 4 jobs, oldest 3d ago") and a group-by-job toggle that re-renders the grid grouped by submitting job.
- **Nomenclator:** every native `<select>` swapped for `BrutalSelect`. `.triage-card` renamed and rebuilt on `.brutal-card`. `.quick-tag`, `.tag-chip`, `.triage-btn--*` all standardized to `.brutalist-control` variants.

#### Altar — Workspace / Tribute / Jobs (commit #4)

- **Workspace:** the bare grid wrapped in `.brutal-card`-bodied results area. Config section becomes `.config-card`. Hardcoded overlay rgbas replaced with `--color-overlay`. Process bar's select becomes `BrutalSelect`. Shuffle grid items get the brutalist border + shadow.
- **Tribute (upload):** upload zone gets `.brutal-card--dashed` (a dashed-border variant); file items become brutalist cards; status colors use the new tokens.
- **Jobs (The Queue):** table goes from 10 columns to a vertical card-row layout. Each row: bigger 96px thumbnail (180px on hover-able desktop), status pill, app/recipe, submitter + time, item count, output count chip, action buttons. Filters bar becomes a brutalist toolbar. Pagination uses `BrutalPagination`. Detail page sections wrapped in `.brutal-card` blocks; output preview cards become `.brutal-card` too.

#### Hecatomb (commit #5)

The current page is a flat `<form>` with filters hidden in a `<details>`. Redesign:

```
┌── Recipe ─────────────────┐  ┌── Count ───┐
│ App: [select]             │  │ N: [10  ▼] │
│ Recipe: [select] [✓ Random]│  │            │
└───────────────────────────┘  └────────────┘

┌── Filters ────────────────────────────────┐
│ Channel: [select]   Tags: [...]           │
│ Min reactions: [...]  Min tags: [...]     │
│ Has-text: [...]   Output index: [select]  │
│                                            │
│ Estimated candidates: 247 items           │ ← live, debounced
└────────────────────────────────────────────┘

[ Hecatomb ] ← brutalist primary; disabled when no candidates
```

Three `.brutal-card`s. Filters are visible by default (not buried). Below the filters card, a live "Estimated candidates: N" computed by calling `/api/search?index=all&count_only=1&...` (already exists for the filter bar; reuse via `searchCount()` helper). Submit button moves to its own row.

#### Dashboard (commit #6)

- "Ledger" panel removed.
- Stat tiles become `.brutal-card`s with a header + a big number in the body.
- Action queue ("Docket") becomes `.brutal-card` list with `.brutal-card__head--inverted` for urgent items.
- "Altar of the Day" becomes `.brutal-card` with the media tile as the body.
- **New "Quick Links" panel** — `.brutal-card` with a body of brutalist link buttons:
  - Built-in row: "Random search", "Latest tributes (Stacks?sort=newest)", "Latest jobs", "Triage failures", "Latents in progress"
  - User-pinned row: buttons sourced from `localStorage.dashboardLinks` (JSON array of `{label, href}`); inline "+ Add" button opens a tiny in-page dialog (no server change).
- Activity feed stays but is wrapped in `.brutal-card` and its colors use tokens.

#### Dog-ears (commit #7)

- `.filter-input` and the two `.filter-select`s replaced by `BrutalSelect` + brutalist search input matching the Stacks toolbar.
- Pagination switched to `BrutalPagination` (windowed). Page-jump input retained but rebuilt as a brutalist input next to the page list.
- `.bm-card`s get the brutalist border + shadow treatment.
- Hardcoded overlay rgbas → `--color-overlay`.
- Adds `?view=grid|feed|list` URL state via a small `src/lib/view-mode.ts` helper. Applied here, on workspace, and (carefully) on the search index. localStorage stays as fallback; URL wins when present.

#### Latents + Keys + Settings + governance (commit #8)

- Latents: light touch — chips/filter bar wrapped in `.brutal-card`; status colors use the new tokens; meta-row inputs get the brutalist focus state; native select swapped for `BrutalSelect`.
- API keys: warning box uses `var(--color-status-warn)` background mix and a `var(--color-accent)` border instead of `#fffbe6`; warning still pops in both modes.
- Settings: `.linked-pill`, success/error message colors → tokens. Settings cards consolidated to `.brutal-card`.
- **Design lint**: `scripts/lint-design.mjs` (Node, no deps beyond `node:fs`) walks `src/pages/admin/**/*.astro` and `src/components/*.svelte` (excluding atelier paths). Flags:
  - Hardcoded color literals in `<style>` blocks (`#XXX`, `#XXXXXX`, `rgb(...)`, `rgba(...)`) except a small allowlist (`#000`, `#fff` for media overlays explicitly tagged with a comment, plus our spec tokens).
  - 1px borders inside admin pages (warn only — sometimes intentional).
  - Direct usage of removed legacy class names (`.btn-sm`, `.btn-save`, `.toggle-btn`).
  Wired into `npm run lint:design` and the pre-commit hook.
- `docs/frontend.md` gets a new "New admin page checklist" subsection linking to the partials and the lint command.

### Tests

- `tests/test_design_lint.py` — runs `node scripts/lint-design.mjs --json` and asserts the exit code is 0 + the report is empty. CI catch.
- No behavioral changes mean existing pytest suite is the regression net. We rely on dev-server visual checks + the user's live smoke test.
- Optional: a Playwright snapshot test for one canonical admin page per section. Decision: skip in this PR (overhead higher than the value for a one-shot polish). If we want it, file as follow-up.

### Risk + rollback

Each commit is independently revertable. The foundation commit is additive (no class renames at the CSS level; existing class names stay but are augmented). Commits #2–#7 each touch only the pages in their group. Commit #8 is mostly governance — easy to back out.

Bigger risk: pagination changes on bookmarks could break in-page links if anything else writes to the page-jump input. We'll grep before touching. Workspace's grid overlays are touched by Player too — we'll verify the play-overlay layout is unchanged.

### Going-forward (the user's "how do we stop this drift")

In priority order:

1. **Lint** (this PR). Hardcoded colors and dead-class regressions get caught on commit.
2. **Partials** (this PR). `BrutalCard`, `BrutalSelect`, `BrutalPagination` make the right thing the easy thing.
3. **`docs/frontend.md` "new admin page" checklist** (this PR). Single page, ~20 bullets, references the partials.
4. **Optional follow-up:** Playwright snapshot tests on a few canonical pages. Catches visual drift on PRs that the lint can't see (e.g. removed border, missing gap). Not in this PR.
5. **Optional follow-up:** a `/dev/style-gallery` admin-only page that renders one of each primitive. Single-page reference + smoke test for the design system. Not in this PR.

### Open questions for the user

- Dashboard quick-links: built-in row OK, but is **user-pinned links via localStorage** acceptable, or do they want it server-backed so it follows them across devices? Default to localStorage for this PR.
- URL-driven view mode: shareable URLs change behavior slightly (someone landing on `?view=list` sees list even if they prefer grid). Default decision: URL beats localStorage; localStorage still records last manual choice.
- Hecatomb candidate preview: requires a count-only call on every filter change (debounced 300ms). Acceptable load? Search already does this for the filter bar so it's a known cost.

If these don't surface objections during the smoke test, ship.

### Appendix — per-page punch lists

Captured during the survey; line numbers reference `polish/admin-consistency` at the head of master (commit `3352540`).

- **catalog/new.astro** — `:260` `#f8f8f8` heading bg (dark-mode primary bug); `:353,405,407` `#c00`/`#a00` reds; `:419` `#f8f6f0` cream drop zone; `:476-486` `#c00`/`#fff` track-remove; `:504,507` `#2a2`/`#c00` status; `:610` `#9a7209` hover; `.btn-sm` / `.btn-save` /`.entity-select` non-brutalist; `.collapsible` should be `.brutal-card`; local `.form-row`/`.form-group` redefs duplicate admin.css.
- **catalog/edit.astro** — identical hardcodes; `.link-row`, `.freeform-row` are local one-offs.
- **catalog/index.astro** — `.status-tabs` ad-hoc divider; `.btn-primary` instead of `.action-btn` for "New Release"; release table fine but wants a `.brutal-card` wrapper.
- **search/failures.astro** — 14 issues, headline: 6-col h-scroll table, plain `<select>`, hardcoded error red.
- **search/slop.astro** — 17 issues: `.slop-card` 1px no shadow, `rgba(255,255,255,...)` banners, hardcoded status badge colors.
- **search/midden.astro** — 14 issues, same shape as slop + hardcoded countdown colors.
- **search/nomenclator.astro** — 17 issues: 5× plain selects, `.triage-card` not `.brutal-card`, `.tag-chip`/`.triage-btn` non-brutalist, `#c00` chip close.
- **search/workspace.astro** — 18 issues: hardcoded grid overlay colors, `.config-section` not `.brutal-card`, plain select in process bar, hardcoded play/duration/type-badge backgrounds.
- **search/upload.astro** — 15 issues: no card chrome, `.upload-btn` and `.upload-zone__btn` duplicate, `#080`/`#c00` status colors.
- **dashboard.astro** — 12 issues: ledger redundant, stats lack `.brutal-card`, `#00000006` altar bg, `#c4453a` urgent accent.
- **bookmarks.astro** — 15 issues: 3 stock selects + `.filter-input`, plain prev/next pagination + plain page-jump, hardcoded `#111` / `rgba(0,0,0,...)` overlays.
- **jobs/index.astro** — 14 issues: 10-col h-scroll, batch banner soft, prev/next only, `#111` thumb bg.
- **jobs/detail.astro** — 8 issues: log block hardcoded, sections lack card chrome, output cards 1px.
- **hecatomb.astro** — 16 issues: 4 stock selects, filters hidden in details, no card grouping, no preview, hardcoded error red.
- **latents/{index,new,detail}** — light: chips not brutalist, status colors hardcoded, meta-row inputs inconsistent, native select on detail.
- **api-keys.astro** — 3 issues: `#fffbe6` warning bg (dark-mode); section heading uses inline styles.
- **settings.astro** — 6 issues: `.linked-pill` `#f4f4f4`, hardcoded green success, plain role select.
