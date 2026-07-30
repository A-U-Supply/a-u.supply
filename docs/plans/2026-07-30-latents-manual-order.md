# Latents index — manual card order

**Status:** implemented
**Date:** 2026-07-30

## Why

The Latents index grid sorted by `updated_at DESC`, hardcoded. David raised it
in Slack: *"can we freely rearrange these somehow?"* — what he wanted was
*"organized by oldest sesh to newest sesh."*

No automatic sort can give him that. The session date exists only inside the
card titles — "2019 SF [TUBE,BDO,DWF]", "1-19-2026 BDO/DWF Kitchen Table Sesh",
"2024 San Fran" — and is not a field on `projects`. The old sort actively
fought him: "2019 SF" sat first because it was *updated* 7/30/2026. Manual
arrangement is the only thing that answers the request.

Brendan proposed drag and drop specifically so the cards wouldn't have to spend
screen space on buttons; David agreed.

**Chronology here means `Project.created_at`** — the server-side row timestamp.
Card titles are never parsed for dates.

## Decisions

| Decision | Choice |
|---|---|
| Order scope | **Shared** house order, server-side. Not per-user. |
| Default | Seeded once from `created_at DESC`; manual thereafter. |
| New latents | **Prepended** — a new latent still appears first. |
| Drag handle | **Visible grip** on each card. |
| Drop indicator | **Highlighted landing slot**, at every breakpoint. |
| Dragging while filtered | **Allowed**, interpolated against visible neighbours. |
| Reset to default | **None.** Once manual, it's manual. |

A drawn insertion line was considered and rejected for the desktop grid: it is
unambiguous in a single column but has no honest place at a row wrap, where
"between" two cards is not a gutter. The landing slot reads the same way at
both breakpoints.

## The hazard that shaped the backend

`Project.updated_at` carries `onupdate=_utcnow`, and every index card renders
"updated {date}". Writing positions through the ORM — or via
`query().update()`, which also fires Core `onupdate` defaults — would re-stamp
every row a drag touches, flipping a dozen cards to today. That would corrupt
the very signal the manual order exists to stop fighting.

**Positions are written with raw SQL (`_SET_POSITION_SQL`), never the ORM.**
It is invisible at the call site, so it carries a comment and a dedicated
regression test (`test_reorder_does_not_bump_updated_at`), which was verified
to fail when the write is swapped back to the ORM.

## Shape

### Storage

`projects.position INTEGER NOT NULL DEFAULT 0`, indexed, **not unique**. The
two-pass negative write in `reorder_slots` exists only to dodge a per-project
unique constraint; a table-global one would buy nothing and would block the
O(1) prepend. Instead the sort is made total —
`position ASC, created_at DESC, id DESC` — so duplicates degrade to a stable
order rather than rendering nondeterministically. The tiebreaks match
`backfill_project_positions`' ranking exactly, so seed and sort cannot disagree.

Migration is the repo's inline ALTER-guard in `main.py`, and the backfill is
the same correlated-`COUNT(*)` shape used for `project_items.position`. The
backfill lives in `latents_api.py` rather than inline because `tests/conftest.py`
imports `main`, which runs inline migrations only against the real `data/`
SQLite — as a function it can be driven against the in-memory engine.

Creates prepend at `min(position) - 1`. **Sparse and negative positions are
legal**: placement reads rank, not value, and every reorder renormalises to
`0..N-1`, so gaps self-heal. Shifting every row `+1` per create would be an
O(n) write for an O(1) need *and* would trip the `updated_at` hazard on every
latent in the system.

### `POST /api/projects/reorder`

```jsonc
{ "moved_id": "uuid", "prev_id": "uuid|null", "next_id": "uuid|null" }
→ 200 { "order": ["id", ...] }
```

Anchors — the moved card's *visible* neighbours — rather than the full ordered
array that `slots/reorder` takes, because **the client does not know the full
order when a status/kind filter is active.**

The invariant: *only the moved latent changes its relative position; every
other latent, including the ones the filter is hiding, keeps its relative order
with every other latent.* Everything else follows from it rather than being a
special case:

- dropped at the top of a filtered view with hidden cards above → lands
  directly above the first **visible** card, not at absolute top;
- dropped at the bottom → the mirror image;
- a hidden card between the two anchors stays put (prev wins over next);
- an anchor another admin deleted mid-drag is not an error — it falls through
  to the other anchor, then to a no-op.

### Page

`index.astro` only. The card becomes a `<div>` holding the grip, the existing
hero layers, and a stretched `<a class="card__link">` — a real `<button>` grip
cannot live inside a link. The link must be a **direct child of `.card`**:
`.card__content` is `position: relative`, so a link nested inside it would
cover only the plate strip.

Sortable survives `refresh()` replacing the grid's `innerHTML` (it resolves its
target at drag start rather than tagging children up front), so it binds once
and is destroyed on `astro:after-swap`. `direction` is deliberately unset —
Sortable's default reads `gridTemplateColumns` per drag, so the 640px collapse
to one column is handled for free.

**Rollback on a failed POST is DOM-only, not a `refresh()`.** The usual failure
is a dead network, where `refresh()` would replace the whole grid with "Network
error" and the card you just dragged would vanish instead of snapping back.
A `generation` counter stops a late rollback from reordering a grid a filter
change already rebuilt; a request token drops responses a later drag superseded.

### Two things that would otherwise have been silent

- **The grip's colour.** It is the only card chrome outside `.card__content`,
  so it cannot inherit the treatment's text colour, and the top of a hero card
  is bare photograph under *every* treatment (plate's opaque strip is pinned to
  the bottom). It gets two explicit rules — muted on the plain card,
  on-overlay + soft backing over imagery, matching `.status-pill` — and
  deliberately no third: hoisting `color` onto `.card--hero` would white out
  plate's theme-native title. This bug class has recurred four times in Latents.
- **`.drag-chosen`.** `DRAG_OPTS` sets it, but it is only styled inside
  `LatentSlots`/`LatentPlaylists`/`LatentSlideshows`' `:global` blocks — it does
  not ship in a global stylesheet. Without restating it here, a touch
  long-press on this page reads as a dead tap.

## Not in scope

- **Keyboard/button reorder path** — consistent with the desktop ruling on the
  detail page (drag-only, no ↑/↓ arrows).
- **A `manage.py` reseed subcommand** — `backfill_project_positions()` is a
  plain function, so this is a few lines whenever it's wanted.
- **A real `session_date` field on latents** — would make this ordering sort
  itself forever, and is the deeper fix. Bigger conversation than this ask.
- **Extending `lint-design.mjs` to `src/components/**`** — its `TARGETS` is
  admin `.astro` only, so no Latents Svelte island is linted. Real gap, parked.

## Files

- `server/models.py` — `Project.position`
- `server/latents_api.py` — `backfill_project_positions`, `_ordered_projects_query`,
  `_SET_POSITION_SQL`, `_resolve_project_order`, `reorder_projects`, prepend in
  `create_project`, sort in `list_projects`, `position` in `_project_summary`
- `main.py` — ALTER guard + index
- `src/pages/admin/latents/index.astro` — card restructure, grip, landing slot,
  Sortable wiring
- `tests/test_latents_api.py` — `TestLatentOrderSeed`, `TestLatentReorder`,
  `TestLatentReorderUnderFilter`, `TestLatentReorderRobustness`,
  `TestLatentReorderAuth`
