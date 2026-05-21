<!--
  PullFromIndex — modal that searches across every index admins can read
  and multi-selects media items to attach to a Latent (and optionally a
  slot). Filters are owned by SearchFilterBar, collapsed by default.

  Props:
    - open: bind:open from the parent
    - projectId: target Latent
    - slotId: optional slot inside that Latent (null = loose)
    - onAttached: callback fired after a successful attach (parent reloads its list)
-->
<script lang="ts">
  import SearchFilterBar, { type Filters } from './SearchFilterBar.svelte';

  type Props = {
    open: boolean;
    projectId: string;
    slotId?: string | null;
    onAttached?: (attachedIds: string[]) => void;
    onClose?: () => void;
  };

  let {
    open = $bindable(),
    projectId,
    slotId = null,
    onAttached,
    onClose,
  }: Props = $props();

  type Hit = {
    id: string;
    filename?: string;
    media_type?: string;
    file_size_bytes?: number;
    tool?: string;
  };

  let q = $state('');
  let filters = $state<Filters>({
    types: ['image', 'audio', 'video'],
    outputIndexes: ['__inputs__'],
    channels: [],
    poster: '',
    jobApp: '',
    colorGroups: [],
    preservedMultiColors: [],
    dateFrom: '',
    dateTo: '',
    tagsText: '',
    reactionsMin: 0,
    tagsMin: 0,
    hasTranscript: '',
    hasText: '',
    sortBy: 'newest',
    includeEmulsion: false,
    voteScoreMin: null,
    upMin: null,
    downMax: null,
    myVotes: '',
  });
  let filtersOpen = $state(false);
  let results = $state<Hit[]>([]);
  let selected = $state<Set<string>>(new Set());
  let loading = $state(false);
  let attaching = $state(false);
  let error = $state<string | null>(null);
  let lastQuery = $state('');

  function toggleSel(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selected = next;
  }

  function buildBody() {
    const sortMap: Record<string, string> = {
      newest: 'created_at:desc',
      oldest: 'created_at:asc',
      random: 'random',
      most_reactions: 'total_reaction_count:desc',
      acclaim: 'vote_score:desc',
      largest: 'file_size_bytes:desc',
      longest: 'duration_seconds:desc',
    };
    // "Emulsion-only" mode: when the user picked exactly __emulsion__ and
    // nothing else, send media_types=[] so the backend skips the public
    // indexes entirely. Without this, default media_types (image/audio/video)
    // makes the backend return all public hits in addition to emulsion.
    const onlyEmulsion =
      filters.outputIndexes.length === 1 &&
      filters.outputIndexes[0] === '__emulsion__';
    const body: any = {
      query: q,
      per_page: 60,
      media_types: onlyEmulsion ? [] : filters.types,
      sort: sortMap[filters.sortBy] || null,
      include_emulsion: filters.includeEmulsion,
      filters: {},
    };
    if (filters.outputIndexes.length) {
      body.filters.output_index = filters.outputIndexes;
    }
    if (filters.jobApp) body.filters.job_app = filters.jobApp;
    if (filters.tagsText.trim()) {
      body.filters.tags = filters.tagsText
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
    }
    if (filters.hasTranscript)
      body.filters.has_transcript = filters.hasTranscript === 'yes';
    if (filters.hasText) body.filters.has_text = filters.hasText === 'yes';
    return body;
  }

  async function runSearch() {
    loading = true;
    error = null;
    lastQuery = q;
    try {
      const res = await fetch('/api/search', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildBody()),
      });
      if (!res.ok) throw new Error(`Search failed (${res.status})`);
      const data = await res.json();
      const hits = data.results || data.hits || [];
      results = hits.slice(0, 60);
    } catch (e: any) {
      error = e?.message || 'Search failed';
    } finally {
      loading = false;
    }
  }

  async function attach() {
    if (selected.size === 0 || attaching) return;
    attaching = true;
    error = null;
    try {
      const payload: any = { media_item_ids: Array.from(selected) };
      if (slotId) payload.slot_id = slotId;
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/items`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || `Attach failed (${res.status})`);
      }
      const ids = Array.from(selected);
      selected = new Set();
      open = false;
      onAttached?.(ids);
      onClose?.();
    } catch (e: any) {
      error = e?.message || 'Attach failed';
    } finally {
      attaching = false;
    }
  }

  function close() {
    open = false;
    onClose?.();
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') close();
  }

  function thumbUrl(id: string): string {
    return `/api/media/${encodeURIComponent(id)}/thumbnail?size=sm`;
  }

  // Re-search when the modal opens (first time) or when filters change
  // (after open). Skip when closed so we don't fire stale requests.
  let lastFilterSig = $state('');
  $effect(() => {
    if (!open) return;
    const sig = JSON.stringify(filters);
    if (sig !== lastFilterSig) {
      lastFilterSig = sig;
      runSearch();
    }
  });
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <div class="overlay" onclick={close} role="presentation">
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-label="Pull from index"
      onclick={(e) => e.stopPropagation()}
    >
      <header class="modal__head">
        <h2>Pull from index</h2>
        <button
          class="action-btn"
          type="button"
          onclick={close}
          aria-label="Close">×</button
        >
      </header>

      <form
        class="search"
        onsubmit={(e) => {
          e.preventDefault();
          runSearch();
        }}
      >
        <button
          class="action-btn filter-toggle"
          type="button"
          aria-expanded={filtersOpen}
          aria-controls="pfi-filters"
          onclick={() => (filtersOpen = !filtersOpen)}
        >
          Filters {filtersOpen ? '▴' : '▾'}
        </button>
        <input
          type="text"
          placeholder="Search across all indices…"
          bind:value={q}
        />
        <button class="action-btn" type="submit">Search</button>
      </form>

      <!-- Single scroll area: filter panel (when open) + error + results
           all live inside .modal__body, which is the only thing that
           scrolls. Header, search row, and footer stay pinned. -->
      <div class="modal__body">
        <div
          id="pfi-filters"
          class="filter-panel"
          class:filter-panel--open={filtersOpen}
        >
          <SearchFilterBar bind:filters />
        </div>

        {#if error}
          <div class="notice notice--error">{error}</div>
        {/if}

        <div class="results">
          {#if loading}
            <div class="muted">Searching…</div>
          {:else if results.length === 0}
            <div class="muted">
              {lastQuery
                ? 'No results.'
                : 'Type a query or apply a filter, then Search.'}
            </div>
          {:else}
            <ul class="grid">
              {#each results as h (h.id)}
                <li class="tile" class:selected={selected.has(h.id)}>
                  <button
                    class="tile__btn"
                    type="button"
                    onclick={() => toggleSel(h.id)}
                    aria-pressed={selected.has(h.id)}
                  >
                    <div class="tile__thumb">
                      {#if h.media_type === 'image'}
                        <img src={thumbUrl(h.id)} alt={h.filename || ''} />
                      {:else}
                        <span class="icon">{h.media_type || 'file'}</span>
                      {/if}
                    </div>
                    <div class="tile__name" title={h.filename || ''}>
                      {h.filename || h.id}
                    </div>
                  </button>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      </div>

      <footer class="modal__foot">
        <span class="muted">{selected.size} selected</span>
        <div class="foot-actions">
          <button class="action-btn" type="button" onclick={close}
            >Cancel</button
          >
          <button
            class="btn-primary"
            type="button"
            disabled={selected.size === 0 || attaching}
            onclick={attach}
            >{attaching
              ? 'Attaching…'
              : `Attach ${selected.size || ''}`.trim()}</button
          >
        </div>
      </footer>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: var(--space-sm);
  }
  .modal {
    background: var(--color-bg);
    border: 2px solid var(--color-text);
    box-shadow: 4px 4px 0 var(--color-text);
    width: min(900px, 100%);
    max-height: 90vh;
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
    padding: var(--space-md);
  }
  .modal__head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .modal__head h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .search {
    display: flex;
    gap: var(--space-sm);
  }
  .search input {
    flex: 1;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 6px 10px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .filter-toggle {
    flex: 0 0 auto;
  }
  /* Single scroll area for filter + results. Header, search row, and
     footer stay pinned; everything inside .modal__body scrolls together. */
  .modal__body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .filter-panel {
    display: none;
    border: 1px solid var(--color-border);
    padding: var(--space-sm);
    background: rgba(0, 0, 0, 0.02);
  }
  .filter-panel--open {
    display: block;
  }
  .results {
    /* No internal scroll — .modal__body owns scrolling. */
    flex-shrink: 0;
  }
  .grid {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 6px;
  }
  .tile {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .tile.selected {
    border-color: var(--color-accent);
    background: rgba(184, 134, 11, 0.06);
  }
  .tile__btn {
    width: 100%;
    background: transparent;
    border: 0;
    padding: 4px;
    cursor: pointer;
    text-align: left;
    color: inherit;
    font: inherit;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .tile__thumb {
    aspect-ratio: 1;
    background: #f4f4f4;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .tile__thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .icon {
    color: var(--color-muted);
    font-size: var(--text-sm);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .tile__name {
    font-size: 0.7rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .modal__foot {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: var(--space-sm);
    border-top: 1px solid var(--color-border);
    padding-top: var(--space-sm);
  }
  .foot-actions {
    display: flex;
    gap: var(--space-sm);
  }
  .muted {
    color: var(--color-muted);
    font-size: var(--text-sm);
  }
  .notice {
    padding: 6px 10px;
    border: 1px solid var(--color-border);
    font-size: var(--text-sm);
  }
  .notice--error {
    border-color: #c00;
    color: #c00;
  }
  @media (max-width: 640px) {
    .modal {
      padding: var(--space-sm);
    }
    .modal__head h2 {
      font-size: var(--text-base);
    }
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
    }
  }
</style>
