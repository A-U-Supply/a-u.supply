# Latents — a viewer, slot slideshows & named slideshows

**Branch:** `latent-slideshow` (plan PR) → implementation PRs linked below
**Status:** plan

Implementation PRs (in order):

1. **PR 1** — the viewer: auto-hiding chrome, and Latents finally opens it
2. **PR 2** — backend: slot slideshow order, named latent slideshows
3. **PR 3** — slot card UI: `[▦ Slideshow (n)]` accordion, View all
4. **PR 4** — Latent-level Slideshows section: several named slideshows

Related: [`2026-07-24-latent-playlists.md`](2026-07-24-latent-playlists.md) —
this is the parallel structure for the media types that plan excluded, and its
UI is the guiding star for this one.
[`2026-05-15-latents.md`](2026-05-15-latents.md) (Latents v1),
[`2026-07-25-latent-mobile-streamline.md`](2026-07-25-latent-mobile-streamline.md)
(the row/collapse grammar a new section has to speak).

## Goal

Latents is the only surface in this app where you cannot look at an image
properly. Search, workspace, bookmarks, midden, slop and jobs all open a
full-screen lightbox on click. In a Latent an image is a 32px thumbnail, and
clicking it navigates you *away* to the Stacks detail page. For a workspace
whose whole job is assembling zines and videos out of images, that is the wrong
hole in the wrong place.

Give Latents the viewer. Open any image or video full-bleed from wherever it
lives, arrow through its neighbours, and save the orders worth returning to —
a **slideshow** on any slot, and named slideshows built by hand from anything
in the Latent.

## Naming

| Thing | Name |
|---|---|
| A slot's images and video, in an order you set | the slot's **slideshow** |
| A hand-assembled latent-level sequence | a **slideshow** (a named slideshow on the Latent) |
| The new detail-page section | **Slideshows** (8th fixed section, `slideshow` section key) |
| UI copy | plain language — "Slideshow", "View all", "Add slides", "New slideshow" |

One word for both layers, unlike playlists/running orders. That was the user's
call: it matches the words he actually used, and one fewer term to learn is
worth the mild ambiguity in "add to the slideshow".

## Decisions (locked with the user)

1. **It is a viewer, not a presentation.** Manual flip-through — arrow keys
   plus on-screen side arrows, exactly like the site's existing viewer. No
   clock, no auto-advance, no dwell time. "Slideshow" is the name he picked for
   the feature, not a description of timed playback.
2. **No soundtrack.** Slideshows and playlists do not know about each other.
   Pairing a slideshow with a running order was considered and rejected.
3. **No transitions.** Hard cut between slides. No captions and no scrim text
   — "keep it just image except for necessary UI".
4. **Video plays on demand, with audio.** It does not autoplay. The user presses
   the centre play button. Advancing does not require the clip to finish, and
   leaving a slide stops it.
5. **Fullscreen.** The existing full-page overlay plus its native Fullscreen
   API button.
6. **Both layers, mirroring playlists.** A slot's slideshow membership is
   *derived* from its image/video attachments; a named latent slideshow is
   *curated* and gains nothing uninvited.
7. **Chrome auto-hides** after ~2s of stillness and returns on movement, so a
   still frame is pure image.
8. **Loose files gets in too** — tile click opens the viewer over the loose
   pile, plus a `View all` button on the section.
9. **Ends clamp, they do not wrap**, matching the existing viewer.

### Deliberately **not** in this plan

- **PDF export of a slot**, with a picker preview before export. The user's
  idea and a good one; explicitly reserved for a follow-up PR once this lands.
- **Any public or shareable surface.** `2026-07-24` decision 6 locked "no
  export, no share link — playlists live in the Darkroom only," and issue #542
  still has an open question about unauthenticated art on pre-release work. A
  slideshow is more exposure surface than one hero image, so it inherits the
  restriction rather than quietly reopening it.
- **Ordering by colour or vibe.** `media_video_meta` carries no colour or AI
  fields, so a mixed image/video show could not order uniformly by them anyway.
  Colour is also filterable-but-not-sortable in Meili today.

### Consequences worth stating up front

- `2026-07-24` decision 2 was **"audio only … video, images … are excluded"**.
  This is the parallel structure for that excluded half, **not a reversal** —
  playlists stays audio-only and untouched.
- A slot can now carry **three independent saved orders**: its file list, its
  playlist, and its slideshow. The pinning rule in `reorder_slot_items` must
  snapshot the slideshow order too, or dragging files will drag an unarranged
  slideshow along with them right up until the first time it is arranged. This
  is the same trap the playlist hit; the fix generalises.
- **Video posters are capped at 640px.** `generate_video_thumbnail`
  (`server/extraction.py:531-571`) takes one ffmpeg frame at 10% duration at
  `scale='min(640,iw)':-2`, and `_resolve_thumbnail_path`
  (`server/search_api.py:267-313`) returns it for `sm`, `md` and `lg` alike.
  A fullscreen video slide shows a soft poster until the user hits play.
  Accepted — noted so nobody re-discovers it as a bug.
- The auto-hiding chrome is **opt-in**. The triage surfaces (midden, slop,
  workspace) live off their toolbar buttons; hiding those would hurt.

## Background: what exists today (survey of `origin/master`)

- **`src/lib/image-viewer.ts` (1339 lines) is already ~90% of this feature.**
  Its own header: "a lightbox-style overlay for images, video, and audio".
  Ordered reel with prev/next (`navigate()`, `:864`), keyboard incl. `←/→`,
  `Esc`, `f`, `?` (`:1079-1207`), native Fullscreen (`:1214-1228`), pinch-zoom
  / pan / wheel / double-click (`:890-1077`), swipe (`:1041-1050`), neighbour
  preload for images only (`:836-846`), progressive thumbnail→large upgrade
  (`:749-782`), `position: fixed; inset: 0; z-index: 10000` — above the
  Player's 9999 (`:143-150`), body scroll lock (`:507`), auto-close on
  `astro:before-preparation` (`:509`).
- It **tears the media element down on every render** — `pause()`,
  `removeAttribute('src')`, `load()` (`:703-712`) — so advancing away from a
  playing video already stops it. Decision 4 is free.
- `renderVideo` (`:784-807`) already sets `controls`, `playsInline`,
  `preload="metadata"` and `poster`, and `.iv-av` hands pointer input to the
  native controls (`:239-244`, `:933-936`).
- **It has no timer.** No `setInterval`/`setTimeout` anywhere in the file. Per
  decision 1, none is wanted.
- Toolbar action buttons are **all conditional** on a callback being passed
  (`:619-650`) — omit `ViewerActions` entirely and it reduces to zoom,
  fullscreen and download.
- `src/lib/admin-viewer.ts:54-83` exports `mediaItemToViewerItem(row)`, which
  maps precisely the row shape Latents items already have.
- **Nothing in `src/components/Latent*.svelte` imports either module.** That is
  the whole reason Latents has no viewer.
- `ProjectItem.position` (`server/models.py:689-711`) already exists and is
  media-type agnostic, so the file-order layer needs nothing.
- `_slot_playlist()` (`server/latents_api.py:263-285`) reconciles a stored id
  array against derived membership, and is **already media-type agnostic** —
  stored ids first, unknown ids ignored rather than deleted, new arrivals
  appended in file order.
- `VALID_SECTION_KEYS` (`server/latents_api.py:428`) currently has seven keys.

## Data model

Mirror playlists exactly. `server/models.py`:

**`ProjectSlot.slideshow_json`** — `Column(String, nullable=True)`. A JSON array
of `media_item_id`. It is the **order, not the membership**; membership derives
from the slot's `image`/`video` attachments, so uploads append, deletes drop
out, and moving a file between slots moves its slide. Comment it the way
`playlist_json` is commented at `models.py:676-680`.

**`ProjectSlideshow`** → `project_slideshows`: `id, project_id, position, name,
created_by, created_at, updated_at`. A copy of `ProjectPlaylist`
(`models.py:713-740`), plus `Project.slideshows` ordered by `position` with
`cascade="all, delete-orphan"`. No unique constraint on name — duplicates are
allowed, as with running orders.

**`ProjectSlideshowItem`** → `project_slideshow_items`: `id, slideshow_id,
media_item_id, position, added_at`, with
`UniqueConstraint("slideshow_id", "media_item_id")`.

**Migration.** There is no Alembic; schema upgrades are guarded blocks at the
top of `main.py`. Add one beside the playlists block at `main.py:197-207`:
a `sa_inspect` column check plus `ALTER TABLE project_slots ADD COLUMN
slideshow_json TEXT`, then `__table__.create(bind=engine)` for the two new
tables if absent. **No backfill** — a null `slideshow_json` already means "file
order", which is the correct starting state.

## API (`server/latents_api.py`)

All `Depends(require_admin)`, mounted under `/api/projects`.

| Route | Purpose |
|---|---|
| `GET /{pid}/slots/{sid}/slideshow` | derived membership in stored order |
| `PUT /{pid}/slots/{sid}/slideshow` | `{order: [media_item_id]}`; may be partial, the rest append on read |
| `GET /{pid}/slideshows` | every named slideshow |
| `POST /{pid}/slideshows` | `{name}` 1..120 → 201 |
| `PATCH /{pid}/slideshows/{sid}` | `{name}` |
| `DELETE /{pid}/slideshows/{sid}` | → 204 |
| `POST /{pid}/slideshows/{sid}/items` | `{media_item_ids}` — forgiving: non-members, non-visual and dupes silently skipped |
| `DELETE /{pid}/slideshows/{sid}/items/{item_id}` | remove one slide |
| `POST /{pid}/slideshows/{sid}/items/reorder` | `{order: [slideshow_item_id]}`; partial accepted, unlisted rows keep their relative order at the end |

Reuse rather than rewrite:

| Need | Reuse |
|---|---|
| Order reconciliation | `_slot_playlist()` (`:263-285`) — factor its body into a shared `_reconcile_order(stored_json, items)` and have both callers use it |
| Membership query | `_slot_audio_items()` (`:249-261`) → a `_slot_visual_items()` twin filtering `media_type in ("image","video")`, same `joinedload` to avoid N+1 |
| Row payload | `_track_summary()` (`:235-246`) → `_slide_summary()` carrying `width`/`height` instead of `duration_seconds` |
| Filter-on-read | `_playlist_summary()` (`:1439-1465`) — drop rows whose media has left the Latent without deleting them, so reattaching restores the slide's place |
| Pinning | `reorder_slot_items` (`:1329-1363`) — extend the snapshot to cover `slideshow_json` |
| Section key | `VALID_SECTION_KEYS` (`:428`) — add `"slideshow"` |

## UI

**Reuse:** `mediaItemToViewerItem` (`admin-viewer.ts:54-83`), `DRAG_OPTS`
(`src/lib/dragOptions.ts`), `RowMove.svelte`, `src/lib/portal.ts`, `isPhone()`
(`src/lib/viewport.svelte.ts`), the `.sec-summary` collapse grammar
(`src/styles/admin.css:285-330`), `LatentStyleButton`.

### PR 1 — the viewer, and Latents opening it

No schema. Independently valuable, so it ships first.

- `src/lib/image-viewer.ts`: add `chrome?: 'persistent' | 'auto-hide'` to the
  open options, defaulting to `'persistent'`. In auto-hide mode a ~2s idle
  timer toggles a class fading the topbar and toolbar; `mousemove`, `keydown`
  and touch restore them. Only Latents passes `'auto-hide'`.
- `LatentSlots.svelte`: clicking a file row's thumbnail opens the viewer over
  that slot's image/video items, starting on the clicked one, with no
  `ViewerActions`.
- `LatentLooseFiles.svelte`: the same over the loose pile, plus a `View all`
  button in the section header.
- **Verify before adding a centre play button.** Native `<video controls>`
  renders one when paused in Chrome and Safari. Only add an explicit overlay if
  the browser pass shows it missing.

### PR 3 — slot card slideshow

A `[▦ Slideshow (12)]` toggle beside `[▶ Playlist (7)]`, rendered only when the
slot holds image/video — mirroring the `audioCount > 0` gate at
`LatentSlots.svelte:1361-1372`. It opens the same inline accordion ("the
ColorPicker accordion technique", never a popover): thumbnail rows with
`RowMove` drag reorder persisting to `PUT …/slideshow`, and a `▷ View all` that
opens the viewer in the saved order. Add `Slideshow(n)` to `tabsFor()`
(`:591-608`) for the phone tab strip.

### PR 4 — the Slideshows section

New `src/components/LatentSlideshows.svelte`, structurally a copy of
`LatentPlaylists.svelte`: boxed wrapping tab strip of names, `+ New`,
`▷ View all`, `+ Add slides` in a portalled sheet, drag-reorderable rows,
`Rename`/`Delete` on desktop and `Order tools ▾` on a phone. Candidates come
from the Latent's own attachments, not a cross-index search.

Section registration — all seven touch points:

1. `detail.astro` markup: `<section id="slideshow-island" class="latent-section"
   data-section="slideshow">`. The id must be `<key>-island` (the section map
   scrolls to it), and DOM order is page order.
2. `detail.astro` `<style>`: `--sec-accent: var(--latent-sec-slideshow)`.
3. `detail.astro` script import.
4. `detail.astro` `mount(...)` with `props: { projectId, styleKey: 'slideshow' }`.
5. `src/lib/latentStyles.ts`: `SECTION_KEYS`, `SECTION_LABELS`, `SECTION_TOKENS`.
6. `src/styles/tailwind.css`: `--latent-sec-slideshow` in **both** the light
   (~61-70) and dark (~107-113) blocks, a hue distinct from all seven.
7. `server/latents_api.py:428`: `VALID_SECTION_KEYS`.

`LatentSectionMap` needs nothing — it derives from `SECTION_KEYS`.

## Mobile

The section and the slot accordion inherit the grammar from
`2026-07-25-latent-mobile-streamline.md` and PR #575: `.sec-summary` collapse at
every width, `RowMove` grip with paired arrows under it on a phone,
`touch-action: none` on the handle only, row actions behind a labeled
`More ▾`, boxed wrapping tabs rather than scroll strips.

The viewer itself is already mobile-competent — swipe navigation, pinch zoom,
≥44px targets, safe-area insets. The auto-hide chrome must restore on touch,
not just `mousemove`, or a phone can never get the toolbar back.

## Testing

`tests/test_latents_api.py`, modelled on `TestSlotPlaylist` /
`TestLatentPlaylists` / `TestPlaylistsSectionStyle` (`:811-1201`):

- **`TestSlotSlideshow`** — derived membership (image and video in; audio,
  documents and sessions out); a new upload appends; a detached file drops out
  but its stored id survives a round trip so reattaching restores its place; a
  partial `PUT` order; dupes and non-members → 400.
- **`TestLatentSlideshows`** — create / rename / delete; forgiving add; reorder
  from a partial list; rows whose media left the Latent are filtered on read.
- **`TestSlideshowSectionStyle`** — `"slideshow"` accepted by `section_styles`,
  unknown keys still rejected.
- **Extend the existing pinning tests** — a file reorder must not disturb an
  untouched slideshow order, and a slideshow reorder must not disturb the file
  order or the playlist.

Commands before each merge:
`uv run pytest`, `npm run build`, `npm run format:check`, `npm run lint:design`.

Then a hand-driven browser pass in **both light and dark**, at desktop width
and 390px: the viewer opens from a slot row, a loose tile, a slot `View all`
and a named slideshow, each on the right slide; arrows step and clamp; chrome
fades and returns; a video slide plays with audio on press and stops on
advance; drag order survives a reload; the three slot orders stay independent;
the section chip lands in the right place in the section map and its
`[▪ Style]` button applies.

## Risks / accepted

- **No CI test gate** — `.github/workflows/` only deploys. The four commands
  above are the whole safety net.
- **Video posters stay soft** at ≤640px on a fullscreen slide until play is
  pressed. Fixing it means a second ffmpeg grab at a larger size, out of scope.
- **Auto-hiding chrome is a discoverability cost** — a first-time user may not
  know the toolbar is one movement away. Mitigated by it being Latents-only,
  and by the chrome being visible on open.
- **Last-write-wins concurrency** on order writes, as with playlists.
- **Stale ids linger** in `slideshow_json` until the next `PUT`. Deliberate —
  it is what makes a reattached file return to its old place.
