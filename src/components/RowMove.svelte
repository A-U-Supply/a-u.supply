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

  `alwaysArrows` opts out of the phone-only rule, for a list whose whole job is
  reordering (the Arrange dialog): there, clicking ↑ beats dragging even with a
  mouse. Off by default, so every existing caller is unaffected.
-->
<script lang="ts">
  import { isPhone } from '../lib/viewport.svelte.ts';

  let {
    label = 'item',
    handleClass = 'drag-handle',
    upDisabled = false,
    downDisabled = false,
    alwaysArrows = false,
    onUp,
    onDown,
  }: {
    label?: string;
    handleClass?: string;
    upDisabled?: boolean;
    downDisabled?: boolean;
    alwaysArrows?: boolean;
    onUp?: () => void;
    onDown?: () => void;
  } = $props();

  const showArrows = $derived(alwaysArrows || isPhone());
  // The phone layout stacks a big grip over paired arrows — 78px of control
  // per row, which is right for a thumb and far too heavy for a mouse. When
  // arrows are on at a desktop width, lay the three out in one short line
  // instead: same controls, a third of the height.
  const inline = $derived(showArrows && !isPhone());
</script>

<span
  class="row-move"
  class:row-move--phone={showArrows && !inline}
  class:row-move--inline={inline}
>
  <button
    class="row-move__grip drag-handle {handleClass}"
    type="button"
    aria-label={`Drag to reorder ${label}`}
    title="Drag to reorder">⠿</button
  >
  {#if showArrows}
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
  /* Grip and both arrows on one line — for a list whose whole job is
     reordering, at a width where a mouse is doing the work. */
  .row-move--inline {
    grid-template-columns: repeat(3, auto);
    align-items: center;
    gap: 3px;
    flex: 0 0 auto;
  }
  .row-move--inline .row-move__grip {
    grid-column: auto;
    min-height: 30px;
    width: 26px;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .row-move--inline .row-move__arrow {
    min-height: 30px;
    width: 26px;
    font-size: 0.95rem;
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
