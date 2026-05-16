<!--
  LatentDocuments — multi-document markdown editor with autosave + revisions.

  Tab strip across the top of all documents; the selected doc opens in an editor
  pane below. Autosave debounces every ~2 seconds. Revision history is a drawer.
-->
<script lang="ts">
  type Props = {
    projectId: string;
  };

  let { projectId }: Props = $props();

  type DocHeader = {
    id: string;
    project_id: string;
    position: number;
    name: string;
    updated_at: string | null;
    created_at: string | null;
  };

  type DocFull = DocHeader & { content: string };

  type Revision = {
    id: string;
    content: string;
    saved_by: number | null;
    saved_at: string | null;
  };

  let docs = $state<DocHeader[]>([]);
  let selectedId = $state<string | null>(null);
  let current = $state<DocFull | null>(null);
  let dirty = $state(false);
  let saving = $state(false);
  let savedAt = $state<string | null>(null);
  let error = $state<string | null>(null);

  let revsOpen = $state(false);
  let revs = $state<Revision[]>([]);

  let saveTimer: any = null;

  async function loadList() {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/documents`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      docs = body.documents || [];
      if (!selectedId && docs.length > 0) selectedId = docs[0].id;
    } catch (e: any) {
      error = e?.message || 'Failed to load documents';
    }
  }

  async function loadDoc(id: string) {
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(id)}`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      current = await res.json();
      dirty = false;
      savedAt = current?.updated_at ?? null;
    } catch (e: any) {
      error = e?.message || 'Failed to load document';
      current = null;
    }
  }

  async function saveCurrent() {
    if (!current || !dirty) return;
    saving = true;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(current.id)}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: current.content,
            name: current.name,
          }),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || `Failed (${res.status})`);
      }
      const body = await res.json();
      current = body;
      dirty = false;
      savedAt = body.updated_at;
      // Update the header in the list
      docs = docs.map((d) =>
        d.id === body.id
          ? { ...d, name: body.name, updated_at: body.updated_at }
          : d,
      );
    } catch (e: any) {
      error = e?.message || 'Failed to save';
    } finally {
      saving = false;
    }
  }

  function scheduleSave() {
    dirty = true;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveCurrent, 2000);
  }

  async function createDoc() {
    const name = prompt('Document name:');
    if (!name) return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/documents`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      docs = [...docs, body];
      selectedId = body.id;
    } catch (e: any) {
      error = e?.message || 'Failed to create document';
    }
  }

  async function renameDoc() {
    if (!current) return;
    const name = prompt('Rename document:', current.name);
    if (!name || name === current.name) return;
    current.name = name;
    dirty = true;
    await saveCurrent();
  }

  async function deleteDoc() {
    if (!current) return;
    if (!confirm(`Delete document "${current.name}"? Revisions go with it.`))
      return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(current.id)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      docs = docs.filter((d) => d.id !== current!.id);
      current = null;
      selectedId = docs.length > 0 ? docs[0].id : null;
    } catch (e: any) {
      error = e?.message || 'Failed to delete document';
    }
  }

  async function openRevs() {
    if (!current) return;
    revsOpen = true;
    revs = [];
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(current.id)}/revisions`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      revs = body.revisions || [];
    } catch (e: any) {
      error = e?.message || 'Failed to load revisions';
    }
  }

  function restoreRev(r: Revision) {
    if (!current) return;
    if (
      !confirm(
        'Restore this revision? Current content will be snapshotted into history.',
      )
    )
      return;
    current.content = r.content;
    dirty = true;
    saveCurrent();
    revsOpen = false;
  }

  function fmtTime(iso: string | null): string {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  $effect(() => {
    if (projectId) loadList();
  });

  $effect(() => {
    if (selectedId) loadDoc(selectedId);
  });
</script>

<section class="docs">
  <header class="docs__head">
    <h2>Documents</h2>
    <div class="docs__tabs">
      {#each docs as d (d.id)}
        <button
          class="tab"
          class:active={d.id === selectedId}
          onclick={() => (selectedId = d.id)}
          type="button">{d.name}</button
        >
      {/each}
      <button class="tab tab--add" onclick={createDoc} type="button"
        >+ Add</button
      >
    </div>
  </header>

  {#if error}
    <div class="error">{error}</div>
  {/if}

  {#if current}
    <div class="editor">
      <div class="editor__bar">
        <span class="status">
          {#if saving}Saving…{:else if dirty}Unsaved changes{:else if savedAt}Saved
            · {fmtTime(savedAt)}{/if}
        </span>
        <button class="link" type="button" onclick={renameDoc}>Rename</button>
        <button class="link" type="button" onclick={openRevs}>History</button>
        <button class="link link--danger" type="button" onclick={deleteDoc}
          >Delete</button
        >
      </div>
      <textarea
        bind:value={current.content}
        oninput={scheduleSave}
        rows="18"
        placeholder="Write in markdown… autosaves every couple seconds."
      ></textarea>
    </div>
  {:else if docs.length === 0}
    <div class="muted">
      No documents yet. Add one to write lyrics, mix notes, checklists…
    </div>
  {/if}

  {#if revsOpen}
    <div class="drawer" role="dialog" aria-label="Revision history">
      <header>
        <strong>History</strong>
        <button class="link" type="button" onclick={() => (revsOpen = false)}
          >Close</button
        >
      </header>
      {#if revs.length === 0}
        <p class="muted">No earlier revisions yet.</p>
      {:else}
        <ul>
          {#each revs as r (r.id)}
            <li>
              <div class="rev__meta">{fmtTime(r.saved_at)}</div>
              <pre>{r.content.slice(0, 600)}{r.content.length > 600
                  ? '…'
                  : ''}</pre>
              <button class="link" type="button" onclick={() => restoreRev(r)}
                >Restore this</button
              >
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}
</section>

<style>
  .docs {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 0.5rem);
  }
  .docs__head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }
  .docs__head h2 {
    margin: 0;
  }
  .docs__tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .tab {
    padding: 4px 10px;
    background: transparent;
    color: inherit;
    border: 2px solid var(--color-border, #333);
    cursor: pointer;
    font: inherit;
    font-size: var(--text-sm, 0.85rem);
  }
  .tab.active {
    background: var(--color-accent, #b8860b);
    color: #000;
  }
  .tab--add {
    color: var(--color-muted, #888);
  }
  .editor {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .editor__bar {
    display: flex;
    gap: 12px;
    align-items: center;
    font-size: var(--text-sm, 0.85rem);
  }
  .status {
    color: var(--color-muted, #888);
    flex: 1;
  }
  textarea {
    background: var(--color-bg-input, #111);
    color: inherit;
    border: 2px solid var(--color-border, #333);
    padding: 10px;
    font-family: var(--font-mono, monospace);
    font-size: 0.95rem;
    line-height: 1.4;
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
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
  .link--danger {
    color: #fca5a5;
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
  .drawer {
    border: 2px solid var(--color-border, #333);
    background: rgba(0, 0, 0, 0.45);
    padding: 10px;
  }
  .drawer header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .drawer ul {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .drawer li {
    border: 1px dashed var(--color-border, #333);
    padding: 6px;
  }
  .drawer pre {
    margin: 4px 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 0.85rem;
    color: var(--color-muted, #888);
  }
  .rev__meta {
    font-size: var(--text-sm, 0.85rem);
    color: var(--color-muted, #888);
  }
</style>
