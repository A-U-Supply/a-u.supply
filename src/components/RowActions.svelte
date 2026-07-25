<!--
  A row's secondary actions.

  Desktop keeps the buttons inline, exactly as before. On a phone they collapse
  behind one labeled `More ▾` and open as a list — where they finally get to be
  words ("Delete permanently") instead of glyphs (🗑), with the file's own
  metadata across the top. That metadata is otherwise invisible on mobile:
  .file-row__type and .file-row__size are display:none under 640px.

  Expands inline (the ColorPicker accordion technique) rather than floating, so
  it inherits the card's layout at every width.

  Primary actions — play, and the comments badge — never come in here. They
  stay in the row.
-->
<script lang="ts">
  import { isPhone } from '../lib/viewport.svelte.ts';

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
</script>

{#if isPhone()}
  <div class="row-actions">
    <button
      class="action-btn row-actions__toggle"
      type="button"
      aria-expanded={open}
      onclick={() => (open = !open)}>More {open ? '▴' : '▾'}</button
    >
    {#if open}
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
{:else}
  {#each actions as a (a.label)}
    {#if a.href}
      <a
        class="action-btn"
        class:action-btn--danger={a.danger}
        href={a.href}
        download={a.download}
        title={a.title}>{a.label}</a
      >
    {:else}
      <button
        class="action-btn"
        class:action-btn--danger={a.danger}
        type="button"
        title={a.title}
        onclick={() => a.onClick?.()}>{a.label}</button
      >
    {/if}
  {/each}
{/if}

<style>
  .row-actions {
    display: contents;
  }
  .row-actions__toggle {
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
  .row-actions__meta {
    font-size: 0.65rem;
    color: var(--color-muted);
    padding: 6px 8px;
    border-bottom: 1px dotted var(--color-border);
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
