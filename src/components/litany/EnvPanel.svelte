<script lang="ts">
  import type {
    EnvelopeParams,
    PlayStyle,
    EnvCurve,
  } from '../../lib/litany/state.ts';

  interface Props {
    envelope: EnvelopeParams;
    playStyle: PlayStyle;
    onChange: (envelope: EnvelopeParams, playStyle: PlayStyle) => void;
    onPreview: () => void;
  }

  let { envelope, playStyle, onChange, onPreview }: Props = $props();

  let autoPreview = $state(false);
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let svgWidth = $state(200);

  const DISPLAY_SECS = 3;

  function attackPx() {
    return (envelope.attack / DISPLAY_SECS) * svgWidth;
  }

  function releasePx() {
    return (envelope.release / DISPLAY_SECS) * svgWidth;
  }

  function releaseStartPx() {
    return svgWidth - releasePx();
  }

  /* ---- curve path generation ---- */

  function envPath() {
    const h = 72;
    const ax = attackPx();
    const rx = releaseStartPx();
    const pts: string[] = [];

    pts.push(`M 0,${h}`);

    // Attack curve
    if (envelope.attack > 0) {
      if (envelope.attackCurve === 'exp') {
        pts.push(`Q ${ax},${h} ${ax},0`);
      } else {
        pts.push(`L ${ax},0`);
      }
    } else {
      pts.push(`L 0,0`);
    }

    // Sustain
    if (rx > ax) {
      pts.push(`L ${rx},0`);
    }

    // Release curve
    if (envelope.release > 0) {
      if (envelope.releaseCurve === 'exp') {
        pts.push(`Q ${rx},${h} ${svgWidth},${h}`);
      } else {
        pts.push(`L ${svgWidth},${h}`);
      }
    } else {
      pts.push(`L ${svgWidth},${h}`);
    }

    return pts.join(' ');
  }

  /* ---- dragging ---- */

  type DragTarget = 'attack' | 'release' | null;

  let dragging: DragTarget = $state(null);

  function dragStart(target: DragTarget, e: MouseEvent | TouchEvent) {
    e.preventDefault();
    dragging = target;
  }

  function dragMove(e: MouseEvent | TouchEvent) {
    if (!dragging) return;
    const svg = (e.currentTarget as SVGElement).closest('svg');
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const clientX = 'touches' in e ? e.touches[0].clientX : e.clientX;
    const x = ((clientX - rect.left) / rect.width) * svgWidth;
    const t = Math.max(0, Math.min(x / svgWidth, 1)) * DISPLAY_SECS;

    const next = { ...envelope };
    if (dragging === 'attack') {
      next.attack = Math.round(t * 100) / 100;
    } else {
      next.release = Math.round((DISPLAY_SECS - t) * 100) / 100;
      if (next.release < 0) next.release = 0;
    }
    onChange(next, playStyle);
    scheduleAutoPreview();
  }

  function dragEnd() {
    dragging = null;
  }

  /* ---- controls ---- */

  function handleCurveChange(
    key: 'attackCurve' | 'releaseCurve',
    value: string,
  ) {
    const next = { ...envelope, [key]: value as EnvCurve };
    onChange(next, playStyle);
    scheduleAutoPreview();
  }

  function handleStyleChange(value: string) {
    onChange(envelope, value as PlayStyle);
    scheduleAutoPreview();
  }

  function scheduleAutoPreview() {
    if (!autoPreview) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => onPreview(), 120);
  }
</script>

<svelte:window
  onmouseup={dragEnd}
  ontouchend={dragEnd}
  onmousemove={(e) => dragMove(e)}
  ontouchmove={(e) => dragMove(e)}
/>

<div class="env-panel" bind:clientWidth={svgWidth}>
  <!-- SVG envelope display -->
  <svg
    class="env-svg"
    viewBox="0 0 {svgWidth} 72"
    preserveAspectRatio="none"
    width="100%"
    height="72"
  >
    <!-- grid lines -->
    <line
      x1="0"
      y1="24"
      x2={svgWidth}
      y2="24"
      stroke="var(--lit-border)"
      stroke-width="0.5"
    />
    <line
      x1="0"
      y1="48"
      x2={svgWidth}
      y2="48"
      stroke="var(--lit-border)"
      stroke-width="0.5"
    />

    <!-- envelope fill -->
    <path d={envPath()} fill="var(--lit-accent)" fill-opacity="0.15" />
    <!-- envelope stroke -->
    <path
      d={envPath()}
      fill="none"
      stroke="var(--lit-accent)"
      stroke-width="1.5"
    />

    <!-- attack handle -->
    {#if envelope.attack > 0}
      <circle
        cx={attackPx()}
        cy="0"
        r="5"
        fill="var(--lit-bg)"
        stroke="var(--lit-text)"
        stroke-width="1.5"
        class="env-handle"
        onmousedown={(e) => dragStart('attack', e)}
        ontouchstart={(e) => dragStart('attack', e)}
      />
    {/if}

    <!-- release handle -->
    {#if envelope.release > 0}
      <circle
        cx={releaseStartPx()}
        cy="0"
        r="5"
        fill="var(--lit-bg)"
        stroke="var(--lit-text)"
        stroke-width="1.5"
        class="env-handle"
        onmousedown={(e) => dragStart('release', e)}
        ontouchstart={(e) => dragStart('release', e)}
      />
    {/if}
  </svg>

  <!-- controls row -->
  <div class="env-ctls">
    <label class="env-ctl">
      <span>A</span>
      <select
        class="brutalist-control"
        value={envelope.attackCurve}
        onchange={(e) =>
          handleCurveChange(
            'attackCurve',
            (e.target as HTMLSelectElement).value,
          )}
      >
        <option value="linear">lin</option>
        <option value="exp">exp</option>
      </select>
      <span class="env-ctl-val">{envelope.attack.toFixed(2)}s</span>
    </label>

    <label class="env-ctl">
      <span>R</span>
      <select
        class="brutalist-control"
        value={envelope.releaseCurve}
        onchange={(e) =>
          handleCurveChange(
            'releaseCurve',
            (e.target as HTMLSelectElement).value,
          )}
      >
        <option value="linear">lin</option>
        <option value="exp">exp</option>
      </select>
      <span class="env-ctl-val">{envelope.release.toFixed(2)}s</span>
    </label>

    <label class="env-ctl">
      <span>STYLE</span>
      <select
        class="brutalist-control"
        value={playStyle}
        onchange={(e) =>
          handleStyleChange((e.target as HTMLSelectElement).value)}
      >
        <option value="one-shot">one-shot</option>
        <option value="cut">cut</option>
        <option value="gate">gate</option>
        <option value="legato">legato</option>
      </select>
    </label>

    <label class="env-auto">
      <input
        type="checkbox"
        checked={autoPreview}
        onchange={(e) => (autoPreview = (e.target as HTMLInputElement).checked)}
      />
      <span>auto</span>
    </label>

    <button class="brutalist-control env-preview-btn" onclick={onPreview}
      >▶</button
    >
  </div>
</div>

<style>
  .env-panel {
    border-top: 1px solid var(--lit-border);
    padding: 8px 0 4px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    overflow: hidden;
  }

  .env-svg {
    display: block;
    border-radius: 2px;
  }

  .env-handle {
    cursor: grab;
  }

  .env-handle:active {
    cursor: grabbing;
  }

  .env-ctls {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    font-size: 0.6rem;
  }

  .env-ctl {
    display: flex;
    align-items: center;
    gap: 3px;
    color: var(--lit-text-dim);
  }

  .env-ctl span {
    flex-shrink: 0;
  }

  .env-ctl select {
    padding: 1px 2px;
    font-size: 0.6rem;
    background: var(--lit-cell);
    color: var(--lit-text);
  }

  .env-ctl-val {
    width: 2.5rem;
    text-align: right;
    color: var(--lit-text-faint);
    flex-shrink: 0;
  }

  .env-auto {
    display: flex;
    align-items: center;
    gap: 3px;
    color: var(--lit-text-faint);
    cursor: pointer;
    margin-left: auto;
  }

  .env-auto input[type='checkbox'] {
    width: 10px;
    height: 10px;
    accent-color: var(--lit-accent);
    cursor: pointer;
    margin: 0;
  }

  .env-preview-btn {
    padding: 2px 8px;
    font-size: 0.6rem;
    color: var(--lit-green);
    border-color: var(--lit-green);
    background: transparent;
  }

  .env-preview-btn:hover {
    background: var(--lit-green);
    color: var(--lit-bg);
    border-color: var(--lit-green);
  }

  @media (pointer: coarse) {
    .env-handle {
      r: 8;
    }

    .env-auto input[type='checkbox'] {
      width: 16px;
      height: 16px;
    }

    .env-ctl select {
      min-height: 28px;
    }
  }
</style>
