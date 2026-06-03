# Plan: Litany envelope & play-style controls

**Date**: 2026-06-02
**Goal**: Per-voice envelope (AR) and play-style controls so each sample can be shaped and gated independently.

## Approach

Add an **ENV panel** to each voice card (toggled like FX) with attack/release sliders, a play-style dropdown, and a sample preview button. The audio engine gains per-voice tracking of active source nodes for choke/gate/legato behaviour. The scheduler gains awareness of `playStyle` to decide when to trigger vs. sustain vs. release.

## Data model

### New types (`src/lib/litany/state.ts`)

```ts
export type PlayStyle = 'one-shot' | 'cut' | 'gate' | 'legato';

export interface EnvelopeParams {
  attack: number;  // 0–2 seconds
  release: number; // 0–3 seconds
}
```

Added to `Voice`:
```ts
export interface Voice {
  // … existing …
  envelope: EnvelopeParams;
  playStyle: PlayStyle;
}
```

Defaults: `attack=0`, `release=0.05`, `playStyle='one-shot'`.

## Play styles

| Style | Trigger (step active) | Release (step inactive) | Polyphony |
|---|---|---|---|
| `one-shot` | Play full sample with AR envelope | N/A — plays to completion | Yes (overlapping) |
| `cut` | Stop previous source, start new with AR envelope | N/A | No (monophonic, choke) |
| `gate` | Start sample with attack | Apply release envelope → stop source | Yes (each step is a new source) |
| `legato` | Start sample with attack ONLY if previous step was inactive | Apply release envelope → stop source | No retrigger on consecutive active steps |

## API surface

### Audio engine (`audio.ts`)

```
playVoice(id, buffer, when, envelope, playStyle) → void
stopVoice(id, when, release) → void
previewVoice(id, buffer) → void
```

- `playVoice`: inserts an envelope GainNode between source and voice chain. Ramps attack, schedules release for one-shot (near buffer end). For `cut` mode, stops any previous source immediately.
- `stopVoice`: ramps release on the GainNode, schedules `source.stop()` after release time.
- `previewVoice`: plays the current pool entry immediately through the master (not via the normal scheduler path), bypassing the step grid.

The engine tracks active sources per voice:
```ts
private activeSources = new Map<string, {
  source: AudioBufferSourceNode;
  envelopeGain: GainNode;
}>();
```

### Pool (`pool.ts`)

No changes — pool just supplies AudioBuffers and entry names as before.

### Scheduler (`scheduler.ts`)

Tracks `previousStepActive: Map<string, boolean>` per voice. On each tick:

1. Determine if step is active (`voice.steps[voiceTick]`)
2. Compare against `previousStepActive` to detect rising/falling edges
3. Based on `playStyle`, call `engine.playVoice()` or `engine.stopVoice()`
4. Update `previousStepActive`

## UI

### VoiceCard meta-row

Add an **ENV** button next to FX:
```
[step-select] [rotation-select] [FX ▾] [ENV ▾]
```

### EnvPanel component (`src/components/litany/EnvPanel.svelte`)

```
┌─────────────────────────────────────────────────┐
│ ENV                                              │
│ ATTACK  [========o----------------] 0.00s        │
│ RELEASE [=====o-------------------] 0.05s        │
│ STYLE   [one-shot ▾]                             │
│ [▶ PREVIEW]  [ ] auto preview on change          │
└─────────────────────────────────────────────────┘
```

- Attack range: 0–2s, step 0.01
- Release range: 0–3s, step 0.01
- Style dropdown: one-shot / cut / gate / legato
- Preview button: plays the current pinned/active sample
- Auto-preview toggle: plays sample automatically when params change
- All changes call `onChange({ ...voice, envelope, playStyle }, true)` (skip history for slider drags)

## Open questions

- **Envelope on one-shot?** Apply attack release as fade-in/fade-out, not as gate. Release is scheduled near buffer end. **Decided: yes.**
- **Cut mode details:** When a new step triggers in cut mode, the previous source is stopped *immediately* (no release ramp) — this is the "choke" behaviour. The new source gets its own attack envelope.

## Files changed

| File | What |
|---|---|
| `src/lib/litany/state.ts` | Add `PlayStyle`, `EnvelopeParams`, update `Voice`, add defaults |
| `src/lib/litany/audio.ts` | Envelope GainNode per trigger, source tracking, `playVoice`/`stopVoice`/`previewVoice` |
| `src/lib/litany/scheduler.ts` | `previousStepActive` map, play-style-aware scheduling |
| `src/components/litany/EnvPanel.svelte` | **New** — AR sliders, play-style dropdown, preview button, auto-toggle |
| `src/components/litany/VoiceCard.svelte` | ENV button in meta-row, `envOpen` state, render `EnvPanel` |
| `src/components/Litany.svelte` | Pass `onPreview` callback, hook up engine preview |
