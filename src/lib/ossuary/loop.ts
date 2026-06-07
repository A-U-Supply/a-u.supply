// Ossuary — minimal audition loop.
//
// Phase 5: a single-track loop that triggers ONE slot's hits so you can hear a
// sample in rhythmic context. Deliberately NOT a sequencer — Litany owns deep
// sequencing. Its own tiny lookahead scheduler (not Litany's engine). The
// pattern's default is seeded from the slot's onset timing by the caller; this
// class just plays whatever boolean pattern it's handed.

export class AuditionLoop {
  private ctx: AudioContext;
  private timer: ReturnType<typeof setInterval> | null = null;
  private nextTime = 0;
  private stepIndex = 0;
  private rrIndex = 0;

  buffers: AudioBuffer[] = [];
  pattern: boolean[] = [];
  bpm = 120;
  rate = 8;
  onStep?: (i: number) => void;

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
  }

  get running(): boolean {
    return this.timer !== null;
  }

  private stepDuration(): number {
    return (60 / this.bpm) * (4 / this.rate);
  }

  start(): void {
    if (this.timer) return;
    this.stepIndex = 0;
    this.rrIndex = 0;
    this.nextTime = this.ctx.currentTime + 0.06;
    this.timer = setInterval(() => this.schedule(), 25);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private schedule(): void {
    const ahead = this.ctx.currentTime + 0.1;
    while (this.nextTime < ahead) {
      const len = this.pattern.length || 1;
      const i = this.stepIndex % len;
      if (this.pattern[i] && this.buffers.length) {
        const buf = this.buffers[this.rrIndex % this.buffers.length];
        this.rrIndex++;
        const src = this.ctx.createBufferSource();
        src.buffer = buf;
        src.connect(this.ctx.destination);
        src.start(this.nextTime);
      }
      const fired = i;
      requestAnimationFrame(() => this.onStep?.(fired));
      this.nextTime += this.stepDuration();
      this.stepIndex++;
    }
  }
}
