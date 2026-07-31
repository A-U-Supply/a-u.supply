<!--
  UploadDock — the persistent upload bar.

  Mounted once in layouts/Admin.astro inside `transition:persist="upload-dock"`,
  exactly like the player, so it and its in-flight XHRs survive a page swap.
  That is the entire feature: before this, `Uploader` lived inside the page,
  ViewTransitions destroyed it on navigation, and every transfer was aborted
  mid-flight without a word.

  It owns nothing itself — `src/lib/uploadQueue.ts` is the engine. This is a
  view over `snapshot()` plus the two things that have to live in a component:
  the measured height and the beforeunload guard.
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import {
    subscribe,
    snapshot,
    counts,
    overallProgress,
    hasActiveTransfers,
    enqueueFiles,
    enqueueBundleEntry,
    enqueueBundleFiles,
    dismissItem,
    dismissBundle,
    dismissFinished,
    retryItem,
    retryBundle,
    retryAllFailed,
    bundleProgress,
    bundleStatusText,
    formatSize,
    fmtSpeed,
    fileKind,
    type Destination,
  } from '../lib/uploadQueue';

  let items = $state(snapshot().items);
  let bundles = $state(snapshot().bundles);
  let expanded = $state(false);
  let dockEl: HTMLElement | null = $state(null);
  let dockObserver: ResizeObserver | null = null;

  let unsubscribe: (() => void) | null = null;
  let startHandler: ((e: Event) => void) | null = null;
  let beforeUnload: ((e: BeforeUnloadEvent) => void) | null = null;

  const visible = $derived(items.length > 0 || bundles.length > 0);
  const c = $derived.by(() => {
    // Touch the arrays so this recomputes when the engine emits.
    void items;
    void bundles;
    return counts();
  });
  const progress = $derived.by(() => {
    void items;
    void bundles;
    return overallProgress();
  });
  const active = $derived(c.active > 0);
  const failed = $derived(c.failed > 0);

  const speed = $derived(
    items.find((i) => i.status === 'uploading' && i.speedBps)?.speedBps ?? 0,
  );

  function refresh() {
    // Re-read from the engine. New array identities each time so Svelte's
    // fine-grained reactivity sees the change — the engine mutates entries in
    // place for progress, which is far cheaper than cloning on every tick.
    const s = snapshot();
    items = [...s.items];
    bundles = [...s.bundles];
  }

  /** Headline text. Deliberately says what it's doing, not just a number. */
  const headline = $derived.by(() => {
    if (active) {
      const n = c.active;
      return `Uploading ${n} file${n === 1 ? '' : 's'} · ${Math.round(progress * 100)}%`;
    }
    if (failed && c.done > 0) return `${c.done} uploaded · ${c.failed} failed`;
    if (failed) return `${c.failed} upload${c.failed === 1 ? '' : 's'} failed`;
    return `${c.done} file${c.done === 1 ? '' : 's'} uploaded`;
  });

  /**
   * Publish the dock's measured height, the same contract the player uses for
   * `--player-h`. Nothing needs it yet; it exists so the next thing that has to
   * clear the dock measures it instead of hardcoding a guess — which is exactly
   * the bug `--player-h` was introduced to end.
   */
  function measure() {
    const h = dockEl?.offsetHeight;
    if (!h) return;
    document.documentElement.style.setProperty('--upload-dock-h', `${h}px`);
  }

  $effect(() => {
    dockObserver?.disconnect();
    if (!dockEl) {
      document.documentElement.style.removeProperty('--upload-dock-h');
      return;
    }
    measure();
    dockObserver = new ResizeObserver(measure);
    dockObserver.observe(dockEl);
  });

  /**
   * Warn before a reload or tab close takes a transfer with it.
   *
   * Registered ONLY while something is in flight and removed the moment the
   * queue settles — a listener left attached prompts on every later navigation
   * for nothing, which trains people to click through it.
   */
  $effect(() => {
    const shouldGuard = active && hasActiveTransfers();
    if (shouldGuard && !beforeUnload) {
      beforeUnload = (e: BeforeUnloadEvent) => {
        e.preventDefault();
        // Wording is the browser's; returnValue is what actually arms it.
        e.returnValue = '';
        return '';
      };
      window.addEventListener('beforeunload', beforeUnload);
    } else if (!shouldGuard && beforeUnload) {
      window.removeEventListener('beforeunload', beforeUnload);
      beforeUnload = null;
    }
  });

  onMount(() => {
    unsubscribe = subscribe(refresh);
    refresh();

    // The handoff. Pages dispatch this instead of uploading themselves.
    startHandler = (e: Event) => {
      const d = (e as CustomEvent).detail || {};
      const dest: Destination = {
        destination: d.destination === 'project' ? 'project' : 'tribute',
        projectId: d.projectId || '',
        slotId: d.slotId || '',
        tags: d.tags || '',
        description: d.description || '',
      };
      if (d.bundleEntry) {
        void enqueueBundleEntry(d.bundleEntry, dest);
        return;
      }
      if (d.bundleFiles?.length) {
        const err = enqueueBundleFiles(d.bundleFiles, dest);
        if (err) alert(err);
        return;
      }
      if (d.files?.length) enqueueFiles(d.files, dest);
    };
    document.addEventListener('upload:start', startHandler);
  });

  onDestroy(() => {
    unsubscribe?.();
    if (startHandler)
      document.removeEventListener('upload:start', startHandler);
    if (beforeUnload) window.removeEventListener('beforeunload', beforeUnload);
    dockObserver?.disconnect();
    document.documentElement.style.removeProperty('--upload-dock-h');
  });
</script>

<!-- Keeps page content clear of the fixed bar, mirroring .player__spacer. -->
<div class="dock__spacer" class:dock__spacer--active={visible}></div>

{#if visible}
  <div
    class="dock"
    class:dock--failed={failed && !active}
    bind:this={dockEl}
    role="status"
    aria-live="polite"
  >
    <div class="dock__bar">
      <button
        class="dock__toggle"
        type="button"
        onclick={() => (expanded = !expanded)}
        aria-expanded={expanded}
        aria-label={expanded ? 'Hide upload details' : 'Show upload details'}
      >
        <span class="dock__chevron" class:open={expanded}>▸</span>
        <span class="dock__headline">{headline}</span>
      </button>

      {#if active}
        <div class="dock__track" aria-hidden="true">
          <div
            class="dock__fill"
            style="width: {(progress * 100).toFixed(1)}%"
          ></div>
        </div>
        {#if speed}<span class="dock__speed">{fmtSpeed(speed)}</span>{/if}
      {/if}

      {#if failed}
        <button
          class="action-btn dock__retry"
          type="button"
          onclick={retryAllFailed}
        >
          Retry
        </button>
      {/if}

      <!-- Dismiss only once nothing is in flight: the queue is the record of
           what happened, and it stays until it's read. -->
      {#if !active}
        <button
          class="dock__close"
          type="button"
          onclick={dismissFinished}
          aria-label="Dismiss uploads">×</button
        >
      {/if}
    </div>

    {#if expanded}
      <ul class="dock__list">
        {#each bundles as b (b.id)}
          <li class="row" data-status={b.status}>
            <span class="row__icon" title={b.tool || 'session bundle'}>▣</span>
            <span class="row__name" title={b.name}>{b.name}</span>
            <span class="row__status">{bundleStatusText(b)}</span>
            {#if b.status === 'uploading' || b.status === 'completing'}
              <span class="row__track" aria-hidden="true">
                <span
                  class="row__fill"
                  style="width: {(bundleProgress(b) * 100).toFixed(1)}%"
                ></span>
              </span>
            {/if}
            {#if b.status === 'error' && b.parts.length > 0}
              <button
                class="action-btn row__btn"
                type="button"
                onclick={() => retryBundle(b.id)}>Retry</button
              >
            {/if}
            <button
              class="row__x"
              type="button"
              onclick={() => dismissBundle(b.id)}
              aria-label="Remove bundle">×</button
            >
          </li>
        {/each}

        {#each items as it (it.id)}
          <li class="row" data-status={it.status}>
            {#if it.preview}
              <img class="row__thumb" src={it.preview} alt="" />
            {:else}
              <span class="row__icon"
                >{it.isSession ? '▣' : fileKind(it.file)}</span
              >
            {/if}
            <span class="row__name" title={it.file.name}>{it.file.name}</span>
            <span class="row__status">
              {#if it.status === 'pending'}Queued{/if}
              {#if it.status === 'uploading'}{Math.round(it.progress * 100)}% · {formatSize(
                  it.file.size,
                )}{/if}
              {#if it.status === 'processing'}Processing…{/if}
              {#if it.status === 'done'}Done{/if}
              {#if it.status === 'error'}{it.message || 'Error'}{/if}
            </span>
            {#if it.status === 'uploading' || it.status === 'processing'}
              <span class="row__track" aria-hidden="true">
                <span
                  class="row__fill"
                  class:indeterminate={it.status === 'processing'}
                  style="width: {(it.progress * 100).toFixed(1)}%"
                ></span>
              </span>
            {/if}
            {#if it.status === 'error'}
              <button
                class="action-btn row__btn"
                type="button"
                onclick={() => retryItem(it.id)}>Retry</button
              >
            {/if}
            <button
              class="row__x"
              type="button"
              onclick={() => dismissItem(it.id)}
              aria-label={it.status === 'uploading' ||
              it.status === 'processing'
                ? 'Cancel upload'
                : 'Remove'}>×</button
            >
          </li>
        {/each}
      </ul>
    {/if}
  </div>
{/if}

<style>
  .dock__spacer {
    height: 0;
  }
  .dock__spacer--active {
    height: var(--upload-dock-h, 44px);
  }

  /* Sits ON TOP OF the player rather than beside it. `--player-h` is the bar's
     MEASURED height, published by Player.svelte and removed when the player
     goes away — so the fallback of 0 puts the dock on the floor when there's
     no music, with no coordination between the two components and no constant
     to drift. */
  .dock {
    position: fixed;
    bottom: var(--player-h, 0px);
    left: 0;
    right: 0;
    z-index: var(--z-player, 9999);
    background: var(--color-surface);
    border-top: 1px solid var(--color-border);
    color: var(--color-fg);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .dock--failed {
    border-top-color: var(--color-status-fail);
  }

  .dock__bar {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: 6px var(--space-sm);
  }

  .dock__toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    cursor: pointer;
    padding: 2px 0;
    /* Long filenames must not push the progress bar off the row. */
    min-width: 0;
    flex: 0 1 auto;
  }
  .dock__chevron {
    display: inline-block;
    transition: transform 0.12s;
  }
  .dock__chevron.open {
    transform: rotate(90deg);
  }
  .dock__headline {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .dock__track {
    flex: 1 1 auto;
    min-width: 60px;
    height: 6px;
    background: var(--color-surface-2);
    border: 1px solid var(--color-border);
  }
  .dock__fill {
    display: block;
    height: 100%;
    background: var(--color-accent);
    transition: width 0.15s linear;
  }
  .dock__speed {
    color: var(--color-muted);
    white-space: nowrap;
  }
  .dock__retry {
    flex: none;
  }
  .dock--failed .dock__headline {
    color: var(--color-status-fail);
  }

  .dock__close {
    flex: none;
    background: none;
    border: none;
    color: var(--color-muted);
    font-size: var(--text-md);
    line-height: 1;
    cursor: pointer;
    padding: 2px 6px;
  }
  .dock__close:hover {
    color: var(--color-fg);
  }

  .dock__list {
    list-style: none;
    margin: 0;
    padding: 0 var(--space-sm) var(--space-sm);
    /* dvh, not vh — a phone's retracting URL bar makes vh taller than the
       visible viewport, which would put the oldest rows under the chrome. */
    max-height: 40dvh;
    overflow-y: auto;
  }
  .row {
    display: flex;
    align-items: center;
    gap: var(--space-xs);
    padding: 3px 0;
    border-top: 1px solid var(--color-border);
  }
  .row__thumb {
    width: 20px;
    height: 20px;
    object-fit: cover;
    flex: none;
  }
  .row__icon {
    flex: none;
    width: 20px;
    text-align: center;
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .row__name {
    /* A flex item's default min-width is min-content, so a long filename
       overflows the row instead of ellipsing. */
    min-width: 0;
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row__status {
    flex: none;
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .row[data-status='error'] .row__status {
    color: var(--color-status-fail);
  }
  .row[data-status='done'] .row__status {
    color: var(--color-status-ok);
  }
  .row__track {
    flex: 0 0 80px;
    height: 4px;
    background: var(--color-surface-2);
  }
  .row__fill {
    display: block;
    height: 100%;
    background: var(--color-accent);
  }
  .row__fill.indeterminate {
    width: 100% !important;
    opacity: 0.5;
  }
  .row__btn,
  .row__x {
    flex: none;
  }
  .row__x {
    background: none;
    border: none;
    color: var(--color-muted);
    cursor: pointer;
    padding: 0 4px;
  }
  .row__x:hover {
    color: var(--color-fg);
  }

  /* One line on a phone: the player is already down there, and two stacked
     bars eat the page. The speed readout is the first thing to go. */
  @media (max-width: 640px) {
    .dock__speed {
      display: none;
    }
    .row__track {
      display: none;
    }
  }
</style>
