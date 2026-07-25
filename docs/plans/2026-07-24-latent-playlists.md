# Latents — slot playlists, latent running orders & manual file order

**Branch:** `latent-playlists` (plan PR) → implementation PRs linked below
**Status:** plan

Implementation PRs (in order):

1. **PR 1** — backend: manual file order, slot playlist, latent playlists
2. **PR 2** — slot card UI: drag-order the files, `[▶ Playlist (n)]` accordion, Play all
3. **PR 3** — Latent-level Playlists section: several named running orders

Related: [`2026-05-15-latents.md`](2026-05-15-latents.md) (Latents v1),
[`2026-07-22-latents-sessions-marginalia.md`](2026-07-22-latents-sessions-marginalia.md)
(the player panel, waveform and durations these playlists feed).

## Goal

A slot holds many takes. Today you can play them one at a time, in whatever
order they happen to sit in, and the only way to hear a section end-to-end is
to click each file as the previous one finishes.

Make each slot able to play as a **playlist** — every audio file in that
section, in an order you set — and let a Latent hold several **named running
orders** assembled by hand across sections (album sequence v3, a set for Tube,
a live set). Hitting play hands the whole list to the existing site player,
which advances through it with Marginalia markers and the waveform working as
they already do.

## Naming

| Thing | Name |
|---|---|
| A slot's audio, in an order you set | the slot's **playlist** |
| A hand-assembled latent-level sequence | a **running order** (a named playlist on the Latent) |
| The new detail-page section | **Playlists** (7th fixed section, `playlists` section key) |
| UI copy | plain language — "Playlist", "Play all", "Add tracks", "New running order" |

## Decisions (locked with the user)

1. **Three orders, all independent.**
   - The slot's **file list** becomes manually orderable (it is not today).
   - The slot's **playlist** starts as a copy of the file order and then goes
     its own way: reordering the playlist never touches the file list, and
     reordering the file list never touches an already-arranged playlist.
   - A latent **running order** is its own sequence entirely.
2. **Audio only.** The playlist contains `media_type == 'audio'` — including
   WAVs extracted out of an uploaded Logic bundle, which are full media peers.
   MIDI (playable via its OGG preview), video, images, sessions and documents
   are excluded. They stay in the file list; they just aren't tracks.
3. **Slot playlists are automatic, running orders are curated.** A new audio
   upload appends to the end of its slot's playlist and a deleted/detached file
   drops out of it — the slot playlist is always a complete view of the section,
   and the only thing you control is sequence. Nothing ever enters a latent
   running order uninvited.
4. **Several named running orders per latent**, not one — so candidate
   sequences can sit side by side.
5. **Reordering is drag-only** (the user's call), with the SortableJS setup
   `LatentSlots` already uses for slot cards. Keyboard/`aria` affordances still
   ship on the rows themselves (play, remove) so nothing but reordering
   requires a pointer.
6. **Play means the persistent player.** `player:queue` with the whole list and
   a `startIndex`; no inline transport, no export, no share link. Playlists
   live in the Darkroom only.
7. **The playlist lives behind a labeled toggle on the slot card** —
   `[▶ Playlist (7)]` sits next to `[▪ Style]` and expands the running order
   inline beneath the file rows (the accordion technique `ColorPicker` uses),
   so cards stay compact until asked. Labeled, never icon-only — see the
   explicit-affordances rule that produced `[▪ Style]`.

### Consequences worth stating up front

- **New uploads land at the bottom of a slot's file list**, where they land at
  the *top* today (items are served `added_at DESC`). Once the order is
  user-owned, appending is the only rule that doesn't shuffle a deliberate
  arrangement out from under you. Existing rows are backfilled into their
  current on-screen order, so nothing moves at deploy time.
- The **Playlists section is a 7th fixed section** and therefore joins the
  section-style grammar: a hue token, an entry in `VALID_SECTION_KEYS`, its own
  `[▪ Style]` button and a chip in the section map. (Marginalia skipped this and
  borrows the threads hue — a known wart this plan does not repeat.)
- Slot playlist membership derives from the slot, so **moving a file between
  slots moves its track** without any bookkeeping.

## Background: what exists today (survey of `origin/master`)

- `ProjectItem` (`server/models.py:686`) has **no `position`** — items are
  listed `ORDER BY added_at DESC` (`server/latents_api.py:980`). Slot cards and
  documents *do* have `position`, with a two-pass reorder endpoint that dodges
  the unique constraint (`/slots/reorder`, `latents_api.py:873-890`) — the
  pattern to copy.
- `_item_summary` (`latents_api.py:194`) exposes no duration. `MediaAudioMeta.duration_seconds`
  (`models.py:298`) has it, and Marginalia's extraction-on-upload means Latents
  audio actually has it populated now.
- `LatentSlots.svelte` already imports SortableJS for slot-card reordering and
  already builds a one-track queue in `playInPlayer()` (~line 800) — the exact
  track shape the player wants, including the MIDI-preview special case.
- `src/components/marginalia.ts` already has `queueMediaTrack()` and the
  `player:time-request`/`player:time` handshake; `Player.svelte` handles
  `{tracks, startIndex, start_time?}` and advances a multi-track queue.
- `LatentDocuments.svelte` is the in-repo precedent for "several named things
  in one section": tab strip + active pane + create/rename/delete.
- Section styling: `VALID_SECTION_KEYS` (`latents_api.py:352`), the six
  `--latent-sec-*` hue tokens in `src/styles/tailwind.css` (light + dark), and
  `SECTION_KEYS/LABELS/TOKENS` in `src/lib/latentStyles.ts`.

## Data model

### 1. Manual file order — `project_items.position`

```
position  INTEGER NOT NULL DEFAULT 0
```

ALTER guard on startup like every other Latents column. **Backfill in today's
display order**: per `(project_id, slot_id)` group, `added_at DESC` → `1..n`, so
the visible order is unchanged on deploy. Listing becomes
`ORDER BY position ASC, added_at DESC`; attach sets `position = max + 1`.
No unique constraint (a two-pass shuffle isn't needed; gaps and ties are
harmless and resolved by the `added_at` tie-break).

Loose files get positions from the same column for free — PR 2 only wires the
drag UI for slot rows, but nothing blocks doing the loose grid later.

### 2. Slot playlist — `project_slots.playlist_json`

A JSON array of `media_item_id` strings: **an ordering hint, not a membership
list**. Membership is always derived, so uploads, deletes, and slot moves need
no bookkeeping:

```
stored   = [id for id in playlist_json if id in slot_audio]      # kept order
derived  = [id for id in slot_audio_in_file_order if id not in stored]  # new arrivals
playlist = stored + derived
```

Reconciliation happens on read and never writes during a GET. `PUT` replaces
the array wholesale, after validating that every id is an audio item attached
to that slot.

**Pinning on file reorder.** An untouched playlist has no stored order — it
just mirrors the file order — so a file reorder would drag the playlist along
with it right up until the first time the playlist itself was arranged. The
file-reorder endpoint therefore snapshots the playlist as it currently reads
*before* renumbering. The rule the user sees is then absolute: dragging files
never moves tracks, from the first drag onward. Pinning stores an order, not a
membership list, so new uploads still append.

### 3. Latent running orders — two new tables

```python
class ProjectPlaylist(Base):            # "project_playlists"
    id, project_id (FK CASCADE, index), name, position,
    created_by, created_at, updated_at

class ProjectPlaylistItem(Base):        # "project_playlist_items"
    id, playlist_id (FK CASCADE, index),
    media_item_id (FK media_items CASCADE, index),
    position, added_at
    UniqueConstraint(playlist_id, media_item_id)
```

Curated, so rows persist as written. On read, tracks are filtered to media
still attached to this Latent (join `project_items`) and still audio — a track
detached from the Latent disappears from the running order without deleting
history, and reappears if reattached. Deleting the media item itself cascades
the row away.

## API (`server/latents_api.py`)

| Route | Purpose |
|---|---|
| `POST /api/projects/{pid}/slots/{sid}/items/reorder` | `{order: [item_id]}` → renumber that slot's items; returns the reordered items |
| `GET /api/projects/{pid}/slots/{sid}/playlist` | reconciled `{tracks: [...], total_seconds}` |
| `PUT /api/projects/{pid}/slots/{sid}/playlist` | `{order: [media_item_id]}` → store the hint |
| `GET /api/projects/{pid}/playlists` | all running orders with their tracks |
| `POST /api/projects/{pid}/playlists` | `{name}` → create (append at end) |
| `PATCH /api/projects/{pid}/playlists/{plid}` | rename |
| `DELETE /api/projects/{pid}/playlists/{plid}` | delete |
| `POST /api/projects/{pid}/playlists/{plid}/items` | `{media_item_ids: []}` → append, skipping dupes/non-audio/non-members |
| `DELETE /api/projects/{pid}/playlists/{plid}/items/{item_id}` | remove one track |
| `POST /api/projects/{pid}/playlists/{plid}/items/reorder` | `{order: [item_id]}` |

Auth: the same admin/member gate every `/api/projects` route already applies.
`_item_summary`'s `media` block gains `duration_seconds` (with
`joinedload(MediaItem.audio_meta)` at the two list sites so it isn't N+1) — the
slot card can then show `7 tracks · 19:04` without a second request.

## UI

### PR 2 — `src/components/LatentSlots.svelte`

- **Drag handles on file rows.** Per-slot `Sortable` on `.file-list` with a
  `handle` selector, mirroring the existing slot-card instance; on drop, POST
  `items/reorder` and resync from the response (same failure path as
  `persistOrder()` — re-`load()` on error).
- **`[▶ Playlist (n)]`** button beside `[▪ Style]` in the slot head; `n` = audio
  count, button hidden entirely when the slot has no audio.
- **Inline accordion** under the file list: a head row
  `▷ Play all · 7 tracks · 19:04`, then one row per track — drag handle,
  position number, filename (wrapping, not truncated), duration, a play button
  that queues the whole list at that index. Reorder → `PUT .../playlist`.
- **Shared queue helper.** `playInPlayer()`'s track-building moves to
  `src/lib/playerQueue.ts` (`buildQueueTrack(media)`, `queueTracks(tracks, startIndex)`),
  keeping the MIDI-preview branch, and is reused by the playlist, the loose-file
  play button, and PR 3. `marginalia.ts`'s `queueMediaTrack()` delegates to it.

### PR 3 — `src/components/LatentPlaylists.svelte` (new)

New `#playlists-island` in `src/pages/admin/latents/detail.astro`, mounted
imperatively like its neighbours, `data-section="playlists"`, placed **after the
slots section and before loose files** (a running order is assembled out of
slots, so it reads after them).

- Tab strip of running-order names + `+ New` (the `LatentDocuments` pattern),
  rename inline, delete with confirm.
- Active list: `▷ Play all · n tracks · mm:ss`, drag-reorderable rows showing
  filename, source slot label, duration, play, and a labeled `Remove`.
- **`+ Add tracks`** opens a dialog listing every audio file in the Latent
  grouped by slot (plus loose), with checkboxes and a filter box — sourced from
  the items the page already fetches, not from a cross-index search, because the
  source set is this Latent. Already-present tracks show as added.
- Empty states for "no running orders yet" and "this one is empty".
- Section styling wiring: `playlists` into `VALID_SECTION_KEYS`
  (`latents_api.py:352`), `--latent-sec-playlists` into `src/styles/tailwind.css`
  (light **and** dark blocks), and `SECTION_KEYS/LABELS/TOKENS` in
  `src/lib/latentStyles.ts` — the section map picks it up automatically.

## Mobile

Reordering is the whole feature and it is drag-only, so touch is the primary
target, not a port. Designed at ≤640px first; every item below is a build
requirement, not a polish pass.

**Dragging vs. scrolling — the hard part.** A vertical drag and a page scroll
are the same gesture. Both Sortable instances (file rows, playlist rows) are
configured:

```js
handle: '.drag-handle',        // never the whole row — the row must stay scrollable
delay: 180,                    // long-press to start a drag…
delayOnTouchOnly: true,        // …on touch only; mouse drags start immediately
touchStartThreshold: 6,        // a shaky finger doesn't cancel the press
scroll: true, bubbleScroll: true,
scrollSensitivity: 60, scrollSpeed: 12,  // auto-scroll near viewport edges
```

`touch-action: none` goes on the **handle only** — putting it on the row kills
scrolling through the list. The handle is a ≥44px square with a visible grip
(`⠿`), and gets a `pressed` state at drag start (scale + shadow + subtle
haptic-looking lift) so the long-press is acknowledged before anything moves —
without that feedback a touch drag feels broken. Auto-scroll matters here
because a 20-take slot is taller than a phone screen.

**Layout at <640px.**

- Track rows go two-line: handle + position + filename on the first line
  (wrapping via `overflow-wrap: anywhere` — never truncated, per the same
  reasoning as the loose-tile fix), duration + play + remove on the second.
  Row height ≥44px per line; play and remove are ≥44px targets, not icon studs.
- `[▶ Playlist (7)]` and `[▪ Style]` sit in a wrapping flex row on the slot
  head, so a long slot label never squeezes either off the card.
- The playlist accordion is full-bleed inside the card at this width — no
  horizontal padding stacking up on an already-narrow column.
- The running-order tab strip scrolls horizontally as a sticky row, the same
  treatment `LatentSectionMap` uses for its chips, rather than wrapping into a
  tall pile of tabs.
- `+ Add tracks` opens as a bottom sheet (`LatentStylePanel`'s pattern):
  sticky header with the filter box, scrollable body, sticky footer with the
  count and Add button, `padding-bottom: env(safe-area-inset-bottom)`, and
  background scroll locked while open.
- Dragging is disabled outright while the add-tracks sheet is open, so a drag
  begun under the sheet can't fire.

**Verification is on a real phone, not a narrow window.** Emulated touch in
desktop devtools does not reproduce the scroll/drag conflict — the mobile
checklist below is run on the actual device.

### Fallback if touch drag disappoints

Drag-only was a deliberate choice, so this plan builds exactly that. But the
risk it carries is real and lands entirely on mobile: if the long-press drag
doesn't feel right in the browser pass, adding labeled `Up`/`Down` buttons to
the track row is a contained change to one component and no backend work — the
reorder endpoint takes a full ordered list either way. Noting it here so the
decision stays cheap to revisit rather than being re-litigated as a rebuild.

## Testing

`tests/test_latents_api.py` gains three classes alongside the existing fixtures
(`project`, `slot`, `attach_item`):

- `TestItemOrder` — backfill preserves current order; reorder renumbers;
  new attach appends; reorder rejects foreign/unknown item ids; loose vs slot
  groups renumber independently.
- `TestSlotPlaylist` — seeded from file order; a stored order survives a file
  reorder; a new audio upload appends; a deleted/detached file drops out; a
  moved file follows its slot; non-audio never appears; `PUT` rejects ids that
  aren't audio in that slot.
- `TestLatentPlaylists` — CRUD, dupes skipped, non-member/non-audio rejected,
  reorder, detached track hidden then restored on reattach, cascade on media
  delete, member-403 regressions in the style of `TestLatentsAuth`.

Commands before each merge: `uv run pytest`, `npm run build`,
`npm run format:check`, `npm run lint:design`.

**Desktop browser pass:** drag a file row and confirm the playlist doesn't
move; drag the playlist and confirm the file list doesn't move; upload into a
slot and watch the track append at the bottom of both; Play all and confirm the
player advances track to track with waveform and markers intact; build a
running order across two slots and play it; both themes.

**Mobile pass — on a real phone, and the gate for shipping PR 2 and PR 3:**

1. Long-press a drag handle: the press is acknowledged visually before the row
   moves.
2. Swipe *on a row but not the handle*: the page scrolls, nothing drags.
3. Drag a track from the top of a long slot to the bottom: the list
   auto-scrolls, and the drop lands where released.
4. Drag with the page itself scrolled mid-way: no jump, no lost drop.
5. Reorder, then reload: the order persisted.
6. Filenames of session-style length (`TRACK08_verse_take3_comp.wav`) wrap
   fully — no truncation, no horizontal overflow of the card.
7. Play from a track row: the player takes the whole list from that index.
8. `+ Add tracks` sheet: opens from the bottom, filter box reachable above the
   keyboard, body scrolls, footer stays visible above the home indicator.
9. Both themes, and the running-order tab strip scrolls rather than stacking.

## Risks / accepted

- **No CI test gate on this repo** — `.github/workflows/` only deploys. The
  local commands above are the whole safety net.
- Reordering is last-write-wins per slot, like every other Latents autosave.
  Two admins dragging the same list at once is not defended against.
- `playlist_json` holds ids for files that later leave the slot until the next
  `PUT` rewrites it. Harmless (they're filtered on read), and it means a file
  that leaves and comes back keeps its old position.
- Drag-only reordering is a deliberate accessibility compromise on the reorder
  action specifically; every other action on a track row is a real button.
