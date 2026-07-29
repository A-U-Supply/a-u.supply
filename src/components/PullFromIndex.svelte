<!--
  PullFromIndex — modal that searches across every index admins can read
  and multi-selects media items to attach to a Latent (and optionally a
  slot). Filters are owned by SearchFilterBar, collapsed by default.

  Props:
    - open: bind:open from the parent
    - projectId: target Latent
    - slotId: optional slot inside that Latent (null = loose)
    - onAttached: callback fired after a successful attach (parent reloads its list)
    - selectMode: single-select picker — no attach; the primary action
      hands the chosen media id to onSelect instead (image filter default)
    - onSelect: callback fired with the chosen media id in selectMode
-->
<script lang="ts">
  import SearchFilterBar, { type Filters } from './SearchFilterBar.svelte';
  import Uploader from './Uploader.svelte';
  import { filtersToSearchBody } from '../lib/filterTranslator.ts';
  import { portal } from '../lib/portal.ts';
  import { hasThumb, thumbAttrs } from '../lib/mediaThumb.ts';

  type Props = {
    open: boolean;
    projectId: string;
    slotId?: string | null;
    onAttached?: (attachedIds: string[]) => void;
    onClose?: () => void;
    selectMode?: boolean;
    onSelect?: (mediaItemId: string) => void;
  };

  let {
    open = $bindable(),
    projectId,
    slotId = null,
    onAttached,
    onClose,
    selectMode = false,
    onSelect,
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
    types: selectMode ? ['image'] : ['image', 'audio', 'video'],
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
    reactionsMax: null,
    tagsMin: 0,
    tagsMax: null,
    hasTranscript: '',
    hasText: '',
    sortBy: 'newest',
    includeEmulsion: false,
    voteScoreMin: null,
    voteScoreMax: null,
    upMin: null,
    upMax: null,
    downMin: null,
    downMax: null,
    myVotes: '',
    aiVibe: [],
    aiColorTemperature: [],
    aiColorCharacter: [],
    hasAiDescription: '',
    isScreenshot: '',
    isMeme: '',
    isPhoto: '',
    isArtwork: '',
    isAiGenerated: '',
    hasHuman: '',
    hasFace: '',
    hasTextOverlay: '',
    isNsfw: '',
  });
  let filtersOpen = $state(false);
  let results = $state<Hit[]>([]);
  let selected = $state<Set<string>>(new Set());
  let loading = $state(false);
  let attaching = $state(false);
  let error = $state<string | null>(null);
  let lastQuery = $state('');
  const PER_PAGE = 60;
  let page = $state(1);
  let total = $state(0);
  let loadingMore = $state(false);
  let bodyEl: HTMLDivElement | null = null;
  // "In this latent" quick-picks (selectMode only): images already attached
  // to the Latent — usually exactly what a card image should be.
  let attachedImages = $state<{ media_item_id: string; filename?: string }[]>(
    [],
  );
  let attachedLoadedFor = $state('');
  let uploadOpen = $state(false);

  function toggleSel(id: string) {
    if (selectMode) {
      // Radio-style single select: picking another tile switches to it.
      selected = selected.has(id) ? new Set<string>() : new Set([id]);
      return;
    }
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selected = next;
  }

  function useSelected() {
    const id = Array.from(selected)[0];
    if (!id) return;
    selected = new Set();
    open = false;
    onSelect?.(id);
    onClose?.();
  }

  function buildBody(pageNum: number) {
    const onlyEmulsion =
      filters.outputIndexes.length === 1 &&
      filters.outputIndexes[0] === '__emulsion__';
    const body = filtersToSearchBody(filters, q, { perPage: PER_PAGE });
    body.page = pageNum;
    if (onlyEmulsion) {
      body.media_types = [];
    }
    return body;
  }

  async function fetchPage(pageNum: number) {
    const res = await fetch('/api/search', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildBody(pageNum)),
    });
    if (!res.ok) throw new Error(`Search failed (${res.status})`);
    const data = await res.json();
    return {
      hits: (data.results || data.hits || []) as Hit[],
      total: Number(data.total ?? 0),
    };
  }

  async function runSearch() {
    loading = true;
    error = null;
    lastQuery = q;
    try {
      const pageData = await fetchPage(1);
      page = 1;
      results = pageData.hits;
      total = pageData.total || pageData.hits.length;
    } catch (e: any) {
      error = e?.message || 'Search failed';
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (loadingMore || loading) return;
    loadingMore = true;
    error = null;
    try {
      const pageData = await fetchPage(page + 1);
      page += 1;
      results = [...results, ...pageData.hits];
      if (pageData.total) total = pageData.total;
    } catch (e: any) {
      error = e?.message || 'Load more failed';
    } finally {
      loadingMore = false;
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

  // Lock the page behind the modal. Without this the wheel scrolls the page
  // whenever the pointer isn't over .modal__body — which on desktop is most
  // of the modal (the head, the search row, the footer, the gutters).
  $effect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  });

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') close();
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

  // Quick-picks: the Latent's attached images. Silent on failure — the
  // strip just doesn't render.
  async function loadAttached() {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/items`,
        { credentials: 'include' },
      );
      if (!res.ok) return;
      const body = await res.json();
      const seen = new Set<string>();
      const images: { media_item_id: string; filename?: string }[] = [];
      for (const it of body.items || []) {
        if (it.media?.media_type !== 'image') continue;
        if (seen.has(it.media_item_id)) continue;
        seen.add(it.media_item_id);
        images.push({
          media_item_id: it.media_item_id,
          filename: it.media?.filename,
        });
      }
      attachedImages = images;
    } catch {}
  }

  // Load once per open (selectMode only).
  $effect(() => {
    if (!open) {
      attachedLoadedFor = ''; // reload next open — attachments may change
      return;
    }
    if (!selectMode || attachedLoadedFor === projectId) return;
    attachedLoadedFor = projectId;
    loadAttached();
  });

  // Direct upload (selectMode): the Uploader attaches the file to the
  // Latent server-side; refresh the quick-picks and auto-select the upload
  // if it's an image (the items endpoint is the type authority — non-image
  // uploads still land in loose files, they just aren't selectable heroes).
  async function onUploadedFile(detail: { media_item_id: string }) {
    await loadAttached();
    if (attachedImages.some((a) => a.media_item_id === detail.media_item_id)) {
      selected = new Set([detail.media_item_id]);
    }
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <!--
    Portaled to <body>. Both callers (LatentSlots, LatentLooseFiles) render
    inside a .latent-section, and those sections set isolation:isolate — a
    later section paints over anything in an earlier one at any z-index. See
    src/lib/portal.ts.
  -->
  <div use:portal class="overlay" onclick={close} role="presentation">
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
          onclick={() => {
            filtersOpen = !filtersOpen;
            // The panel renders at the top of the scroll area — bring it
            // into view when opening from deep in the results.
            if (filtersOpen)
              requestAnimationFrame(() => bodyEl?.scrollTo({ top: 0 }));
          }}
        >
          Filters {filtersOpen ? '▴' : '▾'}
        </button>
        {#if selectMode}
          <button
            class="action-btn filter-toggle"
            type="button"
            aria-expanded={uploadOpen}
            aria-controls="pfi-upload"
            onclick={() => {
              uploadOpen = !uploadOpen;
              if (uploadOpen)
                requestAnimationFrame(() => bodyEl?.scrollTo({ top: 0 }));
            }}
          >
            Upload {uploadOpen ? '▴' : '▾'}
          </button>
        {/if}
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
      <div class="modal__body" bind:this={bodyEl}>
        <div
          id="pfi-filters"
          class="filter-panel"
          class:filter-panel--open={filtersOpen}
        >
          <SearchFilterBar bind:filters />
        </div>

        {#if selectMode}
          <div
            id="pfi-upload"
            class="filter-panel"
            class:filter-panel--open={uploadOpen}
          >
            <Uploader
              destination="project"
              {projectId}
              compact={true}
              onUploaded={onUploadedFile}
            />
            <p class="muted">
              Uploads land in this latent's loose files; images get selected
              here automatically.
            </p>
          </div>
        {/if}

        {#if selectMode && attachedImages.length > 0}
          <div class="attached">
            <span class="attached__label">In this latent</span>
            <div class="attached__strip">
              {#each attachedImages as a (a.media_item_id)}
                <button
                  class="attached__thumb"
                  class:selected={selected.has(a.media_item_id)}
                  type="button"
                  title={a.filename || ''}
                  aria-pressed={selected.has(a.media_item_id)}
                  onclick={() => toggleSel(a.media_item_id)}
                >
                  <!-- Strip is image-only by construction (hero must be an
                       image), so this never needs the video branch. -->
                  <img
                    {...thumbAttrs(a.media_item_id, 'image', '56px')}
                    alt={a.filename || ''}
                  />
                </button>
              {/each}
            </div>
          </div>
        {/if}

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
                      {#if hasThumb(h.media_type)}
                        <!-- Grid is minmax(140px, 1fr), 110px under 600px. -->
                        <img
                          {...thumbAttrs(
                            h.id,
                            h.media_type,
                            '(max-width: 600px) 110px, 140px',
                          )}
                          alt={h.filename || ''}
                        />
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
            {#if results.length < total}
              <button
                class="action-btn load-more"
                type="button"
                disabled={loadingMore}
                onclick={loadMore}
              >
                {loadingMore
                  ? 'Loading…'
                  : `Load more (showing ${results.length} of ${total})`}
              </button>
            {/if}
          {/if}
        </div>
      </div>

      <footer class="modal__foot">
        <span class="muted">{selected.size} selected</span>
        <div class="foot-actions">
          <button class="action-btn" type="button" onclick={close}
            >Cancel</button
          >
          {#if selectMode}
            <button
              class="btn-primary"
              type="button"
              disabled={selected.size === 0}
              onclick={useSelected}>Use as card image</button
            >
          {:else}
            <button
              class="btn-primary"
              type="button"
              disabled={selected.size === 0 || attaching}
              onclick={attach}
              >{attaching
                ? 'Attaching…'
                : `Attach ${selected.size || ''}`.trim()}</button
            >
          {/if}
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
    z-index: var(--z-modal, 10000);
    padding: var(--space-sm);
  }
  .modal {
    background: var(--color-bg);
    border: 2px solid var(--color-text);
    box-shadow: 4px 4px 0 var(--color-text);
    width: min(900px, 100%);
    max-height: 90dvh;
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
    /* Hitting the end of the results must not hand the wheel to the page. */
    overscroll-behavior: contain;
    /* Reserve the scrollbar's width — without it the filter grid's right-hand
       column renders under the bar and its boxes look cut off. */
    scrollbar-gutter: stable;
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .filter-panel {
    display: none;
    border: 1px solid var(--color-border);
    padding: var(--space-sm);
    background: color-mix(in srgb, var(--color-fg) 2%, transparent);
  }
  .filter-panel--open {
    display: block;
  }
  .attached {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .attached__label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
  }
  .attached__strip {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .attached__thumb {
    width: 56px;
    height: 56px;
    padding: 0;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    cursor: pointer;
    overflow: hidden;
  }
  .attached__thumb.selected {
    border-color: var(--color-accent);
    outline: 1px solid var(--color-accent);
  }
  .attached__thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .load-more {
    display: block;
    width: 100%;
    margin-top: var(--space-sm);
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
    background: var(--color-surface);
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
    border-color: var(--color-status-fail);
    color: var(--color-status-fail);
  }
  @media (max-width: 640px) {
    .modal {
      padding: var(--space-sm);
    }
    /* Filters + Upload + the query box + Search don't fit on one 390px line —
       the input and the Search button were running off the right edge. */
    .search {
      flex-wrap: wrap;
    }
    .search input {
      flex: 1 1 100%;
      order: -1;
    }
    .modal__head h2 {
      font-size: var(--text-base);
    }
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
    }
  }
</style>
