# Drag a file from one slot card to another

## Context

Brendan: *"i want users to be able to drag and drop files from one slot card to
another."*

Today there is **no way to move a file between slots at all**. The row's
`More ▾` menu offers Rename, Download, Open in Stacks, Remove from slot, Delete
permanently — so getting take 3 out of Slot 1 and into Slot 2 means detaching it
and re-attaching from the index. On an album latent where slots are songs and
takes get reassigned constantly, that is the wrong shape.

**The endpoint is already written.** `PATCH /api/projects/{id}/items/{item_id}`
with `{ "slot_id": … }` (`server/latents_api.py:1402`, `MoveItemBody:674`)
moves an attachment between slots, or to loose files with `null`. It
repositions the row at the **end** of wherever it lands (`_next_item_position`)
— deliberately, since it has no claim on an order it was never part of — and
recomputes both slots' accents when the moved item is a starred image.
**It has no frontend caller and no test.** Same shape as `delete_project`
before #599: this is a UI job plus the missing tests.

**The drag library is already wired.** `LatentSlots.svelte` uses SortableJS
1.15 for intra-slot reordering — `sortableFiles()` (`:672`) over `.file-row`,
`handle: '.file-row__drag'`, sharing `DRAG_OPTS` from `src/lib/dragOptions.ts`.
Cross-list dragging is built in: a shared `group` plus an `onAdd` handler.

## The constraint that shapes the design

**Slot cards render collapsed, and the file list does not exist when they are**
(`LatentSlots.svelte:1736` gates the body on `isOpen`; on a phone `shows()`
narrows it to one tab). A plain shared Sortable group would only work when
source and target are both open — and on a six-slot album arriving collapsed,
the usual case is dragging toward a card with nothing to drop into.

## Decisions taken (do not re-litigate)

| Decision | Choice |
|---|---|
| Drop target | **The whole card, open or closed.** Drop anywhere on it and the file lands at the end of that slot's files |
| Non-drag path | **`Move to slot ▸` in the existing `More ▾` menu** — phone, keyboard, and discoverability |
| Copy vs move | **Move only.** A take appearing in two songs is a separate feature and a different row |
| Loose files as a target | **Out of scope this round** — different component (`LatentLooseFiles.svelte`), needs a cross-component drag signal. Same endpoint with `slot_id: null` when we want it |

## 1. Backend — `server/latents_api.py`

`move_item` (`:1402`) keeps its shape and gains one fix:

**Clear the old slot's pins for the moved media item.** `SlotPrimaryPin` rows
are per-slot (`slot_id`, `media_type`, `media_item_id`) and `move_item` never
touches them, so a pinned file dragged away leaves the old card showing a
`.slot__pins` thumbnail for a file it no longer holds. Delete the old slot's
pin rows for that media item; do **not** re-pin in the destination — pinning is
a deliberate act and shouldn't travel.

*Not* a bug, checked: a starred image landing in a slot that already has one is
fine. `_primary_image_map` (`:399`) documents multiple stars as expected and
resolves them by latest `added_at`, id as tie-break.

## 2. Frontend — `src/components/LatentSlots.svelte`

**Shared group.** `sortableFiles()` gains
`group: { name: 'latent-files', pull: true, put: true }`, plus `onAdd` →
`PATCH …/items/{id}` with the destination slot, then `load()`. A move is rare
and deliberate; one extra round trip for guaranteed-correct counts, accents and
pins beats reconciling two slots by hand.

**A per-card dropzone that exists even when the card is shut.** Every slot card
renders an inert element that Sortable owns
(`group: { name: 'latent-files', pull: false, put: true }`). It is zero-size and
`pointer-events: none` normally; while a file drag is in flight it becomes an
absolutely-positioned overlay across its card, labelled *"drop to add to this
slot"*, and highlights on hover. `onStart`/`onEnd` on the source lists flip one
component-level `dragging` flag — every slot lives in this one component, so no
event bus is needed.

Two constraints from the house rules: **`position: absolute` inside the card,
never `fixed`**, and no raw z-index ≥ 100 — `scripts/lint-design.mjs` enforces
both, and Latents sections set `isolation: isolate`, which is the trap behind
#573/#574/#575/#581 (see `src/lib/portal.ts` and the notes in `detail.astro`).
The source card must refuse its own dropzone, or a short drag "moves" a file
into the slot it already lives in.

## 3. The menu path — `src/components/RowActions.svelte`

Extend `RowAction` with an optional `children?: RowAction[]`. Phone expands the
child list inline (the accordion technique the component already documents);
desktop renders it in the same portaled panel. Then `LatentSlots` adds one
action per *other* slot, labelled with the slot's name.

Additive — every existing caller passes a flat list and is unaffected.

## 4. Tests

**pytest, `tests/test_latents_api.py`** — there is no coverage of `move_item`
at all today:
- moves between slots; lands at the **end** of the destination's order;
- `slot_id: null` detaches to loose;
- **the old slot's pin is cleared** and the destination's is untouched;
- the *other* slot's items keep their positions;
- 404 unknown item, 404 item belonging to another project, 401/403 per
  `TestLatentsAuth`.

**Browser, `tests/browser/` + a pytest wrapper** — the drop-on-a-**closed**-card
case is the entire point and nothing else can prove it: assert the file leaves
one card, arrives in the other, the counts on both heads update, and it survives
a reload. Prove it can fail by making the dropzone only render when the card is
open.

## Verification

1. `.venv/bin/python -m pytest` (`uv run pytest` is broken here; ~4 min with the
   browser loop up).
2. `npm run format` · `node scripts/lint-design.mjs` · `npm run build`.
3. Drive it: drag between two open cards; drag onto a **collapsed** card; drag
   onto the card it came from (must no-op); move a **pinned** file and confirm
   the old card's pin strip drops it; use the menu path on a phone viewport;
   reload and confirm everything stuck.
4. Both themes, 390px and desktop. Screenshot the drag state — assertions
   confirm geometry, not legibility.
5. Per `AGENTS.md`: worktree off `origin/master`, plan doc committed to
   `docs/plans/2026-07-31-slot-file-drag.md` before implementing.

## Deliberately out of scope

- Dragging to/from **loose files** (see the decisions table).
- Copying rather than moving.
- Dragging **between latents**, or reordering slots themselves.
