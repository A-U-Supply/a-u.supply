# Litany

**Goal:** A browser-side Atelier tool at `/admin/atelier/litany` — a step sequencer using the sounds-bored index as its sound source instead of synthesis. In the spirit of [mpump](https://github.com/gdamdam/mpump) but with real samples.

The name comes from the ecclesiastical register of the site (Auspices, Hecatomb, Photism, Punctum): a litany is voices calling out in repeating pattern — exactly what a step sequencer does.

---

## Approach

**Page:** `src/pages/admin/atelier/litany.astro`, `current="litany"`, dark background `#0d0d0d` added to `applyPageBackground()` in `Admin.astro`. Sidebar hint updated to "browser-side creative tools".

**Island:** `src/components/Litany.svelte` mounted `client:only="svelte"`. Sub-components under `src/components/litany/`. Fully client-side — no SSR.

**Audio:** Web Audio API with a lookahead scheduler (`AudioContext.currentTime` + 25ms `setInterval`, scheduling ~100ms ahead). Pool stores **decoded `AudioBuffer` objects** (not URLs) — `decodeAudioData()` happens at fetch time, not at trigger time.

**URL state:** Full app state gzip+base64 encoded into the `#` fragment via `fflate`. Paste URL → beat loads.

**Sharing:** Admin-only for now. Hash-state URLs work between logged-in users. Public sharing requires guest auth on `/api/serve` — out of scope.

---

## API

```
GET /api/serve?output_index=samples-bored&query={q}&sort=random
→ 302 → /api/media/{uuid}/file  (audio/x-wav, PCM 16-bit 44100Hz)
```

Each call returns a different random WAV. Auth: session cookie in prod, `VITE_AU_API_KEY` Bearer token in local dev. Filename in `content-disposition`.

---

## Voice model

```ts
interface Voice {
  id: string
  label: string           // "KICK"
  query: string           // "kick"
  steps: boolean[]        // length = stepCount
  stepCount: 8 | 16 | 32 // 1 step = 1 sixteenth-note; 16 steps = 1 bar at 4/4
  rotation: 'every-hit' | 'every-bar' | 'every-4bars' | 'pinned'
  pinnedUrl?: string
  volume: number          // 0–1
  muted: boolean          // reserve now, implement as fast-follow
  soloed: boolean         // reserve now, implement as fast-follow
  fx: {
    delayTime: number     // seconds
    delayFeedback: number
    delayWet: number
    reverbWet: number     // delay → reverb is the correct signal order
    filterFreq: number
    filterQ: number
    filterType: BiquadFilterType
  }
}
```

**Step math:** `every-bar` rotation triggers at step 0 of every 16-step cycle; `every-4bars` at every 64 steps.

---

## Audio graph (per voice)

```
AudioBufferSource → GainNode (vol)
                       → DelayNode ──────────────────┐
                       → ConvolverNode (reverb IR) ───┤ dry/wet mix per effect
                       → BiquadFilterNode ────────────┘
                       → masterGain → DynamicsCompressorNode → destination
```

**Effect order: delay → reverb** (conventional: delay repeats get reverb applied).

**ConvolverNode IR:** Programmatically generated — stereo exponential-decay white noise, ~0.5s duration. Never load an external IR file.

**FX bypass:** When `delayWet === 0`, disconnect `DelayNode` from graph entirely. When `reverbWet === 0`, disconnect `ConvolverNode`. Eight idle convolvers are non-trivial; don't just set wet to 0.

---

## AudioContext lifecycle

- Create lazily on first play click (browser gesture requirement).
- Call `ctx.resume()` on play — browsers may suspend the context after inactivity.
- On Svelte `onDestroy` (ViewTransition navigation away): stop scheduler `setInterval`, call `audio.destroy()` which closes `AudioContext` and disconnects all nodes. No zombie intervals.
- **Keyboard:** do not use `Space` — `Player.svelte` claims it globally. Use `p` or `Enter` for play/stop.

---

## Sample pool

- On voice create / query change: fetch 8 samples in parallel (8 calls to `/api/serve`), decode each via `ctx.decodeAudioData()`, store `AudioBuffer[]` ring buffer.
- Refresh in background when half-depleted.
- `pinned` mode: freeze ring buffer at current index.
- On fetch failure / 404: set `status: 'error'`, grey out voice card, silently skip triggers — don't break the scheduler.

---

## Randomize controls

| Button | Scope |
|--------|-------|
| 🎲 Steps | Randomize step pattern — one voice or all |
| 🎲 Query | Random instrument from curated list — one or all |
| 🎲 Voices | Randomize voice count + types |
| 🎲 BPM | Random BPM 60–200 |
| 🎲 ALL | All of the above |

Curated list: `kick snare hi-hat clap tom bass vocal chord melody perc fx`

---

## File layout

```
src/pages/admin/atelier/litany.astro
src/components/Litany.svelte           — top-level island
src/components/litany/
  Toolbar.svelte                       — play/stop, BPM, randomize, share
  VoiceCard.svelte                     — query, steps, vol, rotation, pin, FX
  StepGrid.svelte                      — step buttons + playhead
  FXPanel.svelte                       — delay/reverb/filter, expandable
  MasterSection.svelte                 — master vol + compressor
src/lib/litany/
  state.ts                             — types, URL encode/decode (fflate)
  audio.ts                             — AudioEngine class, IR builder, destroy()
  scheduler.ts                         — lookahead tick loop
  pool.ts                              — SamplePool, ring buffer, pin
  randomize.ts                         — randomize helpers
```

Existing files to modify:
- `src/layouts/Admin.astro` — sidebar entry, `applyPageBackground`, hint text
- `docs/glossary.md` — add `litany` slug row
- `docs/atelier.md` — update hint to "browser-side creative tools", add Litany entry
- `package.json` — add `fflate`
