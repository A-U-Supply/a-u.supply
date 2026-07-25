<!--
  LatentLinks — pinned URLs anchored to a Latent or one of its slots, or
  in read-only mode the cross-Latent set related to a media item.

  Modes (pick one):
    - { projectId }                    → Latent-level links
    - { projectId, slotId }            → links on one slot inside a Latent
    - { mediaItemId, readOnly: true }  → every Latent/slot link related to
                                          this media item (search detail page)

  Slack permalinks get a Slack 🟪 icon; Drive, SoundCloud, YouTube, GitHub,
  Dropbox are similarly badged. Anything else is a generic link chip.
-->
<script lang="ts">
  import { isPhone } from '../lib/viewport.svelte.ts';
  import LatentStyleButton from './LatentStyleButton.svelte';

  type Props = {
    projectId?: string;
    slotId?: string;
    mediaItemId?: string;
    readOnly?: boolean;
    title?: string;
    compact?: boolean;
    /** Latent detail page only: opts the header into the section style
     * grammar (band + Style button). Requires projectId. */
    styleKey?: string | null;
  };

  let {
    projectId,
    slotId,
    mediaItemId,
    readOnly = false,
    title,
    compact = false,
    styleKey = null,
  }: Props = $props();

  type Link = {
    id: string;
    project_id?: string;
    slot_id?: string | null;
    url: string;
    label: string | null;
    kind: string;
    position: number;
    project_slug?: string | null;
    project_name?: string | null;
    slot_label?: string | null;
    slot_position?: number | null;
  };

  let links = $state<Link[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let adding = $state(false);
  let addUrl = $state('');
  let addLabel = $state('');
  let open = $state(false);
  /* Only the page-level Links section collapses. The per-slot instance is
     already inside a slot's Links tab; a second layer there is a trap. */
  let collapsible = $derived(isPhone() && !compact && !!title);
  let editingId = $state<string | null>(null);
  let editUrl = $state('');
  let editLabel = $state('');
  let savingEdit = $state(false);

  // Slack icon — keep it inline so it doesn't pull a font.
  const SLACK_SVG = `<svg viewBox="0 0 122.8 122.8" width="12" height="12" aria-hidden="true"><path d="M25.8 77.6c0 7.1-5.8 12.9-12.9 12.9S0 84.7 0 77.6s5.8-12.9 12.9-12.9h12.9v12.9zm6.5 0c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9v32.3c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V77.6z" fill="#e01e5a"/><path d="M45.2 25.8c-7.1 0-12.9-5.8-12.9-12.9S38.1 0 45.2 0s12.9 5.8 12.9 12.9v12.9H45.2zm0 6.5c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H12.9C5.8 58.1 0 52.3 0 45.2s5.8-12.9 12.9-12.9h32.3z" fill="#ecb22e"/><path d="M97 45.2c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9-5.8 12.9-12.9 12.9H97V45.2zm-6.5 0c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V12.9C64.7 5.8 70.5 0 77.6 0s12.9 5.8 12.9 12.9v32.3z" fill="#2eb67d"/><path d="M77.6 97c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9-12.9-5.8-12.9-12.9V97h12.9zm0-6.5c-7.1 0-12.9-5.8-12.9-12.9s5.8-12.9 12.9-12.9h32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H77.6z" fill="#36c5f0"/></svg>`;

  function kindIcon(kind: string): string {
    switch (kind) {
      case 'slack':
        return SLACK_SVG;
      case 'drive':
        return '📁';
      case 'dropbox':
        return '🗂';
      case 'soundcloud':
        return '☁';
      case 'youtube':
        return '▶';
      case 'github':
        return '⛬';
      default:
        return '🔗';
    }
  }

  function shortLabel(link: Link): string {
    if (link.label) return link.label;
    if (link.kind === 'slack') {
      // …/archives/CHANNEL/p123 → "Slack #CHANNEL"
      const m = link.url.match(/\/archives\/([A-Z0-9]+)/);
      return m ? `Slack #${m[1].slice(0, 8)}` : 'Slack message';
    }
    try {
      const u = new URL(link.url);
      const path = u.pathname.length > 1 ? u.pathname : '';
      return (u.host + path).replace(/^www\./, '').slice(0, 48);
    } catch {
      return link.url.slice(0, 48);
    }
  }

  async function load() {
    loading = true;
    error = null;
    try {
      let url: string;
      if (mediaItemId) {
        url = `/api/media/${encodeURIComponent(mediaItemId)}/latent-links`;
      } else if (projectId) {
        const params = new URLSearchParams();
        if (slotId !== undefined) {
          params.set('slot_id', slotId || '');
        }
        url = `/api/projects/${encodeURIComponent(projectId)}/links?${params.toString()}`;
      } else {
        loading = false;
        return;
      }
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      links = body.links || [];
    } catch (e: any) {
      error = e?.message || 'Failed to load links';
    } finally {
      loading = false;
    }
  }

  async function addLink() {
    if (!projectId || !addUrl.trim()) return;
    adding = true;
    error = null;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/links`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: addUrl.trim(),
            label: addLabel.trim() || null,
            slot_id: slotId || null,
          }),
        },
      );
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || 'Add failed');
      }
      addUrl = '';
      addLabel = '';
      await load();
    } catch (e: any) {
      error = e?.message || 'Add failed';
    } finally {
      adding = false;
    }
  }

  function beginEdit(link: Link) {
    editingId = link.id;
    editUrl = link.url;
    editLabel = link.label || '';
  }

  function cancelEdit() {
    editingId = null;
    editUrl = '';
    editLabel = '';
  }

  async function saveEdit(link: Link) {
    if (!link.project_id) return;
    if (!editUrl.trim()) return;
    savingEdit = true;
    error = null;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(link.project_id)}/links/${encodeURIComponent(link.id)}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: editUrl.trim(),
            label: editLabel.trim(),
          }),
        },
      );
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `Save failed (${res.status})`);
      }
      const updated = await res.json();
      links = links.map((l) => (l.id === link.id ? { ...l, ...updated } : l));
      cancelEdit();
    } catch (e: any) {
      error = e?.message || 'Save failed';
    } finally {
      savingEdit = false;
    }
  }

  async function removeLink(link: Link) {
    if (!projectId || !link.project_id) return;
    if (!confirm('Remove this link?')) return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(link.project_id)}/links/${encodeURIComponent(link.id)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      links = links.filter((l) => l.id !== link.id);
    } catch (e: any) {
      error = e?.message || 'Remove failed';
    }
  }

  $effect(() => {
    if (projectId || mediaItemId) load();
  });
</script>

<section class="links" class:compact>
  {#if title}
    <header class="links__head" class:latent-band={!!styleKey}>
      {#if collapsible}
        <button
          class="sec-summary"
          type="button"
          aria-expanded={open}
          onclick={() => (open = !open)}
        >
          <span class="sec-summary__caret" aria-hidden="true"
            >{open ? '▾' : '▸'}</span
          >
          <span class="sec-summary__label">{title}</span>
          <span class="sec-summary__meta"
            >{links.length} link{links.length === 1 ? '' : 's'}</span
          >
        </button>
      {:else}
        <h3>{title}</h3>
        <span class="muted">{links.length}</span>
      {/if}
      {#if styleKey && projectId}
        <LatentStyleButton
          {projectId}
          scope="section"
          sectionKey={styleKey}
          push
        />
      {/if}
    </header>
  {/if}

  {#if collapsible && !open}
    <!-- collapsed: the summary above carries the count -->
  {:else if loading && links.length === 0}
    <span class="muted">…</span>
  {:else if links.length === 0 && readOnly}
    <span class="muted">No related Latent links.</span>
  {:else if links.length > 0}
    <ul class="chips">
      {#each links as link (link.id)}
        {#if editingId === link.id && !readOnly}
          <li class="chip chip--editing" data-kind={link.kind}>
            <form
              class="chip__edit"
              onsubmit={(e) => {
                e.preventDefault();
                saveEdit(link);
              }}
            >
              <input
                type="url"
                placeholder="URL"
                bind:value={editUrl}
                required
              />
              <input
                type="text"
                placeholder="Label (optional)"
                bind:value={editLabel}
              />
              <button class="action-btn" type="submit" disabled={savingEdit}
                >Save</button
              >
              <button class="action-btn" type="button" onclick={cancelEdit}
                >Cancel</button
              >
            </form>
          </li>
        {:else}
          <li class="chip" data-kind={link.kind}>
            <a href={link.url} target="_blank" rel="noopener" title={link.url}>
              <span class="chip__icon">{@html kindIcon(link.kind)}</span>
              <span class="chip__label">
                {shortLabel(link)}
                {#if readOnly && (link.project_name || link.slot_label)}
                  <span class="chip__context"
                    >· {link.project_name || ''}
                    {#if link.slot_label}
                      / {link.slot_label}
                    {/if}
                  </span>
                {/if}
              </span>
            </a>
            {#if !readOnly}
              <button
                class="chip__edit-btn"
                type="button"
                title="Edit"
                onclick={() => beginEdit(link)}>✎</button
              >
              <button
                class="chip__remove"
                type="button"
                title="Remove"
                onclick={() => removeLink(link)}>×</button
              >
            {/if}
          </li>
        {/if}
      {/each}
    </ul>
  {/if}

  {#if !readOnly && projectId}
    <form
      class="link-add"
      onsubmit={(e) => {
        e.preventDefault();
        addLink();
      }}
    >
      <input
        type="url"
        placeholder="Paste a Slack permalink, Drive folder, anything…"
        bind:value={addUrl}
        required
      />
      <input type="text" placeholder="Label (optional)" bind:value={addLabel} />
      <button class="action-btn" type="submit" disabled={adding}
        >+ Add link</button
      >
    </form>
  {/if}

  {#if error}
    <div class="notice notice--error">{error}</div>
  {/if}
</section>

<style>
  .links {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .links__head {
    display: flex;
    align-items: baseline;
    gap: var(--space-sm);
  }
  .links__head h3 {
    margin: 0;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
  }
  .chips {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    font-size: var(--text-sm);
  }
  .chip a {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 8px;
    text-decoration: none;
    color: var(--color-text);
  }
  .chip a:hover {
    background: var(--color-surface);
  }
  .chip[data-kind='slack'] {
    border-color: #4a154b;
  }
  .chip__icon {
    display: inline-flex;
    align-items: center;
    line-height: 1;
  }
  .chip__label {
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 320px;
    white-space: nowrap;
  }
  .chip__context {
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .chip__remove,
  .chip__edit-btn {
    background: transparent;
    border: 0;
    color: var(--color-muted);
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 0 6px;
    border-left: 1px solid var(--color-border);
  }
  .chip__remove:hover {
    color: #c00;
  }
  .chip__edit-btn:hover {
    color: var(--color-text);
  }
  .chip--editing {
    flex: 1 1 100%;
    padding: 4px;
  }
  .chip__edit {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    align-items: center;
    width: 100%;
  }
  .chip__edit input[type='url'] {
    flex: 2;
    min-width: 200px;
  }
  .chip__edit input[type='text'] {
    flex: 1;
    min-width: 120px;
  }
  .chip__edit input {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 4px 8px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .link-add {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    align-items: center;
  }
  .link-add input[type='url'] {
    flex: 2;
    min-width: 220px;
  }
  .link-add input[type='text'] {
    flex: 1;
    min-width: 140px;
  }
  .link-add input {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 4px 8px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .muted {
    color: var(--color-muted);
    font-size: var(--text-sm);
  }
  .notice--error {
    color: #c00;
    font-size: var(--text-sm);
  }
</style>
