/**
 * The upload queue — one global engine, owned by the persistent UploadDock.
 *
 * This is the transfer machinery lifted out of `Uploader.svelte` so it can
 * outlive the page that started it. `Uploader` now only picks files and
 * dispatches `upload:start`; everything from the first byte to the Slack
 * report happens here, inside an island that `transition:persist` keeps alive
 * across ViewTransitions.
 *
 * Deliberately framework-free — plain module state plus a subscribe callback.
 * The dock is a view over `snapshot()`, and nothing else may mutate the queue
 * except through the functions below.
 *
 * The one real change from the old per-page version: **destination is carried
 * per entry**, not held as a component prop. A global queue can hold a file
 * bound for one Latent's slot next to a Tribute upload, and each has to
 * remember its own target.
 */

export type Destination = {
  destination: 'tribute' | 'project';
  projectId?: string;
  slotId?: string;
  tags?: string;
  description?: string;
};

export type ItemStatus =
  | 'pending'
  | 'uploading'
  | 'processing'
  | 'done'
  | 'error';

export type QueueItem = {
  id: string;
  file: File;
  dest: Destination;
  status: ItemStatus;
  message?: string;
  preview?: string;
  sessionTool: string | null;
  isSession: boolean;
  progress: number; // 0..1 during upload
  bytesSent: number;
  bytesTotal: number;
  speedBps: number;
  mediaItemId?: string;
};

export type BundlePart = {
  file: File;
  path: string; // relative to the bundle root
  uploaded: boolean;
  attempts: number;
  bytesSent: number;
};

export type BundleStatus =
  | 'walking'
  | 'uploading'
  | 'completing'
  | 'done'
  | 'error';

export type QueueBundle = {
  id: string;
  name: string;
  dest: Destination;
  tool: string | null;
  parts: BundlePart[];
  totalBytes: number;
  bytesSent: number;
  partsDone: number;
  status: BundleStatus;
  message?: string;
  bundleId?: string; // server-side staging id
  mediaItemId?: string;
  extractedCount?: number | null;
  extractionFailed?: boolean;
  deduplicated?: boolean;
  cancelled?: boolean;
  activeXhrs: Set<XMLHttpRequest>;
};

// --- state -----------------------------------------------------------------

let items: QueueItem[] = [];
let bundles: QueueBundle[] = [];
let seq = 0;
let draining = false;

/** Failures accumulated since the queue last went idle, for one batched report. */
let failuresThisRun: { name: string; message: string }[] = [];

const listeners = new Set<() => void>();

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) fn();
}

export function snapshot() {
  return { items, bundles };
}

/** True while any transfer could still be interrupted by leaving the page. */
export function hasActiveTransfers(): boolean {
  return (
    items.some(
      (it) => it.status === 'uploading' || it.status === 'processing',
    ) ||
    bundles.some(
      (b) =>
        b.status === 'walking' ||
        b.status === 'uploading' ||
        b.status === 'completing',
    )
  );
}

export function isIdle(): boolean {
  return !hasActiveTransfers() && !draining;
}

export function counts() {
  const all = [...items.map((i) => i.status), ...bundles.map((b) => b.status)];
  return {
    total: all.length,
    done: all.filter((s) => s === 'done').length,
    failed: all.filter((s) => s === 'error').length,
    active: all.filter(
      (s) =>
        s === 'uploading' ||
        s === 'processing' ||
        s === 'completing' ||
        s === 'walking' ||
        s === 'pending',
    ).length,
  };
}

/** 0..1 across everything currently queued, weighted by bytes where known. */
export function overallProgress(): number {
  let sent = 0;
  let total = 0;
  for (const it of items) {
    const t = it.bytesTotal || it.file.size || 0;
    total += t;
    sent += it.status === 'done' ? t : it.bytesSent;
  }
  for (const b of bundles) {
    total += b.totalBytes;
    sent += b.status === 'done' ? b.totalBytes : b.bytesSent;
  }
  return total > 0 ? Math.min(1, sent / total) : 0;
}

// --- formatting helpers (shared with the dock) -----------------------------

export function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024)
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

export function fmtSpeed(bps: number): string {
  if (!bps || bps < 1) return '';
  if (bps < 1024) return `${bps.toFixed(0)} B/s`;
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(0)} KB/s`;
  return `${(bps / (1024 * 1024)).toFixed(1)} MB/s`;
}

export function fileKind(f: File): string {
  if (f.type.startsWith('image/')) return 'image';
  if (f.type.startsWith('audio/')) return 'audio';
  if (f.type.startsWith('video/')) return 'video';
  return 'file';
}

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

export function detectSessionTool(name: string): string | null {
  const n = name.toLowerCase();
  for (const ext of Object.keys(SESSION_EXT_TO_TOOL)) {
    if (n.endsWith(ext) || n.endsWith(ext + '.zip'))
      return SESSION_EXT_TO_TOOL[ext];
  }
  return null;
}

export function isLogicBundleDir(name: string): boolean {
  return name.toLowerCase().endsWith('.logicx');
}

export function looksLikeDirectory(f: File): boolean {
  // Chrome/Safari report empty mime + size=0 + no extension when a folder
  // gets dropped. Best-effort filter; the user can drag actual files inside.
  return !f.type && f.size === 0 && !/\.[A-Za-z0-9]{1,8}$/.test(f.name);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// --- outbound events -------------------------------------------------------

/**
 * Told to whoever is still listening. A live `PullFromIndex` uses `upload:done`
 * to re-select the image you just added; a departed one simply never hears it,
 * which is the entire point of moving the queue out of the page.
 */
function announce(name: string, detail: Record<string, unknown>) {
  if (typeof document === 'undefined') return;
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

// --- plain file uploads ----------------------------------------------------

function uploadWithProgress(
  fd: FormData,
  onProgress: (sent: number, total: number, speedBps: number) => void,
  registerXhr: (xhr: XMLHttpRequest) => void,
): Promise<any> {
  // Use XHR so we can observe upload progress (fetch can't expose it).
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    registerXhr(xhr);
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
        speed = speed ? speed * 0.7 + inst * 0.3 : inst; // EMA smoothing
        lastTs = now;
        lastSent = sent;
      }
      onProgress(sent, total, speed);
    };
    xhr.upload.onload = () => {
      // All bytes have hit the server; we're waiting on the response now.
      const total = (xhr.upload as any).total || 1;
      onProgress(total, total, speed);
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
    xhr.onabort = () => reject(new Error('Cancelled'));
    xhr.send(fd);
  });
}

const itemXhrs = new Map<string, XMLHttpRequest>();

function buildFormData(it: QueueItem): FormData {
  const fd = new FormData();
  fd.append('file', it.file);
  const d = it.dest;
  if (d.tags) fd.append('tags', d.tags);
  if (d.description) fd.append('description', d.description);
  if (d.destination === 'project' && d.projectId) {
    fd.append('project_id', d.projectId);
    if (d.slotId) fd.append('slot_id', d.slotId);
  }
  if (it.isSession) {
    fd.append('force_session', 'true');
    if (it.sessionTool) fd.append('tool', it.sessionTool);
  }
  // Deliberately does NOT set source_type/output_index — both upload paths
  // defaulted to `manual_upload` before this refactor, and changing it here
  // would silently reroute files into a different Meilisearch index.
  return fd;
}

async function uploadItem(it: QueueItem) {
  it.status = 'uploading';
  emit();
  try {
    const body = await uploadWithProgress(
      buildFormData(it),
      (sent, total, speed) => {
        it.progress = total > 0 ? sent / total : 0;
        it.bytesSent = sent;
        it.bytesTotal = total;
        it.speedBps = speed;
        // Bytes done but no response yet = the server is hashing, indexing
        // and attaching. Worth its own state; it can take a while on video.
        it.status = sent < total ? 'uploading' : 'processing';
        emit();
      },
      (xhr) => itemXhrs.set(it.id, xhr),
    );
    it.status = 'done';
    it.message = 'Uploaded';
    it.progress = 1;
    it.mediaItemId = body?.id;
    announce('upload:done', {
      media_item_id: body?.id,
      project_id: it.dest.projectId || null,
      slot_id: it.dest.slotId || null,
      name: it.file.name,
    });
  } catch (e: any) {
    const message = e?.message || 'Upload failed';
    if (message === 'Cancelled') {
      // A cancel is not a failure — it never reaches the Slack report.
      items = items.filter((x) => x !== it);
      emit();
      return;
    }
    it.status = 'error';
    it.message = message;
    failuresThisRun.push({ name: it.file.name, message });
    announce('upload:failed', { name: it.file.name, message });
  } finally {
    itemXhrs.delete(it.id);
    emit();
  }
}

/**
 * Drain the queue one file at a time.
 *
 * Serial on purpose — it matches the previous behaviour, and the bottleneck is
 * the uplink, so parallel plain-file uploads would only make each bar crawl.
 * Bundles run their own 3-way parallelism over parts internally.
 */
async function drain() {
  if (draining) return;
  draining = true;
  try {
    // Re-read `items` each pass: more can arrive from another page mid-drain.
    for (;;) {
      const next = items.find((it) => it.status === 'pending');
      if (!next) break;
      await uploadItem(next);
    }
  } finally {
    draining = false;
    emit();
    void reportFailuresIfSettled();
  }
}

// --- bundles ---------------------------------------------------------------

function readAllEntries(reader: any): Promise<any[]> {
  // readEntries() returns results in batches (≤100 in Chrome) — keep calling
  // until an empty batch or the subtree is truncated silently.
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

function newBundle(
  name: string,
  parts: BundlePart[],
  dest: Destination,
): QueueBundle {
  return {
    id: `bundle-${++seq}`,
    name,
    dest,
    tool: detectSessionTool(name),
    parts,
    totalBytes: parts.reduce((sum, p) => sum + p.file.size, 0),
    bytesSent: 0,
    partsDone: 0,
    status: 'walking',
    activeXhrs: new Set(),
  };
}

function syncBundleBytes(b: QueueBundle) {
  b.bytesSent = b.parts.reduce((sum, p) => sum + p.bytesSent, 0);
  emit();
}

function uploadBundlePart(b: QueueBundle, part: BundlePart): Promise<void> {
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

async function uploadPartWithRetry(b: QueueBundle, part: BundlePart) {
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

async function uploadBundleParts(b: QueueBundle) {
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

async function startBundle(b: QueueBundle) {
  const d = b.dest;
  const res = await fetch('/api/media/bundles', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: b.name,
      ...(d.destination === 'project' && d.projectId
        ? {
            project_id: d.projectId,
            ...(d.slotId ? { slot_id: d.slotId } : {}),
          }
        : {}),
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.detail || `Error ${res.status}`);
  }
  return res.json();
}

async function completeBundle(b: QueueBundle) {
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
async function runBundle(b: QueueBundle, fresh: boolean) {
  try {
    if (fresh) {
      // Best-effort cleanup of a previous staging area before restarting.
      if (b.bundleId) {
        try {
          await fetch(`/api/media/bundles/${encodeURIComponent(b.bundleId)}`, {
            method: 'DELETE',
            credentials: 'include',
          });
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
      emit();
      const created = await startBundle(b);
      if (b.cancelled) return;
      b.bundleId = created.bundle_id;
      b.tool = created.tool || b.tool;
      await uploadBundleParts(b);
    }
    if (b.cancelled) return;
    b.status = 'completing';
    b.message = '';
    emit();
    const item = await completeBundle(b);
    if (b.cancelled) return;
    b.status = 'done';
    b.mediaItemId = item?.id;
    b.deduplicated = !!item?.deduplicated;
    b.extractedCount = item?.session_meta?.extracted_count ?? null;
    const st = item?.session_meta?.extraction_status;
    b.extractionFailed = st === 'failed';
    emit();
    announce('upload:done', {
      media_item_id: item?.id,
      project_id: b.dest.projectId || null,
      slot_id: b.dest.slotId || null,
      name: b.name,
    });
    if (st === 'pending' || st === 'processing') {
      void pollBundleExtraction(b);
    }
  } catch (e: any) {
    if (b.cancelled) return;
    const message = e?.message || 'Upload failed';
    b.status = 'error';
    b.message = message;
    emit();
    failuresThisRun.push({ name: b.name, message });
    announce('upload:failed', { name: b.name, message });
  } finally {
    void reportFailuresIfSettled();
  }
}

// The complete response lands before extraction finishes — poll briefly so
// the queue entry can report the harvested-audio count.
async function pollBundleExtraction(b: QueueBundle) {
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
        emit();
        return;
      }
      if (meta?.extraction_status === 'failed') {
        b.extractionFailed = true;
        emit();
        return;
      }
    } catch {
      return;
    }
  }
}

export function bundleProgress(b: QueueBundle): number {
  return b.totalBytes > 0 ? b.bytesSent / b.totalBytes : 0;
}

export function bundleDoneMessage(b: QueueBundle): string {
  if (b.extractionFailed) return 'Uploaded — extraction failed';
  if ((b.extractedCount ?? 0) > 0) {
    return `Uploaded — ${b.extractedCount} audio file${b.extractedCount === 1 ? '' : 's'} extracted`;
  }
  if (b.deduplicated) return 'Uploaded — already in the index';
  return 'Uploaded — extracting audio…';
}

export function bundleStatusText(b: QueueBundle): string {
  if (b.status === 'walking')
    return `Reading bundle… ${b.parts.length} file${b.parts.length === 1 ? '' : 's'}`;
  if (b.status === 'uploading')
    return `${Math.round(bundleProgress(b) * 100)}% · ${b.partsDone}/${b.parts.length} files`;
  if (b.status === 'completing') return 'Extracting audio…';
  if (b.status === 'done') return bundleDoneMessage(b);
  return b.message || 'Error';
}

// --- public API ------------------------------------------------------------

export function enqueueFiles(list: FileList | File[], dest: Destination) {
  const incoming = Array.from(list).filter((f) => !looksLikeDirectory(f));
  const next: QueueItem[] = [];
  for (const f of incoming) {
    // Same file, same destination, already queued → skip. Two Latents can
    // legitimately want the same file, so the destination is part of identity.
    if (
      items.some(
        (it) =>
          it.file.name === f.name &&
          it.file.size === f.size &&
          it.dest.projectId === dest.projectId &&
          it.dest.slotId === dest.slotId,
      )
    )
      continue;
    const tool = detectSessionTool(f.name);
    const item: QueueItem = {
      id: `item-${++seq}`,
      file: f,
      dest,
      status: 'pending',
      sessionTool: tool,
      isSession: Boolean(tool),
      progress: 0,
      bytesSent: 0,
      bytesTotal: f.size,
      speedBps: 0,
    };
    if (f.type.startsWith('image/')) {
      const reader = new FileReader();
      reader.onload = (e) => {
        item.preview = e.target?.result as string;
        emit();
      };
      reader.readAsDataURL(f);
    }
    next.push(item);
  }
  if (next.length === 0) return;
  items = [...items, ...next];
  emit();
  void drain();
}

export async function enqueueBundleEntry(root: any, dest: Destination) {
  const b = newBundle(root.name, [], dest);
  bundles = [...bundles, b];
  emit();
  const parts: BundlePart[] = [];
  // Walk the root's children directly so paths come out relative to the
  // bundle root (the leading "Name.logicx/" segment is stripped).
  const children = await readAllEntries(root.createReader());
  for (const child of children) {
    await walkEntry(child, '', parts, () => {
      b.parts = [...parts];
      emit();
    });
  }
  if (b.cancelled) return;
  b.parts = parts;
  b.totalBytes = parts.reduce((sum, p) => sum + p.file.size, 0);
  if (parts.length === 0) {
    b.status = 'error';
    b.message = 'Bundle is empty';
    emit();
    return;
  }
  emit();
  void runBundle(b, true);
}

/**
 * webkitdirectory picker path — files arrive flat with webkitRelativePath
 * carrying the bundle folder as the first segment. Returns an error string
 * for the caller to surface, or null on success.
 */
export function enqueueBundleFiles(
  files: File[],
  dest: Destination,
): string | null {
  const rootName = files[0]?.webkitRelativePath?.split('/')[0] || '';
  if (!isLogicBundleDir(rootName)) {
    return `"${rootName || 'Selected folder'}" is not a Logic Pro session bundle (.logicx).`;
  }
  const parts: BundlePart[] = [];
  for (const f of files) {
    const rel = (f.webkitRelativePath || f.name).split('/').slice(1).join('/');
    if (!rel) continue;
    parts.push({
      file: f,
      path: rel,
      uploaded: false,
      attempts: 0,
      bytesSent: 0,
    });
  }
  const b = newBundle(rootName, parts, dest);
  bundles = [...bundles, b];
  if (parts.length === 0) {
    b.status = 'error';
    b.message = 'Bundle is empty';
    emit();
    return null;
  }
  emit();
  void runBundle(b, true);
  return null;
}

export function retryItem(id: string) {
  const it = items.find((x) => x.id === id);
  if (!it || it.status !== 'error') return;
  it.status = 'pending';
  it.message = '';
  it.progress = 0;
  it.bytesSent = 0;
  // Retrying is a fresh attempt at the same run — drop the old failure so a
  // success doesn't still get reported to Slack.
  failuresThisRun = failuresThisRun.filter((f) => f.name !== it.file.name);
  emit();
  void drain();
}

export function retryBundle(id: string) {
  const b = bundles.find((x) => x.id === id);
  if (!b) return;
  failuresThisRun = failuresThisRun.filter((f) => f.name !== b.name);
  const allUploaded = b.parts.length > 0 && b.parts.every((p) => p.uploaded);
  // All parts landed → only complete needs re-running; otherwise start over.
  void runBundle(b, !(allUploaded && b.bundleId));
}

export function retryAllFailed() {
  for (const b of bundles.filter((x) => x.status === 'error'))
    retryBundle(b.id);
  for (const it of items.filter((x) => x.status === 'error')) retryItem(it.id);
}

export function dismissItem(id: string) {
  const it = items.find((x) => x.id === id);
  if (!it) return;
  if (it.status === 'uploading' || it.status === 'processing') {
    itemXhrs.get(id)?.abort();
    return; // the abort handler removes it
  }
  items = items.filter((x) => x !== it);
  emit();
}

export async function dismissBundle(id: string) {
  const b = bundles.find((x) => x.id === id);
  if (!b) return;
  const wasRunning =
    b.status === 'uploading' ||
    b.status === 'completing' ||
    b.status === 'walking';
  b.cancelled = true;
  for (const xhr of b.activeXhrs) xhr.abort();
  bundles = bundles.filter((x) => x !== b);
  emit();
  if (wasRunning && b.bundleId) {
    // Release the server-side staging area.
    try {
      await fetch(`/api/media/bundles/${encodeURIComponent(b.bundleId)}`, {
        method: 'DELETE',
        credentials: 'include',
      });
    } catch {}
  }
}

/** Clear everything that has finished, successfully or not. */
export function dismissFinished() {
  items = items.filter((it) => it.status !== 'done' && it.status !== 'error');
  bundles = bundles.filter((b) => b.status !== 'done' && b.status !== 'error');
  failuresThisRun = [];
  emit();
}

/** Cancel everything in flight and empty the queue. */
export function cancelAll() {
  for (const it of [...items]) dismissItem(it.id);
  for (const b of [...bundles]) void dismissBundle(b.id);
  items = [];
  bundles = [];
  failuresThisRun = [];
  emit();
}

// --- failure reporting -----------------------------------------------------

/**
 * One Slack post per drained queue, never per file.
 *
 * A dropped connection fails every queued file at once; reporting each one
 * would put a wall of red in #supply-side for a single event. Cancels never
 * get here — they're filtered at the point of failure — and a successful retry
 * removes its entry before this runs.
 */
async function reportFailuresIfSettled() {
  if (!isIdle()) return;
  if (failuresThisRun.length === 0) return;
  const failures = failuresThisRun;
  failuresThisRun = [];
  try {
    const t = await (
      await fetch('/api/csrf', { credentials: 'include' })
    ).json();
    await fetch('/api/media/upload/report-failure', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': t.csrf_token,
      },
      body: JSON.stringify({
        failures: failures.map((f) => ({
          name: f.name,
          message: f.message,
        })),
      }),
    });
  } catch {
    // Reporting a failure must never itself become a visible failure.
  }
}

/** Test seam — the browser harness asserts on a clean queue between runs. */
export function __resetForTests() {
  items = [];
  bundles = [];
  failuresThisRun = [];
  itemXhrs.clear();
  emit();
}
