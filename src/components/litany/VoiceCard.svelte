<script lang="ts">
  import type { Voice, StepCount, Rotation } from '../../lib/litany/state.ts';
  import type { PoolStatus } from '../../lib/litany/pool.ts';
  import StepGrid from './StepGrid.svelte';
  import FXPanel from './FXPanel.svelte';

  interface Props {
    voice: Voice;
    globalTick: number;
    poolStatus: PoolStatus;
    currentSampleName: string;
    onChange: (updated: Voice) => void;
    onRandomizeSteps: () => void;
    onRandomizeQuery: () => void;
    onRemove: () => void;
    onPin: () => void;
    onUnpin: () => void;
  }

  let {
    voice,
    globalTick,
    poolStatus,
    currentSampleName,
    onChange,
    onRandomizeSteps,
    onRandomizeQuery,
    onRemove,
    onPin,
    onUnpin,
  }: Props = $props();

  let fxOpen = $state(false);

  const STEP_COUNTS: StepCount[] = [8, 16, 32];
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

<div class="voice-card" class:voice-card--error={poolStatus === 'error'}>
  <div class="card-header">
    <input
      class="label-input"
      value={voice.label}
      oninput={(e) =>
        onChange({ ...voice, label: (e.target as HTMLInputElement).value })}
    />
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

  <div class="steps-wrap">
    <StepGrid
      steps={voice.steps}
      stepCount={voice.stepCount}
      {globalTick}
      onToggle={toggleStep}
    />
  </div>

  <div class="controls-row">
    <label class="vol-label">
      <span>VOL</span>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={voice.volume}
        oninput={(e) =>
          onChange({
            ...voice,
            volume: parseFloat((e.target as HTMLInputElement).value),
          })}
      />
    </label>
    <button
      class="brutalist-control icon-btn"
      onclick={onRandomizeSteps}
      title="Randomize steps">🎲 STEPS</button
    >
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
  </div>

  {#if fxOpen}
    <FXPanel fx={voice.fx} onChange={(fx) => onChange({ ...voice, fx })} />
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

  .voice-card--error {
    border-color: #4a2020;
    opacity: 0.7;
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

  .steps-wrap {
    min-width: 0;
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
</style>
