<script lang="ts">
  import type { StepOverride } from '../../lib/litany/state.ts';

  interface Props {
    steps: boolean[];
    stepCount: number;
    globalTick: number;
    onToggle: (index: number) => void;
    probMode?: boolean;
    overrides?: (StepOverride | null)[];
    onProbCycle?: (index: number) => void;
    selectMode?: boolean;
    selectedStep?: number | null;
    onSelect?: (index: number) => void;
    autoMode?: boolean;
    autoParam?: 'pitch' | 'volume';
  }

  let {
    steps,
    stepCount,
    globalTick,
    onToggle,
    probMode = false,
    overrides = [],
    onProbCycle,
    selectMode = false,
    selectedStep = null,
    onSelect,
    autoMode = false,
    autoParam = 'pitch',
  }: Props = $props();

  let activeStep = $derived(globalTick >= 0 ? globalTick % stepCount : -1);

  const PROB_VALUES = [null, 100, 75, 50, 25, 0];

  function handleClick(i: number) {
    if (selectMode && onSelect) {
      onSelect(i);
    } else if (probMode && onProbCycle) {
      onProbCycle(i);
    } else {
      onToggle(i);
    }
  }
</script>

<div class="step-grid" style="--cols: {stepCount}">
  {#each steps as active, i}
    {@const ov = overrides[i] ?? null}
    {@const prob = ov?.probability}
    {@const autoVal =
      autoMode && autoParam === 'pitch'
        ? (ov?.pitch ?? 0)
        : autoMode && autoParam === 'volume'
          ? (ov?.volume ?? 0.8)
          : null}
    <button
      class="step brutalist-control"
      class:step--on={active}
      class:step--playing={i === activeStep}
      class:step--prob={probMode}
      class:step--selected={selectMode && i === selectedStep}
      class:step--has-override={ov != null && Object.keys(ov).length > 0}
      class:step--auto={autoMode}
      style={[
        prob != null && prob < 100 ? `--prob-fill: ${prob}%` : '',
        autoMode && autoParam === 'pitch' && autoVal != null
          ? `--auto-hue: ${((autoVal as number) + 12) * 10}`
          : '',
        autoMode && autoParam === 'volume' && autoVal != null
          ? `--auto-opacity: ${(autoVal as number).toFixed(2)}`
          : '',
      ]
        .filter(Boolean)
        .join('; ')}
      aria-pressed={active}
      aria-label={probMode
        ? `Step ${i + 1}: ${active ? 'on' : 'off'}, probability ${prob ?? 100}%`
        : `Step ${i + 1}: ${active ? 'on' : 'off'}`}
      onclick={() => handleClick(i)}
    ></button>
  {/each}
</div>

<style>
  .step-grid {
    display: grid;
    grid-template-columns: repeat(var(--cols), 1fr);
    gap: 2px;
  }

  .step {
    aspect-ratio: 1;
    min-width: 0;
    padding: 0;
    background: var(--lit-step-off-bg);
    border: 1px solid var(--lit-step-off-border);
    box-shadow: none;
    cursor: pointer;
    transition:
      background 0.08s,
      border-color 0.08s;
    position: relative;
    overflow: hidden;
  }

  .step:hover {
    border-color: var(--lit-step-off-border-hover);
    box-shadow: none;
  }

  .step--on {
    background: var(--lit-step-on-bg);
    border-color: var(--lit-step-on-bg);
  }

  .step--on:hover {
    background: var(--lit-step-on-hover);
  }

  .step--playing {
    outline: 2px solid var(--lit-step-playing-outline);
    outline-offset: 1px;
  }

  .step--on.step--playing {
    background: var(--lit-step-on-playing);
  }

  /* Probability mode */
  .step--prob::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: var(--prob-fill, 100%);
    background: var(--lit-accent);
    opacity: 0.3;
    pointer-events: none;
  }

  .step--on.step--prob::after {
    background: var(--lit-bg);
    opacity: 0.4;
  }

  .step--prob[style='']::after {
    display: none;
  }

  .step--selected {
    outline: 2px solid var(--lit-text);
    outline-offset: 1px;
    z-index: 1;
  }

  .step--has-override::before {
    content: '';
    position: absolute;
    top: 1px;
    right: 1px;
    width: 4px;
    height: 4px;
    background: var(--lit-blue);
    pointer-events: none;
  }

  .step--auto.step--on {
    background: hsl(var(--auto-hue, 40), 80%, 50%);
    border-color: hsl(var(--auto-hue, 40), 80%, 50%);
  }

  .step--auto:not(.step--on) {
    background: hsl(var(--auto-hue, 40), 30%, 15%);
    border-color: hsl(var(--auto-hue, 40), 30%, 30%);
  }
</style>
