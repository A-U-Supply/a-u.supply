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
      onclick={() => onToggle(i)}
    ></button>
  {/each}
</div>

<style>
  .step-grid {
    display: grid;
    grid-template-columns: repeat(var(--cols), 1fr);
    gap: 3px;
  }

  .step {
    aspect-ratio: 1;
    min-width: 0;
    padding: 0;
    background: transparent;
    border: 1px solid #444;
    box-shadow: none;
    cursor: pointer;
    transition: background 0.05s;
  }

  .step:hover {
    border-color: #888;
    box-shadow: none;
  }

  .step--on {
    background: #b8860b;
    border-color: #b8860b;
  }

  .step--on:hover {
    background: #d4a017;
  }

  .step--playing {
    outline: 1px solid #888;
    outline-offset: 1px;
  }

  .step--on.step--playing {
    background: #e0a020;
  }
</style>
