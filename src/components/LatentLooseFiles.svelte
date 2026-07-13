<!--
  LatentLooseFiles — the grid of files attached to a Latent at the loose level
  (i.e. not inside any slot). Renders the unified Uploader docked at the top
  and a "Pull from index" modal for attaching existing media.
-->
<script lang="ts">
  import Uploader from './Uploader.svelte';
  import PullFromIndex from './PullFromIndex.svelte';
  import { fileExt } from '../lib/fileExt.ts';

  type Props = {
    projectId: string;
  };

  let { projectId }: Props = $props();

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
    } | null;
  };

  let items = $state<Item[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let pullOpen = $state(false);

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
    } catch (e: any) {
      error = e?.message || 'Failed to load files';
    } finally {
      loading = false;
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
</script>

<section class="loose">
  <header class="loose__head">
    <h2>Loose files</h2>
    <span class="muted">{items.length}</span>
    <div class="loose__actions">
      <button class="action-btn" type="button" onclick={() => (pullOpen = true)}
        >+ Pull from index</button
      >
    </div>
  </header>

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
        <li class="tile" data-type={it.media?.media_type}>
          <a
            class="tile__thumb"
            href={`/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`}
          >
            {#if it.media?.media_type === 'image'}
              <img src={thumbUrl(it.media_item_id)} alt={it.media?.filename} />
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
          </a>
          <div class="tile__info">
            <div class="tile__name" title={it.media?.filename}>
              {it.media?.filename || '(unknown)'}
            </div>
            <div class="tile__meta" title={it.media?.mime_type}>
              {ext || it.media?.media_type || ''}
              {#if it.media?.file_size_bytes}
                · {formatSize(it.media.file_size_bytes)}
              {/if}
            </div>
          </div>
          <div class="tile__actions">
            {#if it.media?.media_type === 'image'}
              <button
                class="action-btn"
                type="button"
                onclick={() => setAsHero(it)}>Set as card image</button
              >
            {/if}
            <a
              class="action-btn"
              href={fileUrl(it.media_item_id)}
              download={it.media?.filename}>Download</a
            >
            <button
              class="action-btn action-btn--danger"
              type="button"
              onclick={() => detach(it)}>Detach</button
            >
          </div>
        </li>
      {/each}
    </ul>
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
  .loose__head h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .loose__actions {
    margin-left: auto;
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
  .tile__thumb {
    display: flex;
    align-items: center;
    justify-content: center;
    aspect-ratio: 1;
    background: var(--color-surface);
    text-decoration: none;
    color: inherit;
    overflow: hidden;
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
  .tile__actions {
    display: flex;
    flex-wrap: wrap;
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
  }
</style>
