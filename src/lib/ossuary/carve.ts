// Ossuary — carving.
//
// Phase 3: browser-side onset detection on the wet (interpreted) buffer, slice
// the transients into one-shots, snap cut points to zero crossings (de-click),
// and auto-classify each slice into a slot. Everything here is local + sync; the
// user reassigns/drops in the UI afterwards. Slots are just searchable tags
// (Litany's vocab) — there is no "complete kit" requirement.

export const SLOTS = ['kick', 'snare', 'hi-hat', 'perc'] as const;
export type Slot = (typeof SLOTS)[number];

export const SLOT_COLOR: Record<Slot, string> = {
  kick: '#c98a3a',
  snare: '#cf5b57',
  'hi-hat': '#6fb3c0',
  perc: '#9b8cc0',
};

export interface Hit {
  id: string;
  /** Sample offsets into the source (wet) buffer. */
  start: number;
  end: number;
  slot: Slot;
}

const HOP = 256;
const WIN = 1024;
const MAX_HIT_SECONDS = 2.0;

/**
 * Adaptive energy-flux onset detector. Returns onset positions in samples.
 * `sensitivity` 0..1 lowers the picking threshold (more onsets).
 */
export function detectOnsets(buffer: AudioBuffer, sensitivity = 0.5): number[] {
  const data = buffer.getChannelData(0);
  const nFrames = Math.max(0, Math.floor((data.length - WIN) / HOP));
  if (nFrames < 2) return data.length ? [0] : [];

  // Per-frame RMS energy envelope.
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

  // Detection function: rectified first difference (energy rises = onsets).
  const df = new Float32Array(nFrames);
  let mean = 0;
  for (let i = 1; i < nFrames; i++) {
    df[i] = Math.max(0, env[i] - env[i - 1]);
    mean += df[i];
  }
  mean /= nFrames || 1;

  const W = 20; // local-average half-window (frames)
  const minGap = Math.max(1, Math.floor((0.05 * buffer.sampleRate) / HOP)); // 50ms
  const factor = 1.8 - 1.3 * sensitivity; // higher sensitivity → lower bar
  const floor = mean * (0.6 - 0.5 * sensitivity) + 1e-4;

  const onsets: number[] = [];
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
      onsets.push(i * HOP);
      last = i;
    }
  }
  return onsets;
}

/** Nearest zero crossing to `index`, searched outward within `win` samples. */
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

/**
 * Crude but reassignable slot guess from zero-crossing rate + duration.
 * Low brightness → kick, high → hi-hat, otherwise snare.
 */
export function classifySlot(
  data: Float32Array,
  start: number,
  end: number,
  sampleRate: number,
): Slot {
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
  return 'snare';
}

/** Carve the buffer into classified, zero-cross-snapped hits. */
export function carve(buffer: AudioBuffer, sensitivity = 0.5): Hit[] {
  const data = buffer.getChannelData(0);
  const sr = buffer.sampleRate;
  const maxLen = Math.floor(MAX_HIT_SECONDS * sr);
  const onsets = detectOnsets(buffer, sensitivity);

  const hits: Hit[] = [];
  for (let i = 0; i < onsets.length; i++) {
    const start = snapToZeroCrossing(data, onsets[i]);
    const rawEnd = i + 1 < onsets.length ? onsets[i + 1] : data.length;
    let end = snapToZeroCrossing(data, Math.min(rawEnd, start + maxLen));
    if (end <= start)
      end = Math.min(start + Math.floor(0.05 * sr), data.length);
    hits.push({
      id: crypto.randomUUID(),
      start,
      end,
      slot: classifySlot(data, start, end, sr),
    });
  }
  return hits;
}
