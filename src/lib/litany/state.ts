import { strFromU8, strToU8, gunzipSync, gzipSync } from 'fflate';

export type Rotation = 'every-hit' | 'every-bar' | 'every-4bars' | 'pinned';
export type StepCount = number;
export type PlayStyle = 'one-shot' | 'cut' | 'gate' | 'legato';
export type EnvCurve = 'linear' | 'exp';
export type PatternMode = 'steps' | 'euclidean';

export interface EuclideanParams {
  pulses: number;
  length: number;
  offset: number;
}

export interface EnvelopeParams {
  attack: number;
  release: number;
  attackCurve: EnvCurve;
  releaseCurve: EnvCurve;
}

export interface FxParams {
  delayTime: number;
  delayFeedback: number;
  delayWet: number;
  reverbWet: number;
  filterFreq: number;
  filterQ: number;
  filterType: BiquadFilterType;
}

export interface Voice {
  id: string;
  label: string;
  query: string;
  steps: boolean[];
  stepCount: StepCount;
  rotation: Rotation;
  pinnedUrl?: string;
  volume: number;
  muted: boolean;
  soloed: boolean;
  fx: FxParams;
  envelope: EnvelopeParams;
  playStyle: PlayStyle;
  patternMode: PatternMode;
  euclidean: EuclideanParams;
}

export interface AppState {
  voices: Voice[];
  bpm: number;
}

export function defaultFx(): FxParams {
  return {
    delayTime: 0.3,
    delayFeedback: 0.3,
    delayWet: 0,
    reverbWet: 0,
    filterFreq: 20000,
    filterQ: 1,
    filterType: 'lowpass',
  };
}

export function defaultEnvelope(): EnvelopeParams {
  return {
    attack: 0,
    release: 0.05,
    attackCurve: 'linear',
    releaseCurve: 'linear',
  };
}

export function defaultEuclidean(): EuclideanParams {
  return { pulses: 4, length: 16, offset: 0 };
}

export function bjorklund(
  pulses: number,
  length: number,
  offset: number = 0,
): boolean[] {
  if (pulses <= 0) return Array(length).fill(false);
  if (pulses >= length) return Array(length).fill(true);

  let pattern: boolean[] = [];
  let counts: number[] = [];
  let remainders: boolean[] = [];

  for (let i = 0; i < length; i++) {
    pattern.push(i < pulses);
  }

  while (true) {
    let count = 0;
    let i = 0;
    while (i < pattern.length && pattern[i] === pattern[0]) {
      count++;
      i++;
    }
    if (i >= pattern.length) break;

    let j = pattern.length - 1;
    while (j >= i && pattern[j] === pattern[pattern.length - 1]) {
      j--;
    }
    j++;
    const remainder = pattern.slice(j);
    const main = pattern.slice(0, j);

    const merged: boolean[] = [];
    let mi = 0;
    let ri = 0;
    while (mi < main.length || ri < remainder.length) {
      if (mi < main.length) merged.push(main[mi++]);
      if (ri < remainder.length) merged.push(remainder[ri++]);
    }
    pattern = merged;
  }

  if (offset) {
    const rotated = pattern.slice(offset % length);
    rotated.push(...pattern.slice(0, offset % length));
    return rotated;
  }
  return pattern;
}

export function createVoice(
  label: string,
  query: string,
  stepCount: StepCount = 16,
): Voice {
  return {
    id: crypto.randomUUID(),
    label,
    query,
    steps: Array(stepCount).fill(false),
    stepCount,
    rotation: 'every-hit',
    volume: 0.8,
    muted: false,
    soloed: false,
    fx: defaultFx(),
    envelope: defaultEnvelope(),
    playStyle: 'one-shot',
    patternMode: 'steps',
    euclidean: defaultEuclidean(),
  };
}

export function defaultVoices(): Voice[] {
  const kick = createVoice('KICK', 'kick');
  kick.steps = kick.steps.map((_, i) => i % 4 === 0);

  const snare = createVoice('SNARE', 'snare');
  snare.steps = snare.steps.map((_, i) => i === 4 || i === 12);

  const hat = createVoice('HI-HAT', 'hi-hat');
  hat.steps = hat.steps.map((_, i) => i % 2 === 0);

  return [kick, snare, hat];
}

export function encodeState(state: AppState): string {
  const json = JSON.stringify(state);
  const compressed = gzipSync(strToU8(json));
  return btoa(String.fromCharCode(...compressed));
}

export function decodeState(hash: string): AppState {
  const binary = atob(hash);
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  const json = strFromU8(gunzipSync(bytes));
  return JSON.parse(json) as AppState;
}
