<script lang="ts">
  interface Props {
    steps: boolean[];
    stepCount: number;
    globalTick: number;
    onToggle: (index: number) => void;
  }

  let { steps, stepCount, globalTick, onToggle }: Props = $props();

  let activeStep = $derived(globalTick >= 0 ? globalTick % stepCount : -1);
</script>

<div class="step-grid" style="--cols: {stepCount}">
  {#each steps as active, i}
    <button
      class="step brutalist-control"
      class:step--on={active}
      class:step--playing={i === activeStep}
      aria-pressed={active}
      aria-label="Step {i + 1}: {active ? 'on' : 'off'}"
      onclick={() => onToggle(i)}
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
</style>
