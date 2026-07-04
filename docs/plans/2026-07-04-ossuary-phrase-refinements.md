# Ossuary phrase refinements: dry phrases, discoverability, split export

**Date:** 2026-07-04
**Status:** Approved (Brendan, brainstorm interview same day). Follows the phrase
slot (PR #526, `docs/plans/2026-06-09-ossuary-phrase.md`).

## Context

The phrase slot ships wet-only: phrases carve from the **interpreted** waveform.
Brendan's original intent also covered the **original clip before interpretation** —
grabbing clean words before the brain mangles them. Two more gaps from first contact:
nothing in the UI suggests drag-to-phrase exists, and phrases only export bundled
with the whole kit.

Decisions from the interview:

1. Source-clip ("dry") phrases **survive INTERPRET** — they belong to the clip, not
   the pass. Wet phrases still die on re-interpret behind the confirm guard.
2. Dry vs interpreted phrases are **distinguishable in tags**: flat `dry` /
   `interpreted`. Dry phrases carry no `model:`/`rave`/`rotten`/`rgz-9` — no brain
   touched them.
3. The source waveform gets the same **zoom + scroll** as the wet one (dragging a
   2 s word out of a 3-minute strip needs it).
4. Discoverability, quiet chrome only: permanent caption under each waveform
   ("click: select a hit · drag: carve a phrase"), an **always-visible Phrases
   panel** whose empty state teaches both gestures, and a glossary-style blurb.
   No first-run state to track.
5. Export: **phrase-specific buttons alongside the kit ones**. The existing buttons
   keep meaning "everything"; the combined ZIP nests phrases under `phrases/`.
6. Optional per-phrase **name field**; default filename stays `{kit}_phrase_{n}`.
7. Phrases-only, drums-only, and both are all first-class workflows.

Side effect worth advertising: **uploads become useful**. Phrase-carving is fully
client-side, so an uploaded file (still un-interpretable) can be carved into
phrases immediately.

## Shape

- `Hit` gains `origin: 'source' | 'interpreted'` (auto-carves are always
  `'interpreted'`) and `name?: string` (phrases only, optional).
- `bufferFor(hit)` resolves audition/editor/bake buffers; the loop path stays
  wet-only and dry phrases can't reassign into drum slots (no dropdown), so no
  cross-buffer leaks are possible.
- Drag-select generalizes to a per-canvas state bag instantiated for both waveforms;
  wet keeps click-to-select-nearest, source plain click is a no-op. Same 100 ms
  floor / 15 s clamp / zero-cross snap.
- The phrase strip moves out of the wet slot grid into its own **Phrases panel**
  (visible whenever a clip is loaded): rows show origin marker + duration + name
  input; reassign dropdown only on interpreted phrases; phrase-only export buttons
  live here.
- `sampleTags` becomes origin-aware; phrase filenames prefer `slugify(name)`.

## Addendum (same interview): page-bottom docs

Ossuary's page gets the same collapsed-`<details>` documentation block Litany has
at the bottom of its page (`src/pages/admin/atelier/litany.astro`), covering every
element and step: source/clip, brains + knobs, carving/slots/bench, the hit editor,
the audition loop, phrases (both origins), and export/tags. Same markup pattern and
type scale, restyled to the Ossuary charcoal palette.

## Out of scope

Phrase transcription/auto-naming, phrase consumers (Litany phrase voice, phrase
browser), autoscroll-while-dragging, cap changes. Litany untouched.

## Verification

Node harness (origin stamping, dry/interpreted tag matrix, filename fallback);
`npm run build`; manual dev-server pass: dry phrase under zoom → INTERPRET →
survives; wet phrase; both marked correctly in the panel; named + default phrase
files in Phrases ZIP; combined ZIP nests `phrases/`; upload → carve → export with
no interpretation; captions + teaching empty state on fresh load.
