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
  private previousStepTrig = new Map<string, boolean>();
  private loopCount = new Map<string, number>();
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
    this.previousStepTrig.clear();
    this.loopCount.clear();
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
      const loop = this.loopCount.get(voice.id) ?? 0;

      let stepShouldFire = stepActive;
      if (stepActive) {
        const override = voice.stepOverrides[voiceTick];
        const cond = override?.condition;
        if (cond) {
          stepShouldFire = false;
          switch (cond) {
            case '1:1':
              stepShouldFire = true;
              break;
            case '1:2':
              stepShouldFire = loop % 2 === 0;
              break;
            case '1:4':
              stepShouldFire = loop % 4 === 0;
              break;
            case '1:8':
              stepShouldFire = loop % 8 === 0;
              break;
            case '2:2':
              stepShouldFire = loop % 2 === 1;
              break;
            case '3:4':
              stepShouldFire = loop % 4 === 2;
              break;
            case '4:4':
              stepShouldFire = loop % 4 === 3;
              break;
            case 'PRE':
              stepShouldFire = this.previousStepTrig.get(voice.id) ?? false;
              break;
            case 'NOT_PRE':
              stepShouldFire = !(this.previousStepTrig.get(voice.id) ?? false);
              break;
            case 'FILL':
              stepShouldFire = false; // TODO: global fill trigger
              break;
          }
        }

        if (!stepShouldFire) {
          this.previousStepActive.set(voice.id, false);
          this.previousStepTrig.set(voice.id, false);
          const nextTick = (voiceTick + 1) % voice.stepCount;
          this.voiceTicks.set(voice.id, nextTick);
          if (nextTick === 0) this.loopCount.set(voice.id, loop + 1);
          if (nextTick === 0) this.voiceBarCounts.set(voice.id, barCount + 1);
          continue;
        }
      }

      if (stepShouldFire) {
        const override = voice.stepOverrides[voiceTick];
        let triggered = false;
        if (override?.probability != null) {
          if (Math.random() * 100 >= override.probability) {
            triggered = false;
          } else {
            triggered = true;
          }
        } else {
          triggered = true;
        }

        if (triggered) {
          const pool = this.pools.get(voice.id);
          if (pool && pool.status === 'ready') {
            const shouldTrigger =
              voice.playStyle !== 'legato' ||
              !(this.previousStepTrig.get(voice.id) ?? false);

            if (shouldTrigger) {
              const buffer = pool.next(
                voice.rotation,
                voice.pinnedRotation,
                isBarStart,
                is4BarStart,
              );
              if (buffer) {
                const pitch = override?.pitch ?? voice.pitch;
                this.engine.playVoice(
                  voice.id,
                  buffer,
                  when,
                  voice.envelope.attack,
                  voice.envelope.release,
                  voice.envelope.attackCurve,
                  voice.envelope.releaseCurve,
                  voice.playStyle,
                  pitch,
                );
              }
            }
          }
        }

        this.previousStepTrig.set(voice.id, triggered);
      } else if (
        (voice.playStyle === 'gate' || voice.playStyle === 'legato') &&
        prevActive
      ) {
        this.engine.stopVoice(
          voice.id,
          when,
          voice.envelope.release,
          voice.envelope.releaseCurve,
        );
      }

      this.previousStepActive.set(voice.id, stepActive);

      const nextTick = (voiceTick + 1) % voice.stepCount;
      this.voiceTicks.set(voice.id, nextTick);

      if (nextTick === 0) {
        this.loopCount.set(voice.id, (this.loopCount.get(voice.id) ?? 0) + 1);
        this.voiceBarCounts.set(voice.id, barCount + 1);
      }
    }

    const tick = this.globalTick;
    requestAnimationFrame(() => this.onTick(tick));
  }
}
