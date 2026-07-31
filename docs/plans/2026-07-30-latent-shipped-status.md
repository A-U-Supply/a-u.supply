# A fifth latent status: `shipped`

## Context

David, in Slack (2026-07-29):

> maybe we add one more called "shipped" becuase Im uploading old seshions
> that turned into albums?

Brendan: "color?" — David: "blue?" — 👍.

The use case is precise and the current vocabulary has no word for it. A
latent that became a record is not `fixing` (nothing is being worked on) and
it is emphatically not `abandoned` (it succeeded). Today those latents have
to sit in whichever stage they were in when the album came out, which makes
`fixing` mean two different things on the index.

**This reverses a decision from the original plan.** `docs/plans/2026-05-15-latents.md:48`
says the arc is "`forming → developing → fixing`, plus `abandoned` … No
`archived`, no `done`." That was written for latents that begin in the
workspace and move forward through it. David is doing the opposite —
back-filling finished work — which the original arc did not anticipate.
`shipped` is the terminal success state that `abandoned` is the terminal
failure state.

## What it is

A fifth value of the existing `Project.status` string. That is all.

- No new column, **no migration** — `status` is a free `String` with a
  default, and validation is a Python set (`VALID_PROJECT_STATUSES`).
- Status has never gated anything: it does not lock edits, hide cards, or
  change sort order, and `shipped` does not either. Per Tube's standing
  ruling on latents — *"Keep it simple, nothing fancy."*
- Position in the arc: after `fixing`, before `abandoned`, in both the
  index filter chips and the detail-page status row. Terminal states last,
  success before failure.

## Colour

New token `--color-status-shipped`: `#1a5fb4` light / `#78b0ee` dark.

Measured against `--color-bg`: **6.29:1** light, **8.31:1** dark. That is the
only ratio that matters for this control, because it covers both states —
the inactive button is the blue on the page background, and the active one
inverts to `--color-bg` on the blue.

Why a new token rather than reusing `--latent-sec-repo` (#4a6fa5), the
palette's existing blue: that one is a *section* hue, and it is muted enough
that at pill size beside `forming`'s grey it reads as grey-blue. David asked
for blue.

**Tailwind v4 tree-shakes `@theme` variables nothing references** — that is
how `--z-player` was silently dropped once (see the note in
`src/styles/tailwind.css`). This token is only ever named inside a JS
template string, which the scanner may not count. Verify it survives into
the built CSS; if it does not, add `.status-pill--shipped` to `admin.css`
alongside its `--ok/--fail/--warn/--pending` siblings as a real reference.

## Known and deliberately NOT fixed here

On a latent **with cover art**, in light mode, the inactive status buttons
measure **1.02–2.60:1** — `developing`'s gold is effectively invisible. The
row is `background: transparent` over a ground that composites to mid grey
(art at 15% under a `--color-overlay-soft` veil). This is the same bug the
DELETE button had one line below, fixed in #599 by giving it an opaque
plate; the status buttons were not touched then. The index cards' pills over
artwork have the same problem (1.22:1 worst case).

`shipped` inherits exactly that, no better and no worse than the other four.
**Raised to Brendan with measurements and explicitly deferred** — the fix
changes how every hero card looks, and that is a separate decision.

## Changes

**Backend**
- `server/latents_api.py` — `VALID_PROJECT_STATUSES` gains `"shipped"`.
- `server/models.py:631` — the comment listing the values.
- `server/latents_api.py` status-change notify — `shipped` gets its own
  event, mirroring `abandoned`. It is the counterpart, and on a shared
  workspace "X came out" is the one status change worth reading as news
  rather than as a `prior → next` diff.
- `server/slack_notifier.py` — `_format_latent_shipped`, registered in
  `_IMMEDIATE_FORMATTERS`.

**Frontend**
- `src/styles/tailwind.css` — the token, light and dark.
- `src/pages/admin/latents/index.astro` — filter chip + `statusColor`.
- `src/pages/admin/latents/detail.astro` — `statusColor` + the status-row
  array.

Slot statuses (`forming | developing | fixed`, `LatentSlots.svelte`) are a
different vocabulary on a different object and are untouched.

## Tests

`tests/test_latents_api.py` — `shipped` is accepted by PATCH and comes back
on the summary; an unknown status is still a 400 (so the set didn't become a
pass-through); the Slack event fires as `latent.shipped`, not
`latent.status_changed`. Existing status tests keep passing.

## Verification

1. `.venv/bin/python -m pytest` — full suite (`uv run pytest` is broken here).
2. `npm run format` · `node scripts/lint-design.mjs` · `npm run build`.
3. `grep` the built CSS for `--color-status-shipped` (the tree-shake check).
4. Drive it: chip filters, the button sets the status and survives a reload,
   the pill on the index card is blue, both themes.
