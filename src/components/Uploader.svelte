<!--
  Uploader — unified drag-and-drop / browse file uploader for any destination.

  Props:
    - destination: 'tribute' | 'project' — controls auto-attach behaviour
    - projectId: when destination='project', the Latent to attach to
    - slotId: optional slot inside the Latent to attach to
    - defaultTags: comma-separated string of tags pre-applied to every upload
    - compact: render in a compact mode (no shared-fields box, smaller dropzone)

  Emits CustomEvent('uploaded') on the host element after every successful upload,
  with detail = { media_item_id, project_id, slot_id }. Pages can listen and
  refresh their views.
-->
<script lang="ts">
  type Props = {
    destination?: 'tribute' | 'project';
    projectId?: string;
    slotId?: string;
    defaultTags?: string;
    compact?: boolean;
  };

  let {
    destination = 'tribute',
    projectId = '',
    slotId = '',
    defaultTags = '',
    compact = false,
  }: Props = $props();

  type Item = {
    file: File;
    status: 'pending' | 'uploading' | 'done' | 'error';
    message?: string;
    preview?: string;
    sessionTool?: string | null;
    isSession?: boolean;
  };

  let items = $state<Item[]>([]);
  let tags = $state(defaultTags);
  let description = $state('');
  let busy = $state(false);
  let summary = $state('');
  let host: HTMLDivElement | null = $state(null);
  let fileInput: HTMLInputElement | null = $state(null);
  let dragOver = $state(false);

  const SESSION_EXT_TO_TOOL: Record<string, string> = {
    '.logicx': 'logic',
    '.als': 'ableton',
    '.flp': 'flstudio',
    '.ptx': 'protools',
    '.ptxt': 'protools',
    '.rpp': 'reaper',
    '.bwproject': 'bitwig',
    '.prproj': 'premiere',
    '.drp': 'davinci',
    '.fcpbundle': 'finalcut',
    '.aep': 'aftereffects',
    '.lrcat': 'lightroom',
    '.cosession': 'captureone',
  };

  function detectSessionTool(name: string): string | null {
    const n = name.toLowerCase();
    for (const ext of Object.keys(SESSION_EXT_TO_TOOL)) {
      if (n.endsWith(ext) || n.endsWith(ext + '.zip'))
        return SESSION_EXT_TO_TOOL[ext];
    }
    return null;
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024)
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
  }

  function fileKind(f: File): string {
    if (f.type.startsWith('image/')) return 'image';
    if (f.type.startsWith('audio/')) return 'audio';
    if (f.type.startsWith('video/')) return 'video';
    return 'file';
  }

  function addFiles(list: FileList | File[]) {
    const next: Item[] = [];
    for (const f of list) {
      if (
        items.some((it) => it.file.name === f.name && it.file.size === f.size)
      )
        continue;
      const tool = detectSessionTool(f.name);
      const item: Item = {
        file: f,
        status: 'pending',
        sessionTool: tool,
        isSession: Boolean(tool),
      };
      if (f.type.startsWith('image/')) {
        const reader = new FileReader();
        reader.onload = (e) => {
          item.preview = e.target?.result as string;
          items = [...items];
        };
        reader.readAsDataURL(f);
      }
      next.push(item);
    }
    items = [...items, ...next];
  }

  function removeAt(i: number) {
    items = items.filter((_, idx) => idx !== i);
  }

  function clearAll() {
    items = [];
    summary = '';
  }

  function onDragOver(e: DragEvent) {
    e.preventDefault();
    dragOver = true;
  }
  function onDragLeave() {
    dragOver = false;
  }
  function onDrop(e: DragEvent) {
    e.preventDefault();
    dragOver = false;
    if (e.dataTransfer?.files) addFiles(e.dataTransfer.files);
  }
  function onPickClick() {
    fileInput?.click();
  }
  function onFileInputChange() {
    if (fileInput?.files) addFiles(fileInput.files);
    if (fileInput) fileInput.value = '';
  }

  async function uploadAll() {
    if (items.length === 0 || busy) return;
    busy = true;
    summary = '';
    let ok = 0;
    let fail = 0;

    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.status === 'done') {
        ok++;
        continue;
      }
      it.status = 'uploading';
      items = [...items];

      const fd = new FormData();
      fd.append('file', it.file);
      if (tags) fd.append('tags', tags);
      if (description) fd.append('description', description);
      if (destination === 'project' && projectId) {
        fd.append('project_id', projectId);
        if (slotId) fd.append('slot_id', slotId);
      }
      if (it.isSession) {
        fd.append('force_session', 'true');
        if (it.sessionTool) fd.append('tool', it.sessionTool);
      }

      try {
        const res = await fetch('/api/media/upload', {
          method: 'POST',
          credentials: 'include',
          body: fd,
        });
        if (res.ok) {
          ok++;
          it.status = 'done';
          it.message = 'Uploaded';
          try {
            const body = await res.json();
            host?.dispatchEvent(
              new CustomEvent('uploaded', {
                bubbles: true,
                detail: {
                  media_item_id: body?.id,
                  project_id: projectId || null,
                  slot_id: slotId || null,
                },
              }),
            );
          } catch {}
        } else {
          fail++;
          it.status = 'error';
          try {
            const err = await res.json();
            it.message = err?.detail || `Error ${res.status}`;
          } catch {
            it.message = `Error ${res.status}`;
          }
        }
      } catch (e: any) {
        fail++;
        it.status = 'error';
        it.message = e?.message || 'Network error';
      }
      items = [...items];
    }

    busy = false;
    summary = `Upload complete: ${ok} succeeded, ${fail} failed.`;
    if (fail === 0) {
      setTimeout(() => {
        items = [];
        summary = '';
      }, 1800);
    }
  }
</script>

<div class="uploader" class:compact bind:this={host}>
  <div
    class="dropzone"
    class:dragover={dragOver}
    ondragover={onDragOver}
    ondragleave={onDragLeave}
    ondrop={onDrop}
    onclick={onPickClick}
    role="button"
    tabindex="0"
  >
    <div class="dropzone__inner">
      <p class="dropzone__text">Drag &amp; drop files here</p>
      <p class="dropzone__sub">or</p>
      <span class="dropzone__btn">Browse files</span>
      <input
        bind:this={fileInput}
        type="file"
        multiple
        hidden
        onchange={onFileInputChange}
      />
    </div>
  </div>

  {#if !compact}
    <div class="shared-fields">
      <label class="field">
        <span class="field__label">Tags (applied to every file)</span>
        <input
          type="text"
          bind:value={tags}
          placeholder="Comma-separated tags"
        />
      </label>
      <label class="field">
        <span class="field__label">Description</span>
        <textarea bind:value={description} rows="2" placeholder="Optional notes"
        ></textarea>
      </label>
    </div>
  {/if}

  {#if items.length > 0}
    <ul class="file-list">
      {#each items as it, i}
        <li class="file" data-status={it.status}>
          <div class="file__preview">
            {#if it.preview}
              <img src={it.preview} alt="" />
            {:else if it.isSession}
              <span class="file__icon" title={it.sessionTool || 'session'}
                >▣</span
              >
            {:else}
              <span class="file__icon">{fileKind(it.file)}</span>
            {/if}
          </div>
          <div class="file__info">
            <div class="file__name" title={it.file.name}>{it.file.name}</div>
            <div class="file__meta">
              {formatSize(it.file.size)}
              {#if it.isSession && it.sessionTool}
                · <strong>session</strong> · {it.sessionTool}
              {/if}
            </div>
          </div>
          <span class="file__status">
            {#if it.status === 'pending'}Pending{/if}
            {#if it.status === 'uploading'}Uploading…{/if}
            {#if it.status === 'done'}Done{/if}
            {#if it.status === 'error'}{it.message || 'Error'}{/if}
          </span>
          {#if it.status !== 'uploading'}
            <button
              class="file__remove"
              onclick={() => removeAt(i)}
              aria-label="Remove file"
              type="button">×</button
            >
          {/if}
        </li>
      {/each}
    </ul>

    <div class="actions">
      <button class="btn btn--primary" onclick={uploadAll} disabled={busy}
        >{busy
          ? 'Uploading…'
          : `Upload ${items.length} file${items.length === 1 ? '' : 's'}`}</button
      >
      <button class="btn" onclick={clearAll} disabled={busy} type="button"
        >Clear</button
      >
    </div>
  {/if}

  {#if summary}
    <div class="summary">{summary}</div>
  {/if}
</div>

<style>
  .uploader {
    display: flex;
    flex-direction: column;
    gap: var(--space-md, 1rem);
  }
  .dropzone {
    border: 2px dashed var(--color-border, #333);
    padding: var(--space-xl, 2rem);
    text-align: center;
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
    background: transparent;
  }
  .compact .dropzone {
    padding: var(--space-md, 1rem);
  }
  .dropzone.dragover {
    border-color: var(--color-accent, #b8860b);
    background: rgba(184, 134, 11, 0.06);
  }
  .dropzone__text {
    font-size: var(--text-lg, 1.05rem);
    text-transform: uppercase;
    letter-spacing: 1pt;
    margin: 0 0 var(--space-xs, 0.25rem);
  }
  .dropzone__sub {
    color: var(--color-muted, #888);
    font-size: var(--text-sm, 0.85rem);
    margin: 0 0 var(--space-sm, 0.5rem);
  }
  .dropzone__btn {
    display: inline-block;
    padding: var(--space-xs, 0.3rem) var(--space-md, 1rem);
    background: #1a1a1a;
    color: #fff;
    border: 2px solid var(--color-border, #333);
    box-shadow: 2px 2px 0 #000;
    font-family: var(--font-mono, monospace);
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1pt;
    font-size: var(--text-sm, 0.85rem);
  }
  .shared-fields {
    display: grid;
    gap: var(--space-sm, 0.5rem);
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .field__label {
    font-size: var(--text-sm, 0.85rem);
    color: var(--color-muted, #888);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .field input,
  .field textarea {
    background: var(--color-bg-input, #111);
    color: inherit;
    border: 2px solid var(--color-border, #333);
    padding: 6px 10px;
    font-family: inherit;
  }
  .file-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .file {
    display: grid;
    grid-template-columns: 48px 1fr auto auto;
    align-items: center;
    gap: 10px;
    padding: 6px 8px;
    border: 2px solid var(--color-border, #333);
    background: rgba(255, 255, 255, 0.02);
  }
  .file__preview {
    width: 48px;
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #000;
    overflow: hidden;
  }
  .file__preview img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .file__icon {
    color: var(--color-muted, #888);
    font-size: var(--text-sm, 0.85rem);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .file__info {
    min-width: 0;
  }
  .file__name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .file__meta {
    color: var(--color-muted, #888);
    font-size: var(--text-sm, 0.85rem);
  }
  .file__status {
    font-size: var(--text-sm, 0.85rem);
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted, #888);
  }
  .file[data-status='done'] .file__status {
    color: #4ade80;
  }
  .file[data-status='error'] .file__status {
    color: #f87171;
  }
  .file[data-status='uploading'] .file__status {
    color: var(--color-accent, #b8860b);
  }
  .file__remove {
    background: transparent;
    border: 0;
    color: var(--color-muted, #888);
    font-size: 1.25rem;
    cursor: pointer;
    padding: 0 8px;
  }
  .actions {
    display: flex;
    gap: var(--space-sm, 0.5rem);
  }
  .btn {
    padding: 6px 14px;
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
  .btn--primary {
    background: var(--color-accent, #b8860b);
  }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .summary {
    padding: 8px 10px;
    border: 2px solid var(--color-border, #333);
    background: rgba(255, 255, 255, 0.02);
    font-size: var(--text-sm, 0.85rem);
  }
</style>
