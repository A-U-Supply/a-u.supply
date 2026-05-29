# Sequencer

**Goal:** A browser-based sample sequencer living at `/admin/sequencer` that uses the sounds-bored index as its sound source — in the spirit of [mpump](https://github.com/gdamdam/mpump) but with real samples instead of synthesis.

---

## What it does

Voices are configured by search query (e.g. `kick`, `snare`, `vocal chop`). Each voice pulls random samples from the sounds-bored API, rotating through them on a configurable schedule (every hit, every bar, every 4 bars, or pinned to one sample). A step sequencer grid controls which beats each voice fires on. A randomize system generates full patterns instantly.

---

## API

```
GET /api/serve?output_index=samples-bored&query={q}&sort=random
→ 302 → /api/media/{uuid}/file  (audio/x-wav, PCM 16-bit 44100Hz)
```

Each call returns a different random WAV matching the query. The resolved UUID URL is stable and cacheable. Auth is session cookie (user is logged in) — no JS auth needed. Filename in `content-disposition`.

---

## Approach

**Page:** `src/pages/admin/sequencer.astro` with dark background override (like Punctum/Photism).

**Island:** `src/components/Sequencer.svelte` mounted `client:only="svelte"` — entirely client-side. Sub-components under `src/components/sequencer/`.

**Audio:** Web Audio API with a lookahead scheduler (`AudioContext.currentTime` + 25ms `setInterval`, scheduling ~100ms ahead). Per-voice audio graph: `AudioBufferSource → GainNode → ConvolverNode (reverb) → DelayNode → BiquadFilter → masterGain → DynamicsCompressor → destination`.

**Sample pool:** On voice creation / query change, pre-fetch 8 samples and store resolved URLs in a ring buffer. Rotation advances the ring buffer according to the voice's rotation setting. `pinned` mode bypasses the pool.

**URL state:** Full app state (voices, BPM, patterns, FX) gzip+base64 encoded into the `#` fragment — paste the URL and the beat loads.

---

## Voice model

```ts
interface Voice {
  id: string
  label: string          // "KICK"
  query: string          // "kick"
  steps: boolean[]       // [true, false, false, false, ...]
  stepCount: 8 | 16 | 32
  rotation: 'every-hit' | 'every-bar' | 'every-4bars' | 'pinned'
  pinnedUrl?: string     // media UUID URL when pinned
  volume: number         // 0–1
  fx: {
    reverb: number       // wet 0–1
    delayTime: number    // seconds
    delayFeedback: number
    delayWet: number
    filterFreq: number   // Hz
    filterQ: number
    filterType: BiquadFilterType
  }
}
```

---

## UI

Voice cards grid. Dark bg, brutalist tokens, monospace throughout.

```
SEQUENCER   ▶ STOP   BPM [128]   [🎲 steps] [🎲 query] [🎲 bpm]
                                 [🎲 voices] [🎲 ALL]   [+ VOICE]

┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ KICK          🎲│ │ SNARE         🎲│ │ HI-HAT        🎲│
│ kick_splash.wav │ │ snare_rim.wav   │ │ hat_closed.wav  │
│ [kick      ] 📌 │ │ [snare    ] 📌  │ │ [hat      ] 📌  │
│ ■·□·□·□·■·□·□·□│ │ □·□·□·□·■·□·□·□│ │ ■·■·■·■·■·■·■·■│
│ vol ──────────  │ │ vol ──────────  │ │ vol ──────────  │
│ [FX▾] steps:16  │ │ [FX▾] steps:16  │ │ [FX▾] steps:16  │
│ rotate: /hit    │ │ rotate: /bar    │ │ rotate: /4bar   │
└─────────────────┘ └─────────────────┘ └─────────────────┘

MASTER  vol ─────────────  [compressor▾]  [🔗 share]
```

- Active step: `--color-accent` (dark amber) fill.
- Playhead: column highlight advancing across all cards.
- FX panel: expands inline on click.
- Per-voice step count enables polyrhythm.

---

## Randomize controls

All granular (per-aspect buttons) + one **🎲 ALL** button:

| Button | What it does |
|--------|-------------|
| 🎲 steps | Randomize step pattern (current voice or all voices) |
| 🎲 query | Assign random instrument type from curated list |
| 🎲 voices | Randomize voice count + types |
| 🎲 bpm | Pick random BPM (60–200) |
| 🎲 ALL | All of the above |

Curated type list: `kick snare hi-hat clap tom bass vocal chord melody perc fx`

---

## File layout

```
src/pages/admin/sequencer.astro
src/components/Sequencer.svelte
src/components/sequencer/
  Toolbar.svelte
  VoiceCard.svelte
  StepGrid.svelte
  FXPanel.svelte
  MasterSection.svelte
src/lib/sequencer/
  state.ts      — types, URL encode/decode
  audio.ts      — AudioContext singleton, master bus
  scheduler.ts  — lookahead tick loop
  pool.ts       — sample pool, ring buffer, pin logic
  randomize.ts  — randomize helpers
```

---

## Open questions

- Should the sequencer sidebar entry appear under a new section or alongside existing admin tools?
- Any preferred name other than "Sequencer"?
- Should beats be shareable to non-logged-in users (public URL), or admin-only for now?
