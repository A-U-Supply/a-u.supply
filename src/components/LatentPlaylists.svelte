<!--
  LatentPlaylists — the Latent's running orders: several named, hand-assembled
  sequences of audio drawn from anywhere in the Latent (an album sequence, a
  set for someone, a live set).

  Unlike a slot playlist — which is derived from its slot's audio and only
  stores an order — these are curated: nothing enters one without being added.
  See docs/plans/2026-07-24-latent-playlists.md.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import Sortable from 'sortablejs';
  import LatentStyleButton from './LatentStyleButton.svelte';
  import { queueMedia } from '../lib/playerQueue.ts';
  import RowMove from './RowMove.svelte';
  import { DRAG_OPTS } from '../lib/dragOptions.ts';
  import { isPhone } from '../lib/viewport.svelte.ts';

  type Props = {
    projectId: string;
    styleKey?: string | null;
  };

  let { projectId, styleKey = null }: Props = $props();

  type Track = {
    playlist_item_id: string;
    media_item_id: string;
    slot_id: string | null;
    filename: string | null;
    media_type: string | null;
    duration_seconds: number | null;
  };

  type Playlist = {
    id: string;
    name: string;
    position: number;
    tracks: Track[];
    total_seconds: number;
  };

  type Candidate = {
    media_item_id: string;
    filename: string;
    slot_id: string | null;
    slot_label: string;
  };

  let playlists = $state<Playlist[]>([]);
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

  const selected = $derived(playlists.find((p) => p.id === selectedId) || null);

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
    return `/api/projects/${encodeURIComponent(projectId)}/playlists${path}`;
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

  /** Replace one playlist in place, keeping tab order stable. */
  function merge(pl: Playlist) {
    playlists = playlists.map((p) => (p.id === pl.id ? pl : p));
  }

  async function load() {
    loading = true;
    try {
      const body = await send('');
      playlists = body.playlists || [];
      if (!playlists.some((p) => p.id === selectedId)) {
        selectedId = playlists[0]?.id ?? null;
      }
    } catch (e: any) {
      error = e?.message || 'Failed to load playlists';
    } finally {
      loading = false;
    }
  }

  async function createPlaylist() {
    const name = prompt(
      'Name this running order',
      `Running order ${playlists.length + 1}`,
    );
    if (!name?.trim()) return;
    try {
      const pl = await send('', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim() }),
      });
      playlists = [...playlists, pl];
      selectedId = pl.id;
    } catch (e: any) {
      error = e?.message || 'Failed to create playlist';
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

  async function deletePlaylist() {
    if (!selected) return;
    if (
      !confirm(
        `Delete "${selected.name}"? The audio itself stays in the Latent.`,
      )
    )
      return;
    const id = selected.id;
    try {
      await send(`/${encodeURIComponent(id)}`, { method: 'DELETE' });
      playlists = playlists.filter((p) => p.id !== id);
      selectedId = playlists[0]?.id ?? null;
    } catch (e: any) {
      error = e?.message || 'Failed to delete playlist';
    }
  }

  async function removeTrack(t: Track) {
    if (!selected) return;
    try {
      merge(
        await send(
          `/${encodeURIComponent(selected.id)}/items/${encodeURIComponent(t.playlist_item_id)}`,
          { method: 'DELETE' },
        ),
      );
    } catch (e: any) {
      error = e?.message || 'Failed to remove track';
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

  /** Move one track a step (the arrow-button path, for touch). */
  async function nudge(index: number, delta: number) {
    if (!selected) return;
    const target = index + delta;
    if (target < 0 || target >= selected.tracks.length) return;
    const tracks = [...selected.tracks];
    [tracks[index], tracks[target]] = [tracks[target], tracks[index]];
    merge({ ...selected, tracks });
    await sendOrder(tracks.map((t) => t.playlist_item_id));
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
    const byId = new Map(selected.tracks.map((t) => [t.playlist_item_id, t]));
    merge({
      ...selected,
      tracks: order.map((id) => byId.get(id)!).filter(Boolean),
    });
    await sendOrder(order);
  }

  function play(startIndex = 0) {
    if (!selected?.tracks.length) return;
    queueMedia(
      selected.tracks.map((t) => ({
        id: t.media_item_id,
        media_type: t.media_type || 'audio',
        filename: t.filename,
        duration_seconds: t.duration_seconds,
      })),
      startIndex,
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
        .filter((i: any) => i.media?.media_type === 'audio')
        .filter((i: any) =>
          seen.has(i.media_item_id) ? false : seen.add(i.media_item_id),
        )
        .map((i: any) => ({
          media_item_id: i.media_item_id,
          filename: i.media?.filename || '(unknown)',
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
    return !!selected?.tracks.some((t) => t.media_item_id === mediaId);
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
      error = e?.message || 'Failed to add tracks';
    } finally {
      closeAdd();
    }
  }

  function fmtDuration(s: number | null | undefined): string {
    if (s == null || !isFinite(s)) return '';
    const total = Math.round(s);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
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
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  });
</script>

<section class="playlists">
  <header class="playlists__head" class:latent-band={!!styleKey}>
    <h2>Playlists</h2>
    <div class="playlists__tabs">
      {#each playlists as p (p.id)}
        <button
          class="tab"
          class:active={p.id === selectedId}
          onclick={() => (selectedId = p.id)}
          type="button">{p.name}</button
        >
      {/each}
      <button class="tab tab--add" onclick={createPlaylist} type="button"
        >+ New</button
      >
    </div>
    {#if styleKey}
      <LatentStyleButton
        {projectId}
        scope="section"
        sectionKey={styleKey}
        push
      />
    {/if}
  </header>

  {#if error}
    <div class="error">{error}</div>
  {/if}

  {#if loading}
    <div class="muted empty">Loading…</div>
  {:else if !selected}
    <div class="muted empty">
      No running orders yet. A running order is a sequence you assemble by hand
      out of the audio anywhere in this Latent — an album sequence, a set for
      someone. <button class="link" type="button" onclick={createPlaylist}
        >Make one</button
      >.
    </div>
  {:else}
    <div class="bar">
      <button
        class="action-btn"
        type="button"
        disabled={!selected.tracks.length}
        onclick={() => play()}>▷ Play all</button
      >
      <span class="muted bar__meta"
        >{selected.tracks.length} track{selected.tracks.length === 1
          ? ''
          : 's'}{selected.total_seconds
          ? ` · ${fmtDuration(selected.total_seconds)}`
          : ''}</span
      >
      <span class="spacer"></span>
      <button class="action-btn" type="button" onclick={openAdd}
        >+ Add tracks</button
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
          aria-label="Playlist name"
        />
      {:else if isPhone()}
        <button
          class="action-btn bar__tools"
          type="button"
          aria-expanded={toolsOpen}
          onclick={() => (toolsOpen = !toolsOpen)}
          >Order tools {toolsOpen ? '▴' : '▾'}</button
        >
      {:else}
        <button class="action-btn" type="button" onclick={startRename}
          >Rename</button
        >
        <button
          class="action-btn action-btn--danger"
          type="button"
          onclick={deletePlaylist}>Delete</button
        >
      {/if}
    </div>

    {#if isPhone() && toolsOpen}
      <div class="order-menu" role="group" aria-label="Running order tools">
        <button
          class="order-menu__item"
          type="button"
          onclick={() => {
            toolsOpen = false;
            startRename();
          }}>Rename running order</button
        >
        <button
          class="order-menu__item order-menu__item--danger"
          type="button"
          onclick={() => {
            toolsOpen = false;
            deletePlaylist();
          }}>Delete running order</button
        >
      </div>
    {/if}

    {#if selected.tracks.length === 0}
      <div class="muted empty">
        Nothing in this running order yet — “+ Add tracks” pulls from any slot
        in this Latent.
      </div>
    {:else}
      <ul class="track-list" use:sortableTracks>
        {#each selected.tracks as t, i (t.playlist_item_id)}
          <li class="track-row" data-row-id={t.playlist_item_id}>
            <RowMove
              label={t.filename || 'track'}
              handleClass="track-row__drag"
              upDisabled={i === 0}
              downDisabled={i === selected.tracks.length - 1}
              onUp={() => nudge(i, -1)}
              onDown={() => nudge(i, 1)}
            />
            <span class="track-row__pos">{i + 1}</span>
            <a
              class="track-row__name"
              href={`/admin/search/detail?id=${encodeURIComponent(t.media_item_id)}`}
              title="Open in Stacks">{t.filename || '—'}</a
            >
            <span class="track-row__dur">{fmtDuration(t.duration_seconds)}</span
            >
            <button
              class="action-btn track-row__play"
              type="button"
              aria-label={`Play ${t.filename || 'track'} from here`}
              title="Play from here"
              onclick={() => play(i)}>▶</button
            >
            <button
              class="action-btn track-row__remove"
              type="button"
              title="Remove from this running order. File stays in the Latent."
              onclick={() => removeTrack(t)}>Remove</button
            >
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</section>

{#if adding}
  <!-- Popover on desktop, bottom sheet under 640px — same shape as the Style
       panel, so the keyboard doesn't cover the filter box on a phone. -->
  <div
    class="sheet-backdrop"
    onclick={closeAdd}
    onkeydown={() => {}}
    role="presentation"
  ></div>
  <div class="sheet" role="dialog" aria-modal="true" aria-label="Add tracks">
    <header class="sheet__head">
      <strong>Add tracks</strong>
      <button
        class="action-btn"
        type="button"
        aria-label="Close add tracks"
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
        <div class="muted empty">No audio in this Latent matches.</div>
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
  .playlists__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    border-bottom: 2px solid var(--color-text);
    padding-bottom: var(--space-xs);
  }
  .playlists__head h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
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
    grid-template-columns: 20px 3ch 1fr auto auto auto;
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
  .sheet-backdrop {
    position: fixed;
    inset: 0;
    background: var(--color-overlay-soft);
    z-index: 60;
  }
  .sheet {
    position: fixed;
    z-index: 61;
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
    .track-row {
      grid-template-columns: 76px 3ch 1fr auto;
      grid-template-areas:
        'move pos  name name'
        'move dur  play remove';
      row-gap: 4px;
      padding: 6px 8px;
    }
    .track-row__pos {
      grid-area: pos;
    }
    .track-row__name {
      grid-area: name;
    }
    .track-row__dur {
      grid-area: dur;
    }
    .track-row__play {
      grid-area: play;
      justify-self: stretch;
      min-height: 44px;
    }
    .track-row__remove {
      grid-area: remove;
      justify-self: end;
      min-height: 44px;
    }
    /* Bottom sheet: header and footer pinned, body scrolls, footer clear of
       the home indicator. */
    .sheet {
      left: 0;
      top: auto;
      bottom: 0;
      transform: none;
      width: 100vw;
      max-height: 85vh;
      border-width: 2px 0 0 0;
    }
    .sheet__foot {
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
