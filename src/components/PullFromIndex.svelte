<!--
  PullFromIndex — modal that searches across all four indices (images, audio,
  video, emulsion) and multi-selects media items to attach to a Latent
  (and optionally a slot).

  Props:
    - open: bind:open from the parent
    - projectId: target Latent
    - slotId: optional slot inside that Latent (null = loose)
    - onAttached: callback fired after a successful attach (parent reloads its list)
-->
<script lang="ts">
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
  let mediaTypes = $state<string[]>(['image', 'audio', 'video', 'emulsion']);
  let results = $state<Hit[]>([]);
  let selected = $state<Set<string>>(new Set());
  let loading = $state(false);
  let attaching = $state(false);
  let error = $state<string | null>(null);
  let lastQuery = $state('');

  function toggleType(t: string) {
    if (mediaTypes.includes(t)) mediaTypes = mediaTypes.filter((x) => x !== t);
    else mediaTypes = [...mediaTypes, t];
    runSearch();
  }

  function toggleSel(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selected = next;
  }

  async function runSearch() {
    loading = true;
    error = null;
    lastQuery = q;
    try {
      // The real /api/search wants `media_types` as ['image', 'audio', 'video'];
      // Emulsion is opted into separately via include_emulsion.
      const realTypes = mediaTypes.filter((t) => t !== 'emulsion');
      const body: any = {
        query: q,
        per_page: 40,
        // Always send the explicit selection (including []) so backend
        // respects "emulsion only" instead of defaulting to all public indices.
        media_types: realTypes,
        include_emulsion: mediaTypes.includes('emulsion'),
      };
      const res = await fetch('/api/search', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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

  $effect(() => {
    if (open && results.length === 0) runSearch();
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
        <input
          type="text"
          placeholder="Search across all indices…"
          bind:value={q}
        />
        <button class="action-btn" type="submit">Search</button>
      </form>

      <div class="type-row">
        {#each ['image', 'audio', 'video', 'emulsion'] as t}
          <button
            class="type-pill"
            class:active={mediaTypes.includes(t)}
            onclick={() => toggleType(t)}
            type="button">{t}</button
          >
        {/each}
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
  .type-row {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  .type-pill {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 4px 10px;
    font: inherit;
    font-size: var(--text-sm);
    text-transform: uppercase;
    letter-spacing: 1pt;
    cursor: pointer;
  }
  .type-pill.active {
    background: var(--color-text);
    color: var(--color-bg);
    border-color: var(--color-text);
  }
  .results {
    overflow: auto;
    flex: 1;
    min-height: 200px;
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
