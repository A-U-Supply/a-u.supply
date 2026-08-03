# Frontend

How the Astro / Svelte side is wired. For the big picture, see [`architecture.md`](architecture.md).

## Stack

- **Astro 5.x** — all pages, static output, view transitions. Every URL is a `.astro` file under `src/pages/`.
- **Svelte 5** — islands only. Mounted with `client:only="svelte"` (player) or `client:visible` (most others). No SSR.
- **Tailwind 4** — via the official Vite plugin. Brutalist tokens in `src/styles/tailwind.css`.
- **bits-ui 1.x** — headless component library for accessible primitives (Select, Popover, etc.). Styled with `.brutalist-control` so it inherits the look. `tailwind-variants` and `clsx` are available for variant composition.
- **sortablejs** — drag-to-reorder, used in Latents slots and elsewhere.
- **Vanilla CSS** — `src/styles/global.css` (public + shared) and `src/styles/admin.css` (admin shell, sidebar, login). Page-specific CSS uses Astro's scoped `<style>` blocks.

## UI kit — brutalist design tokens

Defined in `src/styles/tailwind.css` under `@theme`. These override Tailwind defaults so utilities and bits-ui components inherit the look without ad-hoc styling.

| Token                         | Value                       | Notes                                                                                        |
| ----------------------------- | --------------------------- | -------------------------------------------------------------------------------------------- |
| `--color-bg`                  | `#fff`                      | Page background                                                                              |
| `--color-fg`                  | `#1a1a1a`                   | Text, borders, hard shadows                                                                  |
| `--color-muted`               | `#666`                      | Secondary text                                                                               |
| `--color-border`              | `#ccc`                      | Subtle dividers                                                                              |
| `--color-accent`              | `#b8860b`                   | Dark amber — used sparingly                                                                  |
| `--font-mono` / `--font-sans` | Courier New                 | Mono everywhere                                                                              |
| `--radius-*`                  | `0`                         | **No rounded corners by default.** `--radius-full` (`9999px`) only if you explicitly opt in. |
| `--shadow-sm` … `--shadow-xl` | `Npx Npx 0 var(--color-fg)` | Hard offset shadows. No blur.                                                                |

There's an additional fluid type and spacing scale in `src/styles/global.css` (`--text-sm` through `--text-2xl`, `--space-xs` through `--space-xl`, all `clamp()`-based). Use those rather than hard-coded `rem`/`px` for page-level spacing and typography.

### `.brutalist-control`

The reusable component class for admin controls — defined in `src/styles/tailwind.css` under `@layer components`. Apply it on any clickable element (button, popover trigger, list item) that should match the look.

Properties:

- 2px solid border in `--color-fg`
- 2px offset hard shadow
- Uppercase Courier, 0.5pt tracking
- Hover lifts -1px, active sinks +2px (and squashes the shadow)
- Selected/active state inverts to `--color-fg` background with `--color-bg` text. Hooks into `[data-state='open']`, `[data-selected='true']`, `[aria-pressed='true']` — these are what bits-ui sets automatically.

When integrating a new bits-ui primitive, slap `class="brutalist-control"` on the visible part (`Select.Trigger`, `Popover.Trigger`, etc.) and you're done.

### Interaction conventions

- **Plain click = single select / swap state.** Default mouse behaviour.
- **Modifier-click (Cmd / Ctrl / Shift) = multi-select.** Standard everywhere lists are selectable (workspace, search, Stacks).
- **Chunky controls.** 2px borders + drop shadow on anything the user clicks. No flat / hover-only buttons in the admin.

### Atelier tool themes

Atelier tools can override the brutalist look with their own scoped theme. The pattern: a CSS file under `src/styles/atelier/<tool>.css` that:

1. Defines tool-specific custom properties inside the tool's root class scope
2. Overrides `.brutalist-control` within that scope (thinner borders, no shadows, custom transitions)
3. Components reference `var(--tool-xxx)` instead of hardcoded hex colors

See [`atelier.md`](atelier.md) for the per-tool theme files and body background colours. Litany (`src/styles/atelier/litany.css`) is the reference implementation — a dark mpump-style groovebox theme with layered backgrounds, 1px borders, monospace fonts, and color-coded UI states.

## Layouts

| File                      | Used by              | What it provides                                                                        |
| ------------------------- | -------------------- | --------------------------------------------------------------------------------------- |
| `src/layouts/Base.astro`  | All public pages     | HTML shell + ViewTransitions + persistent Player                                        |
| `src/layouts/Admin.astro` | All `/admin/*` pages | Sidebar + auth gate + page-loading indicator + Player + the page-background-painting JS |

### `<Admin current="…">`

Every admin page passes a `current=` prop that names the active sidebar entry. The sidebar renders `aria-current="page"` on the matching link. The mapping lives in [`glossary.md`](glossary.md) (the "Slug (`current=`)" column).

```astro
---
import Admin from '../../layouts/Admin.astro';
---

<Admin title="Auspices" current="dashboard"> …page content… </Admin>
```

If you add a new sidebar entry, also update both the `Admin.astro` `<aside>` list and the glossary.

### Page background colours

Some Atelier pages override the body background (Punctum: `#221c00`, Photism: `#1a1200`). The override lives in `src/layouts/Admin.astro` inside `applyPageBackground()`. Add new entries there.

## Svelte components

All under `src/components/`. Each is mounted directly from `.astro` pages as an island.

| Component                 | Purpose                                                                                                                                                                                                          | Mount                                                                                                             |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `Player.svelte`           | Persistent audio player                                                                                                                                                                                          | `client:only="svelte"` in both layouts; `transition:persist="audio-player"` keeps it alive across ViewTransitions |
| `Threads.svelte`          | Generic threaded discussion (Latents, slots, media items). Props: `anchorType` (`project` / `slot` / `media_item`), `anchorId`, `communityId?`. Proxies all writes through the FastAPI `/api/threads` endpoints. | `client:visible`                                                                                                  |
| `Uploader.svelte`         | Drop / pick / upload UI for files into Emulsion. Props: `destination`, `projectId?`, `slotId?`, `defaultTags?`                                                                                                   | `client:visible`                                                                                                  |
| `LatentSlots.svelte`      | Ordered slot cards with drag-to-reorder, per-slot files, pinned-primary thumbnails, notes, thread badge                                                                                                          | `client:visible`                                                                                                  |
| `LatentDocuments.svelte`  | Tabbed markdown documents per Latent, autosave + revision history                                                                                                                                                | `client:visible`                                                                                                  |
| `LatentLooseFiles.svelte` | Loose files attached at the Latent level (slot_id IS NULL)                                                                                                                                                       | `client:visible`                                                                                                  |
| `LatentRepoStrip.svelte`  | Strip view of Latent contents — emits `latent:slots-changed`                                                                                                                                                     | `client:visible`                                                                                                  |
| `PullFromIndex.svelte`    | Modal that searches all four Meilisearch indices for files to attach to a Latent                                                                                                                                 | `client:visible`                                                                                                  |
| `IndexFilter.svelte`      | bits-ui Select (multi) for filtering by media-type index. Emits a bubbling `index-change` CustomEvent                                                                                                            | `client:visible`                                                                                                  |
| `MarginaliaList.svelte`   | Timestamped comments + cue markers for one media item (list, composer, reply/resolve/edit/delete, seek links). Shared helpers live in `marginalia.ts`.                                                           | Mounted imperatively (search detail)                                                                              |
| `MarginaliaBadge.svelte`  | Compact "💬 n" count chip + popover for slot rows / loose tiles; seeks via `player:queue` + `start_time` or bare `player:seek`                                                                                   | Inside LatentSlots / LatentLooseFiles rows                                                                        |
| `MarginaliaRecent.svelte` | "Latest comments & markers" strip for a Latent (reads the `marginalia` index by `project_id`)                                                                                                                    | Mounted imperatively (Latent detail)                                                                              |

### Event-bus pattern

Pages and components talk to each other through DOM custom events on `document`. This is how the Player gets queued from anywhere and how Latents components signal back to host pages.

| Event                  | Detail                                         | Who fires                                      | Who listens                                            |
| ---------------------- | ---------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------ |
| `player:queue`         | `{ tracks: Track[], startIndex, start_time? }` | Any page or component                          | `Player.svelte`                                        |
| `player:seek`          | `{ seconds }`                                  | Marginalia components (item already playing)   | `Player.svelte`                                        |
| `player:time-request`  | `{}`                                           | Marginalia composers                           | `Player.svelte` → re-fires `player:time` synchronously |
| `latent:slots-changed` | `{}`                                           | `LatentRepoStrip.svelte` after a slot mutation | Host page reloads its slot data                        |
| `index-change`         | `{ value }`                                    | `IndexFilter.svelte`                           | Whatever page hosts the filter                         |

See [`player.md`](player.md) for the full `player:queue` payload shape.

### Conventions for new Svelte components

- **Don't read or write a sibling component's state directly.** Communicate via `document.dispatchEvent` + DOM event listeners. The page acts as broker.
- **All API calls go through the FastAPI layer**, not Meilisearch / Lemmy / Slack directly. The browser must never see those URLs.
- **Style via `.brutalist-control`** for anything clickable. Component-specific styling in a scoped Svelte `<style>` block.
- **bits-ui first for new primitives** that need accessibility (dropdowns, popovers, dialogs). Roll your own only if bits-ui doesn't have it.
- **Reorderable lists go through `createSortable()`** (`src/lib/dragOptions.ts`), never `Sortable.create` directly. `scripts/lint-design.mjs` enforces it.
- **Anything published onto `<html>` or `<body>` goes through `documentState.ts`** (`src/lib/`), never `documentElement.style.setProperty` or `document.body.classList` directly. Also lint-enforced.

### What a ClientRouter navigation destroys

A view-transition navigation is not a repaint of the same document. Astro's swap:

- **`swapRootAttributes()` removes _every_ attribute from `<html>`** and copies the incoming document's over. Only `data-astro-transition` and `data-astro-transition-fallback` survive — an inline `style` with your custom properties in it does not.
- **`swapBodyElement()` replaces `<body>` outright**, so its classes go with it.

An island marked `transition:persist` sails through both. Its effects don't re-run, and a `ResizeObserver` on it never fires — the element never changed size, only the document around it did. **So a persistent island's published state is erased and nothing puts it back.**

Measured at 390px on 2026-08-02: `--player-h` is `165px` with a track playing; one navigation later it is gone, and the comment window (`bottom: var(--player-h, 72px)`) sits **93px behind the player bar**. Reported from a phone that night. The same wipe silently reverts #592 for video — the PiP slides back under the transport — and drops `body.player-active`, so the page's bottom padding stops clearing the bar.

`src/lib/documentState.ts` owns both writes and re-applies them on `astro:after-swap`. Values are **re-measured, not remembered**: the point of `--player-h` is that a measurement can't drift out of sync with the layout, and restoring a stored number would hand that back. `tests/test_player_across_nav_browser.py` navigates with the player up and asserts the comment window still clears the bar.

### Drag-to-reorder, and why it has one entry point

SortableJS rearranges the DOM as you drag. Every reorderable list here is a Svelte keyed `{#each}`, and **Svelte tracks each item as a _range_ of nodes, not one node** — a row followed by an `{#if}` block is `<li>` … anchor comment. Sortable moves the `<li>` alone, so the range breaks: the row now sits ahead of the comment that is supposed to end it.

On the next update Svelte's `move()` (`svelte/internal/client/dom/blocks/each.js`) walks forward from the row looking for that end node, never reaches it, and cycles the nodes in front of its destination for ever. **The tab locks up** — Chrome's "Page Unresponsive" dialog, reported 2026-08-02 after two drags inside one slot. Short of hanging, the same divergence renders an order nobody asked for: the row you dropped on lands where the row you dragged came from.

Nothing catches this short of a browser. Nothing throws, nothing logs, and the API is right the whole time — every reorder reached the server correctly, including the ones the screen got wrong.

So `createSortable()` takes the order the drop produced, puts the DOM back exactly as Svelte left it, and hands that order to `onDrop`. **Assigning state is what moves the row**, which puts a drag on the same path as the ↑/↓ buttons beside it. `tests/test_slot_reorder_freeze_browser.py` drives two real drags and asserts the page still answers and the screen matches the server.

## Astro partials (shared admin chrome)

For chrome that doesn't need reactivity (cards, dropdowns, pagination), prefer an Astro `.astro` partial under `src/components/` over a Svelte island. These render server-side, ship no JS, and inherit the brutalist tokens automatically.

| Partial                  | When to use                                                                                                                                                                                                     |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BrutalCard.astro`       | Any section that needs the 2px-border + 4px-shadow chrome. Pass `title`, optional `action` slot (single header button) and `meta` slot. `inverted` for selection/batch bars.                                    |
| `BrutalSelect.astro`     | Filter / sort / option `<select>`. Drop-in replacement for `<select class="filter-select">` — applies the chevron overlay + hover-invert pattern from the Stacks toolbar.                                       |
| `BrutalPagination.astro` | Windowed prev/next + page-buttons pagination. Pass `current`, `total`, and a `hrefFor(page)` function. For client-driven pages, pass `useEvents` and wire with `wirePagination()` from `src/lib/pagination.ts`. |

`src/lib/view-mode.ts` exposes `initViewMode()` for any page with a grid/feed/list toggle. URL `?view=` wins over `localStorage`; both are kept in sync.

## Status colors

Don't hardcode `#080`, `#c00`, or `#b8860b` for status text. Use the tokens — they have explicit dark-mode counterparts in `tailwind.css`:

```css
color: var(--color-status-ok); /* green checkmark, success */
color: var(--color-status-fail); /* red error, danger */
color: var(--color-status-warn); /* amber warning, accent */
color: var(--color-status-pending); /* muted gray, neutral */
```

For glyph badges floating over media tiles (play arrow, duration, type chip), use `var(--color-overlay)` / `var(--color-overlay-soft)` + `var(--color-on-overlay)` instead of `rgba(0,0,0,0.6)` + `#fff`. These flip correctly in dark mode.

## New admin page checklist

Before opening a PR for a new admin page (or before merging a redesign):

- [ ] Layout uses `<Admin title="…" current="…">` and the `current=` slug is in `glossary.md`.
- [ ] Every section that visually groups content is wrapped in `BrutalCard` (or a `.brutal-card` element with the head + body subdivisions).
- [ ] Every `<select>` is `<BrutalSelect>` (unless it needs full keyboard/popover accessibility, in which case use bits-ui `Select` styled with `.brutalist-control`).
- [ ] Every paged result list uses `<BrutalPagination>` or `renderPagination()` from `src/lib/pagination.ts`.
- [ ] Inputs use `.brutal-input` (or `.form-group input` if the surrounding `<div class="form-group">` is present).
- [ ] No hardcoded hex / rgba colors in scoped `<style>`. Use `var(--color-*)`. Errors / success / warnings use the `--color-status-*` family. Overlays use the `--color-overlay*` family.
- [ ] Selection bar (for any list with multi-select) follows the Stacks blueprint: tri-state checkbox + "N selected" + "Select all M matching" link. Wrap the bar in `BrutalCard` with `inverted`.
- [ ] `npm run lint:design` reports zero issues for the touched files.
- [ ] Verified in both light AND dark mode in `npm run dev` before opening the PR.

## Plain-JS-in-`.astro` pattern

A lot of admin pages are plain Astro markup + `<script>` blocks with vanilla TypeScript. That's the default — only reach for Svelte when you need component reuse, reactivity, or bits-ui integration. The two together is normal: an Astro page can host both a `<script>` block and an `<Svelte client:visible>` island, and they cross-talk via the event bus above.

## Formatting + lint

```bash
npm run format        # Prettier with prettier-plugin-astro + prettier-plugin-svelte
npm run format:check  # CI check
npm run lint:design   # Flags hardcoded colors in admin page <style> blocks
```

Always format before committing. The design lint runs in CI via
`tests/test_design_lint.py` — if it fails, swap the offending hex /
`rgba()` / named color for a `var(--color-*)` / `var(--color-status-*)` /
`var(--color-overlay*)` token. The Atelier and the Stacks blueprint are
intentionally skipped; the rest of `src/pages/admin/` is in scope.

## Related

- [`architecture.md`](architecture.md) — stack overview, directory layout, auth, data flow
- [`atelier.md`](atelier.md) — the Atelier section's tools
- [`player.md`](player.md) — `player:queue` event detail shape
- [`glossary.md`](glossary.md) — sidebar `current=` slug map
