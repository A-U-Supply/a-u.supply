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

| Token | Value | Notes |
|-------|-------|-------|
| `--color-bg` | `#fff` | Page background |
| `--color-fg` | `#1a1a1a` | Text, borders, hard shadows |
| `--color-muted` | `#666` | Secondary text |
| `--color-border` | `#ccc` | Subtle dividers |
| `--color-accent` | `#b8860b` | Dark amber — used sparingly |
| `--font-mono` / `--font-sans` | Courier New | Mono everywhere |
| `--radius-*` | `0` | **No rounded corners by default.** `--radius-full` (`9999px`) only if you explicitly opt in. |
| `--shadow-sm` … `--shadow-xl` | `Npx Npx 0 var(--color-fg)` | Hard offset shadows. No blur. |

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

## Layouts

| File | Used by | What it provides |
|------|---------|------------------|
| `src/layouts/Base.astro` | All public pages | HTML shell + ViewTransitions + persistent Player |
| `src/layouts/Admin.astro` | All `/admin/*` pages | Sidebar + auth gate + page-loading indicator + Player + the page-background-painting JS |

### `<Admin current="…">`

Every admin page passes a `current=` prop that names the active sidebar entry. The sidebar renders `aria-current="page"` on the matching link. The mapping lives in [`glossary.md`](glossary.md) (the "Slug (`current=`)" column).

```astro
---
import Admin from '../../layouts/Admin.astro';
---

<Admin title="Auspices" current="dashboard">
  …page content…
</Admin>
```

If you add a new sidebar entry, also update both the `Admin.astro` `<aside>` list and the glossary.

### Page background colours

Some Atelier pages override the body background (Punctum: `#221c00`, Photism: `#1a1200`). The override lives in `src/layouts/Admin.astro` inside `applyPageBackground()`. Add new entries there.

## Svelte components

All under `src/components/`. Each is mounted directly from `.astro` pages as an island.

| Component | Purpose | Mount |
|-----------|---------|-------|
| `Player.svelte` | Persistent audio player | `client:only="svelte"` in both layouts; `transition:persist="audio-player"` keeps it alive across ViewTransitions |
| `Threads.svelte` | Generic threaded discussion (Latents, slots, media items). Props: `anchorType` (`project` / `slot` / `media_item`), `anchorId`, `communityId?`. Proxies all writes through the FastAPI `/api/threads` endpoints. | `client:visible` |
| `Uploader.svelte` | Drop / pick / upload UI for files into Emulsion. Props: `destination`, `projectId?`, `slotId?`, `defaultTags?` | `client:visible` |
| `LatentSlots.svelte` | Ordered slot cards with drag-to-reorder, per-slot files, pinned-primary thumbnails, notes, thread badge | `client:visible` |
| `LatentDocuments.svelte` | Tabbed markdown documents per Latent, autosave + revision history | `client:visible` |
| `LatentLooseFiles.svelte` | Loose files attached at the Latent level (slot_id IS NULL) | `client:visible` |
| `LatentRepoStrip.svelte` | Strip view of Latent contents — emits `latent:slots-changed` | `client:visible` |
| `PullFromIndex.svelte` | Modal that searches all four Meilisearch indices for files to attach to a Latent | `client:visible` |
| `IndexFilter.svelte` | bits-ui Select (multi) for filtering by media-type index. Emits a bubbling `index-change` CustomEvent | `client:visible` |

### Event-bus pattern

Pages and components talk to each other through DOM custom events on `document`. This is how the Player gets queued from anywhere and how Latents components signal back to host pages.

| Event | Detail | Who fires | Who listens |
|-------|--------|-----------|-------------|
| `player:queue` | `{ tracks: Track[], startIndex }` | Any page or component | `Player.svelte` |
| `latent:slots-changed` | `{}` | `LatentRepoStrip.svelte` after a slot mutation | Host page reloads its slot data |
| `index-change` | `{ value }` | `IndexFilter.svelte` | Whatever page hosts the filter |

See [`player.md`](player.md) for the full `player:queue` payload shape.

### Conventions for new Svelte components

- **Don't read or write a sibling component's state directly.** Communicate via `document.dispatchEvent` + DOM event listeners. The page acts as broker.
- **All API calls go through the FastAPI layer**, not Meilisearch / Lemmy / Slack directly. The browser must never see those URLs.
- **Style via `.brutalist-control`** for anything clickable. Component-specific styling in a scoped Svelte `<style>` block.
- **bits-ui first for new primitives** that need accessibility (dropdowns, popovers, dialogs). Roll your own only if bits-ui doesn't have it.

## Plain-JS-in-`.astro` pattern

A lot of admin pages are plain Astro markup + `<script>` blocks with vanilla TypeScript. That's the default — only reach for Svelte when you need component reuse, reactivity, or bits-ui integration. The two together is normal: an Astro page can host both a `<script>` block and an `<Svelte client:visible>` island, and they cross-talk via the event bus above.

## Formatting

```bash
npm run format        # Prettier with prettier-plugin-astro + prettier-plugin-svelte
npm run format:check  # CI check
```

Always format before committing.

## Related

- [`architecture.md`](architecture.md) — stack overview, directory layout, auth, data flow
- [`atelier.md`](atelier.md) — the Atelier section's tools
- [`player.md`](player.md) — `player:queue` event detail shape
- [`glossary.md`](glossary.md) — sidebar `current=` slug map
