<!--
  Reorder control for a list row: the drag grip on top, the two arrows paired
  beneath it.

  Was a single 44px column with ↑ / ⠿ / ↓ stacked vertically — 112px tall on
  every row, which on a ten-take slot was more page than the filenames. Grip
  over paired arrows is 78px in a 76px column: a little wider, a third shorter.

  The arrows are phone-only (desktop reorders by drag); the grip is always
  there. `handleClass` is the selector the parent's Sortable is configured
  with — `touch-action: none` must stay on the handle alone, or the list
  stops scrolling.
-->
<script lang="ts">
  import { isPhone } from '../lib/viewport.svelte.ts';

  let {
    label = 'item',
    handleClass = 'drag-handle',
    upDisabled = false,
    downDisabled = false,
    onUp,
    onDown,
  }: {
    label?: string;
    handleClass?: string;
    upDisabled?: boolean;
    downDisabled?: boolean;
    onUp?: () => void;
    onDown?: () => void;
  } = $props();
</script>

<span class="row-move" class:row-move--phone={isPhone()}>
  <button
    class="row-move__grip drag-handle {handleClass}"
    type="button"
    aria-label={`Drag to reorder ${label}`}
    title="Drag to reorder">⠿</button
  >
  {#if isPhone()}
    <button
      class="row-move__arrow"
      type="button"
      title="Move up"
      aria-label={`Move ${label} up`}
      disabled={upDisabled}
      onclick={() => onUp?.()}>↑</button
    >
    <button
      class="row-move__arrow"
      type="button"
      title="Move down"
      aria-label={`Move ${label} down`}
      disabled={downDisabled}
      onclick={() => onDown?.()}>↓</button
    >
  {/if}
</span>

<style>
  .row-move {
    display: grid;
    grid-template-columns: 1fr;
    align-self: start;
    gap: 2px;
  }
  .row-move--phone {
    grid-template-columns: 1fr 1fr;
    width: 68px;
    flex: 0 0 68px;
  }
  .row-move__grip {
    grid-column: 1 / -1;
    font-family: inherit;
    background: none;
    border: 0;
    color: var(--color-muted);
    cursor: grab;
    padding: 0 2px;
    touch-action: none;
  }
  .row-move--phone .row-move__grip {
    min-height: 36px;
    width: 100%;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    font-size: 1.1rem;
  }
  .row-move__grip:active {
    cursor: grabbing;
  }
  .row-move__arrow {
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: inherit;
    font-size: 1.1rem;
    min-height: 32px;
    width: 100%;
    background: var(--color-bg);
    border: 1px solid var(--color-border);
    color: var(--color-fg);
    cursor: pointer;
  }
  .row-move__arrow:disabled {
    color: var(--color-border);
    cursor: default;
  }
  .row-move__arrow:focus-visible,
  .row-move__grip:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 1px;
  }
</style>
