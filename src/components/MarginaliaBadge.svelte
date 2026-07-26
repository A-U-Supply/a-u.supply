<!--
  MarginaliaBadge — compact "💬 n" count chip for slot rows / loose tiles.

  Renders nothing when the item has no annotations, unless `showEmpty` is set
  — on a phone the badge is the only visible door to a file's comments, and
  hiding it at zero means a file can never get its first one from there.
  Clicking opens a
  popover (bottom sheet on mobile) listing the item's annotations +
  inherited session cues via MarginaliaList in compact read-only mode —
  each row's [mm:ss] button queues the item in the player at that
  position. Composition happens in the player panel, not here.

  Props:
    - mediaId / mediaType / filename: the item (track built for seeks)
    - counts: { comments, cues, unresolved } from the batch counts endpoint
-->
<script lang="ts">
  import MarginaliaList from './MarginaliaList.svelte';
  import { portal } from '../lib/portal.ts';

  type Props = {
    mediaId: string;
    mediaType?: string;
    filename?: string;
    counts?: { comments: number; cues: number; unresolved: number } | null;
    /** Show a "💬 +" affordance when the item has no annotations yet. */
    showEmpty?: boolean;
  };

  let {
    mediaId,
    mediaType = '',
    filename = '',
    counts = null,
    showEmpty = false,
  }: Props = $props();

  let open = $state(false);
  let panelEl = $state<HTMLDivElement | undefined>(undefined);

  let total = $derived((counts?.comments ?? 0) + (counts?.cues ?? 0));

  function toggle() {
    open = !open;
    if (open) {
      // The list fetches lazily on mount — MarginaliaList only renders
      // while `open`, so nothing loads until the user asks for it.
      setTimeout(() => panelEl?.focus(), 0);
    }
  }

  function onPanelKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      open = false;
    }
  }
</script>

{#if !(counts && total > 0) && showEmpty}
  <button
    class="mgl-badge mgl-badge--empty"
    type="button"
    onclick={toggle}
    aria-expanded={open}
    aria-label={`Comments and markers for ${filename || 'this file'} — none yet`}
    title="Comments and markers — none yet"
  >
    💬 +
  </button>
{:else if counts && total > 0}
  <button
    class="mgl-badge"
    type="button"
    onclick={toggle}
    aria-expanded={open}
    aria-label="{total} comments and markers{counts.unresolved > 0
      ? `, ${counts.unresolved} unresolved`
      : ''}"
    title="{counts.comments} comment{counts.comments === 1
      ? ''
      : 's'} · {counts.cues} marker{counts.cues === 1 ? '' : 's'}"
  >
    💬 {total}{#if counts.unresolved > 0}<span
        class="mgl-badge__dot"
        title="{counts.unresolved} unresolved"
        aria-hidden="true"
      ></span>{/if}
  </button>
{/if}

<!--
  One popover for BOTH badges. It used to live inside the counts > 0 branch,
  which made the `showEmpty` badge a dead button — it toggled `open` and
  nothing rendered, so a file with no comments still had no door to its first
  one (the very thing showEmpty was added for).

  Portaled to <body>: the badge sits inside a .latent-section, and those
  sections set isolation:isolate — a later section paints over the popover at
  any z-index, and it can't beat the player's root-level 9999 either. It still
  docks above the player bar rather than covering it. See src/lib/portal.ts.
-->
{#if open}
  <div
    use:portal
    class="mgl-pop-backdrop"
    onclick={() => (open = false)}
    role="presentation"
  ></div>
  <div
    use:portal
    class="mgl-pop"
    role="dialog"
    aria-label="Comments and markers for {filename || 'media item'}"
    tabindex="-1"
    bind:this={panelEl}
    onkeydown={onPanelKeydown}
  >
    <div class="mgl-pop__head">
      <span class="mgl-pop__title" title={filename}>💬 {filename}</span>
      <button
        class="mgl-pop__close"
        type="button"
        onclick={() => (open = false)}
        aria-label="Close">&times;</button
      >
    </div>
    <div class="mgl-pop__body">
      <MarginaliaList
        {mediaId}
        {mediaType}
        {filename}
        readOnly
        compact
        showComposer={false}
      />
    </div>
  </div>
{/if}

<style>
  .mgl-badge--empty {
    color: var(--color-muted);
  }
  .mgl-badge {
    position: relative;
    border: 1px solid var(--color-border);
    background: transparent;
    color: var(--color-muted);
    font: inherit;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    padding: 1px 6px;
    white-space: nowrap;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .mgl-badge:hover {
    color: var(--color-accent);
    border-color: var(--color-accent);
  }
  .mgl-badge__dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--color-status-warn);
    display: inline-block;
    flex-shrink: 0;
  }
  .mgl-pop-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay-soft);
    z-index: 10000;
  }
  .mgl-pop {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 10001;
    width: min(440px, calc(100vw - 32px));
    max-height: 60vh;
    display: flex;
    flex-direction: column;
    background: var(--color-bg);
    color: var(--color-text);
    border: 2px solid var(--color-text);
    box-shadow: 6px 6px 0 var(--color-text);
    font-family: var(--font-mono);
  }
  /* Portaled out of the section (see the markup), so the z-index above is
     honoured. It still docks above the player bar rather than covering it —
     the player stamps body.player-active. */
  :global(body.player-active) .mgl-pop {
    bottom: 88px;
  }
  .mgl-pop:focus {
    outline: none;
  }
  .mgl-pop__head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    border-bottom: 2px solid var(--color-text);
    background: var(--color-surface);
    flex-shrink: 0;
  }
  .mgl-pop__title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
  }
  .mgl-pop__close {
    background: none;
    border: none;
    color: var(--color-muted);
    font-size: 1.2rem;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
  }
  .mgl-pop__close:hover {
    color: var(--color-text);
  }
  .mgl-pop__body {
    overflow-y: auto;
    padding: 8px;
  }
  @media (max-width: 639px) {
    .mgl-badge {
      min-height: 44px;
      display: inline-flex;
      align-items: center;
    }
    /* Bottom sheet: full width, thumb-reach close, scrollable list. */
    .mgl-pop {
      left: 0;
      right: 0;
      bottom: 0;
      width: auto;
      max-height: 70vh;
      border-left: none;
      border-right: none;
      border-bottom: none;
      box-shadow: none;
    }
    :global(body.player-active) .mgl-pop {
      bottom: 96px;
    }
    .mgl-pop__close {
      min-width: 44px;
      min-height: 44px;
    }
  }
</style>
