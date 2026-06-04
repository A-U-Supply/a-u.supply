import { strFromU8, strToU8, gunzipSync, gzipSync } from 'fflate';

export type Rotation = 'every-hit' | 'every-bar' | 'every-4bars';
export type PinnedRotation =
  | 'every-hit'
  | 'every-bar'
  | 'every-4bars'
  | 'fixed';
export type StepCount = number;
export type PlayStyle = 'one-shot' | 'cut' | 'gate' | 'legato';
export type EnvCurve = 'linear' | 'exp';

export interface EuclideanParams {
  pulses: number;
  length: number;
  offset: number;
}

export type TrigCondition =
  | '1:1'
  | '1:2'
  | '1:4'
  | '1:8'
  | '2:2'
  | '3:4'
  | '4:4'
  | 'PRE'
  | 'NOT_PRE'
  | 'FILL';

export interface StepOverride {
  probability?: number;
  pitch?: number;
  volume?: number;
  condition?: TrigCondition;
}

export interface MacroDef {
  name: string;
  pitch?: number;
  volume?: number;
  probability?: number;
}

export const MACROS: MacroDef[] = [
  { name: '—' },
  { name: 'accent', volume: 1.0, pitch: 1 },
  { name: 'ghost', volume: 0.3, probability: 30 },
  { name: 'soft', volume: 0.5 },
  { name: 'hi', pitch: 4, volume: 0.9 },
  { name: 'lo', pitch: -5, volume: 0.9 },
  { name: 'x', probability: 50, pitch: 7 },
];

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
  pinnedRotation: PinnedRotation;
  pinned: string[];
  volume: number;
  pitch: number;
  muted: boolean;
  soloed: boolean;
  fx: FxParams;
  envelope: EnvelopeParams;
  playStyle: PlayStyle;
  euclidean: EuclideanParams;
  stepOverrides: (StepOverride | null)[];
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

  const pattern: boolean[] = [];
  let prev: number | null = null;

  for (let i = 0; i < length; i++) {
    const curr = Math.floor((i * pulses) / length);
    pattern.push(curr !== prev);
    prev = curr;
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
    pinnedRotation: 'every-hit',
    pinned: [],
    volume: 0.8,
    pitch: 0,
    muted: false,
    soloed: false,
    fx: defaultFx(),
    envelope: defaultEnvelope(),
    playStyle: 'one-shot',
    euclidean: defaultEuclidean(),
    stepOverrides: [],
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
