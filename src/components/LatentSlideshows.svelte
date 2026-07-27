<!--
  LatentSlideshows — the Latent's slideshows: several named, hand-assembled
  sequences of images and video drawn from anywhere in the Latent (a zine
  flip-through, a set of crops, a cut of scenes).

  Unlike a slot slideshow — which is derived from its slot's image/video and
  only stores an order — these are curated: nothing enters one without being
  added. Structurally a twin of LatentPlaylists, down to the CSS, so the two
  sections can't drift apart.
  See docs/plans/2026-07-26-latent-slideshow.md.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import Sortable from 'sortablejs';
  import LatentStyleButton from './LatentStyleButton.svelte';
  import { openLatentViewer } from '../lib/latentViewer.ts';
  import { portal } from '../lib/portal.ts';
  import RowMove from './RowMove.svelte';
  import { DRAG_OPTS } from '../lib/dragOptions.ts';
  import { isPhone } from '../lib/viewport.svelte.ts';
  import {
    readSectionOpen,
    writeSectionOpen,
    SECTION_REVEAL_EVENT,
  } from '../lib/latentCollapse.ts';

  type Props = {
    projectId: string;
    styleKey?: string | null;
  };

  let { projectId, styleKey = null }: Props = $props();

  type Slide = {
    slideshow_item_id: string;
    media_item_id: string;
    slot_id: string | null;
    filename: string | null;
    media_type: string | null;
    width: number | null;
    height: number | null;
  };

  type Slideshow = {
    id: string;
    name: string;
    position: number;
    slides: Slide[];
  };

  type Candidate = {
    media_item_id: string;
    filename: string;
    media_type: string;
    slot_id: string | null;
    slot_label: string;
  };

  let slideshows = $state<Slideshow[]>([]);
  let selectedId = $state<string | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let renaming = $state(false);
  let toolsOpen = $state(false);
  let renameValue = $state('');

  let adding = $state(false);
  let candidates = $state<Candidate[]>([]);
  let candidatesLoading = $state(false);
  let filter = $state('');
  let picked = $state<Set<string>>(new Set());
  let open = $state(readSectionOpen(projectId, 'slideshow'));

  function toggleOpen() {
    open = !open;
    writeSectionOpen(projectId, 'slideshow', open);
  }

  const selected = $derived(
    slideshows.find((p) => p.id === selectedId) || null,
  );

  const filtered = $derived(
    filter.trim()
      ? candidates.filter((c) =>
          `${c.filename} ${c.slot_label}`
            .toLowerCase()
            .includes(filter.trim().toLowerCase()),
        )
      : candidates,
  );

  function api(path: string): string {
    return `/api/projects/${encodeURIComponent(projectId)}/slideshows${path}`;
  }

  async function send(path: string, init: RequestInit = {}): Promise<any> {
    const res = await fetch(api(path), {
      credentials: 'include',
      ...init,
      headers: {
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers || {}),
      },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.detail || `Failed (${res.status})`);
    }
    return res.status === 204 ? null : res.json();
  }

  /** Replace one slideshow in place, keeping tab order stable. */
  function merge(sh: Slideshow) {
    slideshows = slideshows.map((p) => (p.id === sh.id ? sh : p));
  }

  async function load() {
    loading = true;
    try {
      const body = await send('');
      slideshows = body.slideshows || [];
      if (!slideshows.some((p) => p.id === selectedId)) {
        selectedId = slideshows[0]?.id ?? null;
      }
    } catch (e: any) {
      error = e?.message || 'Failed to load slideshows';
    } finally {
      loading = false;
    }
  }

  async function createSlideshow() {
    const name = prompt(
      'Name this slideshow',
      `Slideshow ${slideshows.length + 1}`,
    );
    if (!name?.trim()) return;
    try {
      const sh = await send('', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim() }),
      });
      slideshows = [...slideshows, sh];
      selectedId = sh.id;
    } catch (e: any) {
      error = e?.message || 'Failed to create slideshow';
    }
  }

  function startRename() {
    if (!selected) return;
    renameValue = selected.name;
    renaming = true;
  }

  async function commitRename() {
    if (!selected || !renameValue.trim()) {
      renaming = false;
      return;
    }
    try {
      merge(
        await send(`/${encodeURIComponent(selected.id)}`, {
          method: 'PATCH',
          body: JSON.stringify({ name: renameValue.trim() }),
        }),
      );
    } catch (e: any) {
      error = e?.message || 'Failed to rename';
    } finally {
      renaming = false;
    }
  }

  async function deleteSlideshow() {
    if (!selected) return;
    if (
      !confirm(
        `Delete "${selected.name}"? The images themselves stay in the Latent.`,
      )
    )
      return;
    const id = selected.id;
    try {
      await send(`/${encodeURIComponent(id)}`, { method: 'DELETE' });
      slideshows = slideshows.filter((p) => p.id !== id);
      selectedId = slideshows[0]?.id ?? null;
    } catch (e: any) {
      error = e?.message || 'Failed to delete slideshow';
    }
  }

  async function removeSlide(sl: Slide) {
    if (!selected) return;
    try {
      merge(
        await send(
          `/${encodeURIComponent(selected.id)}/items/${encodeURIComponent(sl.slideshow_item_id)}`,
          { method: 'DELETE' },
        ),
      );
    } catch (e: any) {
      error = e?.message || 'Failed to remove slide';
    }
  }

  /** Send an order. Shared by the drag handles and the arrow buttons. */
  async function sendOrder(order: string[]) {
    if (!selected || !order.length) return;
    try {
      merge(
        await send(`/${encodeURIComponent(selected.id)}/items/reorder`, {
          method: 'POST',
          body: JSON.stringify({ order }),
        }),
      );
    } catch (e: any) {
      error = e?.message || 'Failed to reorder';
      load(); // resync on failure
    }
  }

  /** Move one slide a step (the arrow-button path, for touch). */
  async function nudge(index: number, delta: number) {
    if (!selected) return;
    const target = index + delta;
    if (target < 0 || target >= selected.slides.length) return;
    const slides = [...selected.slides];
    [slides[index], slides[target]] = [slides[target], slides[index]];
    merge({ ...selected, slides });
    await sendOrder(slides.map((sl) => sl.slideshow_item_id));
  }

  async function persistOrder(list: HTMLElement) {
    if (!selected) return;
    const order = Array.from(
      list.querySelectorAll<HTMLElement>('.track-row[data-row-id]'),
    )
      .map((el) => el.dataset.rowId!)
      .filter(Boolean);
    if (!order.length) return;
    // Match state to the DOM Sortable just rearranged so the keyed each block
    // doesn't fight the drop while the request is in flight.
    const byId = new Map(
      selected.slides.map((sl) => [sl.slideshow_item_id, sl]),
    );
    merge({
      ...selected,
      slides: order.map((id) => byId.get(id)!).filter(Boolean),
    });
    await sendOrder(order);
  }

  function view(startIndex = 0) {
    if (!selected?.slides.length) return;
    const reel = selected.slides.map((sl) => ({
      media_item_id: sl.media_item_id,
      media: { filename: sl.filename, media_type: sl.media_type },
    }));
    openLatentViewer(
      reel,
      reel[startIndex]?.media_item_id ?? reel[0].media_item_id,
    );
  }

  /* ── Add tracks ─────────────────────────────────────────────────────────
     Candidates come from this Latent's own attachments, not a cross-index
     search: the source set is the Latent, so a picker over Emulsion at large
     would be the wrong shape. */

  async function openAdd() {
    adding = true;
    filter = '';
    picked = new Set();
    candidatesLoading = true;
    document.body.style.overflow = 'hidden'; // don't scroll the page behind the sheet
    try {
      const [itemsRes, slotsRes] = await Promise.all([
        fetch(`/api/projects/${encodeURIComponent(projectId)}/items`, {
          credentials: 'include',
        }),
        fetch(`/api/projects/${encodeURIComponent(projectId)}/slots`, {
          credentials: 'include',
        }),
      ]);
      if (!itemsRes.ok || !slotsRes.ok) throw new Error('Failed to load files');
      const items = (await itemsRes.json()).items || [];
      const labels = new Map<string, string>(
        ((await slotsRes.json()).slots || []).map((s: any) => [s.id, s.label]),
      );
      const seen = new Set<string>();
      candidates = items
        .filter(
          (i: any) =>
            i.media?.media_type === 'image' || i.media?.media_type === 'video',
        )
        .filter((i: any) =>
          seen.has(i.media_item_id) ? false : seen.add(i.media_item_id),
        )
        .map((i: any) => ({
          media_item_id: i.media_item_id,
          filename: i.media?.filename || '(unknown)',
          media_type: i.media?.media_type || 'image',
          slot_id: i.slot_id,
          slot_label: i.slot_id
            ? labels.get(i.slot_id) || 'Slot'
            : 'Loose files',
        }));
    } catch (e: any) {
      error = e?.message || 'Failed to load files';
    } finally {
      candidatesLoading = false;
    }
  }

  function closeAdd() {
    adding = false;
    document.body.style.overflow = '';
  }

  function togglePick(id: string) {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    picked = next;
  }

  function alreadyIn(mediaId: string): boolean {
    return !!selected?.slides.some((sl) => sl.media_item_id === mediaId);
  }

  async function commitAdd() {
    if (!selected || picked.size === 0) return closeAdd();
    try {
      merge(
        await send(`/${encodeURIComponent(selected.id)}/items`, {
          method: 'POST',
          body: JSON.stringify({ media_item_ids: [...picked] }),
        }),
      );
    } catch (e: any) {
      error = e?.message || 'Failed to add slides';
    } finally {
      closeAdd();
    }
  }

  function fmtDims(w: number | null, h: number | null): string {
    return w && h ? `${w}\u00d7${h}` : '';
  }

  function thumbUrl(id: string): string {
    return `/api/media/${encodeURIComponent(id)}/thumbnail?size=sm`;
  }

  function sortableTracks(node: HTMLElement) {
    const s = Sortable.create(node, {
      ...DRAG_OPTS,
      handle: '.track-row__drag',
      draggable: '.track-row',
      ghostClass: 'track-row--ghost',
      onEnd: () => persistOrder(node),
    });
    return { destroy: () => s.destroy() };
  }

  onMount(() => {
    load();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && adding) closeAdd();
    };
    // The section map jumps here; collapsed, that would be a dead end.
    const onReveal = (e: Event) => {
      if ((e as CustomEvent).detail?.section !== 'slideshow' || open) return;
      open = true;
      writeSectionOpen(projectId, 'slideshow', true);
    };
    document.addEventListener('keydown', onKey);
    window.addEventListener(SECTION_REVEAL_EVENT, onReveal);
    return () => {
      document.removeEventListener('keydown', onKey);
      window.removeEventListener(SECTION_REVEAL_EVENT, onReveal);
      document.body.style.overflow = '';
    };
  });
</script>

<section class="playlists">
  <header class="playlists__head" class:latent-band={!!styleKey}>
    <!-- .sec-summary is the house collapsible-section head (admin.css) — the
         same one Repo, Links, Documents and Threads use. Kept inside the h2 so
         the page outline still has a heading here. -->
    <h2 class="playlists__title">
      <button
        class="sec-summary"
        type="button"
        aria-expanded={open}
        aria-controls="slideshow-body"
        onclick={toggleOpen}
      >
        <span class="sec-summary__caret" aria-hidden="true"
          >{open ? '▾' : '▸'}</span
        >
        <span class="sec-summary__label">Slideshows</span>
        <span class="sec-summary__meta"
          >{slideshows.length} slideshow{slideshows.length === 1
            ? ''
            : 's'}</span
        >
      </button>
    </h2>
    <!-- The tabs pick WHICH slideshow the body shows, so collapsed they'd change
         something you can't see. The Style button acts on the section itself
         and stays, as it does on every other collapsible section. -->
    {#if open}
      <div class="playlists__tabs">
        {#each slideshows as p (p.id)}
          <button
            class="tab"
            class:active={p.id === selectedId}
            onclick={() => (selectedId = p.id)}
            type="button">{p.name}</button
          >
        {/each}
        <button class="tab tab--add" onclick={createSlideshow} type="button"
          >+ New</button
        >
      </div>
    {/if}
    {#if styleKey}
      <LatentStyleButton
        {projectId}
        scope="section"
        sectionKey={styleKey}
        push
      />
    {/if}
  </header>

  {#if open}
    <div id="slideshow-body">
      {#if error}
        <div class="error">{error}</div>
      {/if}

      {#if loading}
        <div class="muted empty">Loading…</div>
      {:else if !selected}
        <div class="muted empty">
          No slideshows yet. A slideshow is a sequence you assemble by hand out
          of the images and video anywhere in this Latent — a zine flip-through,
          a cut of scenes. <button
            class="link"
            type="button"
            onclick={createSlideshow}>Make one</button
          >.
        </div>
      {:else}
        <div class="bar">
          <button
            class="action-btn"
            type="button"
            disabled={!selected.slides.length}
            onclick={() => view()}>▷ View all</button
          >
          <span class="muted bar__meta"
            >{selected.slides.length} slide{selected.slides.length === 1
              ? ''
              : 's'}</span
          >
          <span class="spacer"></span>
          <button class="action-btn" type="button" onclick={openAdd}
            >+ Add slides</button
          >
          {#if renaming}
            <input
              class="rename"
              bind:value={renameValue}
              onblur={commitRename}
              onkeydown={(e) => {
                if (e.key === 'Enter') commitRename();
                if (e.key === 'Escape') renaming = false;
              }}
              aria-label="Slideshow name"
            />
          {:else if isPhone()}
            <button
              class="action-btn bar__tools"
              type="button"
              aria-expanded={toolsOpen}
              onclick={() => (toolsOpen = !toolsOpen)}
              >Slideshow tools {toolsOpen ? '▴' : '▾'}</button
            >
          {:else}
            <button class="action-btn" type="button" onclick={startRename}
              >Rename</button
            >
            <button
              class="action-btn action-btn--danger"
              type="button"
              onclick={deleteSlideshow}>Delete</button
            >
          {/if}
        </div>

        {#if isPhone() && toolsOpen}
          <div class="order-menu" role="group" aria-label="Slideshow tools">
            <button
              class="order-menu__item"
              type="button"
              onclick={() => {
                toolsOpen = false;
                startRename();
              }}>Rename slideshow</button
            >
            <button
              class="order-menu__item order-menu__item--danger"
              type="button"
              onclick={() => {
                toolsOpen = false;
                deleteSlideshow();
              }}>Delete slideshow</button
            >
          </div>
        {/if}

        {#if selected.slides.length === 0}
          <div class="muted empty">
            Nothing in this slideshow yet — “+ Add slides” pulls from any slot
            in this Latent.
          </div>
        {:else}
          <ul class="track-list" use:sortableTracks>
            {#each selected.slides as sl, i (sl.slideshow_item_id)}
              <li class="track-row" data-row-id={sl.slideshow_item_id}>
                <RowMove
                  label={sl.filename || 'slide'}
                  handleClass="track-row__drag"
                  upDisabled={i === 0}
                  downDisabled={i === selected.slides.length - 1}
                  onUp={() => nudge(i, -1)}
                  onDown={() => nudge(i, 1)}
                />
                <div class="track-row__main">
                  <span class="track-row__pos">{i + 1}</span>
                  <button
                    class="slide-thumb"
                    type="button"
                    aria-label={`View ${sl.filename || 'this slide'} full screen`}
                    onclick={() => view(i)}
                  >
                    {#if sl.media_type === 'image'}
                      <img
                        src={thumbUrl(sl.media_item_id)}
                        alt={sl.filename || ''}
                      />
                    {:else}
                      <span class="slide-thumb__chip">V</span>
                    {/if}
                  </button>
                  <a
                    class="track-row__name"
                    href={`/admin/search/detail?id=${encodeURIComponent(sl.media_item_id)}`}
                    title="Open in Stacks">{sl.filename || '—'}</a
                  >
                  <span class="track-row__dur"
                    >{fmtDims(sl.width, sl.height)}</span
                  >
                  <button
                    class="action-btn track-row__play"
                    type="button"
                    aria-label={`View ${sl.filename || 'slide'} from here`}
                    title="View from here"
                    onclick={() => view(i)}>▷</button
                  >
                  <button
                    class="action-btn track-row__remove"
                    type="button"
                    title="Remove from this slideshow. File stays in the Latent."
                    onclick={() => removeSlide(sl)}>Remove</button
                  >
                </div>
              </li>
            {/each}
          </ul>
        {/if}
      {/if}
    </div>
  {/if}
</section>

{#if adding}
  <!-- Popover on desktop, bottom sheet under 640px — same shape as the Style
       panel, so the keyboard doesn't cover the filter box on a phone. -->
  <div
    use:portal
    class="sheet-backdrop"
    onclick={closeAdd}
    onkeydown={() => {}}
    role="presentation"
  ></div>
  <div
    use:portal
    class="sheet"
    role="dialog"
    aria-modal="true"
    aria-label="Add slides"
  >
    <header class="sheet__head">
      <strong>Add slides</strong>
      <button
        class="action-btn"
        type="button"
        aria-label="Close add slides"
        onclick={closeAdd}>✕</button
      >
      <input
        class="sheet__filter"
        placeholder="Filter by name or slot…"
        bind:value={filter}
        aria-label="Filter files"
      />
    </header>
    <div class="sheet__body">
      {#if candidatesLoading}
        <div class="muted empty">Loading files…</div>
      {:else if filtered.length === 0}
        <div class="muted empty">No images or video in this Latent match.</div>
      {:else}
        <ul class="pick-list">
          {#each filtered as c (c.media_item_id)}
            <li class="pick-row">
              <label class="pick-row__label">
                <input
                  type="checkbox"
                  checked={picked.has(c.media_item_id)}
                  onchange={() => togglePick(c.media_item_id)}
                />
                <span class="pick-row__thumb">
                  {#if c.media_type === 'image'}
                    <img src={thumbUrl(c.media_item_id)} alt="" />
                  {:else}
                    <span class="slide-thumb__chip">V</span>
                  {/if}
                </span>
                <span class="pick-row__name">{c.filename}</span>
                <span class="pick-row__slot">{c.slot_label}</span>
                {#if alreadyIn(c.media_item_id)}
                  <span class="pick-row__in">already in</span>
                {/if}
              </label>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
    <footer class="sheet__foot">
      <span class="muted">{picked.size} selected</span>
      <button class="btn-cancel" type="button" onclick={closeAdd}>Cancel</button
      >
      <button
        class="btn-primary"
        type="button"
        disabled={picked.size === 0}
        onclick={commitAdd}>Add</button
      >
    </footer>
  </div>
{/if}

<style>
  .playlists {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  /* One wrapper so the whole body collapses together; it re-declares
     .playlists' column gap now that it's the flex child. */
  #slideshow-body {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .playlists__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    border-bottom: 2px solid var(--color-text);
    padding-bottom: var(--space-xs);
  }
  /* The heading is now a wrapper around .sec-summary, which brings its own
     type. It just has to stop being a block and let the summary fill the row. */
  .playlists__title {
    margin: 0;
    display: flex;
    flex: 1 1 auto;
    min-width: 0;
  }
  .playlists__tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-left: auto;
  }
  .tab {
    padding: 4px 10px;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    cursor: pointer;
    font: inherit;
    font-size: var(--text-sm);
  }
  .tab.active {
    background: var(--color-text);
    color: var(--color-bg);
    border-color: var(--color-text);
  }
  .tab--add {
    color: var(--color-muted);
  }
  .bar {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex-wrap: wrap;
  }
  .bar__meta {
    font-family: var(--font-mono);
    font-size: 0.7rem;
  }
  .spacer {
    flex: 1;
  }
  .rename {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    font: inherit;
    font-size: var(--text-sm);
    padding: 3px 6px;
  }
  .empty {
    font-size: var(--text-sm);
    padding: var(--space-sm) 0;
  }
  .error {
    color: var(--color-status-fail);
    font-size: var(--text-sm);
  }
  .link {
    background: none;
    border: 0;
    padding: 0;
    font: inherit;
    color: var(--color-accent);
    cursor: pointer;
    text-decoration: underline;
  }
  .track-list {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .track-row {
    display: grid;
    /* One more column than the playlist twin: grip, position, THUMB, name,
       size, view, remove. Miscount it and Remove wraps to a second line. */
    grid-template-columns: 20px 3ch 40px 1fr auto auto auto;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border);
    font-size: var(--text-sm);
  }
  .track-row:last-child {
    border-bottom: 0;
  }
  .track-row:hover {
    background: var(--color-surface);
  }
  .track-row--ghost {
    opacity: 0.4;
  }
  /* The only element that swallows touch gestures — on the row it would stop
     the list scrolling. */
  .drag-handle {
    background: transparent;
    border: 0;
    color: var(--color-muted);
    cursor: grab;
    font-size: 0.9rem;
    line-height: 1;
    padding: 0 2px;
    touch-action: none;
    align-self: stretch;
  }
  .drag-handle:active {
    cursor: grabbing;
  }
  :global(.drag-chosen) {
    outline: 2px solid var(--color-accent);
    outline-offset: -2px;
    box-shadow: 0 2px 8px var(--color-overlay-soft);
  }
  /* Transparent on desktop: children stay direct grid items of .track-row. */
  .track-row__main {
    display: contents;
  }
  .track-row__pos {
    font-family: var(--font-mono);
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .track-row__name {
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--color-text);
    text-decoration: none;
  }
  .track-row__name:hover {
    color: var(--color-accent);
  }
  .track-row__dur {
    color: var(--color-muted);
    font-family: var(--font-mono);
    font-size: 0.7rem;
    white-space: nowrap;
  }
  .track-row__play,
  .track-row__remove {
    padding: 2px 6px;
    font-size: 0.75rem;
  }
  /* One vertical control column per row: ↑ above the grip, ↓ below it. Arrows
     are the touch alternative to dragging and appear at <=640px only; on
     desktop the stack collapses to the grip alone. */

  /* --- Add-tracks sheet -------------------------------------------------- */
  /* !important, reluctantly: detail.astro styles every direct child of a
     .latent-section with `position: relative; z-index: 2` at specificity
     (0,3,0), which outranks these scoped rules — so the overlay rendered in
     the page flow instead of over it, on every width. LatentStylePanel is
     mounted outside the sections for the same reason; this one can't be,
     because it belongs to the island that owns the playlist state. */
  .sheet-backdrop {
    position: fixed !important;
    inset: 0;
    background: var(--color-overlay-soft);
    z-index: 60 !important;
  }
  .sheet {
    position: fixed !important;
    z-index: 61 !important;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    width: min(560px, calc(100vw - 2rem));
    max-height: min(70vh, 640px);
    display: flex;
    flex-direction: column;
    background: var(--color-bg);
    border: 2px solid var(--color-text);
  }
  .sheet__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex-wrap: wrap;
    padding: var(--space-sm);
    border-bottom: 1px solid var(--color-border);
    background: var(--color-surface);
  }
  .sheet__filter {
    flex: 1 0 100%;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    font: inherit;
    font-size: var(--text-sm);
    padding: 6px;
  }
  .sheet__body {
    overflow-y: auto;
    flex: 1;
  }
  .sheet__foot {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: var(--space-sm);
    border-top: 1px solid var(--color-border);
    background: var(--color-surface);
  }
  .sheet__foot .btn-primary {
    margin-left: auto;
  }
  .pick-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .pick-row {
    border-bottom: 1px solid var(--color-border);
  }
  .slide-thumb {
    width: 40px;
    height: 40px;
    padding: 0;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    color: var(--color-muted);
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    cursor: zoom-in;
  }
  .slide-thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .slide-thumb:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: -2px;
  }
  .slide-thumb__chip {
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: 0.85rem;
  }
  .pick-row__thumb {
    flex: 0 0 auto;
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    color: var(--color-muted);
    overflow: hidden;
  }
  .pick-row__thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .pick-row__label {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: 8px;
    cursor: pointer;
    font-size: var(--text-sm);
  }
  .pick-row__name {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .pick-row__slot,
  .pick-row__in {
    color: var(--color-muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    white-space: nowrap;
  }

  .bar__tools {
    min-height: 44px;
  }
  .order-menu {
    display: flex;
    flex-direction: column;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    margin-top: 4px;
  }
  .order-menu__item {
    font: inherit;
    font-size: 0.75rem;
    text-align: left;
    background: none;
    border: 0;
    border-bottom: 1px dotted var(--color-border);
    color: var(--color-text);
    padding: 0 8px;
    min-height: 44px;
    cursor: pointer;
  }
  .order-menu__item:last-child {
    border-bottom: 0;
  }
  .order-menu__item--danger {
    color: var(--color-status-fail);
  }
  .order-menu__item:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: -2px;
  }

  @media (max-width: 640px) {
    .playlists__tabs {
      margin-left: 0;
      width: 100%;
      flex-wrap: wrap;
      gap: 3px;
    }
    .tab {
      white-space: nowrap;
      min-height: 44px;
    }
    .bar .action-btn {
      min-height: 44px;
    }
    /* Two lines: identity up top, controls under, drag handle full-height. */
    /* Content sits beside the reorder block, so a row is the taller of the
       two rather than the sum — the same fix .file-row needed. */
    .slide-thumb {
      flex: 0 0 auto;
    }
    .track-row {
      display: flex;
      align-items: flex-start;
      column-gap: 6px;
      padding: 6px 8px;
    }
    :global(.track-row .row-move) {
      flex: 0 0 68px;
    }
    .track-row__main {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      flex: 1 1 0;
      min-width: 0;
      gap: 4px 6px;
    }
    .track-row__pos,
    .track-row__dur {
      flex: 0 0 auto;
    }
    .track-row__name {
      flex: 1 1 140px;
      min-width: 0;
    }
    .track-row__play {
      flex: 1 1 auto;
      min-height: 44px;
    }
    .track-row__remove {
      flex: 0 0 auto;
      min-height: 44px;
    }
    /* A full-screen picker on a phone, not a partial sheet.
       Two things made the partial version unusable: the persistent player is
       fixed at z-index 9999, so it covered the sheet's footer (the Add and
       Cancel buttons) — and 85vh measures the *largest* viewport, so with the
       URL bar showing, the bottom of the sheet sat below the visible area
       with nothing to scroll to. Full screen, above the player, sized in dvh
       fixes both, and makes picking tracks its own screen rather than a
       letterbox. */
    .sheet-backdrop {
      z-index: var(--z-modal, 10000) !important;
    }
    .sheet {
      left: 0;
      right: 0;
      top: 0;
      bottom: 0;
      transform: none;
      width: 100vw;
      height: 100dvh;
      max-height: none;
      border-width: 0;
      z-index: var(--z-modal, 10000) !important;
    }
    .sheet__head {
      position: sticky;
      top: 0;
      z-index: 1;
    }
    .sheet__body {
      overscroll-behavior: contain;
      -webkit-overflow-scrolling: touch;
    }
    .sheet__foot {
      position: sticky;
      bottom: 0;
      padding-bottom: calc(var(--space-sm) + env(safe-area-inset-bottom));
    }
    .sheet__foot .btn-primary,
    .sheet__foot .btn-cancel {
      min-height: 44px;
    }
    .pick-row__label {
      min-height: 44px;
    }
  }
</style>
