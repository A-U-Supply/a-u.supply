<!--
  LatentSlots — vertical stack of slot cards with drag-reorder. Each card holds
  its own files, pinned primaries per media type, notes, and threaded
  discussion. "+ Pull from index" attaches existing media into a slot.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import Sortable from 'sortablejs';
  import Uploader from './Uploader.svelte';
  import Threads from './Threads.svelte';
  import PullFromIndex from './PullFromIndex.svelte';

  type Props = {
    projectId: string;
    projectKind: string;
  };

  let { projectId, projectKind }: Props = $props();

  type Slot = {
    id: string;
    project_id: string;
    position: number;
    label: string;
    status: string;
    notes: string | null;
    notes_updated_at: string | null;
    pinned: Record<string, string>;
    thread_count?: number;
    repo_id?: string | null;
    repo_path?: string | null;
    repo_ref?: string | null;
    run_command?: string | null;
    created_at: string | null;
    updated_at: string | null;
  };

  type RepoMeta = {
    id: string;
    owner: string;
    repo_name: string;
    default_branch: string;
    blob_url_template: string;
  } | null;

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

  let slots = $state<Slot[]>([]);
  let itemsBySlot = $state<Record<string, Item[]>>({});
  let openSlot = $state<string | null>(null);
  let openSection = $state<'files' | 'notes' | 'threads' | 'runs' | null>(null);
  let error = $state<string | null>(null);
  let saveTimers = $state<Record<string, any>>({});

  let repoMeta = $state<RepoMeta>(null);
  let runsBySlot = $state<Record<string, any[]>>({});
  let runningSlots = $state<Set<string>>(new Set());

  // Inline "link a repo file" editor state — one slot at a time.
  let linkingSlot = $state<string | null>(null);
  let linkPath = $state('');
  let linkCmd = $state('');

  function startLink(slot: Slot) {
    linkingSlot = slot.id;
    linkPath = slot.repo_path || '';
    linkCmd = slot.run_command || '';
  }
  function cancelLink() {
    linkingSlot = null;
    linkPath = '';
    linkCmd = '';
  }
  async function saveLink(slot: Slot) {
    if (!repoMeta) return;
    const path = linkPath.trim();
    if (!path) return;
    await patchSlot(slot, {
      repo_id: repoMeta.id,
      repo_path: path,
      run_command: linkCmd.trim() || '',
    });
    cancelLink();
  }
  async function clearLink(slot: Slot) {
    if (!confirm('Unlink this slot from its repo file?')) return;
    await patchSlot(slot, { repo_id: '', repo_path: '', run_command: '' });
  }

  let pullOpenForSlot = $state<string | null>(null);
  let pullOpen = $state(false);

  function openPull(slotId: string) {
    pullOpenForSlot = slotId;
    pullOpen = true;
  }
  function closePull() {
    pullOpen = false;
    pullOpenForSlot = null;
  }

  let slotListEl: HTMLUListElement | null = $state(null);
  let sortable: Sortable | null = null;

  async function load() {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      slots = body.slots || [];
    } catch (e: any) {
      error = e?.message || 'Failed to load slots';
    }
    // Always reload the repo meta alongside slots so source links stay fresh.
    loadRepoMeta();
  }

  async function loadRepoMeta() {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/repo`,
        { credentials: 'include' },
      );
      if (!res.ok) return;
      const body = await res.json();
      repoMeta = body.repo
        ? {
            id: body.repo.id,
            owner: body.repo.owner,
            repo_name: body.repo.repo_name,
            default_branch: body.repo.default_branch,
            blob_url_template: body.repo.blob_url_template,
          }
        : null;
    } catch {}
  }

  function blobUrl(slot: Slot): string | null {
    if (!repoMeta || !slot.repo_path) return null;
    const ref = slot.repo_ref || repoMeta.default_branch || 'main';
    return repoMeta.blob_url_template
      .replace('{ref}', encodeURIComponent(ref))
      .replace(
        '{path}',
        slot.repo_path.split('/').map(encodeURIComponent).join('/'),
      );
  }

  async function runSlot(slot: Slot) {
    if (!repoMeta || !slot.repo_path) return;
    if (runningSlots.has(slot.id)) return;
    runningSlots = new Set([...runningSlots, slot.id]);
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slot.id)}/run`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        },
      );
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || 'Run failed');
      }
      const body = await res.json();
      const jobId = body.job_id;
      // Poll the job
      let attempts = 0;
      while (attempts < 240) {
        await new Promise((r) => setTimeout(r, 2500));
        attempts++;
        const jr = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
          credentials: 'include',
        });
        if (!jr.ok) break;
        const jb = await jr.json();
        if (
          jb.status === 'completed' ||
          jb.status === 'failed' ||
          jb.status === 'cancelled'
        ) {
          break;
        }
      }
      // Reload slot items + runs + slots (pin may have changed)
      await load();
      await loadItems(slot.id);
      await loadRuns(slot.id);
    } catch (e: any) {
      error = e?.message || 'Run failed';
    } finally {
      const next = new Set(runningSlots);
      next.delete(slot.id);
      runningSlots = next;
    }
  }

  async function loadRuns(slotId: string) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slotId)}/runs`,
        { credentials: 'include' },
      );
      if (!res.ok) return;
      const body = await res.json();
      runsBySlot = { ...runsBySlot, [slotId]: body.runs || [] };
    } catch {}
  }

  function fmtRunTime(iso: string | null | undefined): string {
    if (!iso) return '';
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

  async function loadItems(slotId: string) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/items?slot_id=${encodeURIComponent(slotId)}`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      itemsBySlot = { ...itemsBySlot, [slotId]: body.items || [] };
    } catch (e: any) {
      error = e?.message || 'Failed to load items';
    }
  }

  async function addSlot() {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      slots = [...slots, body];
    } catch (e: any) {
      error = e?.message || 'Failed to add slot';
    }
  }

  async function patchSlot(slot: Slot, payload: any) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slot.id)}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      slots = slots.map((s) => (s.id === slot.id ? body : s));
    } catch (e: any) {
      error = e?.message || 'Failed to update slot';
    }
  }

  function renameSlot(slot: Slot) {
    const label = prompt('Slot label:', slot.label);
    if (label && label !== slot.label) patchSlot(slot, { label });
  }

  function setStatus(slot: Slot, status: string) {
    if (status !== slot.status) patchSlot(slot, { status });
  }

  function scheduleNotesSave(slot: Slot) {
    if (saveTimers[slot.id]) clearTimeout(saveTimers[slot.id]);
    saveTimers = {
      ...saveTimers,
      [slot.id]: setTimeout(() => {
        patchSlot(slot, { notes: slot.notes ?? '' });
      }, 1500),
    };
    slots = [...slots];
  }

  async function deleteSlot(slot: Slot) {
    if (
      !confirm(
        `Delete slot "${slot.label}"? Files attached to it become loose; nothing is deleted from the index.`,
      )
    )
      return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slot.id)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      slots = slots.filter((s) => s.id !== slot.id);
    } catch (e: any) {
      error = e?.message || 'Failed to delete slot';
    }
  }

  function toggleSection(
    slotId: string,
    section: 'files' | 'notes' | 'threads' | 'runs',
  ) {
    if (openSlot === slotId && openSection === section) {
      openSlot = null;
      openSection = null;
    } else {
      openSlot = slotId;
      openSection = section;
      if (section === 'files' && !itemsBySlot[slotId]) loadItems(slotId);
      if (section === 'runs' && !runsBySlot[slotId]) loadRuns(slotId);
    }
  }

  async function setPin(slot: Slot, mediaType: string, mediaItemId: string) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slot.id)}/pin`,
        {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            media_type: mediaType,
            media_item_id: mediaItemId,
          }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      slots = slots.map((s) =>
        s.id === slot.id ? { ...s, pinned: body.pinned } : s,
      );
    } catch (e: any) {
      error = e?.message || 'Failed to pin';
    }
  }

  async function clearPin(slot: Slot, mediaType: string) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slot.id)}/pin/${encodeURIComponent(mediaType)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const next = { ...slot.pinned };
      delete next[mediaType];
      slots = slots.map((s) => (s.id === slot.id ? { ...s, pinned: next } : s));
    } catch (e: any) {
      error = e?.message || 'Failed to clear pin';
    }
  }

  async function detachItem(slot: Slot, item: Item) {
    if (!confirm('Detach this file from the slot?')) return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(item.id)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      itemsBySlot = {
        ...itemsBySlot,
        [slot.id]: (itemsBySlot[slot.id] || []).filter((i) => i.id !== item.id),
      };
    } catch (e: any) {
      error = e?.message || 'Failed to detach';
    }
  }

  async function persistOrder(orderedIds: string[]) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/reorder`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order: orderedIds }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      slots = body.slots || slots;
    } catch (e: any) {
      error = e?.message || 'Failed to reorder';
      await load(); // resync on failure
    }
  }

  function thumbUrl(mediaId: string): string {
    return `/api/media/${encodeURIComponent(mediaId)}/thumbnail?size=sm`;
  }

  function statusColor(s: string): string {
    return (
      (
        { forming: '#9ca3af', developing: '#b8860b', fixed: '#080' } as Record<
          string,
          string
        >
      )[s] || '#9ca3af'
    );
  }

  $effect(() => {
    if (projectId) load();
  });

  function onSlotsChanged() {
    load();
  }

  onMount(() => {
    document.addEventListener('latent:slots-changed', onSlotsChanged);
    return () => {
      document.removeEventListener('latent:slots-changed', onSlotsChanged);
      sortable?.destroy();
    };
  });

  $effect(() => {
    // (Re)bind Sortable whenever the slot list element exists. Destroy first
    // to avoid stacking handlers on hot-reload.
    if (!slotListEl) return;
    sortable?.destroy();
    sortable = Sortable.create(slotListEl, {
      handle: '.slot__drag',
      animation: 120,
      ghostClass: 'slot--ghost',
      onEnd: () => {
        if (!slotListEl) return;
        const ids = Array.from(
          slotListEl.querySelectorAll<HTMLLIElement>('.slot[data-slot-id]'),
        )
          .map((el) => el.dataset.slotId!)
          .filter(Boolean);
        if (ids.length) persistOrder(ids);
      },
    });
  });
</script>

<section class="slots">
  <header class="slots__head">
    <h2>Slots ({projectKind})</h2>
    <span class="muted">{slots.length}</span>
  </header>

  {#if error}
    <div class="notice notice--error">{error}</div>
  {/if}

  <ul class="slot-list" bind:this={slotListEl}>
    {#each slots as slot (slot.id)}
      <li class="slot" data-slot-id={slot.id}>
        <div class="slot__head">
          <button class="slot__drag" type="button" aria-label="Drag to reorder"
            >⋮⋮</button
          >
          <span class="slot__pos">#{slot.position}</span>
          <button
            class="slot__label"
            onclick={() => renameSlot(slot)}
            type="button"
            title="Click to rename">{slot.label}</button
          >
          <div class="slot__status">
            {#each ['forming', 'developing', 'fixed'] as st}
              <button
                class="status-pill"
                class:active={slot.status === st}
                style="--c: {statusColor(st)}"
                onclick={() => setStatus(slot, st)}
                type="button">{st}</button
              >
            {/each}
          </div>
          <div class="slot__actions">
            <button
              class="action-btn"
              type="button"
              onclick={() => toggleSection(slot.id, 'files')}>Files</button
            >
            <button
              class="action-btn"
              type="button"
              onclick={() => toggleSection(slot.id, 'notes')}>Notes</button
            >
            <button
              class="action-btn"
              type="button"
              onclick={() => toggleSection(slot.id, 'threads')}
              >Threads{#if slot.thread_count}
                ({slot.thread_count}){/if}</button
            >
            {#if slot.repo_path}
              <button
                class="action-btn"
                type="button"
                onclick={() => toggleSection(slot.id, 'runs')}>Runs</button
              >
            {/if}
            <button
              class="action-btn action-btn--danger"
              type="button"
              onclick={() => deleteSlot(slot)}>Delete</button
            >
          </div>
        </div>

        {#if repoMeta}
          <div class="slot__repo">
            {#if linkingSlot === slot.id}
              <input
                type="text"
                class="link-input"
                placeholder="tracks/01_wtf.py"
                bind:value={linkPath}
                onkeydown={(e) => {
                  if (e.key === 'Enter') saveLink(slot);
                  if (e.key === 'Escape') cancelLink();
                }}
              />
              <input
                type="text"
                class="link-input link-input--cmd"
                placeholder="python tracks/01_wtf.py (optional)"
                bind:value={linkCmd}
                onkeydown={(e) => {
                  if (e.key === 'Enter') saveLink(slot);
                  if (e.key === 'Escape') cancelLink();
                }}
              />
              <button class="btn-primary btn-run" type="button" onclick={() => saveLink(slot)}>Save</button>
              <button class="action-btn" type="button" onclick={cancelLink}>Cancel</button>
            {:else if slot.repo_path}
              <span class="muted">Source:</span>
              <a class="repo-path" href={blobUrl(slot) || '#'} target="_blank" rel="noopener"
                >{slot.repo_path}</a
              >
              {#if slot.repo_ref}
                <span class="ref-pill" title={slot.repo_ref}>{slot.repo_ref.slice(0, 7)}</span>
              {/if}
              <button class="link link--small" type="button" onclick={() => startLink(slot)}>edit</button>
              <button class="link link--small link--danger" type="button" onclick={() => clearLink(slot)}>unlink</button>
              <span class="spacer"></span>
              <button
                class="btn-primary btn-run"
                type="button"
                onclick={() => runSlot(slot)}
                disabled={runningSlots.has(slot.id)}
                >{runningSlots.has(slot.id) ? '⟳ Running…' : '▶ Run'}</button
              >
            {:else}
              <span class="muted">No source linked.</span>
              <button class="action-btn" type="button" onclick={() => startLink(slot)}>+ Link a repo file</button>
            {/if}
          </div>
        {/if}

        <div class="slot__pins">
          {#each ['image', 'audio', 'video', 'session'] as mt}
            <div class="pin" data-type={mt}>
              <span class="pin__label">{mt}</span>
              {#if slot.pinned[mt]}
                <a
                  class="pin__thumb"
                  href={`/admin/search/detail?id=${encodeURIComponent(slot.pinned[mt])}`}
                >
                  {#if mt === 'image'}
                    <img src={thumbUrl(slot.pinned[mt])} alt="" />
                  {:else}
                    <span class="icon">{mt}</span>
                  {/if}
                </a>
                <button
                  class="link"
                  type="button"
                  onclick={() => clearPin(slot, mt)}>Unpin</button
                >
              {:else}
                <div class="pin__empty">—</div>
              {/if}
            </div>
          {/each}
        </div>

        {#if openSlot === slot.id && openSection === 'files'}
          <div class="slot__panel">
            <Uploader
              destination="project"
              {projectId}
              slotId={slot.id}
              compact={true}
              onUploaded={() => loadItems(slot.id)}
            />
            <div class="slot__panel-actions">
              <button
                class="action-btn"
                type="button"
                onclick={() => openPull(slot.id)}>+ Pull from index</button
              >
            </div>
            {#if (itemsBySlot[slot.id] || []).length === 0}
              <div class="muted">No files in this slot yet.</div>
            {:else}
              <ul class="grid">
                {#each itemsBySlot[slot.id] as it (it.id)}
                  <li class="tile" data-type={it.media?.media_type}>
                    <a
                      class="tile__thumb"
                      href={`/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`}
                    >
                      {#if it.media?.media_type === 'image'}
                        <img
                          src={thumbUrl(it.media_item_id)}
                          alt={it.media?.filename}
                        />
                      {:else if it.media?.media_type === 'session'}
                        <span class="icon">▣ session</span>
                      {:else}
                        <span class="icon"
                          >{it.media?.media_type || 'file'}</span
                        >
                      {/if}
                    </a>
                    <div class="tile__name" title={it.media?.filename}>
                      {it.media?.filename || '?'}
                    </div>
                    <div class="tile__actions">
                      {#if it.media?.media_type && ['image', 'audio', 'video', 'session'].includes(it.media.media_type)}
                        <button
                          class="action-btn"
                          type="button"
                          onclick={() =>
                            setPin(
                              slot,
                              it.media!.media_type,
                              it.media_item_id,
                            )}>Pin</button
                        >
                      {/if}
                      <button
                        class="action-btn action-btn--danger"
                        type="button"
                        onclick={() => detachItem(slot, it)}>Detach</button
                      >
                    </div>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/if}

        {#if openSlot === slot.id && openSection === 'notes'}
          <div class="slot__panel">
            <textarea
              rows="6"
              placeholder="Slot notes — lyrics, mix notes, ideas. Autosaves."
              bind:value={slot.notes}
              oninput={() => scheduleNotesSave(slot)}
            ></textarea>
          </div>
        {/if}

        {#if openSlot === slot.id && openSection === 'threads'}
          <div class="slot__panel">
            <Threads
              anchorType="slot"
              anchorId={slot.id}
              title="Slot discussion"
              compact={true}
            />
          </div>
        {/if}

        {#if openSlot === slot.id && openSection === 'runs'}
          <div class="slot__panel">
            {#if (runsBySlot[slot.id] || []).length === 0}
              <div class="muted">No runs yet — hit ▶ Run above.</div>
            {:else}
              <ul class="runs-list">
                {#each runsBySlot[slot.id] as r (r.id)}
                  <li class="run" data-ok={r.exit_code === 0}>
                    <div class="run__head">
                      <span class="run__sha" title={r.ref}
                        >{(r.ref || '').slice(0, 7)}</span
                      >
                      <span class="muted">{fmtRunTime(r.started_at)}</span>
                      {#if r.exit_code === 0}
                        <span class="run__ok">✓ ok</span>
                      {:else if r.exit_code !== null && r.exit_code !== undefined}
                        <span class="run__err">✗ exit {r.exit_code}</span>
                      {:else if r.finished_at == null}
                        <span class="muted">running…</span>
                      {/if}
                      <span class="run__outputs"
                        >{(r.outputs || []).length} output{(r.outputs || [])
                          .length === 1
                          ? ''
                          : 's'}</span
                      >
                    </div>
                    <code class="run__cmd">{r.command}</code>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/if}
      </li>
    {/each}
  </ul>

  <div class="slots__footer">
    <button class="action-btn" type="button" onclick={addSlot}
      >+ Add slot</button
    >
  </div>

  <PullFromIndex
    bind:open={pullOpen}
    {projectId}
    slotId={pullOpenForSlot}
    onAttached={() => pullOpenForSlot && loadItems(pullOpenForSlot)}
    onClose={closePull}
  />
</section>

<style>
  .slots {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .slots__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    border-bottom: 2px solid var(--color-text);
    padding-bottom: var(--space-xs);
  }
  .slots__head h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .slot-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .slot {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .slot--ghost {
    opacity: 0.3;
  }
  .slot__head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-sm);
    padding: 6px var(--space-sm);
    border-bottom: 1px dashed var(--color-border);
  }
  .slot__drag {
    background: transparent;
    border: 0;
    color: var(--color-muted);
    font-size: var(--text-base);
    cursor: grab;
    padding: 0 4px;
    line-height: 1;
    letter-spacing: -1px;
  }
  .slot__drag:active {
    cursor: grabbing;
  }
  .slot__pos {
    font-family: var(--font-mono);
    color: var(--color-muted);
    font-size: var(--text-sm);
  }
  .slot__label {
    background: transparent;
    border: 0;
    color: inherit;
    font: inherit;
    font-weight: bold;
    cursor: text;
    flex: 1;
    min-width: 80px;
    text-align: left;
  }
  .slot__status {
    display: flex;
    gap: 4px;
  }
  .status-pill {
    background: transparent;
    color: var(--c);
    border: 1px solid var(--c);
    padding: 2px 8px;
    font: inherit;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    cursor: pointer;
  }
  .status-pill.active {
    background: var(--c);
    color: var(--color-bg);
  }
  .slot__actions {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  .slot__pins {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    padding: var(--space-sm);
  }
  .pin {
    border: 1px dashed var(--color-border);
    padding: 6px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
  }
  .pin__label {
    text-transform: uppercase;
    letter-spacing: 1pt;
    font-size: 0.65rem;
    color: var(--color-muted);
  }
  .pin__thumb {
    display: block;
    width: 72px;
    height: 72px;
    background: #f4f4f4;
    text-decoration: none;
    color: inherit;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .pin__thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .pin__empty {
    width: 72px;
    height: 72px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--color-muted);
  }
  .slot__panel {
    padding: var(--space-sm);
    border-top: 1px dashed var(--color-border);
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .slot__panel-actions {
    display: flex;
    gap: var(--space-sm);
  }
  .slot__repo {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px var(--space-sm);
    border-top: 1px dashed var(--color-border);
    background: #fafafa;
    flex-wrap: wrap;
    font-size: var(--text-sm);
  }
  .slot__repo .muted {
    text-transform: uppercase;
    letter-spacing: 1pt;
    font-size: 0.65rem;
  }
  .repo-path {
    color: var(--color-accent);
    font-family: var(--font-mono);
    word-break: break-all;
  }
  .ref-pill {
    font-family: var(--font-mono);
    color: var(--color-muted);
    font-size: 0.7rem;
    border: 1px dashed var(--color-border);
    padding: 0 4px;
    background: var(--color-bg);
  }
  .spacer {
    flex: 1;
  }
  .btn-run {
    padding: 3px 10px;
    font-size: 0.75rem;
  }
  .link--small {
    font-size: 0.7rem;
  }
  .link--danger {
    color: #c00;
  }
  .link-input {
    flex: 1;
    min-width: 160px;
    padding: 4px 8px;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .link-input--cmd {
    flex: 1.5;
    min-width: 200px;
  }
  .runs-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .run {
    border: 1px solid var(--color-border);
    padding: 6px 8px;
    background: var(--color-bg);
  }
  .run[data-ok='false'] {
    border-color: #c00;
  }
  .run__head {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: var(--text-sm);
  }
  .run__sha {
    font-family: var(--font-mono);
    background: var(--color-text);
    color: var(--color-bg);
    padding: 0 6px;
    font-size: 0.7rem;
  }
  .run__ok {
    color: #080;
  }
  .run__err {
    color: #c00;
  }
  .run__outputs {
    color: var(--color-muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .run__cmd {
    display: block;
    margin-top: 4px;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    color: var(--color-muted);
    word-break: break-all;
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
    display: flex;
    flex-direction: column;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .tile__thumb {
    display: flex;
    align-items: center;
    justify-content: center;
    aspect-ratio: 1;
    background: #f4f4f4;
    text-decoration: none;
    color: inherit;
    overflow: hidden;
  }
  .tile__thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .icon {
    color: var(--color-muted);
    text-transform: uppercase;
    letter-spacing: 1pt;
    font-size: var(--text-sm);
  }
  .tile__name {
    padding: 4px 6px;
    font-size: var(--text-sm);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tile__actions {
    padding: 0 6px 6px;
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  textarea {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 8px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    width: 100%;
    box-sizing: border-box;
  }
  .link {
    background: transparent;
    border: 0;
    color: var(--color-accent);
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
    font: inherit;
    font-size: 0.7rem;
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
    .slot__pins {
      grid-template-columns: repeat(2, 1fr);
    }
    .slot__head {
      gap: 6px;
    }
    .slot__actions {
      flex-basis: 100%;
      justify-content: flex-end;
    }
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    }
  }
</style>
