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

export class AudioEngine {
  ctx: AudioContext;
  masterGain: GainNode;
  compressor: DynamicsCompressorNode;
  private ir: AudioBuffer | null = null;
  private voices = new Map<string, VoiceChain>();

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
  }

  async resume(): Promise<void> {
    if (this.ctx.state === 'suspended') await this.ctx.resume();
  }

  getIR(): AudioBuffer {
    if (!this.ir) this.ir = buildIR(this.ctx);
    return this.ir;
  }

  createVoiceChain(id: string): VoiceChain {
    const c = this.ctx;
    const ir = this.getIR();

    const inputGain = c.createGain();

    // Delay section
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

    // Reverb section (delay → reverb)
    const convolverNode = c.createConvolver();
    convolverNode.buffer = ir;
    const reverbDryGain = c.createGain();
    const reverbWetGain = c.createGain();
    const reverbSum = c.createGain();

    reverbDryGain.gain.value = 1;
    reverbWetGain.gain.value = 0;

    delaySum.connect(reverbDryGain);
    // convolverNode not connected yet (reverbWet starts at 0)
    reverbDryGain.connect(reverbSum);
    reverbWetGain.connect(reverbSum);

    // Filter
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

    // Connect/disconnect convolver based on wet value
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

  playBuffer(id: string, buffer: AudioBuffer, when: number): void {
    const chain = this.voices.get(id);
    if (!chain) return;
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(chain.inputGain);
    source.start(when);
  }

  removeVoiceChain(id: string): void {
    const chain = this.voices.get(id);
    if (!chain) return;
    chain.filterNode.disconnect();
    chain.reverbSum.disconnect();
    chain.delaySum.disconnect();
    chain.inputGain.disconnect();
    this.voices.delete(id);
  }

  destroy(): void {
    this.voices.forEach((_, id) => this.removeVoiceChain(id));
    this.ctx.close();
  }
}

function buildIR(ctx: AudioContext): AudioBuffer {
  const sampleRate = ctx.sampleRate;
  const length = Math.floor(sampleRate * 0.5);
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
