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

  // --- Session bundles (.logicx directory drops) ---------------------------
  // A .logicx is a macOS package directory: one file in Finder, a whole tree
  // to the browser. Each contained file uploads as a raw streamed part; the
  // queue shows the bundle as ONE entry with aggregate progress.
  type BundlePart = {
    file: File;
    path: string; // relative to the bundle root
    uploaded: boolean;
    attempts: number;
    bytesSent: number;
  };

  type Bundle = {
    id: string; // local queue id
    name: string;
    tool: string | null;
    parts: BundlePart[];
    totalBytes: number;
    bytesSent: number;
    partsDone: number;
    status: 'walking' | 'uploading' | 'completing' | 'done' | 'error';
    message?: string;
    bundleId?: string; // server-side staging id
    mediaItemId?: string;
    extractedCount?: number | null;
    extractionFailed?: boolean;
    deduplicated?: boolean;
    cancelled?: boolean;
    activeXhrs: Set<XMLHttpRequest>;
  };

  let bundles = $state<Bundle[]>([]);
  let bundleSeq = 0;
  let bundleInput: HTMLInputElement | null = $state(null);

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

  function looksLikeDirectory(f: File): boolean {
    // Chrome/Safari report empty mime + size=0 + no extension when a folder
    // gets dropped. Best-effort filter; the user can drag actual files inside.
    return !f.type && f.size === 0 && !/\.[A-Za-z0-9]{1,8}$/.test(f.name);
  }

  function addFiles(list: FileList | File[]) {
    const incoming: File[] = Array.from(list).filter((f) => {
      if (looksLikeDirectory(f)) return false;
      return true;
    });
    // Confirm before sweeping in a huge batch — accidental Cmd+A or folder
    // drops on Firefox can balloon the queue without warning otherwise.
    if (incoming.length > 10) {
      const ok = confirm(
        `About to add ${incoming.length} files at once. Continue?`,
      );
      if (!ok) return;
    }
    const next: Item[] = [];
    for (const f of incoming) {
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
    // Auto-fire — staging behind a button was easy to miss.
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
  async function onDrop(e: DragEvent) {
    e.preventDefault();
    dragOver = false;
    const dt = e.dataTransfer;
    if (!dt) return;

    // Prefer the entries API so directories can be explicitly rejected
    // rather than slipping through as zero-byte File objects. A directory
    // whose name ends in .logicx is a session bundle instead — walk it.
    const dtItems = dt.items;
    if (
      dtItems &&
      dtItems.length &&
      typeof (dtItems[0] as any).webkitGetAsEntry === 'function'
    ) {
      const files: File[] = [];
      const dirNames: string[] = [];
      const entries = Array.from(dtItems)
        .map((it) => (it as any).webkitGetAsEntry?.())
        .filter(Boolean);
      for (const entry of entries) {
        if (entry.isDirectory) {
          if (isLogicBundleDir(entry.name)) {
            void addBundleEntry(entry);
            continue;
          }
          dirNames.push(entry.name);
          continue;
        }
        const f = await new Promise<File | null>((resolve) => {
          entry.file?.(
            (file: File) => resolve(file),
            () => resolve(null),
          );
        });
        if (f) files.push(f);
      }
      if (dirNames.length) {
        alert(
          `Folders can't be uploaded directly. Skipped: ${dirNames.join(', ')}\n\n` +
            `Open the folder and drop the files inside instead.`,
        );
      }
      if (files.length) addFiles(files);
      return;
    }

    // Older browsers: fall back to dataTransfer.files; addFiles still
    // filters out anything that looks like a directory placeholder.
    if (dt.files && dt.files.length) addFiles(dt.files);
  }
  function onPickClick() {
    fileInput?.click();
  }
  function onDropzoneKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onPickClick();
    }
  }
  function onFileInputChange() {
    if (fileInput?.files) addFiles(fileInput.files);
    if (fileInput) fileInput.value = '';
  }
  function onBundlePickClick() {
    bundleInput?.click();
  }
  function onBundleInputChange() {
    const files = bundleInput?.files ? Array.from(bundleInput.files) : [];
    if (bundleInput) bundleInput.value = '';
    if (files.length) addBundleFromFileList(files);
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

  // --- Bundle walking + upload ----------------------------------------------

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  function isLogicBundleDir(name: string): boolean {
    return name.toLowerCase().endsWith('.logicx');
  }

  // readEntries() returns results in batches (≤100 in Chrome) — keep calling
  // until an empty batch or the subtree is truncated silently.
  function readAllEntries(reader: any): Promise<any[]> {
    return new Promise((resolve) => {
      const all: any[] = [];
      const step = () => {
        reader.readEntries(
          (batch: any[]) => {
            if (!batch || batch.length === 0) {
              resolve(all);
              return;
            }
            all.push(...batch);
            step();
          },
          () => resolve(all),
        );
      };
      step();
    });
  }

  async function walkEntry(
    entry: any,
    prefix: string,
    out: BundlePart[],
    onFile: () => void,
  ): Promise<void> {
    if (entry.isFile) {
      const file = await new Promise<File | null>((resolve) => {
        entry.file(
          (f: File) => resolve(f),
          () => resolve(null),
        );
      });
      if (file) {
        out.push({
          file,
          path: prefix + entry.name,
          uploaded: false,
          attempts: 0,
          bytesSent: 0,
        });
        onFile();
      }
      return;
    }
    if (entry.isDirectory) {
      const children = await readAllEntries(entry.createReader());
      for (const child of children) {
        await walkEntry(child, prefix + entry.name + '/', out, onFile);
      }
    }
  }

  function newBundle(name: string, parts: BundlePart[]): Bundle {
    return {
      id: `bundle-${++bundleSeq}`,
      name,
      tool: detectSessionTool(name),
      parts,
      totalBytes: parts.reduce((sum, p) => sum + p.file.size, 0),
      bytesSent: 0,
      partsDone: 0,
      status: 'walking',
      activeXhrs: new Set(),
    };
  }

  async function addBundleEntry(root: any) {
    const b = newBundle(root.name, []);
    bundles = [...bundles, b];
    const parts: BundlePart[] = [];
    // Walk the root's children directly so paths come out relative to the
    // bundle root (the leading "Name.logicx/" segment is stripped).
    const children = await readAllEntries(root.createReader());
    for (const child of children) {
      await walkEntry(child, '', parts, () => {
        b.parts = [...parts];
        bundles = [...bundles];
      });
    }
    if (b.cancelled) return;
    b.parts = parts;
    b.totalBytes = parts.reduce((sum, p) => sum + p.file.size, 0);
    if (parts.length === 0) {
      b.status = 'error';
      b.message = 'Bundle is empty';
      bundles = [...bundles];
      return;
    }
    bundles = [...bundles];
    void runBundle(b, true);
  }

  // webkitdirectory picker path — files arrive flat with webkitRelativePath
  // carrying the bundle folder as the first segment.
  function addBundleFromFileList(files: File[]) {
    const rootName = files[0]?.webkitRelativePath?.split('/')[0] || '';
    if (!isLogicBundleDir(rootName)) {
      alert(
        `"${rootName || 'Selected folder'}" is not a Logic Pro session bundle (.logicx).`,
      );
      return;
    }
    const parts: BundlePart[] = [];
    for (const f of files) {
      const rel = (f.webkitRelativePath || f.name)
        .split('/')
        .slice(1)
        .join('/');
      if (!rel) continue;
      parts.push({
        file: f,
        path: rel,
        uploaded: false,
        attempts: 0,
        bytesSent: 0,
      });
    }
    const b = newBundle(rootName, parts);
    bundles = [...bundles, b];
    if (parts.length === 0) {
      b.status = 'error';
      b.message = 'Bundle is empty';
      bundles = [...bundles];
      return;
    }
    void runBundle(b, true);
  }

  function syncBundleBytes(b: Bundle) {
    b.bytesSent = b.parts.reduce((sum, p) => sum + p.bytesSent, 0);
    bundles = [...bundles];
  }

  function uploadBundlePart(b: Bundle, part: BundlePart): Promise<void> {
    // XHR (not fetch) so the aggregate bar sees per-part byte progress and
    // cancel can abort in-flight parts — same trade-off as uploadWithProgress.
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      b.activeXhrs.add(xhr);
      xhr.open(
        'POST',
        `/api/media/bundles/${encodeURIComponent(b.bundleId!)}/files`,
      );
      xhr.withCredentials = true;
      xhr.setRequestHeader('X-Bundle-Path', encodeURIComponent(part.path));
      xhr.upload.onprogress = (ev) => {
        part.bytesSent = ev.loaded;
        syncBundleBytes(b);
      };
      const settle = () => {
        b.activeXhrs.delete(xhr);
        syncBundleBytes(b);
      };
      xhr.onload = () => {
        settle();
        if (xhr.status >= 200 && xhr.status < 300) {
          part.uploaded = true;
          part.bytesSent = part.file.size;
          b.partsDone++;
          syncBundleBytes(b);
          resolve();
        } else {
          let detail = `Error ${xhr.status}`;
          try {
            const body = JSON.parse(xhr.responseText);
            detail = body?.detail || detail;
          } catch {}
          reject(new Error(detail));
        }
      };
      xhr.onerror = () => {
        settle();
        reject(new Error('Network error'));
      };
      xhr.onabort = () => {
        settle();
        reject(new Error('Cancelled'));
      };
      xhr.send(part.file);
    });
  }

  async function uploadPartWithRetry(b: Bundle, part: BundlePart) {
    // Up to 2 retries with backoff; the server overwrites partial parts.
    const backoffs = [1000, 3000];
    let lastErr: any = null;
    for (let attempt = 0; attempt <= backoffs.length; attempt++) {
      if (b.cancelled) throw new Error('Cancelled');
      if (attempt > 0) await sleep(backoffs[attempt - 1]);
      part.attempts = attempt + 1;
      try {
        await uploadBundlePart(b, part);
        return;
      } catch (e: any) {
        lastErr = e;
        if (b.cancelled) throw e;
      }
    }
    throw lastErr;
  }

  async function uploadBundleParts(b: Bundle) {
    const pending = b.parts.filter((p) => !p.uploaded);
    let next = 0;
    const workers = Array.from(
      { length: Math.min(3, pending.length) },
      async () => {
        while (next < pending.length) {
          if (b.cancelled) throw new Error('Cancelled');
          const part = pending[next++];
          await uploadPartWithRetry(b, part);
        }
      },
    );
    await Promise.all(workers);
  }

  async function startBundle(b: Bundle) {
    const res = await fetch('/api/media/bundles', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: b.name,
        ...(destination === 'project' && projectId
          ? { project_id: projectId, ...(slotId ? { slot_id: slotId } : {}) }
          : {}),
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.detail || `Error ${res.status}`);
    }
    return res.json();
  }

  async function completeBundle(b: Bundle) {
    const res = await fetch(
      `/api/media/bundles/${encodeURIComponent(b.bundleId!)}/complete`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      },
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.detail || `Error ${res.status}`);
    }
    return res.json();
  }

  // fresh=true runs the whole flow (start → parts → complete); fresh=false
  // skips straight to complete, for retrying when every part already landed.
  async function runBundle(b: Bundle, fresh: boolean) {
    try {
      if (fresh) {
        // Best-effort cleanup of a previous staging area before restarting.
        if (b.bundleId) {
          try {
            await fetch(
              `/api/media/bundles/${encodeURIComponent(b.bundleId)}`,
              { method: 'DELETE', credentials: 'include' },
            );
          } catch {}
        }
        b.bundleId = undefined;
        b.mediaItemId = undefined;
        b.extractedCount = null;
        b.extractionFailed = false;
        b.deduplicated = false;
        b.bytesSent = 0;
        b.partsDone = 0;
        for (const p of b.parts) {
          p.uploaded = false;
          p.attempts = 0;
          p.bytesSent = 0;
        }
        b.status = 'uploading';
        b.message = '';
        bundles = [...bundles];
        const created = await startBundle(b);
        if (b.cancelled) return;
        b.bundleId = created.bundle_id;
        b.tool = created.tool || b.tool;
        await uploadBundleParts(b);
      }
      if (b.cancelled) return;
      b.status = 'completing';
      b.message = '';
      bundles = [...bundles];
      const item = await completeBundle(b);
      if (b.cancelled) return;
      b.status = 'done';
      b.mediaItemId = item?.id;
      b.deduplicated = !!item?.deduplicated;
      b.extractedCount = item?.session_meta?.extracted_count ?? null;
      const st = item?.session_meta?.extraction_status;
      b.extractionFailed = st === 'failed';
      bundles = [...bundles];
      try {
        onUploaded?.({
          media_item_id: item?.id,
          project_id: projectId || null,
          slot_id: slotId || null,
        });
      } catch {}
      if (st === 'pending' || st === 'processing') {
        void pollBundleExtraction(b);
      }
    } catch (e: any) {
      if (b.cancelled) return;
      b.status = 'error';
      b.message = e?.message || 'Upload failed';
      bundles = [...bundles];
    }
  }

  // The complete response lands before extraction finishes — poll briefly so
  // the queue entry can report the harvested-audio count.
  async function pollBundleExtraction(b: Bundle) {
    for (let attempt = 0; attempt < 40; attempt++) {
      await sleep(3000);
      if (b.cancelled || !bundles.includes(b) || !b.mediaItemId) return;
      try {
        const res = await fetch(
          `/api/media/${encodeURIComponent(b.mediaItemId)}`,
          { credentials: 'include' },
        );
        if (!res.ok) return;
        const body = await res.json();
        const meta = body?.session_meta;
        if (meta?.extraction_status === 'done') {
          b.extractedCount = meta.extracted_count ?? 0;
          bundles = [...bundles];
          return;
        }
        if (meta?.extraction_status === 'failed') {
          b.extractionFailed = true;
          bundles = [...bundles];
          return;
        }
      } catch {
        return;
      }
    }
  }

  function retryBundle(b: Bundle) {
    const allUploaded = b.parts.length > 0 && b.parts.every((p) => p.uploaded);
    // All parts landed → only complete needs re-running; otherwise start over.
    void runBundle(b, !(allUploaded && b.bundleId));
  }

  async function cancelBundle(b: Bundle) {
    b.cancelled = true;
    for (const xhr of b.activeXhrs) xhr.abort();
    bundles = bundles.filter((x) => x !== b);
    if (b.bundleId) {
      try {
        await fetch(`/api/media/bundles/${encodeURIComponent(b.bundleId)}`, {
          method: 'DELETE',
          credentials: 'include',
        });
      } catch {}
    }
  }

  function onBundleDismiss(b: Bundle) {
    if (b.status === 'uploading' || b.status === 'completing') {
      void cancelBundle(b);
    } else {
      b.cancelled = true;
      bundles = bundles.filter((x) => x !== b);
    }
  }

  function bundleDoneMessage(b: Bundle): string {
    if (b.extractionFailed) return 'Uploaded — extraction failed';
    if ((b.extractedCount ?? 0) > 0) {
      return `Uploaded — ${b.extractedCount} audio file${b.extractedCount === 1 ? '' : 's'} extracted`;
    }
    if (b.deduplicated) return 'Uploaded — already in the index';
    return 'Uploaded — extracting audio…';
  }

  function bundleProgress(b: Bundle): number {
    return b.totalBytes > 0 ? b.bytesSent / b.totalBytes : 0;
  }

  function bundleStatusText(b: Bundle): string {
    if (b.status === 'walking')
      return `Reading bundle… ${b.parts.length} file${b.parts.length === 1 ? '' : 's'}`;
    if (b.status === 'uploading')
      return `${Math.round(bundleProgress(b) * 100)}% · ${b.partsDone}/${b.parts.length} files`;
    if (b.status === 'completing') return 'Extracting audio…';
    if (b.status === 'done') return bundleDoneMessage(b);
    return b.message || 'Error';
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
    onkeydown={onDropzoneKeydown}
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

  <div class="bundle-browse">
    <button class="action-btn" type="button" onclick={onBundlePickClick}
      >Browse .logicx session bundle</button
    >
    <input
      bind:this={bundleInput}
      type="file"
      webkitdirectory
      hidden
      onchange={onBundleInputChange}
    />
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

  {#if items.length > 0 || bundles.length > 0}
    <ul class="file-list">
      {#each bundles as b (b.id)}
        <li
          class="file file--bundle"
          data-status={b.status === 'walking'
            ? 'pending'
            : b.status === 'completing'
              ? 'processing'
              : b.status}
        >
          <div class="file__preview">
            <span class="file__icon" title={b.tool || 'session bundle'}>▣</span>
          </div>
          <div class="file__info">
            <div class="file__name" title={b.name}>{b.name}</div>
            <div class="file__meta">
              session bundle{#if b.tool}&nbsp;· {b.tool}{/if}
              {#if b.status !== 'walking'}
                &nbsp;· {formatSize(b.totalBytes)} · {b.parts.length} file{b
                  .parts.length === 1
                  ? ''
                  : 's'}{/if}
            </div>
            {#if b.status === 'uploading' || b.status === 'completing'}
              <div class="file__bar" aria-hidden="true">
                <div
                  class="file__bar-fill"
                  class:indeterminate={b.status === 'completing'}
                  style="width: {(bundleProgress(b) * 100).toFixed(1)}%"
                ></div>
              </div>
            {/if}
          </div>
          <span class="file__status">{bundleStatusText(b)}</span>
          <span class="file__btns">
            {#if b.status === 'error' && b.parts.length > 0}
              <button
                class="action-btn file__retry"
                type="button"
                onclick={() => retryBundle(b)}>Retry</button
              >
            {/if}
            <button
              class="file__remove"
              onclick={() => onBundleDismiss(b)}
              aria-label={b.status === 'uploading' || b.status === 'completing'
                ? 'Cancel bundle upload'
                : 'Remove bundle'}
              type="button">×</button
            >
          </span>
        </li>
      {/each}
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

    {#if items.length > 0}
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
  .dropzone:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }
  .bundle-browse {
    display: flex;
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
    background: var(--color-surface);
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
    background: var(--color-surface-2);
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
  .file__btns {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .file__retry {
    padding: 2px 8px;
    font-size: 0.7rem;
    white-space: nowrap;
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
    .file--bundle .file__retry,
    .file--bundle .file__remove,
    .bundle-browse .action-btn {
      min-height: 44px;
      min-width: 44px;
    }
  }
</style>
