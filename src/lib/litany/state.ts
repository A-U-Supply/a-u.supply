import { strFromU8, strToU8, gunzipSync, gzipSync } from 'fflate';

export type Rotation = 'every-hit' | 'every-bar' | 'every-4bars' | 'pinned';
export type StepCount = 8 | 16 | 32;

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
