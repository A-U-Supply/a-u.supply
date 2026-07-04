# Ossuary iteration: glossary, perc rule, kit curation

Three fixes from first real sessions with Ossuary (design: PR #490). One PR, three commits.

## Goal

Make the interpreter self-explanatory, make the `perc` slot reachable, and keep long-clip carves browsable instead of dumping 500+ hits per slot.

## 1. Interpreter glossary

The interpreter section (brain picker + knobs) has no explanations anywhere — the glossary promised in the Phase-1 scaffold was never built.

- Native `<details class="oss-glossary">` between the interpreter header and the knobs (same pattern as the Litany page's details blocks).
- `MODEL_NOTES` in `interpret.ts` — one blurb per brain, adapted from `apps/rottengenizdat.toml` param descriptions into sound-design voice.
- `KNOB_NOTES` local to `Ossuary.svelte` — one blurb per single-pass knob.
- Charcoal styling from the existing `--oss-*` palette; collapsed by default; works before any clip loads.

## 2. Perc rule in `classifySlot`

`classifySlot()` can only ever return kick/hi-hat/snare — `perc` is unreachable. Insert between the hi-hat check and the snare fallthrough:

```ts
if (crossingsPerSec < 3000 && durMs >= 240) return 'perc';
```

Kick and hi-hat branches are untouched; perc is carved exclusively from the current snare residue. Rationale from the chain's own constants: 1500–3000 crossings/sec is the mid band below the "hat-bright" line where toms/congas/blocks ring; 240 ms is 2× the existing 120 ms "crack" threshold — snare cracks and claps stay under ~200 ms, tonal perc rings longer.

## 3. Kit curation — auto-keep top-N + bench

Long clips carve absurd hit counts (574 kicks, 351 snares in one session); the only cap is the 2 s per-hit duration cap. A browsable kit runs ~5–15 variations per instrument.

Hybrid curation:

- `detectOnsets` returns `{ sample, strength }[]` — the `df[i]` onset strength it already computes, currently discarded.
- `Hit` gains `strength` and `kept`. `carve()` auto-keeps the top `DEFAULT_KEEP_LIMIT = 12` per slot by strength (`applyAutoKeep`); the cap is carve-time only, never re-enforced on reassign/promote.
- Kept hits render as today; the rest sit in a per-slot bench drawer (`<details>`, "+ N more") with promote/demote. Benched hits are editable in place and drawn dimmer on the waveform.
- Keep-limit slider (4–32) next to Sensitivity calls `resizeKeep`: fill promotes the strongest benched, trim demotes the weakest kept — manual promotions survive raises and usually survive trims (no reset).
- Loop, ZIP export, and index-to-library use kept hits only; export buttons disable at 0 kept.
- `applyAutoKeep`/`resizeKeep` iterate `SLOTS` only, so a future `phrase` slot (PR #513) is invisible to them — phrase capture will set `kept: true` itself.

## Out of scope

The phrase slot (PR #513) and its `PercSlot` narrowing — trivial rebase later. No JS test infra exists; verification is manual against the dev server.
