<script lang="ts">
  import type {
    Voice,
    StepCount,
    Rotation,
    PatternMode,
  } from '../../lib/litany/state.ts';
  import type { PoolStatus } from '../../lib/litany/pool.ts';
  import { INSTRUMENT_TYPES } from '../../lib/litany/randomize.ts';
  import StepGrid from './StepGrid.svelte';
  import FXPanel from './FXPanel.svelte';
  import EnvPanel from './EnvPanel.svelte';

  interface Props {
    voice: Voice;
    globalTick: number;
    layout: 'grid' | 'rows';
    poolStatus: PoolStatus;
    currentSampleName: string;
    poolEntryNames: string[];
    onChange: (updated: Voice, skipHistory?: boolean) => void;
    onBeforeDrag: () => void;
    onRandomizeSteps: () => void;
    onRandomizeQuery: () => void;
    onReRoll: () => void;
    onSelectSample: (name: string) => void;
    onRemove: () => void;
    onPin: () => void;
    onUnpin: () => void;
    onPreview: () => void;
  }

  let {
    voice,
    globalTick,
    layout,
    poolStatus,
    currentSampleName,
    poolEntryNames,
    onChange,
    onBeforeDrag,
    onRandomizeSteps,
    onRandomizeQuery,
    onReRoll,
    onSelectSample,
    onRemove,
    onPin,
    onUnpin,
    onPreview,
  }: Props = $props();

  let fxOpen = $state(false);
  let envOpen = $state(false);

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

  function setPatternMode(mode: PatternMode) {
    onChange({ ...voice, patternMode: mode });
  }

  function updateEuclidean(key: string, value: number) {
    const euclidean = { ...voice.euclidean, [key]: value };
    onChange({ ...voice, euclidean }, true);
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
            aria-label="Randomize instrument type"
            onclick={onRandomizeQuery}>🎲</button
          >
          <button
            class="brutalist-control icon-btn"
            title="Remove voice"
            aria-label="Remove voice"
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
        <select
          class="brutalist-control sample-select"
          value={currentSampleName}
          onchange={(e) =>
            onSelectSample((e.target as HTMLSelectElement).value)}
        >
          {#each poolEntryNames as name}
            <option value={name}>{name}</option>
          {/each}
        </select>
      {/if}
    </div>

    <div class="query-row">
      <input
        class="query-input brutalist-control"
        value={voice.query}
        placeholder="search…"
        list="voice-queries-{voice.id}"
        onchange={(e) =>
          onChange({ ...voice, query: (e.target as HTMLInputElement).value })}
      />
      <datalist id="voice-queries-{voice.id}">
        {#each INSTRUMENT_TYPES as t}
          <option value={t} />
        {/each}
      </datalist>
      <button
        class="brutalist-control icon-btn"
        title="Re-roll samples with same search"
        onclick={onReRoll}>↻</button
      >
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
    {#if voice.patternMode === 'euclidean'}
      <div class="euclid-ctls">
        <label class="euclid-field">
          <span>P</span>
          <input
            type="range"
            min="1"
            max={voice.euclidean.length}
            value={voice.euclidean.pulses}
            onpointerdown={onBeforeDrag}
            oninput={(e) =>
              updateEuclidean(
                'pulses',
                parseInt((e.target as HTMLInputElement).value) || 1,
              )}
          />
          <span class="euclid-val">{voice.euclidean.pulses}</span>
        </label>
        <label class="euclid-field">
          <span>L</span>
          <input
            type="range"
            min="1"
            max="32"
            value={voice.euclidean.length}
            onpointerdown={onBeforeDrag}
            oninput={(e) =>
              updateEuclidean(
                'length',
                parseInt((e.target as HTMLInputElement).value) || 16,
              )}
          />
          <span class="euclid-val">{voice.euclidean.length}</span>
        </label>
        <label class="euclid-field">
          <span>R</span>
          <input
            type="range"
            min="0"
            max={voice.euclidean.length - 1}
            value={voice.euclidean.offset}
            onpointerdown={onBeforeDrag}
            oninput={(e) =>
              updateEuclidean(
                'offset',
                parseInt((e.target as HTMLInputElement).value) || 0,
              )}
          />
          <span class="euclid-val">{voice.euclidean.offset}</span>
        </label>
      </div>
    {/if}
    <StepGrid
      steps={voice.steps}
      stepCount={voice.stepCount}
      {globalTick}
      onToggle={voice.patternMode === 'steps' ? toggleStep : () => {}}
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
      <label class="vol-label">
        <span>PIT</span>
        <input
          type="range"
          min="-12"
          max="12"
          step="1"
          value={voice.pitch}
          onpointerdown={onBeforeDrag}
          oninput={(e) =>
            onChange(
              {
                ...voice,
                pitch: parseInt((e.target as HTMLInputElement).value) || 0,
              },
              true,
            )}
        />
      </label>
      <button
        class="brutalist-control icon-btn mute-btn"
        class:mute-btn--active={voice.muted}
        aria-pressed={voice.muted}
        aria-label="Mute voice"
        title="Mute"
        onclick={() => onChange({ ...voice, muted: !voice.muted }, true)}
        >M</button
      >
      <button
        class="brutalist-control icon-btn solo-btn"
        class:solo-btn--active={voice.soloed}
        aria-pressed={voice.soloed}
        aria-label="Solo voice"
        title="Solo"
        onclick={() => onChange({ ...voice, soloed: !voice.soloed }, true)}
        >S</button
      >
      {#if layout === 'grid'}
        <button
          class="brutalist-control icon-btn"
          onclick={onRandomizeSteps}
          title="Randomize steps">🎲</button
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
        onchange={(e) => {
          const val = (e.target as HTMLSelectElement).value as Rotation;
          if (val === 'pinned') {
            onPin();
            onChange({ ...voice, rotation: 'pinned' });
          } else {
            if (voice.rotation === 'pinned') onUnpin();
            onChange({ ...voice, rotation: val });
          }
        }}
      >
        {#each ROTATIONS as r}
          <option value={r.value}>{r.label}</option>
        {/each}
      </select>
      <button
        class="brutalist-control meta-btn"
        aria-pressed={voice.patternMode === 'euclidean'}
        onclick={() =>
          setPatternMode(voice.patternMode === 'steps' ? 'euclidean' : 'steps')}
      >
        {voice.patternMode === 'steps' ? 'STEP' : 'EUCL'}
      </button>
      <button
        class="brutalist-control meta-btn"
        aria-pressed={fxOpen}
        onclick={() => (fxOpen = !fxOpen)}
      >
        FX {fxOpen ? '▴' : '▾'}
      </button>
      <button
        class="brutalist-control meta-btn"
        aria-pressed={envOpen}
        onclick={() => (envOpen = !envOpen)}
      >
        ENV {envOpen ? '▴' : '▾'}
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

  {#if envOpen}
    <div class="fx-wrap">
      <EnvPanel
        envelope={voice.envelope}
        playStyle={voice.playStyle}
        onChange={(env, style) =>
          onChange({ ...voice, envelope: env, playStyle: style }, true)}
        {onPreview}
      />
    </div>
  {/if}
</div>

<style>
  .voice-card {
    background: var(--lit-panel);
    border: 1px solid var(--lit-border);
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
    overflow: hidden;
  }

  .voice-card--row {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    padding: 6px 8px;
    gap: 6px;
  }

  .voice-card--error {
    border-color: var(--lit-error-border);
    opacity: 0.7;
  }

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
    font-family: var(--lit-font);
    font-size: 0.75rem;
    font-weight: 700;
    background: transparent;
    border: none;
    color: var(--lit-text);
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
    color: var(--lit-text-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-height: 1em;
  }

  .sample-select {
    width: 100%;
    font-size: 0.6rem;
    padding: 1px 3px;
    background: var(--lit-cell);
    color: var(--lit-text-dim);
    border: 1px solid var(--lit-border);
    cursor: pointer;
  }

  .status-loading {
    color: var(--lit-text-faint);
    font-style: italic;
  }

  .status-error {
    color: var(--lit-red-dim);
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
    background: var(--lit-cell);
    color: var(--lit-text);
  }

  .pin--active {
    background: var(--lit-pin-bg) !important;
  }

  .mute-btn {
    font-size: 0.65rem;
    padding: 2px 5px;
    min-width: 1.5rem;
  }

  .mute-btn--active {
    background: var(--lit-mute-bg) !important;
    border-color: var(--lit-red-dim) !important;
    color: var(--lit-mute-text) !important;
  }

  .solo-btn {
    font-size: 0.65rem;
    padding: 2px 5px;
    min-width: 1.5rem;
  }

  .solo-btn--active {
    background: var(--lit-solo-bg) !important;
    border-color: var(--lit-solo-border) !important;
    color: var(--lit-solo-text) !important;
  }

  .steps-wrap {
    min-width: 0;
  }

  .voice-card--row .steps-wrap {
    flex: 1;
    min-width: 200px;
  }

  .euclid-ctls {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-bottom: 4px;
  }

  .euclid-field {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 0.65rem;
    color: var(--lit-text-dim);
  }

  .euclid-field span:first-child {
    flex-shrink: 0;
    width: 0.8rem;
  }

  .euclid-field input[type='range'] {
    flex: 1;
    min-width: 0;
  }

  .euclid-val {
    width: 1.6rem;
    text-align: right;
    color: var(--lit-text-faint);
  }

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
    color: var(--lit-text-dim);
  }

  .voice-card--row .vol-label {
    min-width: 80px;
  }

  .vol-label input[type='range'] {
    flex: 1;
    min-width: 0;
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
    background: var(--lit-cell);
    color: var(--lit-text-dim);
  }

  .meta-btn {
    flex: 1;
    cursor: pointer;
  }

  .fx-wrap {
    width: 100%;
  }

  .voice-card--row .fx-wrap {
    flex-basis: 100%;
  }
</style>
