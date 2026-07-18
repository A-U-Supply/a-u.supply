<!--
  LatentRepoStrip — the GitHub-repo header row that sits at the top of a
  Latent detail page. Shows the linked repo + last sync, exposes a sync
  button + manifest-apply prompt + unlink. If no repo is linked, shows a
  small "Link a GitHub repo" CTA that opens the modal.
-->
<script lang="ts">
  import LinkRepoModal from './LinkRepoModal.svelte';
  import LatentStyleButton from './LatentStyleButton.svelte';

  type Props = {
    projectId: string;
    styleKey?: string | null;
  };

  let { projectId, styleKey = null }: Props = $props();

  type Repo = {
    id: string;
    project_id: string;
    url: string;
    owner: string;
    repo_name: string;
    default_branch: string;
    visibility: string;
    last_sha: string | null;
    last_synced_at: string | null;
    has_manifest: boolean;
    webhook_secret: string | null;
  };

  let repo = $state<Repo | null>(null);
  let loaded = $state(false);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let modalOpen = $state(false);
  let newTracks = $state<any[]>([]);
  let applying = $state(false);
  let webhookOpen = $state(false);

  async function load() {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/repo`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      repo = body.repo;
      loaded = true;
      if (repo?.has_manifest) loadManifestDiff();
    } catch (e: any) {
      error = e?.message || 'Failed';
      loaded = true;
    }
  }

  async function loadManifestDiff() {
    if (!repo) return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/repo/manifest`,
        { credentials: 'include' },
      );
      if (!res.ok) return;
      const body = await res.json();
      newTracks = body.new_tracks || [];
    } catch {}
  }

  async function sync() {
    if (!repo || busy) return;
    busy = true;
    error = null;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/repo/sync`,
        { method: 'POST', credentials: 'include' },
      );
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `Sync failed (${res.status})`);
      }
      const body = await res.json();
      repo = body.repo;
      await loadManifestDiff();
    } catch (e: any) {
      error = e?.message || 'Sync failed';
    } finally {
      busy = false;
    }
  }

  async function applyManifest() {
    if (!repo || applying) return;
    applying = true;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/repo/manifest/apply`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ import_new: true }),
        },
      );
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || 'Apply failed');
      }
      newTracks = [];
      // Tell parent page to refresh slots
      document.dispatchEvent(new CustomEvent('latent:slots-changed'));
    } catch (e: any) {
      error = e?.message || 'Apply failed';
    } finally {
      applying = false;
    }
  }

  async function unlink() {
    if (!repo) return;
    if (
      !confirm(
        'Unlink this repo from the Latent? Slot file associations clear; nothing is deleted from GitHub.',
      )
    )
      return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/repo`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error('Unlink failed');
      repo = null;
      newTracks = [];
      document.dispatchEvent(new CustomEvent('latent:slots-changed'));
    } catch (e: any) {
      error = e?.message || 'Unlink failed';
    }
  }

  function fmtTime(iso: string | null): string {
    if (!iso) return 'never';
    try {
      const d = new Date(iso);
      const mins = Math.round((Date.now() - d.getTime()) / 60000);
      if (mins < 1) return 'just now';
      if (mins < 60) return `${mins}m ago`;
      if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
      return d.toLocaleString();
    } catch {
      return iso;
    }
  }

  $effect(() => {
    if (projectId) load();
  });
</script>

<section class="repo-strip" class:latent-band={!!styleKey}>
  {#if !loaded}
    <div class="placeholder">…</div>
  {:else if repo}
    <div class="row">
      <div class="left">
        <span class="icon" aria-hidden="true">⛬</span>
        <a class="repo-link" href={repo.url} target="_blank" rel="noopener">
          {repo.owner}/{repo.repo_name}
        </a>
        <span class="branch">{repo.default_branch}</span>
        {#if repo.visibility === 'private'}
          <span class="vis-pill">private</span>
        {/if}
        <span class="sep">·</span>
        <span class="muted">synced {fmtTime(repo.last_synced_at)}</span>
        {#if repo.last_sha}
          <span class="sha" title={repo.last_sha}
            >{repo.last_sha.slice(0, 7)}</span
          >
        {/if}
      </div>
      <div class="right">
        <button class="action-btn" type="button" onclick={sync} disabled={busy}
          >{busy ? 'Syncing…' : 'Sync'}</button
        >
        <button
          class="action-btn"
          type="button"
          onclick={() => (webhookOpen = !webhookOpen)}>Webhook</button
        >
        <button
          class="action-btn action-btn--danger"
          type="button"
          onclick={unlink}>Unlink</button
        >
        {#if styleKey}
          <LatentStyleButton
            {projectId}
            scope="section"
            sectionKey={styleKey}
          />
        {/if}
      </div>
    </div>

    {#if newTracks.length}
      <div class="banner">
        <span>
          Manifest has <strong>{newTracks.length}</strong>
          track{newTracks.length === 1 ? '' : 's'} not yet in this Latent.
        </span>
        <button
          class="btn-primary"
          type="button"
          onclick={applyManifest}
          disabled={applying}>{applying ? 'Importing…' : 'Import'}</button
        >
      </div>
    {/if}

    {#if webhookOpen && repo.webhook_secret}
      <div class="webhook">
        <div>
          <strong>Webhook URL:</strong>
          <code>https://a-u.supply/api/github/webhook</code>
        </div>
        <div>
          <strong>Content type:</strong> <code>application/json</code>
        </div>
        <div>
          <strong>Secret:</strong>
          <code class="secret">{repo.webhook_secret}</code>
        </div>
        <div class="muted">
          Add a webhook in your GitHub repo settings with these values; push
          events will post a thread to this Latent.
        </div>
      </div>
    {/if}

    {#if error}
      <div class="notice notice--error">{error}</div>
    {/if}
  {:else}
    <div class="empty">
      <span class="icon">⛬</span>
      <span class="muted">No repo linked.</span>
      <button
        class="action-btn"
        type="button"
        onclick={() => (modalOpen = true)}>Link a GitHub repo</button
      >
      {#if styleKey}
        <LatentStyleButton
          {projectId}
          scope="section"
          sectionKey={styleKey}
          push
        />
      {/if}
    </div>
  {/if}

  <LinkRepoModal
    bind:open={modalOpen}
    {projectId}
    onLinked={() => {
      modalOpen = false;
      load();
    }}
  />
</section>

<style>
  .repo-strip {
    display: flex;
    flex-direction: column;
    gap: 6px;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    padding: var(--space-sm);
  }
  /* The strip has no separate header — the whole row IS the head band, so
     the band wash applies to the root. Re-declared here (not just the
     global .latent-band) because this scoped background rule would
     otherwise win on specificity. Recipe must match global.css. */
  .repo-strip.latent-band {
    background: color-mix(
      in srgb,
      var(--sec-head, var(--sec-accent, transparent)) 12%,
      var(--color-surface)
    );
  }
  .placeholder {
    color: var(--color-muted);
    font-size: var(--text-sm);
  }
  .row {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex-wrap: wrap;
  }
  .left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    flex: 1;
    min-width: 0;
    font-size: var(--text-sm);
  }
  .icon {
    font-size: var(--text-base);
  }
  .repo-link {
    font-weight: 700;
    color: var(--color-text);
    text-decoration: none;
    border-bottom: 1px solid var(--color-border);
  }
  .repo-link:hover {
    border-color: var(--color-text);
  }
  .branch {
    background: var(--color-text);
    color: var(--color-bg);
    padding: 1px 6px;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .vis-pill {
    border: 1px solid var(--color-accent);
    color: var(--color-accent);
    padding: 1px 6px;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .sep {
    color: var(--color-muted);
  }
  .muted {
    color: var(--color-muted);
    font-size: var(--text-sm);
  }
  .sha {
    font-family: var(--font-mono);
    color: var(--color-muted);
    font-size: 0.7rem;
    border: 1px dashed var(--color-border);
    padding: 0 4px;
  }
  .right {
    display: flex;
    gap: 4px;
  }
  .banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
    padding: var(--space-sm);
    background: rgba(184, 134, 11, 0.08);
    border-left: 4px solid var(--color-accent);
  }
  .empty {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex-wrap: wrap;
  }
  .webhook {
    background: var(--color-surface);
    border: 1px dashed var(--color-border);
    padding: var(--space-sm);
    font-size: var(--text-sm);
    display: flex;
    flex-direction: column;
    gap: 4px;
    word-break: break-all;
  }
  .webhook code {
    background: var(--color-bg);
    padding: 1px 4px;
    font-family: var(--font-mono);
  }
  .secret {
    user-select: all;
  }
  .notice {
    padding: 6px 10px;
    border: 1px solid #c00;
    color: #c00;
    font-size: var(--text-sm);
  }
  @media (max-width: 640px) {
    .row {
      flex-direction: column;
      align-items: flex-start;
    }
    .right {
      width: 100%;
      flex-wrap: wrap;
    }
  }
</style>
