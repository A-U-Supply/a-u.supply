<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  interface Props {
    onClose: () => void;
    onAdd: (hits: { id: string; filename: string }[]) => void;
    poolEntryCount: number;
  }

  let { onClose, onAdd, poolEntryCount }: Props = $props();

  const MAX_POOL_SIZE = 16;
  const maxAddable = $derived(MAX_POOL_SIZE - poolEntryCount);

  interface SearchHit {
    id: string;
    filename: string;
    voice?: string;
    instrument?: string;
    sample_rate?: number;
    channels?: number;
    duration_seconds?: number;
  }

  let query = $state('');
  let hits = $state<SearchHit[]>([]);
  let total = $state(0);
  let loading = $state(false);
  let error = $state('');
  let selectedIds = $state<Set<string>>(new Set());
  let previewAudio: AudioBufferSourceNode | null = null;
  let audioCtx: AudioContext | null = null;
  let searching = $state(false);
  let searchTimeout: ReturnType<typeof setTimeout> | null = null;
  let adding = $state(false);

  function getAudioCtx(): AudioContext {
    if (!audioCtx) audioCtx = new AudioContext();
    return audioCtx;
  }

  function stopPreview() {
    try {
      previewAudio?.stop();
    } catch {
      /* already stopped */
    }
    previewAudio?.disconnect();
    previewAudio = null;
  }

  async function previewSample(id: string) {
    stopPreview();

    const ctx = getAudioCtx();
    if (ctx.state === 'suspended') await ctx.resume();

    const headers: Record<string, string> = {};
    const key = import.meta.env.VITE_AU_API_KEY as string | undefined;
    if (key) headers['Authorization'] = `Bearer ${key}`;

    let response: Response;
    try {
      response = await fetch(`/api/media/${encodeURIComponent(id)}/file`, {
        credentials: 'include',
        headers,
      });
    } catch {
      return;
    }

    if (!response.ok) return;

    const arrayBuffer = await response.arrayBuffer();
    let audioBuffer: AudioBuffer;
    try {
      audioBuffer = await ctx.decodeAudioData(arrayBuffer);
    } catch {
      return;
    }

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);
    source.start();
    previewAudio = source;
  }

  function toggleSelected(id: string) {
    const next = new Set(selectedIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      if (next.size < maxAddable) next.add(id);
    }
    selectedIds = next;
  }

  async function doSearch() {
    if (!query.trim()) {
      hits = [];
      total = 0;
      return;
    }

    loading = true;
    error = '';

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      const key = import.meta.env.VITE_AU_API_KEY as string | undefined;
      if (key) headers['Authorization'] = `Bearer ${key}`;

      const body = {
        query: query.trim(),
        filters: { output_index: ['samples-bored'] },
        per_page: 50,
        page: 1,
      };

      const res = await fetch('/api/search', {
        method: 'POST',
        credentials: 'include',
        headers,
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        error = `Search failed (${res.status})`;
        hits = [];
        total = 0;
        return;
      }

      const data = await res.json();
      hits = (data.hits || []) as SearchHit[];
      total = data.total || 0;
    } catch (err: any) {
      error = err?.message ?? 'Search failed';
      hits = [];
      total = 0;
    } finally {
      loading = false;
    }
  }

  function onQueryInput() {
    if (searchTimeout) clearTimeout(searchTimeout);
    searching = true;
    searchTimeout = setTimeout(() => {
      searching = false;
      doSearch();
    }, 300);
  }

  function addSingle(hit: SearchHit) {
    if (maxAddable <= 0) return;
    onAdd([hit]);
  }

  function addSelected() {
    if (selectedIds.size === 0 || adding) return;
    adding = true;
    const selected = hits
      .filter((h) => selectedIds.has(h.id))
      .map((h) => ({ id: h.id, filename: h.filename }));
    onAdd(selected);
    selectedIds = new Set();
    adding = false;
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      stopPreview();
      onClose();
    }
  }

  onDestroy(() => {
    stopPreview();
    if (audioCtx) {
      audioCtx.close().catch(() => {});
      audioCtx = null;
    }
    if (searchTimeout) clearTimeout(searchTimeout);
  });

  function shortName(filename: string): string {
    const dot = filename.lastIndexOf('.');
    const base = dot > -1 ? filename.slice(0, dot) : filename;
    if (base.length > 24) return base.slice(0, 21) + '...';
    return base;
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="modal-overlay"
  onclick={(e) => {
    if (e.target === e.currentTarget) {
      stopPreview();
      onClose();
    }
  }}
>
  <div
    class="modal-box"
    role="dialog"
    aria-modal="true"
    aria-label="Search samples-bored"
  >
    <div class="modal-head">
      <h2>Search samples-bored</h2>
      <button
        class="brutalist-control icon-btn"
        onclick={() => {
          stopPreview();
          onClose();
        }}>&times;</button
      >
    </div>

    <div class="modal-search">
      <input
        class="brutalist-control search-input"
        type="text"
        placeholder="Search samples (e.g. kick, snare, 808, vocal)..."
        value={query}
        oninput={(e) => {
          query = (e.target as HTMLInputElement).value;
          onQueryInput();
        }}
        onkeydown={(e) => {
          if (e.key === 'Enter') {
            if (searchTimeout) clearTimeout(searchTimeout);
            searching = false;
            doSearch();
          }
        }}
      />
      {#if loading || searching}
        <span class="search-status">searching...</span>
      {/if}
    </div>

    <div class="modal-body">
      {#if error}
        <div class="modal-error">{error}</div>
      {:else if loading}
        <div class="modal-loading">searching...</div>
      {:else if query && hits.length === 0}
        <div class="modal-empty">No samples found for "{query}"</div>
      {:else}
        <div class="results-info">
          {total} result{total !== 1 ? 's' : ''}
          {#if total > 50}
            (showing first 50){/if}
          {#if maxAddable <= 0}
            <span class="pool-full"> &mdash; pool full</span>
          {/if}
        </div>
        <div class="results-grid">
          {#each hits as hit (hit.id)}
            {@const selected = selectedIds.has(hit.id)}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <!-- svelte-ignore a11y_no_static_element_interactions -->
            <div
              class="result-card"
              class:result-card--selected={selected}
              onclick={() => previewSample(hit.id)}
            >
              <div class="result-name" title={hit.filename}>
                {shortName(hit.filename)}
              </div>
              <div class="result-meta">
                {#if hit.voice}
                  <span class="meta-badge meta-badge--voice">{hit.voice}</span>
                {/if}
                {#if hit.instrument}
                  <span class="meta-badge meta-badge--inst"
                    >{hit.instrument}</span
                  >
                {/if}
                {#if hit.sample_rate}
                  <span class="meta-badge">{hit.sample_rate}Hz</span>
                {/if}
              </div>
              <div class="result-actions">
                <button
                  class="brutalist-control meta-btn"
                  disabled={maxAddable <= 0}
                  onclick={(e) => {
                    e.stopPropagation();
                    addSingle(hit);
                  }}
                  title="Add to pool">+</button
                >
                <label class="checkbox-label">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={!selected && maxAddable <= 0}
                    onchange={() => toggleSelected(hit.id)}
                  />
                </label>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <div class="modal-foot">
      <span class="selected-count">
        {selectedIds.size} selected
      </span>
      <div class="modal-foot-actions">
        <button
          class="brutalist-control meta-btn"
          disabled={selectedIds.size === 0}
          onclick={addSelected}
        >
          Add selected
        </button>
        <button
          class="brutalist-control meta-btn"
          onclick={() => {
            stopPreview();
            onClose();
          }}
        >
          Done
        </button>
      </div>
    </div>
  </div>
</div>

<style>
  .modal-overlay {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }

  .modal-box {
    background: var(--lit-panel);
    border: 1px solid var(--lit-border);
    max-width: 800px;
    width: 100%;
    max-height: 85vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  }

  .modal-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    border-bottom: 1px solid var(--lit-border);
  }

  .modal-head h2 {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--lit-text);
    margin: 0;
  }

  .modal-search {
    padding: 8px 12px;
    border-bottom: 1px solid var(--lit-border);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .search-input {
    flex: 1;
    font-size: 0.75rem;
    padding: 5px 8px;
    background: var(--lit-cell);
    color: var(--lit-text);
  }

  .search-status {
    font-size: 0.6rem;
    color: var(--lit-text-faint);
    white-space: nowrap;
  }

  .modal-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 8px 12px;
  }

  .modal-error,
  .modal-loading,
  .modal-empty {
    font-size: 0.7rem;
    color: var(--lit-text-dim);
    padding: 20px;
    text-align: center;
  }

  .modal-error {
    color: var(--lit-red-dim);
  }

  .results-info {
    font-size: 0.6rem;
    color: var(--lit-text-faint);
    margin-bottom: 8px;
  }

  .pool-full {
    color: var(--lit-red-dim);
  }

  .results-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 4px;
  }

  .result-card {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 6px 8px;
    background: var(--lit-cell);
    border: 1px solid var(--lit-border);
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
  }

  .result-card:hover {
    border-color: var(--lit-border-hover);
  }

  .result-card--selected {
    border-color: var(--lit-accent);
  }

  .result-name {
    font-size: 0.65rem;
    color: var(--lit-text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .result-meta {
    display: flex;
    gap: 3px;
    flex-wrap: wrap;
  }

  .meta-badge {
    font-size: 0.55rem;
    padding: 0 3px;
    background: var(--lit-panel);
    color: var(--lit-text-faint);
    border: 1px solid var(--lit-border);
  }

  .meta-badge--voice {
    color: var(--lit-accent);
    border-color: #3a3500;
  }

  .meta-badge--inst {
    color: var(--lit-blue);
    border-color: #1a2a40;
  }

  .result-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 4px;
    margin-top: 2px;
  }

  .result-actions .meta-btn {
    font-size: 0.6rem;
    padding: 1px 6px;
  }

  .result-actions .meta-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .checkbox-label {
    display: flex;
    align-items: center;
  }

  .checkbox-label input[type='checkbox'] {
    accent-color: var(--lit-accent);
    cursor: pointer;
    width: 14px;
    height: 14px;
  }

  .modal-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-top: 1px solid var(--lit-border);
  }

  .selected-count {
    font-size: 0.65rem;
    color: var(--lit-text-dim);
  }

  .modal-foot-actions {
    display: flex;
    gap: 6px;
  }

  .modal-foot-actions .meta-btn {
    font-size: 0.7rem;
    padding: 3px 10px;
  }

  .modal-foot-actions .meta-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
