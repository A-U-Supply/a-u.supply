<!--
  Uploader — unified drag-and-drop / browse file uploader for any destination.

  Props:
    - destination: 'tribute' | 'project' — controls auto-attach behaviour
    - projectId: when destination='project', the Latent to attach to
    - slotId: optional slot inside the Latent to attach to
    - defaultTags: comma-separated string of tags pre-applied to every upload
    - compact: render in a compact mode (no shared-fields box, smaller dropzone)
    - onUploaded: callback fired after each successful upload, with the upload payload
-->
<script lang="ts">
  type Props = {
    destination?: 'tribute' | 'project';
    projectId?: string;
    slotId?: string;
    defaultTags?: string;
    compact?: boolean;
    onUploaded?: (detail: {
      media_item_id: string;
      project_id: string | null;
      slot_id: string | null;
    }) => void;
  };

  let {
    destination = 'tribute',
    projectId = '',
    slotId = '',
    defaultTags = '',
    compact = false,
    onUploaded,
  }: Props = $props();

  type Item = {
    file: File;
    status: 'pending' | 'uploading' | 'processing' | 'done' | 'error';
    message?: string;
    preview?: string;
    sessionTool?: string | null;
    isSession?: boolean;
    progress?: number; // 0..1 during upload
    bytesSent?: number;
    bytesTotal?: number;
    speedBps?: number; // smoothed bytes/sec
  };

  let items = $state<Item[]>([]);
  let tags = $state(defaultTags);
  let description = $state('');
  let busy = $state(false);
  let summary = $state('');
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
    // Auto-fire the upload — staging files behind a separate "Upload" button
    // was confusing as hell. uploadAll() is a no-op if already busy or empty.
    if (next.length > 0) {
      void uploadAll();
    }
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

  function uploadWithProgress(
    fd: FormData,
    onProgress: (sent: number, total: number, speedBps: number) => void,
  ): Promise<any> {
    // Use XHR so we can observe upload progress (fetch can't expose it).
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/media/upload');
      xhr.withCredentials = true;
      let lastTs = performance.now();
      let lastSent = 0;
      let speed = 0;
      xhr.upload.onprogress = (ev) => {
        const total = ev.lengthComputable ? ev.total : 0;
        const sent = ev.loaded;
        const now = performance.now();
        const dt = (now - lastTs) / 1000;
        if (dt > 0.25) {
          const inst = (sent - lastSent) / dt; // bytes/sec
          // EMA smoothing
          speed = speed ? speed * 0.7 + inst * 0.3 : inst;
          lastTs = now;
          lastSent = sent;
        }
        onProgress(sent, total, speed);
      };
      xhr.upload.onload = () => {
        // All bytes have hit the server; we're waiting on the response now.
        onProgress(
          xhr.upload && (xhr.upload as any).total
            ? (xhr.upload as any).total
            : 1,
          (xhr.upload as any).total || 1,
          speed,
        );
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            resolve(null);
          }
        } else {
          let detail = `Error ${xhr.status}`;
          try {
            const body = JSON.parse(xhr.responseText);
            detail = body?.detail || detail;
          } catch {}
          reject(new Error(detail));
        }
      };
      xhr.onerror = () => reject(new Error('Network error'));
      xhr.ontimeout = () => reject(new Error('Timed out'));
      xhr.send(fd);
    });
  }

  function fmtSpeed(bps: number): string {
    if (!bps || bps < 1) return '';
    if (bps < 1024) return `${bps.toFixed(0)} B/s`;
    if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(0)} KB/s`;
    return `${(bps / (1024 * 1024)).toFixed(1)} MB/s`;
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
        const body = await uploadWithProgress(fd, (sent, total, speed) => {
          it.progress = total > 0 ? sent / total : 0;
          it.bytesSent = sent;
          it.bytesTotal = total;
          it.speedBps = speed;
          if (sent < total) {
            it.status = 'uploading';
          } else {
            // Upload bytes done, server is processing (sha + index + attach).
            it.status = 'processing';
          }
          items = [...items];
        });
        ok++;
        it.status = 'done';
        it.message = 'Uploaded';
        it.progress = 1;
        try {
          onUploaded?.({
            media_item_id: body?.id,
            project_id: projectId || null,
            slot_id: slotId || null,
          });
        } catch {}
      } catch (e: any) {
        fail++;
        it.status = 'error';
        it.message = e?.message || 'Upload failed';
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

<div class="uploader" class:compact>
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
      <span class="action-btn">Browse files</span>
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
        <input type="text" bind:value={tags} placeholder="Comma-separated" />
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
                · session · {it.sessionTool}
              {/if}
              {#if it.status === 'uploading' && it.speedBps}
                · {fmtSpeed(it.speedBps)}
              {/if}
            </div>
            {#if it.status === 'uploading' || it.status === 'processing'}
              <div class="file__bar" aria-hidden="true">
                <div
                  class="file__bar-fill"
                  class:indeterminate={it.status === 'processing'}
                  style="width: {((it.progress ?? 0) * 100).toFixed(1)}%"
                ></div>
              </div>
            {/if}
          </div>
          <span class="file__status">
            {#if it.status === 'pending'}Pending{/if}
            {#if it.status === 'uploading'}
              {Math.round((it.progress ?? 0) * 100)}%
            {/if}
            {#if it.status === 'processing'}Processing…{/if}
            {#if it.status === 'done'}Done{/if}
            {#if it.status === 'error'}{it.message || 'Error'}{/if}
          </span>
          {#if it.status !== 'uploading' && it.status !== 'processing'}
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
      <button class="btn-primary" onclick={uploadAll} disabled={busy}
        >{busy
          ? 'Uploading…'
          : `Upload ${items.length} file${items.length === 1 ? '' : 's'}`}</button
      >
      <button
        class="action-btn"
        onclick={clearAll}
        disabled={busy}
        type="button">Clear</button
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
    gap: var(--space-sm);
  }
  .dropzone {
    border: 2px dashed var(--color-border);
    padding: var(--space-lg);
    text-align: center;
    cursor: pointer;
    background: var(--color-bg);
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .compact .dropzone {
    padding: var(--space-md);
  }
  .dropzone.dragover {
    border-color: var(--color-accent);
    background: rgba(184, 134, 11, 0.06);
  }
  .dropzone__text {
    font-size: var(--text-base);
    text-transform: uppercase;
    letter-spacing: 1pt;
    margin: 0 0 var(--space-xs);
  }
  .dropzone__sub {
    color: var(--color-muted);
    font-size: var(--text-sm);
    margin: 0 0 var(--space-xs);
  }
  .shared-fields {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--space-sm);
  }
  @media (max-width: 640px) {
    .shared-fields {
      grid-template-columns: 1fr;
    }
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .field__label {
    font-size: var(--text-sm);
    color: var(--color-muted);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .field input,
  .field textarea {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 6px 10px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .file-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .file {
    display: grid;
    grid-template-columns: 44px 1fr auto auto;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .file__preview {
    width: 44px;
    height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #f4f4f4;
    overflow: hidden;
  }
  .file__preview img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .file__icon {
    color: var(--color-muted);
    font-size: var(--text-sm);
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
    font-size: var(--text-sm);
  }
  .file__meta {
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .file__bar {
    margin-top: 4px;
    height: 4px;
    background: #eee;
    border: 1px solid var(--color-border);
    position: relative;
    overflow: hidden;
  }
  .file__bar-fill {
    height: 100%;
    background: var(--color-accent);
    transition: width 0.15s linear;
  }
  .file__bar-fill.indeterminate {
    width: 100% !important;
    background-image: linear-gradient(
      90deg,
      var(--color-accent) 0%,
      var(--color-accent) 40%,
      rgba(184, 134, 11, 0.4) 40%,
      rgba(184, 134, 11, 0.4) 60%,
      var(--color-accent) 60%,
      var(--color-accent) 100%
    );
    background-size: 200% 100%;
    animation: bar-pulse 1.4s linear infinite;
  }
  @keyframes bar-pulse {
    from {
      background-position: 200% 0;
    }
    to {
      background-position: -200% 0;
    }
  }
  .file__status {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
    white-space: nowrap;
  }
  .file[data-status='done'] .file__status {
    color: #080;
  }
  .file[data-status='error'] .file__status {
    color: #c00;
  }
  .file[data-status='uploading'] .file__status,
  .file[data-status='processing'] .file__status {
    color: var(--color-accent);
  }
  .file__remove {
    background: transparent;
    border: 0;
    color: var(--color-muted);
    font-size: 1.25rem;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
  }
  .actions {
    display: flex;
    gap: var(--space-sm);
    flex-wrap: wrap;
  }
  .summary {
    padding: 6px 10px;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    font-size: var(--text-sm);
  }
  @media (max-width: 640px) {
    .file {
      grid-template-columns: 40px 1fr auto;
    }
    .file__status {
      grid-column: 2 / -1;
      grid-row: 2;
      font-size: 0.65rem;
    }
  }
</style>
