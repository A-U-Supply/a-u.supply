<!--
  LatentLooseFiles — the grid of files attached to a Latent at the loose level
  (i.e. not inside any slot). Renders the unified Uploader docked at the top
  and a "Pull from index" modal for attaching existing media.
-->
<script lang="ts">
  import Uploader from './Uploader.svelte';
  import PullFromIndex from './PullFromIndex.svelte';
  import LatentStyleButton from './LatentStyleButton.svelte';
  import MarginaliaBadge from './MarginaliaBadge.svelte';
  import {
    fetchAnnotationCounts,
    type AnnotationCounts,
  } from './marginalia.ts';
  import { fileExt } from '../lib/fileExt.ts';
  import RowActions from './RowActions.svelte';
  import { isPhone } from '../lib/viewport.svelte.ts';
  import { openLatentViewer, isViewable } from '../lib/latentViewer.ts';
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

  type Item = {
    id: string;
    slot_id: string | null;
    media_item_id: string;
    added_at: string;
    media?: {
      id: string;
      filename: string;
      media_type: string;
      mime_type: string;
      file_size_bytes: number;
      parent_media_item_id?: string | null;
    } | null;
  };

  let items = $state<Item[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let pullOpen = $state(false);
  let rootEl: HTMLElement | null = $state(null);
  let annotationCounts = $state<AnnotationCounts>({});
  // The pile is the tallest thing on the page once it fills up. Collapsed, the
  // head line keeps the count so it still reports what's in there.
  let open = $state(readSectionOpen(projectId, 'loose'));

  function toggleOpen() {
    open = !open;
    writeSectionOpen(projectId, 'loose', open);
  }

  // The loose pile mixes audio, sessions and documents in with the pictures;
  // only images and video get a lightbox.
  let viewableCount = $derived(
    items.filter((it) => isViewable(it.media?.media_type)).length,
  );

  function viewAll() {
    const first = items.find((it) => isViewable(it.media?.media_type));
    if (first) openLatentViewer(items, first.media_item_id);
  }

  async function load() {
    loading = true;
    error = null;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/items?loose_only=true`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      items = body.items || [];
      // One batched counts request for every visible loose item.
      const fresh = await fetchAnnotationCounts(
        items.map((i) => i.media_item_id),
      );
      if (Object.keys(fresh).length) {
        annotationCounts = { ...annotationCounts, ...fresh };
      }
    } catch (e: any) {
      error = e?.message || 'Failed to load files';
    } finally {
      loading = false;
    }
  }

  async function rename(item: Item) {
    const current = item.media?.filename || '';
    const name = prompt('Rename file:', current);
    if (!name || name === current) return;
    try {
      const res = await fetch(
        `/api/media/${encodeURIComponent(item.media_item_id)}`,
        {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: name }),
        },
      );
      if (!res.ok) {
        if (res.status === 409)
          throw new Error('A file with that name already exists');
        throw new Error(`Failed (${res.status})`);
      }
      const body = await res.json();
      items = items.map((i) =>
        i.id === item.id && i.media
          ? { ...i, media: { ...i.media, filename: body.filename } }
          : i,
      );
    } catch (e: any) {
      error = e?.message || 'Failed to rename';
    }
  }

  async function detach(item: Item) {
    if (!confirm('Detach this file from the Latent?')) return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(item.id)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      items = items.filter((i) => i.id !== item.id);
    } catch (e: any) {
      error = e?.message || 'Failed to detach';
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function thumbUrl(mediaId: string): string {
    return `/api/media/${encodeURIComponent(mediaId)}/thumbnail?size=sm`;
  }

  function fileUrl(mediaId: string): string {
    return `/api/media/${encodeURIComponent(mediaId)}/file`;
  }

  function playInPlayer(mediaId: string, mediaType: string, title: string) {
    document.dispatchEvent(
      new CustomEvent('player:queue', {
        detail: {
          tracks: [
            {
              track_id: mediaId,
              title: title || 'Untitled',
              release_title: '',
              release_code: '',
              media_type: mediaType,
              // MIDI items stream their synthesized WAV preview.
              stream_url:
                mediaType === 'midi'
                  ? `/api/media/${encodeURIComponent(mediaId)}/audio`
                  : `/api/media/${encodeURIComponent(mediaId)}/file`,
              cover_url:
                mediaType === 'image' || mediaType === 'video'
                  ? `/api/media/${encodeURIComponent(mediaId)}/thumbnail`
                  : '/assets/default-cover.jpg',
              duration: 0,
              entity_name: '',
            },
          ],
          startIndex: 0,
        },
      }),
    );
  }

  // Extracted session children land here as loose peers of the bundle — the
  // chip jumps back to the parent tile when it's in this list.
  function parentOf(item: Item): Item | undefined {
    const pid = item.media?.parent_media_item_id;
    if (!pid) return undefined;
    return items.find((i) => i.media_item_id === pid);
  }

  function scrollToParent(mediaId: string) {
    const el = rootEl?.querySelector(
      `[data-media-id="${CSS.escape(mediaId)}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('tile--flash');
    setTimeout(() => el.classList.remove('tile--flash'), 1600);
  }

  // Cover art naturally lands here via the Uploader — upload-then-click
  // is the whole hero journey. The LatentHero island listens for the
  // broadcast and updates itself (and the header accent).
  async function setAsHero(item: Item) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hero_media_item_id: item.media_item_id }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      window.dispatchEvent(
        new CustomEvent('latent-hero-changed', { detail: await res.json() }),
      );
    } catch (e: any) {
      error = e?.message || 'Failed to set card image';
    }
  }

  $effect(() => {
    if (projectId) load();
  });

  // The section map jumps here; if we're collapsed it would jump to a dead end.
  $effect(() => {
    const onReveal = (e: Event) => {
      if ((e as CustomEvent).detail?.section !== 'loose' || open) return;
      open = true;
      writeSectionOpen(projectId, 'loose', true);
    };
    window.addEventListener(SECTION_REVEAL_EVENT, onReveal);
    return () => window.removeEventListener(SECTION_REVEAL_EVENT, onReveal);
  });
</script>

<section class="loose" bind:this={rootEl}>
  <header class="loose__head" class:latent-band={!!styleKey}>
    <h2>
      <button
        class="sec-toggle"
        type="button"
        aria-expanded={open}
        aria-controls="loose-body"
        title={open ? 'Collapse loose files' : 'Expand loose files'}
        onclick={toggleOpen}
      >
        <span class="sec-toggle__caret" aria-hidden="true"
          >{open ? '▾' : '▸'}</span
        >
        Loose files
        <span class="sec-toggle__count">{items.length}</span>
      </button>
    </h2>
    <!-- Collapsed, the head line is just the disclosure: nothing to act on
         until you can see what you'd be acting on. -->
    {#if open}
      <div class="loose__actions">
        {#if viewableCount > 0}
          <button
            class="action-btn"
            type="button"
            title="Look through every image and video in the loose pile"
            onclick={viewAll}>▷ View all ({viewableCount})</button
          >
        {/if}
        <button
          class="action-btn"
          type="button"
          onclick={() => (pullOpen = true)}>+ Pull from index</button
        >
        {#if styleKey}
          <LatentStyleButton
            {projectId}
            scope="section"
            sectionKey={styleKey}
          />
        {/if}
      </div>
    {/if}
  </header>

  {#if open}
    <div id="loose-body">
      <Uploader
        destination="project"
        {projectId}
        compact={true}
        onUploaded={() => load()}
      />

      {#if error}
        <div class="notice notice--error">{error}</div>
      {/if}
      {#if loading && items.length === 0}
        <div class="muted">Loading…</div>
      {:else if items.length === 0}
        <div class="muted">
          No loose files yet — drop something above, or pull from the index.
        </div>
      {:else}
        <ul class="grid">
          {#each items as it (it.id)}
            {@const ext = fileExt(it.media?.filename)}
            <li
              class="tile"
              data-type={it.media?.media_type}
              data-media-id={it.media_item_id}
            >
              <svelte:element
                this={isViewable(it.media?.media_type) ? 'button' : 'a'}
                class="tile__thumb"
                type={isViewable(it.media?.media_type) ? 'button' : undefined}
                title={isViewable(it.media?.media_type)
                  ? `View ${it.media?.filename || 'this file'} full screen`
                  : 'Open in Stacks'}
                href={isViewable(it.media?.media_type)
                  ? undefined
                  : `/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`}
                onclick={isViewable(it.media?.media_type)
                  ? () => openLatentViewer(items, it.media_item_id)
                  : undefined}
              >
                {#if it.media?.media_type === 'image'}
                  <img
                    src={thumbUrl(it.media_item_id)}
                    alt={it.media?.filename}
                  />
                {:else}
                  <span class="filechip">
                    <span class="filechip__ext"
                      >{ext ? '.' + ext : it.media?.media_type || 'file'}</span
                    >
                    <span class="filechip__type"
                      >{it.media?.media_type === 'session'
                        ? '▣ session'
                        : it.media?.media_type || 'file'}</span
                    >
                  </span>
                {/if}
              </svelte:element>
              <div class="tile__info">
                <div class="tile__name" title={it.media?.filename}>
                  {it.media?.filename || '(unknown)'}
                </div>
                {#if it.media?.parent_media_item_id}
                  {@const parent = parentOf(it)}
                  {#if parent}
                    <button
                      class="session-chip"
                      type="button"
                      title={`Extracted from ${parent.media?.filename || 'session bundle'} — click to jump to it`}
                      onclick={() => scrollToParent(parent.media_item_id)}
                      >from session</button
                    >
                  {:else}
                    <span
                      class="session-chip"
                      title="Extracted from a session bundle">extracted</span
                    >
                  {/if}
                {/if}
                <div class="tile__meta" title={it.media?.mime_type}>
                  {ext || it.media?.media_type || ''}
                  {#if it.media?.file_size_bytes}
                    · {formatSize(it.media.file_size_bytes)}
                  {/if}
                </div>
              </div>
              <div class="tile__actions">
                {#if it.media?.media_type === 'audio' || it.media?.media_type === 'video' || it.media?.media_type === 'midi'}
                  <button
                    class="action-btn"
                    type="button"
                    aria-label={`Play ${it.media?.filename || 'file'}`}
                    title="Play (queues in the persistent Player)"
                    onclick={() =>
                      playInPlayer(
                        it.media_item_id,
                        it.media!.media_type,
                        it.media?.filename || '',
                      )}>▶ Play</button
                  >
                {/if}
                {#if it.media}
                  <MarginaliaBadge
                    mediaId={it.media_item_id}
                    mediaType={it.media.media_type}
                    filename={it.media.filename || ''}
                    counts={annotationCounts[it.media_item_id] || null}
                    showEmpty={isPhone()}
                  />
                {/if}
                <RowActions
                  label={it.media?.filename || 'this file'}
                  meta={[
                    fileExt(it.media?.filename) ||
                      it.media?.media_type ||
                      'file',
                    it.media?.file_size_bytes
                      ? formatSize(it.media.file_size_bytes)
                      : '',
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                  actions={[
                    { label: 'Rename', onClick: () => rename(it) },
                    ...(it.media?.media_type === 'image'
                      ? [
                          {
                            label: 'Set as card image',
                            onClick: () => setAsHero(it),
                          },
                        ]
                      : []),
                    {
                      label: 'Download',
                      href: fileUrl(it.media_item_id),
                      download: it.media?.filename || undefined,
                    },
                    {
                      label: 'Open in Stacks',
                      href: `/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`,
                    },
                    {
                      label: 'Detach',
                      danger: true,
                      title: 'Remove from this Latent. File stays in Emulsion.',
                      onClick: () => detach(it),
                    },
                  ]}
                />
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}

  <PullFromIndex bind:open={pullOpen} {projectId} onAttached={() => load()} />
</section>

<style>
  .loose {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .loose__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    border-bottom: 2px solid var(--color-text);
    padding-bottom: var(--space-xs);
  }
  /* The body is one wrapper so the whole thing collapses together; it has to
     re-declare .loose's column gap, since it's now the flex child. */
  #loose-body {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .loose__head h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .loose__actions {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .grid {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 8px;
  }
  .tile {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    display: flex;
    flex-direction: column;
  }
  /* Renders as <button> for images and video (opens the viewer) and <a> for
     everything else (Stacks) — so it carries the reset for both. */
  .tile__thumb {
    display: flex;
    align-items: center;
    justify-content: center;
    aspect-ratio: 1;
    width: 100%;
    padding: 0;
    border: 0;
    font: inherit;
    background: var(--color-surface);
    text-decoration: none;
    color: inherit;
    overflow: hidden;
  }
  button.tile__thumb {
    cursor: zoom-in;
  }
  .tile__thumb:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: -2px;
  }
  .tile__thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .filechip {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 0 6px;
    min-width: 0;
  }
  .filechip__ext {
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: var(--text-md);
    color: var(--color-text);
    word-break: break-all;
    text-align: center;
  }
  .filechip__type {
    color: var(--color-muted);
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .tile__info {
    padding: 6px 8px;
    min-width: 0;
  }
  .tile__name {
    /* Full filename, wrapped — truncation hid the part that identifies
       the file, and hover titles don't exist on mobile. */
    overflow-wrap: anywhere;
    font-size: var(--text-sm);
  }
  .tile__meta {
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .session-chip {
    display: inline-block;
    margin-top: 2px;
    border: 1px solid var(--color-border);
    background: transparent;
    color: var(--color-muted);
    font: inherit;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    padding: 1px 6px;
    white-space: nowrap;
  }
  button.session-chip {
    cursor: pointer;
  }
  button.session-chip:hover {
    color: var(--color-accent);
    border-color: var(--color-accent);
  }
  .tile--flash {
    animation: tile-flash 1.6s ease-out;
  }
  @keyframes tile-flash {
    0% {
      background: rgba(184, 134, 11, 0.35);
    }
    100% {
      background: transparent;
    }
  }
  .tile__actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    padding: 0 6px 6px;
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
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    }
    .loose__head {
      flex-wrap: wrap;
    }
    .loose__actions {
      margin-left: 0;
    }
    .session-chip {
      min-height: 44px;
      display: inline-flex;
      align-items: center;
    }
    .tile__actions .action-btn {
      min-height: 44px;
    }
  }
</style>
