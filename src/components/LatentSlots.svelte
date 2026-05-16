<!--
  LatentSlots — vertical stack of slot cards. Each card holds its own files,
  pinned primaries per media type, notes, and a discussion badge that opens
  the slot's threaded discussion inline.
-->
<script lang="ts">
  import Uploader from './Uploader.svelte';
  import Threads from './Threads.svelte';

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
    created_at: string | null;
    updated_at: string | null;
  };

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
  let openSection = $state<'files' | 'notes' | 'threads' | null>(null);
  let error = $state<string | null>(null);
  let saveTimers = $state<Record<string, any>>({});

  async function load() {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      slots = body.slots || [];
      // load items per slot lazily on expand
    } catch (e: any) {
      error = e?.message || 'Failed to load slots';
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
    section: 'files' | 'notes' | 'threads',
  ) {
    if (openSlot === slotId && openSection === section) {
      openSlot = null;
      openSection = null;
    } else {
      openSlot = slotId;
      openSection = section;
      if (section === 'files' && !itemsBySlot[slotId]) loadItems(slotId);
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

  function thumbUrl(mediaId: string): string {
    return `/api/media/${encodeURIComponent(mediaId)}/thumbnail?size=200`;
  }

  function statusColor(s: string): string {
    return (
      {
        forming: '#9ca3af',
        developing: '#fbbf24',
        fixed: '#4ade80',
      }[s] || '#9ca3af'
    );
  }

  $effect(() => {
    if (projectId) load();
  });
</script>

<section class="slots">
  <header class="slots__head">
    <h2>Slots</h2>
    <span class="muted">{slots.length}</span>
  </header>

  {#if error}
    <div class="error">{error}</div>
  {/if}

  <ul class="slot-list">
    {#each slots as slot (slot.id)}
      <li class="slot">
        <div class="slot__head">
          <span class="slot__pos">#{slot.position}</span>
          <button
            class="slot__label"
            onclick={() => renameSlot(slot)}
            type="button">{slot.label}</button
          >
          <div class="slot__status">
            {#each ['forming', 'developing', 'fixed'] as st}
              <button
                class="pill"
                class:active={slot.status === st}
                style="--c: {statusColor(st)}"
                onclick={() => setStatus(slot, st)}
                type="button">{st}</button
              >
            {/each}
          </div>
          <div class="slot__actions">
            <button
              class="link"
              type="button"
              onclick={() => toggleSection(slot.id, 'files')}>Files</button
            >
            <button
              class="link"
              type="button"
              onclick={() => toggleSection(slot.id, 'notes')}>Notes</button
            >
            <button
              class="link"
              type="button"
              onclick={() => toggleSection(slot.id, 'threads')}>Threads</button
            >
            <button
              class="link link--danger"
              type="button"
              onclick={() => deleteSlot(slot)}>Delete</button
            >
          </div>
        </div>

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
                  class="link link--small"
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
            />
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
                      {:else}
                        <span class="icon"
                          >{it.media?.media_type || 'file'}</span
                        >
                      {/if}
                    </a>
                    <div class="tile__name">{it.media?.filename || '?'}</div>
                    <div class="tile__actions">
                      {#if it.media?.media_type && ['image', 'audio', 'video', 'session'].includes(it.media.media_type)}
                        <button
                          class="link link--small"
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
                        class="link link--small"
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
            />
          </div>
        {/if}
      </li>
    {/each}
  </ul>

  <div class="slots__footer">
    <button class="btn" type="button" onclick={addSlot}>+ Add slot</button>
  </div>
</section>

<style>
  .slots {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 0.5rem);
  }
  .slots__head {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .slots__head h2 {
    margin: 0;
  }
  .slot-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .slot {
    border: 2px solid var(--color-border, #333);
    background: rgba(255, 255, 255, 0.02);
  }
  .slot__head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-bottom: 1px dashed var(--color-border, #333);
  }
  .slot__pos {
    font-family: var(--font-mono, monospace);
    color: var(--color-muted, #888);
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
  .pill {
    background: transparent;
    color: inherit;
    border: 2px solid var(--c);
    padding: 2px 8px;
    font: inherit;
    font-size: var(--text-sm, 0.85rem);
    text-transform: uppercase;
    letter-spacing: 1pt;
    cursor: pointer;
  }
  .pill.active {
    background: var(--c);
    color: #000;
  }
  .slot__actions {
    display: flex;
    gap: 10px;
  }
  .slot__pins {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    padding: 10px;
  }
  .pin {
    border: 1px dashed var(--color-border, #333);
    padding: 6px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
  }
  .pin__label {
    text-transform: uppercase;
    letter-spacing: 1pt;
    font-size: var(--text-sm, 0.85rem);
    color: var(--color-muted, #888);
  }
  .pin__thumb {
    display: block;
    width: 72px;
    height: 72px;
    background: #000;
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
    color: var(--color-muted, #888);
  }
  .slot__panel {
    padding: 10px;
    border-top: 1px dashed var(--color-border, #333);
  }
  .grid {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 8px;
  }
  .tile {
    display: flex;
    flex-direction: column;
    border: 2px solid var(--color-border, #333);
    background: #0a0a0a;
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
  .tile__name {
    padding: 4px 6px;
    font-size: var(--text-sm, 0.85rem);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tile__actions {
    padding: 0 6px 6px;
    display: flex;
    gap: 6px;
  }
  .link {
    background: transparent;
    border: 0;
    color: var(--color-accent, #b8860b);
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
    font: inherit;
  }
  .link--small {
    font-size: var(--text-sm, 0.85rem);
  }
  .link--danger {
    color: #fca5a5;
  }
  textarea {
    background: var(--color-bg-input, #111);
    color: inherit;
    border: 2px solid var(--color-border, #333);
    padding: 8px;
    font-family: inherit;
    width: 100%;
    box-sizing: border-box;
  }
  .btn {
    padding: 6px 12px;
    background: #1a1a1a;
    color: #fff;
    border: 2px solid var(--color-border, #333);
    box-shadow: 2px 2px 0 #000;
    font-family: var(--font-mono, monospace);
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1pt;
    font-size: var(--text-sm, 0.85rem);
    cursor: pointer;
  }
  .slots__footer {
    display: flex;
    justify-content: flex-start;
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
