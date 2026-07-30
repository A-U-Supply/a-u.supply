# Latents — delete a latent

**Status:** implemented
**Date:** 2026-07-30

## Why

David asked in Slack whether two dead latents could go: the Curtis contribs
(the same content now lives in the bachelor sessions) and "how how", an initial
test. There was no way to delete a latent from the UI at all.

Curtis floated a delete button gated by a password sign-off from the creator.
Brendan deferred to Tube, who ruled:

> "I would allow deleting latents easily, but it shouldn't delete files from the
> search index. Keep it simple, nothing fancy."

Tube also killed the "latent graveyard" idea Brendan floated — *"a project is
only interesting if it's being worked on. data is always interesting and should
be guarded."*

That's the whole design in one line: **the latent is disposable, the data it
points at is not.**

## What was already there

`DELETE /api/projects/{project_id}` has existed for a long time — `require_admin`,
204, `db.delete(project)`. It had **no caller anywhere in the frontend and no
test**. So most of this work is the UI and the test; the endpoint itself only
gained a Slack notice.

It already satisfied Tube's condition, for a structural reason worth writing
down: a latent's connection to a file is a row in `project_items`, a *join* row.
Deleting the latent deletes the join. `media_items` — the file, its metadata,
tags, votes, and its Meilisearch document — is never touched.

## What dies and what survives

`PRAGMA foreign_keys=ON` is set (`server/models.py`), so the DB cascade is real.

**Dies with the latent:** slots and their primary pins · documents *and every
revision* · playlists (running orders) · slideshows · pinned links · the GitHub
repo attachment and its run history · hero pick, accent and per-section styling.

Documents are the only real loss — they exist nowhere else.

**Survives:** every file, in Emulsion and in the search index · annotations and
marginalia (they hang off `media_items`, not the project) · the GitHub repo
itself · any linked fold community.

**Threads survive, deliberately.** `Thread` is anchored by
`(anchor_type, anchor_id)` with no foreign key, so it does not cascade. The
Lemmy posts stay on the fold either way — that's the band's conversation, and it
lives elsewhere by Tube's rule. We chose to leave the pointer rows with them
rather than purge them, and to say so in the confirmation dialog instead of
silently orphaning them. `test_threads_survive` pins the decision so it isn't
"fixed" by accident later.

## Decisions

| Decision | Choice |
|---|---|
| Confirmation | Type the latent's name. Not `confirm()`. |
| Threads | Left alone; named in the dialog. |
| Slack | New `latent.deleted` immediate event. |
| Who can delete | `require_admin`, like every write in this router. No creator sign-off — overruled. |
| Delete on the index cards | No. That corner is the drag grip now; an irreversible action beside the control you grab to rearrange is one mis-tap from losing a latent. Deleting costs a page load. |
| Soft delete / graveyard | No. `abandoned` already is it, and the index filters on it. This completes the pair — abandon keeps it and hides it; delete means gone. |

The typed-name gate is the one place in the app that works this way. Every other
destructive action uses a native `confirm()`, and that is right for them: a
detached file, a deleted slot, a revoked token are all recoverable or small.
Deleting a latent takes documents, threads-worth of structure and months of
curation with it, and a native confirm is one stray Enter away.

## Where the button lives

The detail header renders three lines: the name input, the slug/kind/status row,
and a `<p class="summary">` ("session latent · 14 files · 2 documents · 3
slots"). `#hero-island` — the card images — is the next node. The summary line's
right half was empty, so that's where DELETE LATENT sits: above the fold, above
the images, spelled out rather than an `×` or a trash glyph.

### The contrast trap, again

`.action-btn--danger` hardcodes `#c00`, a red tuned for a light page. This header
can carry the latent's cover art as a backdrop, and that ground composites to a
*mid* tone in light mode — the image at 15% under a `--color-overlay-soft` veil,
which is black in **both** themes. Measured, `#c00` lands around 3.4:1 there,
under AA. `--color-danger-on-overlay` — the token built for exactly this class of
bug on the dark slot faces — is *worse* on a mid ground, around 1.1:1.

Neither token is the answer, because the header's ground is neither the page nor
a dark photo. The header already documents its own strategy in a comment:
*"Inputs sit on opaque grounds so text stays fully legible."* The button follows
it and takes `background: var(--color-bg)`, which puts `#c00` back on the pairing
that is validated everywhere else, in both themes, regardless of the artwork.

This is the seventh time this bug class has come up in Latents. The general
lesson is now explicit: **a face is not always dark. Measure the actual composited
ground before reaching for the on-overlay token.**

## The dialog

A native `<dialog>` + `showModal()`, in the page's static Astro markup rather
than inside the header's `innerHTML`, so it's built once and its listeners bind
once.

Native `<dialog>` is the point, not a detail. It renders in the browser's top
layer, so it needs no `position: fixed` and no `z-index`, and it cannot be
swallowed by the header's `isolation: isolate` — the stacking trap that cost four
separate fixes (#573, #574, #575, #581). Focus trapping and Escape come free. The
precedent is `.quick-link-dialog` in `admin/dashboard.astro`, including the
`if (dialog.open) return;` guard, since `showModal()` throws on an already-open
dialog and the exception would silently abort the rest of the handler.

Its text is built at open time from the project object the header already holds,
so there is no extra API call. It names the counts that are about to disappear,
names the files that aren't, and — when the latent has threads — says they stay
on the fold.

On failure the dialog **stays open** with the error inside it. Closing it would
strand the user on a page that may or may not still exist.

## Tests

`TestLatentDelete` in `tests/test_latents_api.py`. The load-bearing one is
`test_media_survives` — attach a file, delete the latent, assert the media row is
still there and the join is gone. That's Tube's condition expressed in code.

Alongside it: the cascade actually fires; threads don't; sibling latents keep
both their `position` and their `updated_at` (the `onupdate` hazard from the
manual-order work — `db.delete` shouldn't touch siblings, so it's asserted);
404/401/403; and the Slack event fires exactly once on success and not at all on
the 404 path.

## Out of scope

- Bulk delete, or deleting from the index grid.
- Purging the orphaned `threads` rows — revisit only if they accumulate.
- An undo window. `abandoned` is the reversible option and it already exists.
