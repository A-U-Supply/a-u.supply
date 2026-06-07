// Ossuary — per-hit rendering.
//
// Phase 4: one offline render that applies a hit's edits (trim is already baked
// into start/end by the caller) — reverse, pitch, gain/normalize, a tweakable
// amplitude envelope, and an FX chain (filter, peaking EQ, delay, reverb). The
// SAME function powers live audition now and the baked WAV on save (Phase 6),
// so what you hear is what you export.

export interface FxParams {
  filterType: BiquadFilterType;
  filterFreq: number;
  filterQ: number;
  eqFreq: number;
  eqGain: number;
  eqQ: number;
  delayTime: number;
  delayFeedback: number;
  delayMix: number;
  reverbSize: number;
  reverbMix: number;
}

export interface HitEdit {
  gainDb: number;
  normalize: boolean;
  reverse: boolean;
  pitch: number;
  attack: number;
  decay: number;
  curve: number;
  fx: FxParams;
}

export function defaultEdit(): HitEdit {
  return {
    gainDb: 0,
    normalize: false,
    reverse: false,
    pitch: 0,
    attack: 0.004,
    decay: 0.06,
    curve: 0,
    fx: {
      filterType: 'lowpass',
      filterFreq: 20000,
      filterQ: 0.7,
      eqFreq: 1000,
      eqGain: 0,
      eqQ: 1,
      delayTime: 0.25,
      delayFeedback: 0.3,
      delayMix: 0,
      reverbSize: 1.5,
      reverbMix: 0,
    },
  };
}

function shape(x: number, curve: number): number {
  const clamped = Math.min(1, Math.max(0, x));
  const exponent = Math.exp(-curve * 4);
  return Math.pow(clamped, exponent);
}

function buildEnvelope(
  outDur: number,
  attack: number,
  decay: number,
  curve: number,
  peak: number,
  sampleRate: number,
): Float32Array {
  const N = Math.min(4096, Math.max(16, Math.floor(outDur * sampleRate)));
  const arr = new Float32Array(N);
  const aFrac = outDur > 0 ? Math.min(1, attack / outDur) : 0;
  const dFrac = outDur > 0 ? Math.min(1, decay / outDur) : 0;
  for (let i = 0; i < N; i++) {
    const t = i / (N - 1);
    let amp: number;
    if (aFrac > 0 && t < aFrac) {
      amp = shape(t / aFrac, curve);
    } else if (dFrac > 0 && t > 1 - dFrac) {
      amp = shape((1 - t) / dFrac, curve);
    } else {
      amp = 1;
    }
    arr[i] = amp * peak;
  }
  return arr;
}

function makeImpulse(ctx: OfflineAudioContext, seconds: number): AudioBuffer {
  const len = Math.max(1, Math.floor(seconds * ctx.sampleRate));
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) {
    d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2.5);
  }
  return buf;
}

export async function renderHit(
  source: AudioBuffer,
  start: number,
  end: number,
  edit: HitEdit,
): Promise<AudioBuffer> {
  const sr = source.sampleRate;
  const segLen = Math.max(1, end - start);
  const src0 = source.getChannelData(0);

  const seg = new Float32Array(segLen);
  for (let i = 0; i < segLen; i++) {
    const idx = start + (edit.reverse ? segLen - 1 - i : i);
    seg[i] = src0[idx] ?? 0;
  }

  let peak = 0;
  for (let i = 0; i < segLen; i++) {
    const a = Math.abs(seg[i]);
    if (a > peak) peak = a;
  }
  const normFactor = edit.normalize && peak > 0 ? 1 / peak : 1;
  const linGain = Math.pow(10, edit.gainDb / 20) * normFactor;
  const rate = Math.pow(2, edit.pitch / 12);
  const outDur = segLen / rate / sr;

  const { fx } = edit;
  const tail =
    Math.max(
      fx.reverbMix > 0 ? fx.reverbSize : 0,
      fx.delayMix > 0 ? fx.delayTime * 6 : 0,
      edit.decay,
    ) + 0.03;
  const totalLen = Math.ceil((outDur + tail) * sr);

  const ctx = new OfflineAudioContext(1, totalLen, sr);

  const segBuf = ctx.createBuffer(1, segLen, sr);
  segBuf.copyToChannel(seg, 0);
  const node = ctx.createBufferSource();
  node.buffer = segBuf;
  node.playbackRate.value = rate;

  const env = ctx.createGain();
  env.gain.setValueCurveAtTime(
    buildEnvelope(outDur, edit.attack, edit.decay, edit.curve, linGain, sr),
    0,
    Math.max(0.001, outDur),
  );

  const filter = ctx.createBiquadFilter();
  filter.type = fx.filterType;
  filter.frequency.value = fx.filterFreq;
  filter.Q.value = fx.filterQ;

  const eq = ctx.createBiquadFilter();
  eq.type = 'peaking';
  eq.frequency.value = fx.eqFreq;
  eq.gain.value = fx.eqGain;
  eq.Q.value = fx.eqQ;

  const master = ctx.createGain();
  master.connect(ctx.destination);

  node.connect(env);
  env.connect(filter);
  filter.connect(eq);
  eq.connect(master);

  if (fx.delayMix > 0) {
    const delay = ctx.createDelay(2.0);
    delay.delayTime.value = fx.delayTime;
    const fb = ctx.createGain();
    fb.gain.value = Math.min(0.95, fx.delayFeedback);
    const wet = ctx.createGain();
    wet.gain.value = fx.delayMix;
    eq.connect(delay);
    delay.connect(fb);
    fb.connect(delay);
    delay.connect(wet);
    wet.connect(master);
  }

  if (fx.reverbMix > 0) {
    const conv = ctx.createConvolver();
    conv.buffer = makeImpulse(ctx, fx.reverbSize);
    const wet = ctx.createGain();
    wet.gain.value = fx.reverbMix;
    eq.connect(conv);
    conv.connect(wet);
    wet.connect(master);
  }

  node.start(0);
  return ctx.startRendering();
}
