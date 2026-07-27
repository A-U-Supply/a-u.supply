<!--
  A row's secondary actions, behind one labeled `More ▾` at every width.

  The actions are always words ("Delete permanently"), never glyphs (🗑) — but
  they only take up room once you ask for them. Rendering all of them inline
  was what collapsed the desktop filename column to zero width: the row grid is
  `… minmax(…, 1fr) auto auto auto`, and five word-buttons in that last `auto`
  ate the row. Loose tiles had it worse — six of them wrapping inside a 160px
  tile.

  Two presentations of the same menu:
    - phone: expands inline, full row width (the ColorPicker accordion
      technique), so it inherits the card's layout and can't overflow.
    - desktop: floats, anchored under the toggle, portaled to <body>.

  The desktop panel MUST be portaled. Latents sections set isolation:isolate,
  so a menu left inside one is painted over by every section below it — see
  src/lib/portal.ts. Anchoring is done in script from the toggle's rect because
  a portaled node has no layout relationship to its button any more.

  Primary actions — play, and the comments badge — never come in here. They
  stay in the row.
-->
<script lang="ts">
  import { isPhone } from '../lib/viewport.svelte.ts';
  import { portal } from '../lib/portal.ts';

  export type RowAction = {
    label: string;
    /** Rendered as a link when set (Download needs a real <a download>). */
    href?: string;
    download?: string;
    danger?: boolean;
    title?: string;
    onClick?: () => void;
  };

  let {
    label = 'this file',
    meta = '',
    actions = [],
  }: { label?: string; meta?: string; actions?: RowAction[] } = $props();

  let open = $state(false);
  let toggleEl = $state<HTMLButtonElement | undefined>(undefined);
  let panelEl = $state<HTMLDivElement | undefined>(undefined);
  let top = $state(0);
  let left = $state(0);
  let maxHeight = $state(0);

  const PANEL_W = 200; // keep in sync with .row-actions__panel--float width
  const GAP = 2;

  /**
   * Park the floating panel under the toggle, right edges aligned, flipping
   * above when the row is near the bottom of the viewport. Coordinates are
   * viewport-relative because the panel is position:fixed — so anything
   * placed past the bottom edge is UNREACHABLE, not merely awkward.
   *
   * The earlier version of this fell back to `r.bottom + GAP` when neither
   * side had room, which overflowed exactly like the Style panel bug, and it
   * never capped the height, so a menu taller than the viewport always
   * overflowed. Both fixed below.
   *
   * NOTE: this duplicates src/lib/anchoredPanel.ts, which landed separately as
   * the single implementation of this. Collapse this onto the helper once both
   * are on master — it is only hand-rolled here because that helper isn't on
   * this branch.
   */
  function place() {
    if (!toggleEl) return;
    const r = toggleEl.getBoundingClientRect();
    // scrollHeight, not offsetHeight: offsetHeight is already limited by the
    // maxHeight we set last pass, so reading it would shrink the menu a bit
    // more on every re-place.
    const h = panelEl?.scrollHeight ?? 0;
    const MARGIN = 4;
    const roomBelow = window.innerHeight - r.bottom - GAP - MARGIN;
    const roomAbove = r.top - GAP - MARGIN;
    const fits = (room: number) => (h > 0 ? h <= room : room >= 140);
    const down =
      fits(roomBelow) || (!fits(roomAbove) && roomBelow >= roomAbove);

    if (down) {
      top = r.bottom + GAP;
      maxHeight = Math.max(0, roomBelow);
    } else {
      maxHeight = Math.max(0, roomAbove);
      top = Math.max(MARGIN, r.top - GAP - Math.min(h || maxHeight, maxHeight));
    }
    left = Math.max(
      MARGIN,
      Math.min(r.right - PANEL_W, window.innerWidth - PANEL_W - MARGIN),
    );
  }

  function toggle() {
    open = !open;
    if (open && !isPhone()) {
      place();
      // Measure again once the panel exists — the flip decision needs its
      // real height, which is unknown on the first pass.
      requestAnimationFrame(place);
    }
  }

  // While a floating menu is open, anything that moves the row under it
  // invalidates the anchor. Cheapest correct answer: close it.
  $effect(() => {
    if (!open || isPhone()) return;
    const onScroll = () => (open = false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') open = false;
    };
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
      document.removeEventListener('keydown', onKey);
    };
  });
</script>

<div class="row-actions">
  <button
    class="action-btn row-actions__toggle"
    class:row-actions__toggle--phone={isPhone()}
    type="button"
    bind:this={toggleEl}
    aria-expanded={open}
    aria-label={`Actions for ${label}`}
    onclick={toggle}>More {open ? '▴' : '▾'}</button
  >

  {#if open && isPhone()}
    <div
      class="row-actions__panel"
      role="group"
      aria-label={`Actions for ${label}`}
    >
      {#if meta}
        <div class="row-actions__meta">{meta}</div>
      {/if}
      {#each actions as a (a.label)}
        {#if a.href}
          <a
            class="row-actions__item"
            class:row-actions__item--danger={a.danger}
            href={a.href}
            download={a.download}
            title={a.title}
            onclick={() => (open = false)}>{a.label}</a
          >
        {:else}
          <button
            class="row-actions__item"
            class:row-actions__item--danger={a.danger}
            type="button"
            title={a.title}
            onclick={() => {
              open = false;
              a.onClick?.();
            }}>{a.label}</button
          >
        {/if}
      {/each}
    </div>
  {/if}
</div>

{#if open && !isPhone()}
  <!-- Portaled: see the header comment. Backdrop catches the outside click. -->
  <div
    use:portal
    class="row-actions__scrim"
    onclick={() => (open = false)}
    role="presentation"
  ></div>
  <div
    use:portal
    bind:this={panelEl}
    class="row-actions__panel row-actions__panel--float"
    style="top: {top}px; left: {left}px; max-height: {maxHeight}px;"
    role="group"
    aria-label={`Actions for ${label}`}
  >
    <div class="row-actions__meta row-actions__meta--name" title={label}>
      {label}
    </div>
    {#each actions as a (a.label)}
      {#if a.href}
        <a
          class="row-actions__item"
          class:row-actions__item--danger={a.danger}
          href={a.href}
          download={a.download}
          title={a.title}
          onclick={() => (open = false)}>{a.label}</a
        >
      {:else}
        <button
          class="row-actions__item"
          class:row-actions__item--danger={a.danger}
          type="button"
          title={a.title}
          onclick={() => {
            open = false;
            a.onClick?.();
          }}>{a.label}</button
        >
      {/if}
    {/each}
  </div>
{/if}

<style>
  .row-actions {
    display: contents;
  }
  /* Only the phone target needs the 44px floor; desktop rows are 83px of
     content and a 44px button would set the row height on its own. */
  .row-actions__toggle--phone {
    min-height: 44px;
  }
  .row-actions__panel {
    /* Full row of the parent flex/grid, like the ColorPicker accordion. */
    flex: 1 0 100%;
    display: flex;
    flex-direction: column;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    margin-top: 4px;
  }
  .row-actions__panel--float {
    position: fixed;
    overflow-y: auto; /* the max-height set in script has to be able to bite */
    z-index: var(--z-menu, 10100);
    flex: none;
    width: 200px; /* keep in sync with PANEL_W */
    margin-top: 0;
    border: 2px solid var(--color-text);
    box-shadow: 4px 4px 0 var(--color-text);
  }
  .row-actions__scrim {
    position: fixed;
    inset: 0;
    z-index: var(--z-menu, 10100);
  }
  .row-actions__meta {
    font-size: 0.65rem;
    color: var(--color-muted);
    padding: 6px 8px;
    border-bottom: 1px dotted var(--color-border);
  }
  .row-actions__meta--name {
    overflow-wrap: anywhere;
    color: var(--color-text);
  }
  .row-actions__item {
    font-family: inherit;
    font-size: 0.75rem;
    text-align: left;
    background: none;
    border: 0;
    border-bottom: 1px dotted var(--color-border);
    color: var(--color-fg);
    padding: 0 8px;
    min-height: 44px;
    display: flex;
    align-items: center;
    text-decoration: none;
    cursor: pointer;
  }
  /* A floating menu is a pointer target, not a thumb target. */
  .row-actions__panel--float .row-actions__item {
    min-height: 30px;
  }
  .row-actions__item:last-child {
    border-bottom: 0;
  }
  .row-actions__item:hover {
    background: var(--color-surface-2);
  }
  .row-actions__item--danger {
    color: var(--color-status-fail);
  }
  .row-actions__item:focus-visible,
  .row-actions__toggle:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: -2px;
  }
</style>
