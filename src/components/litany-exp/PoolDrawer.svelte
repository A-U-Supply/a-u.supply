<script lang="ts">
  import type { PoolStatus } from '../../lib/litany-exp/pool.ts';

  interface Props {
    entries: { name: string; source: string; pinned: boolean }[];
    activeEntryIndex: number;
    poolEntryCount: number;
    poolPinnedCount: number;
    poolStatus: PoolStatus;
    onPreviewEntry: (index: number) => void;
    onTogglePin: (index: number) => void;
    onRemoveEntry: (index: number) => void;
    onMoveEntry: (fromIndex: number, toIndex: number) => void;
    onOpenSearch: () => void;
    onFetchMore: () => void;
  }

  let {
    entries,
    activeEntryIndex,
    poolEntryCount,
    poolPinnedCount,
    poolStatus,
    onPreviewEntry,
    onTogglePin,
    onRemoveEntry,
    onMoveEntry,
    onOpenSearch,
    onFetchMore,
  }: Props = $props();

  const MAX_POOL_SIZE = 16;

  let dragFrom = $state<number | null>(null);
  let dragOver = $state<number | null>(null);

  function handleDragStart(e: DragEvent, idx: number) {
    dragFrom = idx;
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(idx));
    }
  }

  function handleDragOver(e: DragEvent, idx: number) {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    dragOver = idx;
  }

  function handleDragLeave() {
    dragOver = null;
  }

  function handleDrop(e: DragEvent, idx: number) {
    e.preventDefault();
    dragOver = null;
    if (dragFrom != null && dragFrom !== idx) {
      onMoveEntry(dragFrom, idx);
    }
    dragFrom = null;
  }

  function handleDragEnd() {
    dragFrom = null;
    dragOver = null;
  }

  function shortName(name: string): string {
    const dot = name.lastIndexOf('.');
    const base = dot > -1 ? name.slice(0, dot) : name;
    if (base.length > 18) return base.slice(0, 15) + '...';
    return base;
  }
</script>

<div class="pool-drawer">
  {#if poolStatus === 'loading'}
    <div class="pool-status">loading samples...</div>
  {:else if entries.length === 0}
    <div class="pool-status pool-status--empty">no samples in pool</div>
  {:else}
    <div class="pool-grid">
      {#each entries as entry, i (i)}
        {#if entry.name !== '(empty)'}
          <div
            class="pool-chip"
            class:pool-chip--active={i === activeEntryIndex}
            class:pool-chip--dragging={dragFrom === i}
            class:pool-chip--drover={dragOver === i}
            class:pool-chip--pinned={entry.pinned}
            draggable="true"
            ondragstart={(e) => handleDragStart(e, i)}
            ondragover={(e) => handleDragOver(e, i)}
            ondragleave={handleDragLeave}
            ondrop={(e) => handleDrop(e, i)}
            ondragend={handleDragEnd}
          >
            <span class="chip-drag" title="Drag to reorder">&#9776;</span>
            <span class="chip-name" title={entry.name}
              >{shortName(entry.name)}</span
            >
            <button
              class="chip-btn"
              title="Preview this sample"
              onclick={(e) => {
                e.stopPropagation();
                onPreviewEntry(i);
              }}>&#9654;</button
            >
            <button
              class="chip-btn chip-pin"
              class:chip-pin--on={entry.pinned}
              title={entry.pinned
                ? 'Unlock sample'
                : 'Lock sample (preserved on re-roll)'}
              onclick={(e) => {
                e.stopPropagation();
                onTogglePin(i);
              }}>&#128274;</button
            >
            <button
              class="chip-btn chip-remove"
              title="Remove from pool"
              onclick={(e) => {
                e.stopPropagation();
                onRemoveEntry(i);
              }}>&times;</button
            >
          </div>
        {/if}
      {/each}
    </div>
  {/if}

  <div class="pool-footer">
    <span class="pool-stats"
      >{entries.filter((e) => e.name !== '(empty)').length}/{MAX_POOL_SIZE}
      {#if poolPinnedCount > 0}
        &middot; {poolPinnedCount} pinned
      {/if}
    </span>
    <div class="pool-actions">
      <button
        class="brutalist-control meta-btn"
        disabled={poolEntryCount >= MAX_POOL_SIZE}
        title={poolEntryCount >= MAX_POOL_SIZE
          ? 'Pool full'
          : 'Search samples-bored'}
        onclick={onOpenSearch}
      >
        + Search
      </button>
      <button
        class="brutalist-control meta-btn"
        disabled={poolEntryCount >= MAX_POOL_SIZE}
        title={poolEntryCount >= MAX_POOL_SIZE
          ? 'Pool full'
          : 'Add 4 random samples'}
        onclick={onFetchMore}
      >
        &#8633; +4
      </button>
    </div>
  </div>
</div>

<style>
  .pool-drawer {
    border: 1px solid var(--lit-border);
    background: var(--lit-cell);
    padding: 6px;
  }

  .pool-status {
    font-size: 0.6rem;
    color: var(--lit-text-dim);
    padding: 4px;
    text-align: center;
  }

  .pool-status--empty {
    color: var(--lit-text-faint);
    font-style: italic;
  }

  .pool-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
    gap: 3px;
  }

  .pool-chip {
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 2px 4px;
    background: var(--lit-panel);
    border: 1px solid var(--lit-border);
    font-size: 0.6rem;
    color: var(--lit-text-dim);
    cursor: default;
    transition:
      border-color 0.15s,
      background 0.15s;
  }

  .pool-chip--active {
    border-color: var(--lit-accent);
    background: #1a1800;
  }

  .pool-chip--pinned {
    border-color: var(--lit-accent);
    background: #1a1600;
  }

  .pool-chip--dragging {
    opacity: 0.4;
  }

  .pool-chip--drover {
    border-color: var(--lit-accent);
    border-style: dashed;
  }

  .chip-drag {
    cursor: grab;
    color: var(--lit-text-faint);
    font-size: 0.55rem;
    flex-shrink: 0;
  }

  .chip-name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .chip-btn {
    background: none;
    border: none;
    color: var(--lit-text-faint);
    cursor: pointer;
    font-size: 0.55rem;
    padding: 0 1px;
    flex-shrink: 0;
    line-height: 1;
  }

  .chip-btn:hover {
    color: var(--lit-text);
  }

  .chip-pin {
    color: var(--lit-text-faint);
  }

  .chip-pin--on {
    color: var(--lit-accent);
    text-shadow: 0 0 4px rgba(230, 168, 23, 0.4);
  }

  .chip-remove:hover {
    color: var(--lit-red);
  }

  .pool-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-top: 6px;
    padding-top: 4px;
    border-top: 1px solid var(--lit-border);
  }

  .pool-stats {
    font-size: 0.6rem;
    color: var(--lit-text-faint);
  }

  .pool-actions {
    display: flex;
    gap: 3px;
  }

  .pool-actions .meta-btn {
    font-size: 0.6rem;
    padding: 1px 5px;
  }

  .pool-actions .meta-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  @media (pointer: coarse) {
    .chip-btn {
      min-width: 28px;
      min-height: 28px;
      font-size: 0.7rem;
      padding: 2px 4px;
    }

    .chip-drag {
      min-width: 28px;
      min-height: 28px;
      font-size: 0.7rem;
    }
  }
</style>
