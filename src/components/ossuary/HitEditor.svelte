<script lang="ts">
  import { untrack } from 'svelte';
  import { snapToZeroCrossing, type Hit } from '../../lib/ossuary/carve.ts';

  let {
    hit,
    buffer,
    playing,
    onAudition,
  }: {
    hit: Hit;
    buffer: AudioBuffer;
    playing: boolean;
    onAudition: () => void;
  } = $props();

  let canvas: HTMLCanvasElement | undefined;
  let container: HTMLDivElement | undefined;
  let dragging: 'start' | 'end' | null = $state(null);

  // A stable view window (segment + padding) so trim handles can grow or shrink
  // without the waveform shifting under the cursor. Reset only when the hit id
  // changes, not on every trim tweak.
  let win = $state({ a: 0, b: 1 });
  $effect(() => {
    void hit.id;
    untrack(() => {
      const pad = Math.floor((hit.end - hit.start) * 0.4) || 2000;
      win = {
        a: Math.max(0, hit.start - pad),
        b: Math.min(buffer.length, hit.end + pad),
      };
    });
  });

  const pct = (sample: number) =>
    ((sample - win.a) / Math.max(1, win.b - win.a)) * 100;

  function draw() {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 600;
    const h = canvas.clientHeight || 110;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    const g = canvas.getContext('2d');
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);

    const data = buffer.getChannelData(0);
    const span = Math.max(1, win.b - win.a);
    const mid = h / 2;

    // Windowed peaks.
    const samplesPerCol = Math.max(1, Math.floor(span / w));
    g.strokeStyle = '#c9a227';
    g.beginPath();
    for (let x = 0; x < w; x++) {
      const s0 = win.a + x * samplesPerCol;
      let min = 1;
      let max = -1;
      for (let i = 0; i < samplesPerCol; i++) {
        const v = data[s0 + i] ?? 0;
        if (v < min) min = v;
        if (v > max) max = v;
      }
      g.moveTo(x + 0.5, mid - max * mid);
      g.lineTo(x + 0.5, mid - min * mid);
    }
    g.stroke();

    // Shade outside the trimmed region.
    const xs = (pct(hit.start) / 100) * w;
    const xe = (pct(hit.end) / 100) * w;
    g.fillStyle = 'rgba(0,0,0,0.55)';
    g.fillRect(0, 0, xs, h);
    g.fillRect(xe, 0, w - xe, h);
  }

  $effect(() => {
    void hit.start;
    void hit.end;
    void win;
    requestAnimationFrame(draw);
  });

  function onPointerDown(edge: 'start' | 'end', e: PointerEvent) {
    dragging = edge;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragging || !container) return;
    const rect = container.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    let sample = Math.round(win.a + frac * (win.b - win.a));
    sample = snapToZeroCrossing(buffer.getChannelData(0), sample);
    const guard = 64;
    if (dragging === 'start') {
      hit.start = Math.max(win.a, Math.min(sample, hit.end - guard));
    } else {
      hit.end = Math.min(win.b, Math.max(sample, hit.start + guard));
    }
  }

  function onPointerUp() {
    dragging = null;
  }

  const durationMs = $derived(
    ((hit.end - hit.start) / buffer.sampleRate) * 1000,
  );
</script>

<div class="oss-editor">
  <div
    class="oss-editor__wave"
    bind:this={container}
    onpointermove={onPointerMove}
    onpointerup={onPointerUp}
    onpointerleave={onPointerUp}
  >
    <canvas bind:this={canvas}></canvas>
    <button
      class="oss-handle oss-handle--start"
      style={`left:${pct(hit.start)}%`}
      onpointerdown={(e) => onPointerDown('start', e)}
      aria-label="Trim start"
    ></button>
    <button
      class="oss-handle oss-handle--end"
      style={`left:${pct(hit.end)}%`}
      onpointerdown={(e) => onPointerDown('end', e)}
      aria-label="Trim end"
    ></button>
  </div>

  <div class="oss-editor__bar">
    <button class="brutalist-control" onclick={onAudition}>
      {playing ? '■ Stop' : '▶ Audition (edited)'}
    </button>
    <span class="oss-hint">{durationMs.toFixed(0)} ms</span>
  </div>

  <!-- Shape -->
  <div class="oss-editor__group">
    <div class="oss-editor__group-title">Shape</div>
    <div class="oss-knobs">
      <label class="oss-knob">
        <span>Gain <em>{hit.edit.gainDb.toFixed(1)} dB</em></span>
        <input
          type="range"
          min="-24"
          max="24"
          step="0.5"
          bind:value={hit.edit.gainDb}
        />
      </label>
      <label class="oss-knob">
        <span>Pitch <em>{hit.edit.pitch} st</em></span>
        <input
          type="range"
          min="-24"
          max="24"
          step="1"
          bind:value={hit.edit.pitch}
        />
      </label>
      <label class="oss-knob">
        <span>Attack <em>{(hit.edit.attack * 1000).toFixed(0)} ms</em></span>
        <input
          type="range"
          min="0"
          max="0.5"
          step="0.002"
          bind:value={hit.edit.attack}
        />
      </label>
      <label class="oss-knob">
        <span>Decay <em>{(hit.edit.decay * 1000).toFixed(0)} ms</em></span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.005"
          bind:value={hit.edit.decay}
        />
      </label>
      <label class="oss-knob">
        <span>Curve <em>{hit.edit.curve.toFixed(2)}</em></span>
        <input
          type="range"
          min="-1"
          max="1"
          step="0.05"
          bind:value={hit.edit.curve}
        />
      </label>
      <label class="oss-knob oss-knob--toggle">
        <input type="checkbox" bind:checked={hit.edit.normalize} />
        <span>Normalize</span>
      </label>
      <label class="oss-knob oss-knob--toggle">
        <input type="checkbox" bind:checked={hit.edit.reverse} />
        <span>Reverse</span>
      </label>
    </div>
  </div>

  <!-- Filter / EQ -->
  <div class="oss-editor__group">
    <div class="oss-editor__group-title">Filter / EQ</div>
    <div class="oss-knobs">
      <label class="oss-knob">
        <span>Filter</span>
        <select class="brutalist-control" bind:value={hit.edit.fx.filterType}>
          <option value="lowpass">lowpass</option>
          <option value="highpass">highpass</option>
          <option value="bandpass">bandpass</option>
        </select>
      </label>
      <label class="oss-knob">
        <span>Cutoff <em>{hit.edit.fx.filterFreq.toFixed(0)} Hz</em></span>
        <input
          type="range"
          min="40"
          max="20000"
          step="10"
          bind:value={hit.edit.fx.filterFreq}
        />
      </label>
      <label class="oss-knob">
        <span>Reso <em>{hit.edit.fx.filterQ.toFixed(1)}</em></span>
        <input
          type="range"
          min="0.1"
          max="12"
          step="0.1"
          bind:value={hit.edit.fx.filterQ}
        />
      </label>
      <label class="oss-knob">
        <span>EQ freq <em>{hit.edit.fx.eqFreq.toFixed(0)} Hz</em></span>
        <input
          type="range"
          min="40"
          max="16000"
          step="10"
          bind:value={hit.edit.fx.eqFreq}
        />
      </label>
      <label class="oss-knob">
        <span>EQ gain <em>{hit.edit.fx.eqGain.toFixed(1)} dB</em></span>
        <input
          type="range"
          min="-18"
          max="18"
          step="0.5"
          bind:value={hit.edit.fx.eqGain}
        />
      </label>
      <label class="oss-knob">
        <span>EQ Q <em>{hit.edit.fx.eqQ.toFixed(1)}</em></span>
        <input
          type="range"
          min="0.1"
          max="12"
          step="0.1"
          bind:value={hit.edit.fx.eqQ}
        />
      </label>
    </div>
  </div>

  <!-- Delay -->
  <div class="oss-editor__group">
    <div class="oss-editor__group-title">Delay</div>
    <div class="oss-knobs">
      <label class="oss-knob">
        <span>Time <em>{(hit.edit.fx.delayTime * 1000).toFixed(0)} ms</em></span
        >
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          bind:value={hit.edit.fx.delayTime}
        />
      </label>
      <label class="oss-knob">
        <span>Feedback <em>{hit.edit.fx.delayFeedback.toFixed(2)}</em></span>
        <input
          type="range"
          min="0"
          max="0.95"
          step="0.01"
          bind:value={hit.edit.fx.delayFeedback}
        />
      </label>
      <label class="oss-knob">
        <span>Mix <em>{hit.edit.fx.delayMix.toFixed(2)}</em></span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          bind:value={hit.edit.fx.delayMix}
        />
      </label>
    </div>
  </div>

  <!-- Reverb -->
  <div class="oss-editor__group">
    <div class="oss-editor__group-title">Reverb</div>
    <div class="oss-knobs">
      <label class="oss-knob">
        <span>Size <em>{hit.edit.fx.reverbSize.toFixed(2)} s</em></span>
        <input
          type="range"
          min="0.1"
          max="4"
          step="0.05"
          bind:value={hit.edit.fx.reverbSize}
        />
      </label>
      <label class="oss-knob">
        <span>Mix <em>{hit.edit.fx.reverbMix.toFixed(2)}</em></span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          bind:value={hit.edit.fx.reverbMix}
        />
      </label>
    </div>
  </div>
</div>
