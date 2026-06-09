# Ossuary — the `phrase` slot (long samples)

**Date:** 2026-06-09
**Status:** Design for review (Tube). Direction pre-agreed in Slack; this doc locks the details.
**Lives:** `/admin/atelier/ossuary` (existing tool — this is an extension, not a new page)
**Depends on:** Ossuary MVP (merged); server filtering ask tracked separately as a GitHub issue.

> **Identity (firm):** a `phrase` is **any long material** — a whole word, a spoken
> phrase, a melodic run, a texture pull. It is *not* vocals-specific: vocal-ness stays
> discoverable through the AI-tagged `voice` field; the slot describes **form, not
> content**. And it is still not a drum machine feature — phrases never enter the
> audition loop. They're long bones for the vault.

## Context — why this exists

Ossuary carves percussive one-shots (≤ 2 s, onset-detected, auto-classified into
kick/snare/hi-hat/perc). Brendan wants to carve **bigger pieces** — whole words and
phrases — out of interpreted clips. From the Slack thread with Tube (2026-06-09):

- Not normal or ideal for drum kits — but **a valid use case for Ossuary**.
- They **stay in `samples-bored`** — no separate collection. The requirement is that
  search-doc metadata makes them **easy to filter out** ("give me a random sample
  that is NOT long").
- Mechanism: **a new instrument-type tag akin to kick/snare/hi-hat** (naming
  delegated; we choose `phrase` — see below).

## Nomenclature: why `phrase`

Tube floated "vocal" and "long". Both rejected:
- **`vocal`** names *content* — but a long melodic run through the `sol_full` brain
  belongs in this slot just as much as a word does. Content is already covered: the
  AI tagging pipeline writes `voice: vox` on vocal material independently.
- **`long`** names what `duration_seconds` already measures. The tag should say what
  the thing *is*, not how big it is — and duration is the more robust filter anyway
  (it's server-computed on every upload; tags depend on the client behaving).

`slot:phrase` reads naturally beside `slot:kick`, and the existing tag pipeline
(`sampleTags()` in `src/lib/ossuary/export.ts:58`) emits it with zero changes.

## How phrases are made (two gestures, both deliberate)

The auto-classifier **never** assigns `phrase`. The onset detector fires on every
syllable and breath, so automatic phrase detection would be noise; instead phrases
only come from explicit user gestures (plus the existing slot-reassign dropdown).

### Gesture 1 — drag-select on the wet waveform

- Pointer-drag across the wet waveform paints a translucent phrase-colored selection.
  A plain click (< ~5 px of movement) keeps the existing behavior **verbatim**:
  select-nearest-hit (the recent click-to-select feature), and dismisses any active
  selection. One canvas, one coherent rule.
- On release: both ends snap to zero crossings (existing helper,
  `src/lib/ossuary/carve.ts:85-101`); selections under 100 ms are discarded;
  selections over **15 s** are clamped at the drag origin with a visible
  "clamped to 15 s" hint.
- A selection bar appears: **▶ Selection** (audition the region pre-commit, reuses
  `toggleRange`), **Carve as phrase (X.X s)** (creates the hit and opens the editor),
  and ✕ clear.
- Works under zoom — the coordinate math already accounts for canvas scaling.

### Gesture 2 — merge adjacent slices

When the carver's onsets happen to bracket a word nicely, stitching beats dragging:
- Each hit row gets a small **merge checkbox**. With ≥ 2 checked, a bar offers
  **Merge N slices → phrase (X.X s)**.
- Validation, not clamping: the button disables with a reason if the slices aren't
  **adjacent** (no unselected hit between them, sorted by start) or the combined span
  exceeds **15 s**. Clamping a merge would silently drop a slice — refuse instead.
  (The asymmetry with drag-clamp is intentional: a drag is continuous, a merge is
  discrete units.)
- Merging **consumes** the source hits — that's what merge means; Re-carve is the
  recovery path. Re-carve / re-INTERPRET get a `confirm()` guard when phrase hits
  exist, since they'd wipe deliberate work.

## The 15-second soft cap

`MAX_HIT_SECONDS = 2.0` stays untouched — it constrains auto-carve only. A new
exported `MAX_PHRASE_SECONDS = 15` (in `carve.ts`) caps both gestures. Rationale:
generous for words/phrases/textures while keeping AudioBuffers, offline renders, and
upload sizes sane. Not a server contract — just a client guardrail.

## Loop exclusion

Phrases are **click-to-audition only**. The phrase section simply has **no ↻ loop
button**, and the type system enforces the rest: `loopSlot` narrows to `PercSlot`,
so the scheduler (`loop.ts`) and the onset-seeded default pattern never see phrases.
No `loop.ts` changes. (Reassigning a phrase *to* kick makes a 15 s loopable hit —
allowed; that's the user's deliberate chaos.)

## Implementation sketch

All client-side, two files of substance:

**`src/lib/ossuary/carve.ts`** — type split + pure functions:

```ts
export const SLOTS = ['kick','snare','hi-hat','perc'] as const;  // auto-carve + loopable
export type PercSlot = (typeof SLOTS)[number];
export const ALL_SLOTS = [...SLOTS, 'phrase'] as const;          // assignable
export type Slot = (typeof ALL_SLOTS)[number];
export const MAX_PHRASE_SECONDS = 15;

export function carvePhrase(buffer: AudioBuffer, startSample: number, endSample: number): Hit
export function mergeHits(selected: Hit[]): Hit          // min(start) → max(end), fresh edit
export function areAdjacent(all: Hit[], selected: Hit[]): boolean
```

`Hit` shape unchanged (`{id, start, end, slot, edit}`); nothing outside `carve()`
assumes ≤ 2 s. `classifySlot` narrows its return type to `PercSlot` (no behavior
change). New `SLOT_COLOR.phrase`.

**`src/components/Ossuary.svelte`** —
- Typing pass: `loopSlot`/`startLoop`/`toggleLoop`/`renderSlotBuffers` take
  `PercSlot`; the reassign `<select>` iterates `ALL_SLOTS`.
- Hit-row markup extracted into a `{#snippet hitRow(...)}`; the four-column
  percussive grid stays as-is, with a **full-width phrase section** at the bottom
  (`.oss-slot--phrase`, `grid-column: 1 / -1`, rendered only when phrase hits exist).
  Phrase rows show duration (`· 8.2 s`).
- Canvas `onclick` becomes `pointerdown/move/up` (+ `pointercancel`) with pointer
  capture; selection overlay drawn in the existing waveform redraw `$effect`.
- Merge checkboxes + merge bar; selection bar.

**`src/styles/atelier/ossuary.css`** — phrase section, overlay, bars.

**Verified no-changes:** HitEditor, `render.ts`, `export.ts`. `renderHit` is
duration-agnostic (FX tails compute on top of any length; a 15 s offline render is
sub-second). Envelope defaults are absolute edge fades (4 ms attack / 60 ms decay) —
on a phrase that's a gentle de-click, not a percussive gate. Export iterates all hits:
filenames come out `{slug}_phrase_1.wav`, tags `slot:phrase`, automatically.

## Server contract (Tube — tracked as a GitHub issue, not in this change)

Phrase docs land in `samples-bored` exactly like one-shots, with two filterable
discriminators already in the Meili doc:
- `tags` contains **`slot:phrase`** (filterable — `tags` is in
  `FILTERABLE_ATTRIBUTES`, `server/search_client.py:71-131`)
- **`duration_seconds`** — server-computed on upload, filterable + sortable
  (`search_client.py:80,138`)

What's missing is exposure: `/api/serve` (`server/search_api.py:2870-2946`) accepts
no filter parameter, and Litany queries it unfiltered (`src/lib/litany/pool.ts:49`),
so phrases would pollute Litany's random pulls. The ask: expose filtering on
`/api/serve` (e.g. `max_duration_seconds=` and/or tag exclusion — Tube's call which),
and add the Litany-side exclusion Tube mentioned he'd do separately.

## Edge cases

- Drag wider than the zoomed viewport: zoom out first; no autoscroll in v1.
- Drag < 100 ms → discarded. Drag > 15 s → clamped + hint. Merge > 15 s or
  non-adjacent → disabled with reason.
- Reassign while the old slot is looping → stale loop buffers until next loop
  render; identical to existing drop/reassign behavior today. Accepted.
- Re-carve/re-INTERPRET with phrases present → confirm guard.

## Verification (manual, dev server → Atelier → Ossuary)

1. Source a ≥ 20 s clip → INTERPRET → auto-carve: hits land in kick/snare/hi-hat
   only; no phrase section visible.
2. **Click regression:** single click still selects the nearest hit; creates no
   selection.
3. Drag ~5 s → overlay; release → snapped; ▶ Selection plays; Carve as phrase →
   full-width phrase row appears, editor opens; trim/gain/FX/audition all work.
   Drag ~20 s → clamps to 15 s with hint.
4. Merge: 3 adjacent slices → one phrase spanning them, sources gone. Non-adjacent →
   disabled "must be adjacent". > 15 s total → disabled "exceeds 15 s".
5. Loop: phrase section has no ↻; a snare loop + PLR controls work; reassign
   snare ↔ phrase via dropdown moves the hit between sections.
6. Zoom 6× and drag-select — selection lands where dragged.
7. Export ZIP → `{slug}_phrase_1.wav` present, correct length, plays. Index to
   library → doc carries `source:ossuary, slot:phrase, model:<m>, kit:<slug>,
   carved, rave, rotten, rgz-9`.
8. Re-carve with phrases present → confirm guard fires.
