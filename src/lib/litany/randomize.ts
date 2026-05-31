import { createVoice, defaultFx, type Voice, type StepCount } from './state.ts';

const INSTRUMENT_TYPES = [
  'kick',
  'snare',
  'hi-hat',
  'clap',
  'tom',
  'bass',
  'vocal',
  'chord',
  'melody',
  'perc',
  'fx',
];

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function randomizeSteps(stepCount: StepCount): boolean[] {
  return Array.from({ length: stepCount }, () => Math.random() < 0.3);
}

export function randomizeQuery(): string {
  return pick(INSTRUMENT_TYPES);
}

export function randomizeBpm(): number {
  return Math.floor(Math.random() * (160 - 70 + 1)) + 70;
}

export function randomizeVoices(count: number): Voice[] {
  const queries = [...INSTRUMENT_TYPES]
    .sort(() => Math.random() - 0.5)
    .slice(0, count);
  return queries.map((query) => {
    const voice = createVoice(query.toUpperCase(), query);
    voice.steps = randomizeSteps(voice.stepCount);
    return voice;
  });
}

export function randomizeVoiceSteps(voice: Voice): Voice {
  return { ...voice, steps: randomizeSteps(voice.stepCount) };
}

export function randomizeVoiceQuery(voice: Voice): Voice {
  const query = randomizeQuery();
  return { ...voice, query, label: query.toUpperCase(), fx: defaultFx() };
}
