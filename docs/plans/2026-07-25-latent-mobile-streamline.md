# Latent detail on mobile — streamline without losing anything

**Status:** design agreed from an interactive mockup (2026-07-25), built in this
branch. Mockup: `https://claude.ai/code/artifact/9cc46440-148a-4bf4-9092-e992536f9f45`

## Why

The latent detail page on a phone is both too long and too crowded. Measured on
`origin/master` for a six-slot latent with eight files each and a dozen loose
files, the page is **~16,200px — about 20 phone screens**, and every section is
expanded at all times.

Where the weight is:

| Region | Est. at 390px |
|---|---|
| Slots (6 cards) | ~9,400px |
| Loose files (12 tiles, 2-up) | ~2,700px |
| Playlists section | ~1,700px |
| Header | ~490px |
| Marginalia "Latest comments", open by default | ~520px |
| Documents — `rows="18"` textarea, always open | ~460px |
| Unconditional bottom padding + player spacer | ~237px |

Two structural causes:

1. **Per-row cost.** A slot file row carries up to 13 interactive elements, 9 of
   them icon-only (`✏️ ▶ × 🗑 ★ ⠿ ↑ ↓`), each forced to 44×44 at ≤640px and
   wrapping onto its own line — 112px minimum per row before the actions wrap.
   `.file-row__type` and `.file-row__size` are `display: none` there, so the
   extension and size are simply lost on mobile.
2. **Per-card furniture.** `LatentLinks` (with its two-field add form) and the
   `Uploader` dropzone render inside **every** slot card, always — `1 + S`
   instances each, ~250–350px per card of intake chrome you scroll past when
   you are only listening.

## The constraint that shaped the design

Asked which job the phone is for — auditioning, intake, or monitoring — Brendan
said **all of them**: "different users may have different priorities so we want
all these features to be prevalent." So nothing may be demoted to a hard-to-find
place. Combined with the existing rule that affordances are labeled
(`docs/plans/2026-07-17-latent-section-styles.md`, "No icon-only or dot-only
affordances") and the documented failure of *burying* controls
(`docs/plans/2026-05-21-admin-design-polish.md`: "filters buried in a `<details>`
so most users never use them"), the design rule is:

> Fewer things on screen at once, and **more** explicit when you get to them.
> Every job keeps a visible, labeled, counted door. Nothing hides behind a glyph.

## Decisions (settled against the mockup, in order)

1. **One labeled tab strip per slot card** — `Files(4) · Playlist(3) · Add
   files · Links(2) · Notes · Threads(2) · Runs`. One body at a time, Files
   default. This is what lets the dropzone and link form stop taxing every
   scroll while staying one tap away. Reverses the current
   "Files are ALWAYS visible inline. No click-to-expand." comment, deliberately.
2. **Row actions collapse into `More ▾`, and get labels inside** — `Rename ·
   Download · Open in Stacks · Remove from slot · Delete permanently`, with the
   file's size, length and date across the top (the metadata mobile currently
   hides). `Download` is new to slot rows; loose tiles already had it.
3. **Comments stay in the row**, never inside `More` — `💬 2`, or `💬 +` when
   there are none yet, so the door to the first comment is visible. Also on
   playlist track rows.
4. **Reorder: grip on top, arrows paired beneath** — `⠿` full width, then
   `↑ ↓` side by side. 78px instead of 112px, in a 76px column instead of 44px.
5. **Tabs are boxed and wrap to two lines. Never a scroll strip, never
   shrink-to-fit.** Each tab gets its own 1px box; counts close up against the
   name (`Files(4)`, not `Files (4)`). Tabs hug their text — no 44px minimum.
6. **Sections collapse to a summary that carries their state** — `Repo ·
   A-U-Supply/ashes · synced 2h ago`, `Documents · 1 doc · Lyrics · saved
   11:04`, `#2 Bounces · 2 files · 2 audio · forming`. Collapsed, the page is a
   status board; expanded, nothing is missing.
7. **Status becomes one labeled control** — `Status: developing ▾` opening the
   choices, instead of three pills at 0.7rem/2px that never met the touch rule.
8. **Slot-level config moves behind `Slot tools ▾`** — rename, reorder, clear
   files, delete slot.
9. **The section map becomes wrapping boxed chips**, still sticky, instead of a
   row that scrolls off the edge.
10. **The star sizes to its line** — no 44px minimum forcing the identity line
    open; star, letter square (32px → 20px) and filename share one line.
11. **Playlists section**: order names become boxed wrapping tabs, the bar
    (`▷ Play all`, count, `+ Add tracks`, `Order tools ▾`) sits above the
    panels because it acts on the selected order, and `Rename`/`Delete` move
    into the tools menu.
12. **Nothing is removed.** Every control in production has a home; the audit
    that proves it is below.

### Deliberately *not* changed

- The `↑ ⠿ ↓` reorder controls stay always-on. They are function, not
  furniture, and were asked for a session ago.
- Faces/backgrounds, the Style button and panel, and every colour decision.
- Desktop layout. Every rule here is inside `@media (max-width: 640px)` or is
  structural in a way that reads the same on both.

## Build

Reuse first — most of this exists:

| Need | Reuse |
|---|---|
| Tab strip | `.status-tabs` / `.tab` in `admin.css:635` (already `overflow-x`-aware, correct ARIA precedent in `notifications.astro`) |
| Section collapse | `.disclosure` in `admin.css:1001` — a finished `<details>` primitive with a 36px tap target, used on exactly one page today |
| Bottom sheet | `LatentStylePanel`'s pattern (the one the docs point at); currently copy-pasted three ways with `MarginaliaBadge` and `LatentPlaylists` |
| Inline disclosure | The ColorPicker accordion technique, already cited by the playlist accordion |
| Drag options | `DRAG_OPTS`, currently duplicated as source in `LatentSlots` and `LatentPlaylists` |

New shared pieces (extractions, so the net line count should fall):

- `src/lib/sheet.ts` — open/close + body scroll lock, replacing the two inline
  `document.body.style.overflow = 'hidden'` copies.
- `src/lib/dragOptions.ts` — one `DRAG_OPTS`, imported by both components.
- `src/components/RowActions.svelte` — the `More ▾` disclosure (inline under
  640px logic lives here), used by slot rows, playlist rows and loose tiles.
- `src/components/RowMove.svelte` — grip-over-paired-arrows, used by all three
  reorder lists.

Files changed: `LatentSlots.svelte` (2,751 lines — the bulk), `LatentPlaylists`,
`LatentLooseFiles`, `LatentLinks`, `LatentDocuments`, `LatentRepoStrip`,
`LatentHero`, `LatentSectionMap`, `MarginaliaRecent`,
`src/pages/admin/latents/detail.astro`, `src/styles/admin.css`.

## Free fixes folded in

Found while measuring; no design tradeoff:

- `.admin-main` reserves `padding-bottom: calc(var(--space-md) + 120px)` ≈ 137px
  **unconditionally**, whether or not the player is visible, on top of the
  player's own 100px spacer. `body.player-active` already exists — gate it.
- The player's anchored panels dock at `bottom: 96px` while its spacer is
  100px. Pick one.
- The slot's `⋮⋮` drag handle has no touch target at any width (`padding: 0 4px`).
- `LatentLinks` and `ColorPicker` have **zero** media queries.
- Missing `aria-label`s: `✏️ ▶ × 🗑 ☆`, the playlist `▶`, the add-tracks `✕`,
  and `LatentLinks`' `✎`/`×`.

## Audit — what must still work after this

Every one of these exists on `master` today and must be verified by hand in the
running app, on a phone-width viewport, in both themes:

**Buttons, per surface**
- Slot head: rename slot (label tap), reorder slot, status change ×3, Style,
  clear files, delete slot.
- Slot file row: star/unstar (repaints the card face), open in Stacks (thumb and
  name), rename, play, remove from slot, delete permanently, download,
  comments badge → popover, session `▸ N extracted files` → children list, and
  `from session` → scroll-to-parent.
- Slot tabs: Files, Playlist, Add files (dropzone, `Browse files`, `Browse
  .logicx session bundle`, `+ Pull from index`), Links (add/edit/remove),
  Notes (autosave), Threads, Runs (`▶ Run`, `show output`, change/unlink source).
- Playlists: `▷ Play all`, per-track play-from-here, remove, reorder by drag and
  by arrows, `+ Add tracks` sheet (filter, select, `already in`), new order,
  rename, delete.
- Loose tiles: rename, play, set as card image, download, detach.
- Page: name, slug, kind, status ×4, description autosave, metadata add/edit,
  hero set/replace/remove/treatment/accent/auto, repo sync/webhook/unlink/import,
  documents add/rename/history/delete, threads new, marginalia seek links,
  section map jumps, `+ Add slot`.

**Playback specifically** (regression surface of PR #570, merged today)
- Play a file → the persistent player starts.
- **Auto-advance**: let a track end → the next one plays *without* a tap. This
  is the bug fixed in #570; the tab work must not reintroduce a `src=` binding
  or `bind:paused` on the media element (see `docs/player.md`).
- `▷ Play all` from a slot playlist and from a latent running order.
- Play from a marginalia timestamp (seeks, doesn't restart).
- Queue panel, prev/next, repeat one, repeat all wrap, shuffle.

**Naming**
- Rename a slot, a file (slot row **and** loose tile — same endpoint, two
  surfaces), a playlist, a document, the latent itself. Renamed file keeps its
  extension, thumbnails follow, and the new name appears in the player bar,
  the playlist rows and the marginalia list without a reload.

**Ordering**
- Drag and arrow reorder in: slot file list, slot playlist, latent running
  order. The pinning rule still holds — reordering files must not drag an
  already-arranged playlist along (`POST /slots/{id}/items/reorder` snapshots
  first).

**Gates** — `uv run pytest`, `npm run build`, `npm run format:check`,
`npm run lint:design`, and both themes checked in `npm run dev`.
