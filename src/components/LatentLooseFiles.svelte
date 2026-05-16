<!--
  LatentLooseFiles — the grid of files attached to a Latent at the loose level
  (i.e. not inside any slot). Renders the unified Uploader docked at the top.
-->
<script lang="ts">
  import Uploader from './Uploader.svelte';

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
  let host: HTMLElement | null = $state(null);

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
    return `/api/media/${encodeURIComponent(mediaId)}/thumbnail?size=240`;
  }

  function onUploaded() {
    load();
  }

  $effect(() => {
    if (projectId) load();
  });
</script>

<section class="loose" bind:this={host} onuploaded={onUploaded}>
  <header class="loose__head">
    <h2>Loose files</h2>
    <span class="muted">{items.length}</span>
  </header>

  <Uploader destination="project" {projectId} compact={true} />

  {#if error}
    <div class="error">{error}</div>
  {/if}
  {#if loading && items.length === 0}
    <div class="muted">Loading…</div>
  {:else if items.length === 0}
    <div class="muted">
      No loose files yet — drop something above or attach from the Stacks.
    </div>
  {:else}
    <ul class="grid">
      {#each items as it (it.id)}
        <li class="tile" data-type={it.media?.media_type}>
          <a
            class="tile__thumb"
            href={`/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`}
          >
            {#if it.media?.media_type === 'image'}
              <img src={thumbUrl(it.media_item_id)} alt={it.media?.filename} />
            {:else if it.media?.media_type === 'session'}
              <span class="icon">▣ session</span>
            {:else}
              <span class="icon">{it.media?.media_type || 'file'}</span>
            {/if}
          </a>
          <div class="tile__info">
            <div class="tile__name" title={it.media?.filename}>
              {it.media?.filename || '(unknown)'}
            </div>
            <div class="tile__meta">
              {it.media?.media_type || ''}
              {#if it.media?.file_size_bytes}
                · {formatSize(it.media.file_size_bytes)}
              {/if}
            </div>
          </div>
          <div class="tile__actions">
            <button class="link" type="button" onclick={() => detach(it)}
              >Detach</button
            >
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .loose {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 0.5rem);
  }
  .loose__head {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .loose__head h2 {
    margin: 0;
  }
  .grid {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px;
  }
  .tile {
    border: 2px solid var(--color-border, #333);
    background: rgba(255, 255, 255, 0.02);
    display: flex;
    flex-direction: column;
  }
  .tile__thumb {
    display: block;
    aspect-ratio: 1;
    background: #000;
    display: flex;
    align-items: center;
    justify-content: center;
    text-decoration: none;
    color: inherit;
  }
  .tile__thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .icon {
    color: var(--color-muted, #888);
    text-transform: uppercase;
    letter-spacing: 1pt;
    font-size: var(--text-sm, 0.85rem);
  }
  .tile__info {
    padding: 6px 8px;
    min-width: 0;
  }
  .tile__name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--text-sm, 0.85rem);
  }
  .tile__meta {
    color: var(--color-muted, #888);
    font-size: var(--text-sm, 0.85rem);
  }
  .tile__actions {
    padding: 0 8px 8px;
  }
  .link {
    background: transparent;
    border: 0;
    color: var(--color-accent, #b8860b);
    cursor: pointer;
    padding: 0;
    text-decoration: underline;
    font: inherit;
  }
  .muted {
    color: var(--color-muted, #888);
    font-size: var(--text-sm, 0.85rem);
  }
  .error {
    padding: 8px 10px;
    border: 2px solid #ef4444;
    color: #fca5a5;
    font-size: var(--text-sm, 0.85rem);
  }
</style>
