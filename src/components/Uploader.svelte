<!--
  Uploader — drag-and-drop / browse file picker for any destination.

  It used to run the transfers itself, which is why navigating away killed
  them: this component lives inside the page, and ViewTransitions destroys the
  page. Now it only *picks* files and hands them to the persistent UploadDock
  via an `upload:start` event; the dock owns the queue, the progress and the
  outcome. See `src/lib/uploadQueue.ts`.

  What that means for callers: there is no progress UI here any more, and
  `onUploaded` fires from the dock's `upload:done` event — so it only runs if
  you're still on the page when the file lands. The attach itself is
  server-side (`project_id`/`slot_id` are form fields on /api/media/upload), so
  missing the callback costs a refresh, never a file.

  Props:
    - destination: 'tribute' | 'project' — controls auto-attach behaviour
    - projectId: when destination='project', the Latent to attach to
    - slotId: optional slot inside the Latent to attach to
    - defaultTags: comma-separated string of tags pre-applied to every upload
    - compact: render in a compact mode (no shared-fields box, smaller dropzone)
    - onUploaded: called when an upload started from *this* page finishes
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { isLogicBundleDir, looksLikeDirectory } from '../lib/uploadQueue';

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

  let tags = $state(defaultTags);
  let description = $state('');
  let fileInput: HTMLInputElement | null = $state(null);
  let bundleInput: HTMLInputElement | null = $state(null);
  let dragOver = $state(false);
  let handed = $state('');

  let doneHandler: ((e: Event) => void) | null = null;

  function dest() {
    return { destination, projectId, slotId, tags, description };
  }

  function handOff(detail: Record<string, unknown>) {
    document.dispatchEvent(
      new CustomEvent('upload:start', { detail: { ...dest(), ...detail } }),
    );
  }

  function note(n: number) {
    handed = `${n} file${n === 1 ? '' : 's'} handed to the upload bar`;
    setTimeout(() => (handed = ''), 4000);
  }

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list).filter((f) => !looksLikeDirectory(f));
    if (incoming.length === 0) return;
    // Confirm before sweeping in a huge batch — accidental Cmd+A or folder
    // drops on Firefox can balloon the queue without warning otherwise.
    if (incoming.length > 10) {
      if (!confirm(`About to add ${incoming.length} files at once. Continue?`))
        return;
    }
    handOff({ files: incoming });
    note(incoming.length);
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
    // whose name ends in .logicx is a session bundle instead — hand it over
    // whole and let the dock walk it.
    const dtItems = dt.items;
    if (
      dtItems &&
      dtItems.length &&
      typeof (dtItems[0] as any).webkitGetAsEntry === 'function'
    ) {
      const files: File[] = [];
      const dirNames: string[] = [];
      // Capture the entries synchronously — the DataTransfer is neutered as
      // soon as this handler yields.
      const entries = Array.from(dtItems)
        .map((it) => (it as any).webkitGetAsEntry?.())
        .filter(Boolean);
      for (const entry of entries) {
        if (entry.isDirectory) {
          if (isLogicBundleDir(entry.name)) {
            handOff({ bundleEntry: entry });
            note(1);
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
    if (!files.length) return;
    handOff({ bundleFiles: files });
    note(1);
  }

  onMount(() => {
    if (!onUploaded) return;
    // Only fires while this page is still mounted. A departed listener simply
    // never hears it — the file has already landed and attached server-side.
    doneHandler = (e: Event) => {
      const d = (e as CustomEvent).detail || {};
      // Ignore completions bound for a different Latent/slot than ours.
      if ((d.project_id || '') !== (projectId || '')) return;
      if ((d.slot_id || '') !== (slotId || '')) return;
      onUploaded?.({
        media_item_id: d.media_item_id,
        project_id: d.project_id ?? null,
        slot_id: d.slot_id ?? null,
      });
    };
    document.addEventListener('upload:done', doneHandler);
  });

  onDestroy(() => {
    if (doneHandler) document.removeEventListener('upload:done', doneHandler);
  });
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

  {#if handed}
    <div class="summary" role="status" aria-live="polite">
      {handed} — it keeps going if you navigate away.
    </div>
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
    background: color-mix(in srgb, var(--color-accent) 6%, transparent);
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
    .bundle-browse .action-btn {
      min-height: 44px;
      min-width: 44px;
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
  .summary {
    padding: 6px 10px;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    font-size: var(--text-sm);
    color: var(--color-muted);
  }
</style>
