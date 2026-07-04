// Ossuary — carving.
//
// Phase 3: browser-side onset detection on the wet (interpreted) buffer, slice
// the transients into one-shots, snap cut points to zero crossings (de-click),
// and auto-classify each slice into a slot. Everything here is local + sync; the
// user reassigns/drops in the UI afterwards. Slots are just searchable tags
// (Litany's vocab) — there is no "complete kit" requirement.

import { defaultEdit, type HitEdit } from './render.ts';

// Percussive slots: auto-carved, auto-classified, loopable.
export const SLOTS = ['kick', 'snare', 'hi-hat', 'perc'] as const;
export type PercSlot = (typeof SLOTS)[number];

// `phrase` is long material (a word, a run, a texture) — only ever created by a
// deliberate gesture (drag-select or merge), never by the classifier, and never
// looped. See docs/plans/2026-06-09-ossuary-phrase.md.
export const ALL_SLOTS = [...SLOTS, 'phrase'] as const;
export type Slot = (typeof ALL_SLOTS)[number];

export const SLOT_COLOR: Record<Slot, string> = {
  kick: '#c98a3a',
  snare: '#cf5b57',
  'hi-hat': '#6fb3c0',
  perc: '#9b8cc0',
  phrase: '#8fa87c',
};

export interface Onset {
  sample: number;
  strength: number;
}

export interface Hit {
  id: string;
  start: number;
  end: number;
  slot: Slot;
  strength: number;
  kept: boolean;
  edit: HitEdit;
}

const HOP = 256;
const WIN = 1024;
const MAX_HIT_SECONDS = 2.0;

// Client guardrail for both phrase gestures, not a server contract — keeps
// AudioBuffers, offline renders, and upload sizes sane.
export const MAX_PHRASE_SECONDS = 15;

export function detectOnsets(buffer: AudioBuffer, sensitivity = 0.5): Onset[] {
  const data = buffer.getChannelData(0);
  const nFrames = Math.max(0, Math.floor((data.length - WIN) / HOP));
  if (nFrames < 2) return data.length ? [{ sample: 0, strength: 1 }] : [];

  const env = new Float32Array(nFrames);
  for (let f = 0; f < nFrames; f++) {
    const s = f * HOP;
    let sum = 0;
    for (let i = 0; i < WIN; i++) {
      const v = data[s + i];
      sum += v * v;
    }
    env[f] = Math.sqrt(sum / WIN);
  }

  const df = new Float32Array(nFrames);
  let mean = 0;
  for (let i = 1; i < nFrames; i++) {
    df[i] = Math.max(0, env[i] - env[i - 1]);
    mean += df[i];
  }
  mean /= nFrames || 1;

  const W = 20;
  const minGap = Math.max(1, Math.floor((0.05 * buffer.sampleRate) / HOP));
  const factor = 1.8 - 1.3 * sensitivity;
  const floor = mean * (0.6 - 0.5 * sensitivity) + 1e-4;

  const onsets: Onset[] = [];
  let last = -minGap;
  for (let i = 1; i < nFrames - 1; i++) {
    let acc = 0;
    let cnt = 0;
    for (let j = Math.max(0, i - W); j < Math.min(nFrames, i + W); j++) {
      acc += df[j];
      cnt++;
    }
    const thresh = (acc / cnt) * factor + floor;
    if (
      df[i] > thresh &&
      df[i] >= df[i - 1] &&
      df[i] > df[i + 1] &&
      i - last >= minGap
    ) {
      onsets.push({ sample: i * HOP, strength: df[i] });
      last = i;
    }
  }
  return onsets;
}

export function snapToZeroCrossing(
  data: Float32Array,
  index: number,
  win = 256,
): number {
  const clamped = Math.max(1, Math.min(data.length - 1, index));
  for (let d = 0; d < win; d++) {
    for (const k of [clamped + d, clamped - d]) {
      if (k > 0 && k < data.length) {
        const a = data[k - 1];
        const b = data[k];
        if ((a <= 0 && b > 0) || (a >= 0 && b < 0)) return k;
      }
    }
  }
  return clamped;
}

export function classifySlot(
  data: Float32Array,
  start: number,
  end: number,
  sampleRate: number,
): PercSlot {
  const n = Math.max(1, end - start);
  let zc = 0;
  for (let i = start + 1; i < end; i++) {
    if (data[i - 1] < 0 !== data[i] < 0) zc++;
  }
  const crossingsPerSec = (zc / n) * sampleRate;
  const durMs = (n / sampleRate) * 1000;

  if (crossingsPerSec < 1500) return 'kick';
  if (crossingsPerSec > 5000 || (durMs < 120 && crossingsPerSec > 3000))
    return 'hi-hat';
  // Mid-brightness (below the 3000 hat-bright line) that rings ≥240ms — 2× the
  // 120ms "crack" window — is tonal perc: toms, congas, blocks. Snare cracks
  // and claps stay short and fall through.
  if (crossingsPerSec < 3000 && durMs >= 240) return 'perc';
  return 'snare';
}

export const DEFAULT_KEEP_LIMIT = 12;

// Reset pass: kept = top-limit by onset strength, per slot. Carve-time only —
// the cap is never re-enforced on reassign or promote. Iterates SLOTS, so any
// future non-slot hit kind (phrase) is invisible here.
export function applyAutoKeep(hits: Hit[], limit: number): void {
  for (const slot of SLOTS) {
    const inSlot = hits.filter((h) => h.slot === slot);
    inSlot.sort((a, b) => b.strength - a.strength);
    inSlot.forEach((h, i) => (h.kept = i < limit));
  }
}

// Nudge toward a new limit without resetting: fill promotes the strongest
// benched, trim demotes the weakest kept — so manual promotions survive
// raises, and usually survive trims.
export function resizeKeep(hits: Hit[], limit: number): void {
  for (const slot of SLOTS) {
    const inSlot = hits.filter((h) => h.slot === slot);
    const kept = inSlot
      .filter((h) => h.kept)
      .sort((a, b) => a.strength - b.strength);
    const bench = inSlot
      .filter((h) => !h.kept)
      .sort((a, b) => b.strength - a.strength);
    while (kept.length < limit && bench.length) {
      const h = bench.shift()!;
      h.kept = true;
      kept.unshift(h);
    }
    while (kept.length > limit) kept.shift()!.kept = false;
  }
}

export function carve(
  buffer: AudioBuffer,
  sensitivity = 0.5,
  keepLimit = DEFAULT_KEEP_LIMIT,
): Hit[] {
  const data = buffer.getChannelData(0);
  const sr = buffer.sampleRate;
  const maxLen = Math.floor(MAX_HIT_SECONDS * sr);
  const onsets = detectOnsets(buffer, sensitivity);

  const hits: Hit[] = [];
  for (let i = 0; i < onsets.length; i++) {
    const start = snapToZeroCrossing(data, onsets[i].sample);
    const rawEnd = i + 1 < onsets.length ? onsets[i + 1].sample : data.length;
    let end = snapToZeroCrossing(data, Math.min(rawEnd, start + maxLen));
    if (end <= start)
      end = Math.min(start + Math.floor(0.05 * sr), data.length);
    hits.push({
      id: crypto.randomUUID(),
      start,
      end,
      slot: classifySlot(data, start, end, sr),
      strength: onsets[i].strength,
      kept: false,
      edit: defaultEdit(),
    });
  }
  applyAutoKeep(hits, keepLimit);
  return hits;
}

// ── Phrases ─────────────────────────────────────────────────────────────────
// Both creators return a kept hit — phrases have no bench; they only exist
// because the user asked for them.

/** Carve a phrase from a drag-selected region. Ends snap to zero crossings. */
export function carvePhrase(
  buffer: AudioBuffer,
  startSample: number,
  endSample: number,
): Hit {
  const data = buffer.getChannelData(0);
  const start = snapToZeroCrossing(data, Math.min(startSample, endSample));
  let end = snapToZeroCrossing(data, Math.max(startSample, endSample));
  if (end <= start)
    end = Math.min(start + Math.floor(0.1 * buffer.sampleRate), data.length);
  return {
    id: crypto.randomUUID(),
    start,
    end,
    slot: 'phrase',
    strength: 0,
    kept: true,
    edit: defaultEdit(),
  };
}

/**
 * Adjacent = no unselected non-phrase hit sits between the selected ones when
 * sorted by start. (Phrases overlap the timeline freely, so they don't count.)
 */
export function areAdjacent(all: Hit[], selected: Hit[]): boolean {
  if (selected.length < 2) return false;
  const ids = new Set(selected.map((h) => h.id));
  const sorted = [...all].sort((a, b) => a.start - b.start);
  const positions: number[] = [];
  sorted.forEach((h, i) => {
    if (ids.has(h.id)) positions.push(i);
  });
  if (positions.length !== selected.length) return false;
  return (
    positions[positions.length - 1] - positions[0] === positions.length - 1
  );
}

/** Merge slices into one phrase spanning min(start)→max(end), fresh edit. */
export function mergeHits(selected: Hit[]): Hit {
  return {
    id: crypto.randomUUID(),
    start: Math.min(...selected.map((h) => h.start)),
    end: Math.max(...selected.map((h) => h.end)),
    slot: 'phrase',
    strength: Math.max(...selected.map((h) => h.strength)),
    kept: true,
    edit: defaultEdit(),
  };
}
