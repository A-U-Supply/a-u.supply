<script lang="ts">
  import type { Voice, StepCount, Rotation } from '../../lib/litany/state.ts';
  import type { PoolStatus } from '../../lib/litany/pool.ts';
  import StepGrid from './StepGrid.svelte';
  import FXPanel from './FXPanel.svelte';

  interface Props {
    voice: Voice;
    globalTick: number;
    layout: 'grid' | 'rows';
    poolStatus: PoolStatus;
    currentSampleName: string;
    onChange: (updated: Voice, skipHistory?: boolean) => void;
    onBeforeDrag: () => void;
    onRandomizeSteps: () => void;
    onRandomizeQuery: () => void;
    onRemove: () => void;
    onPin: () => void;
    onUnpin: () => void;
  }

  let {
    voice,
    globalTick,
    layout,
    poolStatus,
    currentSampleName,
    onChange,
    onBeforeDrag,
    onRandomizeSteps,
    onRandomizeQuery,
    onRemove,
    onPin,
    onUnpin,
  }: Props = $props();

  let fxOpen = $state(false);

  const STEP_COUNTS: StepCount[] = Array.from({ length: 32 }, (_, i) => i + 1);
  const ROTATIONS: { value: Rotation; label: string }[] = [
    { value: 'every-hit', label: '/HIT' },
    { value: 'every-bar', label: '/BAR' },
    { value: 'every-4bars', label: '/4BR' },
    { value: 'pinned', label: 'PIN' },
  ];

  function updateStepCount(count: StepCount) {
    const steps = Array(count)
      .fill(false)
      .map((_, i) => voice.steps[i] ?? false);
    onChange({ ...voice, stepCount: count, steps });
  }

  function toggleStep(i: number) {
    const steps = [...voice.steps];
    steps[i] = !steps[i];
    onChange({ ...voice, steps });
  }

  function handlePin() {
    if (voice.rotation === 'pinned') {
      onUnpin();
      onChange({ ...voice, rotation: 'every-hit' });
    } else {
      onPin();
      onChange({ ...voice, rotation: 'pinned' });
    }
  }
</script>

<div
  class="voice-card"
  class:voice-card--error={poolStatus === 'error'}
  class:voice-card--row={layout === 'rows'}
>
  <!-- Left column: label + query (fixed width in row mode) -->
  <div class="left-col">
    <div class="card-header">
      <input
        class="label-input"
        value={voice.label}
        oninput={(e) =>
          onChange(
            { ...voice, label: (e.target as HTMLInputElement).value },
            true,
          )}
      />
      {#if layout === 'grid'}
        <div class="header-actions">
          <button
            class="brutalist-control icon-btn"
            title="Randomize query"
            onclick={onRandomizeQuery}>🎲</button
          >
          <button
            class="brutalist-control icon-btn"
            title="Remove voice"
            onclick={onRemove}>✕</button
          >
        </div>
      {/if}
    </div>

    <div class="sample-name">
      {#if poolStatus === 'loading'}
        <span class="status-loading">loading…</span>
      {:else if poolStatus === 'error'}
        <span class="status-error">no samples</span>
      {:else}
        <span class="name-text">{currentSampleName}</span>
      {/if}
    </div>

    <div class="query-row">
      <input
        class="query-input brutalist-control"
        value={voice.query}
        placeholder="search…"
        onchange={(e) =>
          onChange({ ...voice, query: (e.target as HTMLInputElement).value })}
      />
      <button
        class="brutalist-control icon-btn"
        class:pin--active={voice.rotation === 'pinned'}
        title={voice.rotation === 'pinned'
          ? 'Unpin sample'
          : 'Pin current sample'}
        onclick={handlePin}>📌</button
      >
    </div>
  </div>

  <!-- Centre: step grid (flex:1 in row mode) -->
  <div class="steps-wrap">
    <StepGrid
      steps={voice.steps}
      stepCount={voice.stepCount}
      {globalTick}
      onToggle={toggleStep}
    />
  </div>

  <!-- Right column: controls -->
  <div class="right-col">
    <div class="controls-row">
      <label class="vol-label">
        <span>VOL</span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={voice.volume}
          onpointerdown={onBeforeDrag}
          oninput={(e) =>
            onChange(
              {
                ...voice,
                volume: parseFloat((e.target as HTMLInputElement).value),
              },
              true,
            )}
        />
      </label>
      <button
        class="brutalist-control icon-btn mute-btn"
        class:mute-btn--active={voice.muted}
        title="Mute"
        onclick={() => onChange({ ...voice, muted: !voice.muted }, true)}
        >M</button
      >
      <button
        class="brutalist-control icon-btn solo-btn"
        class:solo-btn--active={voice.soloed}
        title="Solo"
        onclick={() => onChange({ ...voice, soloed: !voice.soloed }, true)}
        >S</button
      >
      {#if layout === 'grid'}
        <button
          class="brutalist-control icon-btn"
          onclick={onRandomizeSteps}
          title="Randomize steps">🎲 STEPS</button
        >
      {/if}
    </div>

    <div class="meta-row">
      <select
        class="brutalist-control meta-select"
        value={voice.stepCount}
        onchange={(e) =>
          updateStepCount(
            parseInt((e.target as HTMLSelectElement).value) as StepCount,
          )}
      >
        {#each STEP_COUNTS as n}
          <option value={n}>{n}</option>
        {/each}
      </select>
      <select
        class="brutalist-control meta-select"
        value={voice.rotation}
        onchange={(e) =>
          onChange({
            ...voice,
            rotation: (e.target as HTMLSelectElement).value as Rotation,
          })}
      >
        {#each ROTATIONS as r}
          <option value={r.value}>{r.label}</option>
        {/each}
      </select>
      <button
        class="brutalist-control meta-btn"
        aria-pressed={fxOpen}
        onclick={() => (fxOpen = !fxOpen)}
      >
        FX {fxOpen ? '▴' : '▾'}
      </button>
      {#if layout === 'rows'}
        <button
          class="brutalist-control icon-btn"
          title="Randomize steps"
          onclick={onRandomizeSteps}>🎲</button
        >
        <button
          class="brutalist-control icon-btn"
          title="Remove voice"
          onclick={onRemove}>✕</button
        >
      {/if}
    </div>
  </div>

  {#if fxOpen}
    <div class="fx-wrap">
      <FXPanel
        fx={voice.fx}
        {onBeforeDrag}
        onChange={(fx) => onChange({ ...voice, fx }, true)}
      />
    </div>
  {/if}
</div>

<style>
  .voice-card {
    background: #111;
    border: 2px solid #333;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }

  /* Row layout: horizontal arrangement */
  .voice-card--row {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    padding: 6px 8px;
    gap: 6px;
  }

  .voice-card--error {
    border-color: #4a2020;
    opacity: 0.7;
  }

  /* Left col: label + query — fixed width in row mode */
  .left-col {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .voice-card--row .left-col {
    width: 130px;
    flex-shrink: 0;
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
  }

  .label-input {
    font-family: var(--font-mono, monospace);
    font-size: 0.75rem;
    font-weight: bold;
    background: transparent;
    border: none;
    color: #ddd;
    outline: none;
    flex: 1;
    min-width: 0;
    letter-spacing: 0.05em;
  }

  .header-actions {
    display: flex;
    gap: 4px;
  }

  .icon-btn {
    padding: 2px 5px;
    font-size: 0.7rem;
    line-height: 1;
  }

  .sample-name {
    font-size: 0.65rem;
    color: #666;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-height: 1em;
  }

  .status-loading {
    color: #555;
    font-style: italic;
  }

  .status-error {
    color: #8a4040;
  }

  .name-text {
    color: #666;
  }

  .query-row {
    display: flex;
    gap: 4px;
  }

  .query-input {
    flex: 1;
    min-width: 0;
    font-size: 0.75rem;
    padding: 3px 5px;
    background: #0d0d0d;
    color: #ccc;
  }

  .pin--active {
    background: #2a2000 !important;
  }

  .mute-btn {
    font-size: 0.65rem;
    padding: 2px 5px;
    min-width: 1.5rem;
  }

  .mute-btn--active {
    background: #2a0000 !important;
    border-color: #8a4040 !important;
    color: #cc6666 !important;
  }

  .solo-btn {
    font-size: 0.65rem;
    padding: 2px 5px;
    min-width: 1.5rem;
  }

  .solo-btn--active {
    background: #1a1200 !important;
    border-color: #b8860b !important;
    color: #d4a017 !important;
  }

  /* Centre: step grid — flex:1 in row mode */
  .steps-wrap {
    min-width: 0;
  }

  .voice-card--row .steps-wrap {
    flex: 1;
    min-width: 200px;
  }

  /* Right col: controls */
  .right-col {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }

  .voice-card--row .right-col {
    flex-shrink: 0;
  }

  .controls-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .vol-label {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
    font-size: 0.65rem;
    color: #666;
  }

  .voice-card--row .vol-label {
    min-width: 80px;
  }

  .vol-label input[type='range'] {
    flex: 1;
    accent-color: #b8860b;
    cursor: pointer;
  }

  .meta-row {
    display: flex;
    gap: 4px;
    align-items: center;
  }

  .meta-select,
  .meta-btn {
    font-size: 0.65rem;
    padding: 2px 4px;
    background: #0d0d0d;
    color: #aaa;
  }

  .meta-btn {
    flex: 1;
    cursor: pointer;
  }

  /* FX panel spans full width in row mode */
  .fx-wrap {
    width: 100%;
  }

  .voice-card--row .fx-wrap {
    flex-basis: 100%;
  }
</style>
