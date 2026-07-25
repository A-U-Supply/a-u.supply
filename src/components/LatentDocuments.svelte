<!--
  LatentDocuments — multi-document markdown editor with autosave + revisions.

  Tab strip across the top of all documents; the selected doc opens in an editor
  pane below. Autosave debounces every ~2 seconds. Revision history is a drawer.
-->
<script lang="ts">
  import LatentStyleButton from './LatentStyleButton.svelte';

  type Props = {
    projectId: string;
    styleKey?: string | null;
  };

  let { projectId, styleKey = null }: Props = $props();

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
  <header class="docs__head" class:latent-band={!!styleKey}>
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
    <div class="notice notice--error">{error}</div>
  {/if}

  {#if current}
    <div class="editor">
      <div class="editor__bar">
        <span class="status">
          {#if saving}Saving…{:else if dirty}Unsaved changes{:else if savedAt}Saved
            · {fmtTime(savedAt)}{/if}
        </span>
        <button class="action-btn" type="button" onclick={renameDoc}
          >Rename</button
        >
        <button class="action-btn" type="button" onclick={openRevs}
          >History</button
        >
        <button
          class="action-btn action-btn--danger"
          type="button"
          onclick={deleteDoc}>Delete</button
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
        <button
          class="action-btn"
          type="button"
          onclick={() => (revsOpen = false)}>Close</button
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
              <button
                class="action-btn"
                type="button"
                onclick={() => restoreRev(r)}>Restore this</button
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
    gap: var(--space-sm);
  }
  .docs__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    border-bottom: 2px solid var(--color-text);
    padding-bottom: var(--space-xs);
  }
  .docs__head h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .docs__tabs {
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
  .editor {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .editor__bar {
    display: flex;
    gap: var(--space-sm);
    align-items: center;
    font-size: var(--text-sm);
    flex-wrap: wrap;
  }
  .status {
    color: var(--color-muted);
    flex: 1;
  }
  textarea {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: var(--space-sm);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    line-height: 1.5;
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
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
  .drawer {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    padding: var(--space-sm);
  }
  .drawer header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-xs);
  }
  .drawer ul {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .drawer li {
    border: 1px dashed var(--color-border);
    padding: 6px;
  }
  .drawer pre {
    margin: 4px 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 0.7rem;
    color: var(--color-muted);
  }
  .rev__meta {
    font-size: 0.7rem;
    color: var(--color-muted);
  }
  @media (max-width: 640px) {
    /* rows="18" is ~330px — the tallest fixed block on the page. Half that on
       a phone; the field still scrolls and still grows with the window. */
    textarea {
      min-height: 40vh;
      height: 40vh;
    }

    .docs__tabs {
      margin-left: 0;
      width: 100%;
    }
  }
</style>
