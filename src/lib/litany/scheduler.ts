import type { AudioEngine } from './audio.ts';
import type { SamplePool } from './pool.ts';
import type { Voice } from './state.ts';

const SCHEDULE_AHEAD = 0.1; // seconds
const LOOKAHEAD_MS = 25; // ms

export class Scheduler {
  private engine: AudioEngine;
  private pools: Map<string, SamplePool>;
  private getVoices: () => Voice[];
  private onTick: (globalTick: number) => void;
  private getBpm: () => number;

  private nextNoteTime = 0;
  private globalTick = 0;
  private voiceTicks = new Map<string, number>();
  private voiceBarCounts = new Map<string, number>();
  private previousStepActive = new Map<string, boolean>();
  private intervalId: ReturnType<typeof setInterval> | null = null;

  constructor(
    engine: AudioEngine,
    pools: Map<string, SamplePool>,
    getVoices: () => Voice[],
    onTick: (globalTick: number) => void,
    getBpm: () => number,
  ) {
    this.engine = engine;
    this.pools = pools;
    this.getVoices = getVoices;
    this.onTick = onTick;
    this.getBpm = getBpm;
  }

  start(): void {
    this.nextNoteTime = this.engine.ctx.currentTime + 0.05;
    this.globalTick = 0;
    this.voiceTicks.clear();
    this.voiceBarCounts.clear();
    this.previousStepActive.clear();
    this.intervalId = setInterval(() => this.schedule(), LOOKAHEAD_MS);
  }

  stop(): void {
    if (this.intervalId !== null) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
  }

  private schedule(): void {
    const ctx = this.engine.ctx;
    while (this.nextNoteTime < ctx.currentTime + SCHEDULE_AHEAD) {
      this.scheduleStep(this.nextNoteTime);
      const stepDuration = 60 / this.getBpm() / 4;
      this.nextNoteTime += stepDuration;
      this.globalTick++;
    }
  }

  private scheduleStep(when: number): void {
    const voices = this.getVoices();
    const anySoloed = voices.some((v) => v.soloed);

    for (const voice of voices) {
      if (voice.muted) continue;
      if (anySoloed && !voice.soloed) continue;

      const voiceTick = this.voiceTicks.get(voice.id) ?? 0;
      const isBarStart = voiceTick === 0;
      const barCount = this.voiceBarCounts.get(voice.id) ?? 0;
      const is4BarStart = isBarStart && barCount % 4 === 0;

      const stepActive = voice.steps[voiceTick] ?? false;
      const prevActive = this.previousStepActive.get(voice.id) ?? false;

      if (stepActive) {
        const pool = this.pools.get(voice.id);
        if (pool && pool.status === 'ready') {
          const shouldTrigger = voice.playStyle !== 'legato' || !prevActive;

          if (shouldTrigger) {
            const buffer = pool.next(voice.rotation, isBarStart, is4BarStart);
            if (buffer) {
              this.engine.playVoice(
                voice.id,
                buffer,
                when,
                voice.envelope.attack,
                voice.envelope.release,
                voice.playStyle,
              );
            }
          }
        }
      } else if (
        (voice.playStyle === 'gate' || voice.playStyle === 'legato') &&
        prevActive
      ) {
        this.engine.stopVoice(voice.id, when, voice.envelope.release);
      }

      this.previousStepActive.set(voice.id, stepActive);

      const nextTick = (voiceTick + 1) % voice.stepCount;
      this.voiceTicks.set(voice.id, nextTick);

      if (nextTick === 0) {
        this.voiceBarCounts.set(voice.id, barCount + 1);
      }
    }

    const tick = this.globalTick;
    requestAnimationFrame(() => this.onTick(tick));
  }
}
