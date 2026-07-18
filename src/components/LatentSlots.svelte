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
  import LatentLinks from './LatentLinks.svelte';
  import LatentStyleButton from './LatentStyleButton.svelte';
  import { fileExt } from '../lib/fileExt.ts';
  import {
    safeHex,
    effectiveAccent,
    autoTextColor,
    currentTheme,
    watchTheme,
  } from '../lib/latentStyles.ts';

  type Props = {
    projectId: string;
    projectKind: string;
    styleKey?: string | null;
  };

  let { projectId, projectKind, styleKey = null }: Props = $props();

  type Slot = {
    id: string;
    project_id: string;
    position: number;
    label: string;
    status: string;
    notes: string | null;
    notes_updated_at: string | null;
    style?: Record<string, string>;
    accent_auto?: string | null;
    accent?: string | null;
    primary_image_media_id?: string | null;
    pinned: Record<string, string>;
    thread_count?: number;
    item_count?: number;
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
    is_primary?: boolean;
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
  // Surface the most recent finished run per slot so the user sees ✓/✗ + tail
  // after Run completes, instead of the button just snapping back to idle.
  let lastRun = $state<Record<string, any>>({});

  // Inline "link a repo file" editor state — one slot at a time.
  let linkingSlot = $state<string | null>(null);
  let linkPath = $state('');
  let linkCmd = $state('');

  function suggestedPath(slot: Slot): string {
    const pos = String(slot.position).padStart(2, '0');
    const lab = (slot.label || `track-${pos}`)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_|_$/g, '');
    return `tracks/${pos}_${lab}.py`;
  }

  function startLink(slot: Slot) {
    linkingSlot = slot.id;
    // Pre-fill rather than rely on placeholders, so clicking Save with no
    // edits actually persists the suggested path.
    linkPath = slot.repo_path || suggestedPath(slot);
    linkCmd = slot.run_command || `python ${linkPath}`;
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
      announceSlots();
    } catch (e: any) {
      error = e?.message || 'Failed to load slots';
    }
    // Always reload the repo meta alongside slots so source links stay fresh.
    loadRepoMeta();
    // Eagerly fetch items for every slot that has any. Visible-by-default
    // means the cards are unhelpful without this.
    for (const s of slots) {
      if ((s.item_count ?? 0) > 0 && !itemsBySlot[s.id]) {
        loadItems(s.id);
      }
    }
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
      // Poll the job until it leaves pending/running.
      let finalJob: any = null;
      let attempts = 0;
      while (attempts < 720) {
        // ~30 min at 2.5s
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
          finalJob = jb;
          break;
        }
      }
      // Reload slot data so primary pins / items reflect the run output.
      await load();
      await loadItems(slot.id);
      await loadRuns(slot.id);
      // Stash the most recent run for the slot strip's status display.
      const runs = runsBySlot[slot.id] || [];
      if (runs.length) {
        lastRun = { ...lastRun, [slot.id]: runs[0] };
      } else if (finalJob) {
        lastRun = {
          ...lastRun,
          [slot.id]: {
            exit_code: finalJob.status === 'completed' ? 0 : -1,
            stderr_tail: finalJob.error_message || '',
          },
        };
      }
    } catch (e: any) {
      error = e?.message || 'Run failed';
      lastRun = {
        ...lastRun,
        [slot.id]: { exit_code: -1, stderr_tail: e?.message || 'Run failed' },
      };
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
      announceSlots();
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
      announceSlots();
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
      announceSlots();
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

  async function deleteMediaItem(slot: Slot, item: Item) {
    const name = item.media?.filename || 'this file';
    if (
      !confirm(
        `Permanently delete "${name}" from Emulsion?\n\nThis removes it from every Latent and every slot it's attached to, and from the search index. Cannot be undone.`,
      )
    )
      return;
    try {
      const res = await fetch(
        `/api/media/${encodeURIComponent(item.media_item_id)}`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      itemsBySlot = {
        ...itemsBySlot,
        [slot.id]: (itemsBySlot[slot.id] || []).filter(
          (i) => i.media_item_id !== item.media_item_id,
        ),
      };
      await load(); // refresh counts
    } catch (e: any) {
      error = e?.message || 'Delete failed';
    }
  }

  async function togglePrimary(slot: Slot, item: Item) {
    try {
      const next = !item.is_primary;
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/items/${encodeURIComponent(item.id)}/primary`,
        {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_primary: next }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      itemsBySlot = {
        ...itemsBySlot,
        [slot.id]: (itemsBySlot[slot.id] || []).map((i) =>
          i.id === item.id ? { ...i, is_primary: body.is_primary } : i,
        ),
      };
      // The response carries a fresh slot summary so the card's auto
      // accent/background repaints the moment the star lands.
      if (body.slot?.id) {
        slots = slots.map((s) =>
          s.id === body.slot.id ? { ...s, ...body.slot } : s,
        );
        announceSlots(); // the auto accent may have changed
      }
    } catch (e: any) {
      error = e?.message || 'Failed to toggle primary';
    }
  }

  async function clearSlotItems(slot: Slot) {
    const count = slot.item_count ?? 0;
    if (
      !confirm(
        `Clear all ${count} file${count === 1 ? '' : 's'} from "${slot.label}"?\n\nAny Emulsion-only uploads on this slot are permanently deleted from the index. Files shared with other slots/projects are just detached.\n\nCannot be undone.`,
      )
    )
      return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slot.id)}/items?purge=true`,
        { method: 'DELETE', credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      itemsBySlot = { ...itemsBySlot, [slot.id]: [] };
      await load();
    } catch (e: any) {
      error = e?.message || 'Clear failed';
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
      announceSlots();
    } catch (e: any) {
      error = e?.message || 'Failed to reorder';
      await load(); // resync on failure
    }
  }

  function thumbUrl(mediaId: string): string {
    return `/api/media/${encodeURIComponent(mediaId)}/thumbnail?size=sm`;
  }

  function fmtBytes(n: number | null | undefined): string {
    if (n == null) return '';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  function playInPlayer(mediaId: string, mediaType: string, title: string) {
    document.dispatchEvent(
      new CustomEvent('player:queue', {
        detail: {
          tracks: [
            {
              track_id: mediaId,
              title: title || 'Untitled',
              release_title: '',
              release_code: '',
              media_type: mediaType,
              stream_url: `/api/media/${encodeURIComponent(mediaId)}/file`,
              cover_url:
                mediaType === 'image' || mediaType === 'video'
                  ? `/api/media/${encodeURIComponent(mediaId)}/thumbnail`
                  : '/assets/default-cover.jpg',
              duration: 0,
              entity_name: '',
            },
          ],
          startIndex: 0,
        },
      }),
    );
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

  // --- Slot card faces (2026-07-18-latent-faces) ---------------------------
  // All colors are server-validated #rrggbb, but safeHex re-validates before
  // anything reaches a style attribute (the shared injection stance).

  // Solid-face text is computed for the ACTIVE theme; a toggle re-derives
  // every card via this reactive state (watchTheme in onMount).
  let theme = $state<'light' | 'dark'>('light');

  type Face =
    | { kind: 'image'; mediaId: string; treatment: string }
    | { kind: 'solid'; color: string };

  function slotBgMode(slot: Slot): string {
    return slot.style?.bg_mode || 'auto';
  }

  function slotFace(slot: Slot): Face | null {
    const mode = slotBgMode(slot);
    const treatment = (slot.style?.bg_style as string) || 'scrim';
    if (mode === 'auto' && slot.primary_image_media_id)
      return { kind: 'image', mediaId: slot.primary_image_media_id, treatment };
    if (mode === 'image' && slot.style?.bg_media_item_id)
      return { kind: 'image', mediaId: slot.style.bg_media_item_id, treatment };
    if (mode === 'solid') {
      const color = safeHex(slot.style?.bg_color);
      if (color) return { kind: 'solid', color };
    }
    return null;
  }

  function slotFaceTreatment(slot: Slot): string | null {
    const face = slotFace(slot);
    return face?.kind === 'image' ? face.treatment : null;
  }

  function slotAccent(slot: Slot): string | null {
    return effectiveAccent(slot.style, slot.accent_auto ?? null);
  }

  function slotVars(slot: Slot): string {
    const vars: string[] = [];
    const accent = slotAccent(slot);
    if (accent) vars.push(`--slot-accent:${accent}`);
    // Border = the card's LINEWORK: redefining the token scopes every
    // internal line (box, dashed dividers, file rows, dropzone, inputs) to
    // the picked color. Status pills and semantic error reds keep their own
    // colors by design; PullFromIndex mounts outside the li and never sees
    // this.
    const border = safeHex(slot.style?.border);
    if (border) vars.push(`--color-border:${border}`);
    const text = safeHex(slot.style?.text);
    if (text) vars.push(`--slot-text:${text}`);
    const face = slotFace(slot);
    if (face?.kind === 'solid') {
      vars.push(`--slot-bg-color:${face.color}`);
      vars.push(`--slot-face-text:${text || autoTextColor(face.color, theme)}`);
    }
    return vars.join(';');
  }

  // Un-styled slots must render byte-identical to before — chrome only
  // switches on when there's something to show.
  function slotStyled(slot: Slot): boolean {
    return !!(slotVars(slot) || slotFace(slot));
  }

  function bgThumbUrl(mediaId: string): string {
    return `/api/media/${encodeURIComponent(mediaId)}/thumbnail?size=md`;
  }

  function removeBgLayers(e: Event) {
    const img = e.currentTarget as HTMLElement;
    const veil = img.parentElement?.querySelector(':scope > .slot__veil');
    veil?.remove();
    img.remove();
  }

  // Tell the section map (and any future listener) what slots exist now —
  // fired after every mutation that changes the list, an order, a label, or
  // an accent. The style panel's own edits flow through latent-style-changed
  // instead, which the map also hears.
  function announceSlots() {
    window.dispatchEvent(
      new CustomEvent('latent-slots-updated', {
        detail: {
          projectId,
          slots: slots.map((s) => ({
            id: s.id,
            label: s.label,
            position: s.position,
            accent: slotAccent(s),
          })),
        },
      }),
    );
  }

  $effect(() => {
    if (projectId) load();
  });

  function onSlotsChanged() {
    load();
  }

  // Style-panel edits broadcast fresh summaries; swap the matching slot so
  // spine/band/background repaint without a refetch.
  function onStyleChanged(e: Event) {
    const d = (e as CustomEvent).detail;
    if (!d || d.projectId !== projectId || d.scope !== 'slot' || !d.summary)
      return;
    slots = slots.map((s) =>
      s.id === d.summary.id ? { ...s, ...d.summary } : s,
    );
  }

  onMount(() => {
    theme = currentTheme();
    const stopTheme = watchTheme((t) => (theme = t));
    document.addEventListener('latent:slots-changed', onSlotsChanged);
    window.addEventListener('latent-style-changed', onStyleChanged);
    return () => {
      stopTheme();
      document.removeEventListener('latent:slots-changed', onSlotsChanged);
      window.removeEventListener('latent-style-changed', onStyleChanged);
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
  <header class="slots__head" class:latent-band={!!styleKey}>
    <h2>Slots ({projectKind})</h2>
    <span class="muted">{slots.length}</span>
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

  <ul class="slot-list" bind:this={slotListEl}>
    {#each slots as slot (slot.id)}
      <li
        class="slot"
        class:slot--styled={slotStyled(slot)}
        class:slot--faced={!!slotFace(slot)}
        class:slot--face-solid={slotFace(slot)?.kind === 'solid'}
        class:slot--face-scrim={slotFaceTreatment(slot) === 'scrim'}
        class:slot--face-plate={slotFaceTreatment(slot) === 'plate'}
        class:slot--face-treat={slotFaceTreatment(slot) === 'treat'}
        style={slotVars(slot) || undefined}
        data-slot-id={slot.id}
      >
        {#if slotFace(slot)?.kind === 'image'}
          <img
            class="slot__bg"
            src={bgThumbUrl((slotFace(slot) as { mediaId: string }).mediaId)}
            alt=""
            loading="lazy"
            onerror={removeBgLayers}
          />
          <div class="slot__veil"></div>
        {/if}
        <div class="slot__head">
          <!-- Two rows: the title owns its line; controls sit underneath.
               (One shared row squeezed the label into wrapping.) -->
          <div class="slot__title-row">
            <button
              class="slot__drag"
              type="button"
              aria-label="Drag to reorder">⋮⋮</button
            >
            <span class="slot__pos">#{slot.position}</span>
            <button
              class="slot__label"
              onclick={() => renameSlot(slot)}
              type="button"
              title="Click to rename">{slot.label}</button
            >
          </div>
          <div class="slot__controls-row">
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
              <!-- Files are always visible inline below; no Files toggle needed. -->
              <span class="slot__file-count"
                >{slot.item_count ?? 0} file{(slot.item_count ?? 0) === 1
                  ? ''
                  : 's'}</span
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
              {#if (slot.item_count ?? 0) > 0}
                <button
                  class="action-btn action-btn--danger"
                  type="button"
                  title="Detach + permanently delete every Emulsion file on this slot"
                  onclick={() => clearSlotItems(slot)}>Clear files</button
                >
              {/if}
              <button
                class="action-btn action-btn--danger"
                type="button"
                onclick={() => deleteSlot(slot)}>Delete</button
              >
              <LatentStyleButton
                {projectId}
                scope="slot"
                slotId={slot.id}
                accent={slotAccent(slot)}
                noInherit
              />
            </div>
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
              <button
                class="btn-primary btn-run"
                type="button"
                onclick={() => saveLink(slot)}>Save</button
              >
              <button class="action-btn" type="button" onclick={cancelLink}
                >Cancel</button
              >
            {:else if slot.repo_path}
              <span class="muted">Source:</span>
              <a
                class="repo-path"
                href={blobUrl(slot) || '#'}
                target="_blank"
                rel="noopener">{slot.repo_path}</a
              >
              {#if slot.repo_ref}
                <span class="ref-pill" title={slot.repo_ref}
                  >{slot.repo_ref.slice(0, 7)}</span
                >
              {/if}
              <button
                class="link link--small"
                type="button"
                onclick={() => startLink(slot)}>edit</button
              >
              <button
                class="link link--small link--danger"
                type="button"
                onclick={() => clearLink(slot)}>unlink</button
              >
              <span class="spacer"></span>
              <button
                class="btn-primary btn-run"
                type="button"
                onclick={() => runSlot(slot)}
                disabled={runningSlots.has(slot.id)}
                >{runningSlots.has(slot.id) ? '⟳ Running…' : '▶ Run'}</button
              >
              {#if lastRun[slot.id] && !runningSlots.has(slot.id)}
                <div
                  class="run-status"
                  data-ok={lastRun[slot.id].exit_code === 0}
                >
                  {#if lastRun[slot.id].exit_code === 0}
                    ✓ last run ok
                  {:else}
                    ✗ exit {lastRun[slot.id].exit_code} —
                    <button
                      class="link link--small"
                      type="button"
                      onclick={() => toggleSection(slot.id, 'runs')}
                      >show output</button
                    >
                  {/if}
                </div>
              {/if}
            {:else}
              <span class="muted">No source linked.</span>
              <button
                class="action-btn"
                type="button"
                onclick={() => startLink(slot)}>+ Link a repo file</button
              >
            {/if}
          </div>
        {/if}

        <!--
          Files are ALWAYS visible inline. No click-to-expand. The slot card
          eagerly loads its items on mount so users see what's there without
          a hidden affordance. Drag-and-drop / +Upload dropzone sits at the
          bottom of the panel; "+ Pull from index" is right next to it.
        -->
        <div class="slot__panel">
          {#if (itemsBySlot[slot.id] || []).length > 0}
            <ul class="file-list">
              {#each itemsBySlot[slot.id] as it (it.id)}
                <li
                  class="file-row"
                  class:file-row--primary={it.is_primary}
                  data-type={it.media?.media_type}
                >
                  <button
                    class="file-row__star"
                    type="button"
                    title={it.is_primary
                      ? 'Primary (click to unstar)'
                      : 'Mark as primary'}
                    onclick={() => togglePrimary(slot, it)}
                    aria-pressed={it.is_primary}
                    >{it.is_primary ? '★' : '☆'}</button
                  >
                  <a
                    class="file-row__thumb"
                    href={`/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`}
                    title="Open in Stacks"
                  >
                    {#if it.media?.media_type === 'image'}
                      <img
                        src={thumbUrl(it.media_item_id)}
                        alt={it.media?.filename}
                      />
                    {:else}
                      <span class="icon"
                        >{it.media?.media_type?.[0]?.toUpperCase() || '?'}</span
                      >
                    {/if}
                  </a>
                  <a
                    class="file-row__name"
                    href={`/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`}
                    title={it.media?.filename}
                  >
                    {it.media?.filename || '(unknown)'}
                  </a>
                  <span class="file-row__type" title={it.media?.mime_type}
                    >{fileExt(it.media?.filename) ||
                      it.media?.media_type ||
                      'file'}</span
                  >
                  <span class="file-row__size"
                    >{fmtBytes(it.media?.file_size_bytes)}</span
                  >
                  <div class="file-row__actions">
                    {#if it.media?.media_type === 'audio' || it.media?.media_type === 'video'}
                      <button
                        class="action-btn"
                        type="button"
                        title="Play (queues in the persistent Player)"
                        onclick={() =>
                          playInPlayer(
                            it.media_item_id,
                            it.media!.media_type,
                            it.media?.filename || '',
                          )}>▶</button
                      >
                    {/if}
                    <button
                      class="action-btn"
                      type="button"
                      title="Remove from slot. File stays in Emulsion."
                      onclick={() => detachItem(slot, it)}>×</button
                    >
                    <button
                      class="action-btn action-btn--danger"
                      type="button"
                      title="Permanently delete from Emulsion. Cannot be undone."
                      onclick={() => deleteMediaItem(slot, it)}>🗑</button
                    >
                  </div>
                </li>
              {/each}
            </ul>
          {/if}
          <LatentLinks
            {projectId}
            slotId={slot.id}
            title="Slot links"
            compact={true}
          />
          <div class="slot__add">
            <Uploader
              destination="project"
              {projectId}
              slotId={slot.id}
              compact={true}
              onUploaded={() => loadItems(slot.id)}
            />
            <button
              class="action-btn"
              type="button"
              onclick={() => openPull(slot.id)}>+ Pull from index</button
            >
          </div>
        </div>

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
  /* Slot card faces (2026-07-18-latent-faces). Everything rides the
     custom properties emitted (re-validated) by slotVars(); un-styled slots
     take none of these rules and render exactly as before. The face is the
     WHOLE card, rendered with the index-card recipe at full strength. */
  .slot--styled {
    position: relative;
    isolation: isolate;
    border-left: 4px solid var(--slot-accent, var(--color-border));
    color: var(--slot-text, inherit);
  }
  /* Head band survives ONLY for face-less accented cards; a face replaces
     it — the whole card is the identity zone. */
  .slot--styled:not(.slot--faced) .slot__head {
    background: color-mix(
      in srgb,
      var(--slot-accent, transparent) 12%,
      var(--color-surface)
    );
  }
  .slot--faced {
    border-color: color-mix(
      in srgb,
      var(--slot-accent, var(--color-border)) 55%,
      var(--color-border)
    );
    border-left: 4px solid var(--slot-accent, var(--color-border));
  }
  .slot__bg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    z-index: 0;
  }
  .slot__veil {
    position: absolute;
    inset: 0;
    z-index: 1;
    pointer-events: none;
  }
  /* scrim — the index-card recipe with the gradient anchored to the TOP:
     slot heads sit at the top of the card, index-card content at the
     bottom. Deliberate deviation, not drift. */
  .slot--face-scrim .slot__veil {
    background: linear-gradient(
      to bottom,
      var(--color-overlay),
      transparent 70%
    );
  }
  .slot--face-scrim .slot__head {
    color: var(--slot-text, var(--color-on-overlay));
  }
  .slot--face-scrim .slot__head .slot__pos,
  .slot--face-scrim .slot__head .slot__drag,
  .slot--face-scrim .slot__head .slot__file-count {
    color: color-mix(in srgb, var(--color-on-overlay) 75%, transparent);
  }
  /* treat — whole-card duotone; the brightness clamp guarantees a dark
     field for on-overlay text. */
  .slot--face-treat .slot__bg {
    filter: grayscale(1) brightness(0.45);
  }
  .slot--face-treat .slot__veil {
    background: var(--slot-accent, var(--color-overlay-soft));
    mix-blend-mode: color;
    opacity: 0.55;
  }
  .slot--face-treat .slot__head {
    color: var(--slot-text, var(--color-on-overlay));
  }
  .slot--face-treat .slot__head .slot__pos,
  .slot--face-treat .slot__head .slot__drag,
  .slot--face-treat .slot__head .slot__file-count {
    color: color-mix(in srgb, var(--color-on-overlay) 75%, transparent);
  }
  /* plate — image at full strength; head + panels sit on opaque theme
     plates, the face reads through padding and gaps. Theme-native text. */
  .slot--face-plate .slot__head,
  .slot--face-plate .slot__panel {
    background: var(--color-bg);
  }
  /* solid — first-class full-strength color; text auto-contrast-computed
     client-side (--slot-face-text), overridable. */
  .slot--face-solid {
    background: var(--slot-bg-color);
  }
  .slot--face-solid .slot__head {
    color: var(--slot-text, var(--slot-face-text, inherit));
  }
  .slot--styled > :not(.slot__bg):not(.slot__veil) {
    position: relative;
    z-index: 2;
  }
  .slot--ghost {
    opacity: 0.3;
  }
  .slot__head {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px var(--space-sm);
    border-bottom: 1px dashed var(--color-border);
  }
  .slot__title-row {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    min-width: 0;
  }
  .slot__controls-row {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex-wrap: wrap;
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
    min-width: 0; /* full title row — no squeeze, no wrap */
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
    align-items: center;
  }
  .slot__upload-btn {
    padding: 3px 10px;
    font-size: 0.7rem;
  }
  .slot__file-count {
    color: var(--color-muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    margin-right: auto;
  }
  .slot__add {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: var(--space-sm);
    padding-top: var(--space-sm);
    border-top: 1px dashed var(--color-border);
  }
  .slot__add > :global(.uploader) {
    /* compact uploader sits naturally */
  }
  /* Dense file list — replaces the old grid of square tiles. */
  .file-list {
    list-style: none;
    padding: 0;
    margin: 0;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .file-row {
    display: grid;
    grid-template-columns: 24px 32px 1fr auto auto auto;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border);
    font-size: var(--text-sm);
  }
  .file-row:last-child {
    border-bottom: 0;
  }
  .file-row:hover {
    background: var(--color-surface);
  }
  .file-row--primary {
    background: rgba(184, 134, 11, 0.06);
  }
  .file-row__star {
    background: transparent;
    border: 0;
    color: var(--color-muted);
    cursor: pointer;
    font-size: 0.95rem;
    line-height: 1;
    padding: 0;
  }
  .file-row--primary .file-row__star {
    color: var(--color-accent);
  }
  .file-row__thumb {
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--color-surface);
    text-decoration: none;
    color: var(--color-muted);
    overflow: hidden;
  }
  .file-row__thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .file-row__thumb .icon {
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: 0.85rem;
  }
  .file-row__name {
    color: var(--color-text);
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .file-row__name:hover {
    color: var(--color-accent);
  }
  .file-row__type {
    color: var(--color-muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .file-row__size {
    color: var(--color-muted);
    font-family: var(--font-mono);
    font-size: 0.7rem;
    white-space: nowrap;
  }
  .file-row__actions {
    display: flex;
    gap: 2px;
  }
  .file-row__actions .action-btn {
    padding: 2px 6px;
    font-size: 0.75rem;
  }
  @media (max-width: 640px) {
    .file-row {
      grid-template-columns: 24px 32px 1fr auto;
    }
    .file-row__type,
    .file-row__size {
      display: none;
    }
  }

  /* Old grid tile styles kept only for back-compat with any consumer; the
     slot UI uses .file-list above. */
  .tile {
    position: relative;
  }
  .tile__star {
    position: absolute;
    top: 4px;
    left: 4px;
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid var(--color-border);
    color: var(--color-muted);
    cursor: pointer;
    font-size: 0.9rem;
    line-height: 1;
    padding: 0 4px;
    z-index: 1;
  }
  .tile--primary {
    border-color: var(--color-accent);
    box-shadow: 0 0 0 1px var(--color-accent) inset;
  }
  .tile--primary .tile__star {
    color: var(--color-accent);
    background: var(--color-bg);
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
    background: var(--color-surface);
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
    background: var(--color-surface);
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
  .run-status {
    flex-basis: 100%;
    font-size: 0.7rem;
    color: var(--color-muted);
    padding: 2px 0;
  }
  .run-status[data-ok='false'] {
    color: #c00;
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
    background: var(--color-surface);
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
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    }
  }
</style>
