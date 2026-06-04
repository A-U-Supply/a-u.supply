import type { EnvCurve } from './state.ts';

export interface VoiceChain {
  inputGain: GainNode;
  delayNode: DelayNode;
  delayFeedbackGain: GainNode;
  delayDryGain: GainNode;
  delayWetGain: GainNode;
  delaySum: GainNode;
  convolverNode: ConvolverNode;
  reverbDryGain: GainNode;
  reverbWetGain: GainNode;
  reverbSum: GainNode;
  filterNode: BiquadFilterNode;
  convolverConnected: boolean;
}

interface ActiveSource {
  source: AudioBufferSourceNode;
  envelopeGain: GainNode;
}

export class AudioEngine {
  ctx: AudioContext;
  masterGain: GainNode;
  compressor: DynamicsCompressorNode;
  private ir: AudioBuffer | null = null;
  private voices = new Map<string, VoiceChain>();
  private activeSources = new Map<string, ActiveSource>();

  constructor() {
    this.ctx = new AudioContext();
    this.masterGain = this.ctx.createGain();
    this.compressor = this.ctx.createDynamicsCompressor();
    this.compressor.threshold.value = -24;
    this.compressor.ratio.value = 4;
    this.compressor.attack.value = 0.003;
    this.compressor.release.value = 0.25;
    this.masterGain.connect(this.compressor);
    this.compressor.connect(this.ctx.destination);
    this.ctx.addEventListener('statechange', this.onStateChange);
  }

  async resume(): Promise<void> {
    if (this.ctx.state !== 'running' && this.ctx.state !== 'closed') {
      await this.ctx.resume();
    }
  }

  getIR(): AudioBuffer {
    if (!this.ir) this.ir = buildIR(this.ctx);
    return this.ir;
  }

  private onStateChange = (): void => {
    if (this.ctx.state === 'interrupted') {
      this.ctx.resume().catch(() => {});
    }
  };

  createVoiceChain(id: string): VoiceChain {
    const c = this.ctx;
    const ir = this.getIR();

    const inputGain = c.createGain();

    const delayNode = c.createDelay(2.0);
    const delayFeedbackGain = c.createGain();
    const delayDryGain = c.createGain();
    const delayWetGain = c.createGain();
    const delaySum = c.createGain();

    delayDryGain.gain.value = 1;
    delayWetGain.gain.value = 0;
    delayFeedbackGain.gain.value = 0.3;

    inputGain.connect(delayDryGain);
    inputGain.connect(delayNode);
    delayNode.connect(delayFeedbackGain);
    delayFeedbackGain.connect(delayNode);
    delayNode.connect(delayWetGain);
    delayDryGain.connect(delaySum);
    delayWetGain.connect(delaySum);

    const convolverNode = c.createConvolver();
    convolverNode.buffer = ir;
    const reverbDryGain = c.createGain();
    const reverbWetGain = c.createGain();
    const reverbSum = c.createGain();

    reverbDryGain.gain.value = 1;
    reverbWetGain.gain.value = 0;

    delaySum.connect(reverbDryGain);
    reverbDryGain.connect(reverbSum);
    reverbWetGain.connect(reverbSum);

    const filterNode = c.createBiquadFilter();
    filterNode.type = 'lowpass';
    filterNode.frequency.value = 20000;
    filterNode.Q.value = 1;

    reverbSum.connect(filterNode);
    filterNode.connect(this.masterGain);

    const chain: VoiceChain = {
      inputGain,
      delayNode,
      delayFeedbackGain,
      delayDryGain,
      delayWetGain,
      delaySum,
      convolverNode,
      reverbDryGain,
      reverbWetGain,
      reverbSum,
      filterNode,
      convolverConnected: false,
    };

    this.voices.set(id, chain);
    return chain;
  }

  updateVoiceChain(
    id: string,
    fx: {
      delayTime: number;
      delayFeedback: number;
      delayWet: number;
      reverbWet: number;
      filterFreq: number;
      filterQ: number;
      filterType: BiquadFilterType;
    },
    volume: number,
  ): void {
    const chain = this.voices.get(id);
    if (!chain) return;

    chain.inputGain.gain.value = volume;
    chain.delayNode.delayTime.value = fx.delayTime;
    chain.delayFeedbackGain.gain.value = Math.min(fx.delayFeedback, 0.95);
    chain.delayDryGain.gain.value = 1 - fx.delayWet;
    chain.delayWetGain.gain.value = fx.delayWet;
    chain.filterNode.type = fx.filterType;
    chain.filterNode.frequency.value = fx.filterFreq;
    chain.filterNode.Q.value = fx.filterQ;

    if (fx.reverbWet > 0 && !chain.convolverConnected) {
      chain.delaySum.connect(chain.convolverNode);
      chain.convolverNode.connect(chain.reverbWetGain);
      chain.convolverConnected = true;
    } else if (fx.reverbWet === 0 && chain.convolverConnected) {
      chain.delaySum.disconnect(chain.convolverNode);
      chain.convolverNode.disconnect(chain.reverbWetGain);
      chain.convolverConnected = false;
    }
    chain.reverbWetGain.gain.value = fx.reverbWet;
    chain.reverbDryGain.gain.value = 1 - fx.reverbWet;
  }

  playVoice(
    id: string,
    buffer: AudioBuffer,
    when: number,
    attack: number,
    release: number,
    attackCurve: EnvCurve,
    releaseCurve: EnvCurve,
    playStyle: 'one-shot' | 'cut' | 'gate' | 'legato',
    pitch: number,
  ): void {
    const chain = this.voices.get(id);
    if (!chain) return;

    const prev = this.activeSources.get(id);
    if (playStyle === 'cut' && prev) {
      prev.source.stop(when);
    }

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = 2 ** (pitch / 12);

    const envelopeGain = this.ctx.createGain();
    const attackEnd = when + attack;

    if (attack <= 0) {
      envelopeGain.gain.setValueAtTime(1, when);
    } else if (attackCurve === 'exp') {
      envelopeGain.gain.setValueAtTime(0.001, when);
      envelopeGain.gain.exponentialRampToValueAtTime(1, attackEnd);
    } else {
      envelopeGain.gain.setValueAtTime(0, when);
      envelopeGain.gain.linearRampToValueAtTime(1, attackEnd);
    }

    if (playStyle === 'one-shot') {
      const rate = source.playbackRate.value;
      const dur = buffer.duration / rate;
      const releaseStart = Math.max(when + dur - release, attackEnd);
      if (release <= 0) {
        envelopeGain.gain.setValueAtTime(1, releaseStart);
        envelopeGain.gain.setValueAtTime(0, when + dur);
      } else if (releaseCurve === 'exp') {
        envelopeGain.gain.setValueAtTime(1, releaseStart);
        envelopeGain.gain.exponentialRampToValueAtTime(0.001, when + dur);
      } else {
        envelopeGain.gain.setValueAtTime(1, releaseStart);
        envelopeGain.gain.linearRampToValueAtTime(0, when + dur);
      }
      source.start(when);
      source.stop(when + dur + 0.05);
    } else {
      source.start(when);
    }

    source.connect(envelopeGain);
    envelopeGain.connect(chain.inputGain);

    source.onended = () => this.cleanupSource(id);

    this.activeSources.set(id, { source, envelopeGain });
  }

  stopVoice(
    id: string,
    when: number,
    release: number,
    releaseCurve: EnvCurve,
  ): void {
    const active = this.activeSources.get(id);
    if (!active) return;

    const now = Math.max(when, this.ctx.currentTime);
    active.envelopeGain.gain.cancelScheduledValues(now);

    if (release <= 0) {
      active.envelopeGain.gain.setValueAtTime(0, now);
      active.source.stop(now + 0.01);
    } else if (releaseCurve === 'exp') {
      active.envelopeGain.gain.setValueAtTime(
        active.envelopeGain.gain.value,
        now,
      );
      active.envelopeGain.gain.exponentialRampToValueAtTime(
        0.001,
        now + release,
      );
      active.source.stop(now + release + 0.05);
    } else {
      active.envelopeGain.gain.setValueAtTime(
        active.envelopeGain.gain.value,
        now,
      );
      active.envelopeGain.gain.linearRampToValueAtTime(0, now + release);
      active.source.stop(now + release + 0.05);
    }
  }

  previewVoice(
    id: string,
    buffer: AudioBuffer,
    attack: number,
    release: number,
    attackCurve: EnvCurve,
    releaseCurve: EnvCurve,
    pitch: number,
  ): void {
    const chain = this.voices.get(id);
    if (!chain) return;

    const now = this.ctx.currentTime;
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = 2 ** (pitch / 12);
    const rate = source.playbackRate.value;
    const dur = buffer.duration / rate;

    const envelopeGain = this.ctx.createGain();
    const attackEnd = now + attack;

    if (attack <= 0) {
      envelopeGain.gain.setValueAtTime(0.6, now);
    } else if (attackCurve === 'exp') {
      envelopeGain.gain.setValueAtTime(0.001, now);
      envelopeGain.gain.exponentialRampToValueAtTime(0.6, attackEnd);
    } else {
      envelopeGain.gain.setValueAtTime(0, now);
      envelopeGain.gain.linearRampToValueAtTime(0.6, attackEnd);
    }

    if (release > 0) {
      const releaseStart = Math.max(now + dur - release, attackEnd);
      if (releaseCurve === 'exp') {
        envelopeGain.gain.setValueAtTime(0.6, releaseStart);
        envelopeGain.gain.exponentialRampToValueAtTime(0.001, now + dur);
      } else {
        envelopeGain.gain.setValueAtTime(0.6, releaseStart);
        envelopeGain.gain.linearRampToValueAtTime(0, now + dur);
      }
    }

    source.connect(envelopeGain);
    envelopeGain.connect(chain.inputGain);

    source.start();
    source.stop(now + dur + 0.05);
    source.onended = () => {
      source.disconnect();
      envelopeGain.disconnect();
    };
  }

  private cleanupSource(id: string): void {
    const active = this.activeSources.get(id);
    if (active) {
      active.source.disconnect();
      active.envelopeGain.disconnect();
      this.activeSources.delete(id);
    }
  }

  removeVoiceChain(id: string): void {
    const active = this.activeSources.get(id);
    if (active) {
      active.source.disconnect();
      active.envelopeGain.disconnect();
      this.activeSources.delete(id);
    }
    const chain = this.voices.get(id);
    if (!chain) return;
    chain.filterNode.disconnect();
    chain.reverbSum.disconnect();
    chain.delaySum.disconnect();
    chain.inputGain.disconnect();
    this.voices.delete(id);
  }

  destroy(): void {
    this.ctx.removeEventListener('statechange', this.onStateChange);
    this.activeSources.forEach((active) => {
      active.source.disconnect();
      active.envelopeGain.disconnect();
    });
    this.activeSources.clear();
    this.voices.forEach((_, id) => this.removeVoiceChain(id));
    this.ctx.close();
  }
}

function buildIR(ctx: AudioContext): AudioBuffer {
  const sampleRate = ctx.sampleRate;
  const length = Math.floor(sampleRate * 0.2);
  const ir = ctx.createBuffer(2, length, sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const data = ir.getChannelData(ch);
    for (let i = 0; i < length; i++) {
      const decay = Math.pow(1 - i / length, 2);
      data[i] = (Math.random() * 2 - 1) * decay;
    }
  }
  return ir;
}
