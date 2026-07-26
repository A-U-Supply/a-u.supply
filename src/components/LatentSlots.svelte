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
  import MarginaliaBadge from './MarginaliaBadge.svelte';
  import RowMove from './RowMove.svelte';
  import RowActions from './RowActions.svelte';
  import {
    fetchAnnotationCounts,
    type AnnotationCounts,
  } from './marginalia.ts';
  import { fileExt } from '../lib/fileExt.ts';
  import { DRAG_OPTS } from '../lib/dragOptions.ts';
  import { isPhone } from '../lib/viewport.svelte.ts';
  import { queueMedia } from '../lib/playerQueue.ts';
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
      parent_media_item_id?: string | null;
      duration_seconds?: number | null;
      session_extraction_status?: string | null;
      session_extracted_count?: number | null;
    } | null;
  };

  type Track = {
    item_id: string;
    media_item_id: string;
    slot_id: string | null;
    filename: string | null;
    media_type: string | null;
    duration_seconds: number | null;
  };

  type Playlist = { tracks: Track[]; total_seconds: number };

  type SessionChild = {
    id: string;
    filename: string;
    media_type: string;
    file_size_bytes: number;
    mime_type: string;
    description: string | null;
    duration_seconds: number | null;
    tags: string[];
    created_at: string | null;
  };

  let slots = $state<Slot[]>([]);
  let itemsBySlot = $state<Record<string, Item[]>>({});
  // Playlists are per slot and independent of the panels above — a playlist
  // can stay open while notes or threads are.
  let playlistOpen = $state<Record<string, boolean>>({});
  let playlistBySlot = $state<Record<string, Playlist>>({});
  let playlistLoading = $state<Record<string, boolean>>({});
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

  // Marginalia badge counts, merged per media id as slots/children load.
  let annotationCounts = $state<AnnotationCounts>({});

  async function loadAnnotationCounts(mediaIds: string[]) {
    const fresh = await fetchAnnotationCounts(mediaIds);
    if (Object.keys(fresh).length) {
      annotationCounts = { ...annotationCounts, ...fresh };
    }
  }

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
      watchSessions(slotId);
      // One batched counts request for every visible item in this slot.
      loadAnnotationCounts(
        (body.items || []).map((i: Item) => i.media_item_id),
      );
      // An upload or detach changes what's in the playlist; keep an open one honest.
      if (playlistOpen[slotId]) loadPlaylist(slotId);
    } catch (e: any) {
      error = e?.message || 'Failed to load items';
    }
  }

  // --- File order + playlists ----------------------------------------------
  // Two independent orders per slot: the file rows (project_items.position)
  // and the playlist (an order hint on the slot). Dragging one never moves
  // the other — see docs/plans/2026-07-24-latent-playlists.md.

  function audioCount(slotId: string): number {
    return (itemsBySlot[slotId] || []).filter(
      (i) => i.media?.media_type === 'audio',
    ).length;
  }

  /** Read the DOM order Sortable just produced, from a given row selector. */
  function domOrder(
    list: HTMLElement,
    selector: string,
    attr: string,
  ): string[] {
    return Array.from(list.querySelectorAll<HTMLElement>(selector))
      .map((el) => el.dataset[attr])
      .filter((v): v is string => !!v);
  }

  /** Send a file order. Shared by the drag handles and the arrow buttons. */
  async function postFileOrder(slotId: string, order: string[]) {
    if (!order.length) return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slotId)}/items/reorder`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      itemsBySlot = { ...itemsBySlot, [slotId]: body.items || [] };
    } catch (e: any) {
      error = e?.message || 'Failed to reorder files';
      loadItems(slotId); // resync on failure
    }
  }

  async function persistFileOrder(slotId: string, list: HTMLElement) {
    const order = domOrder(list, '.file-row[data-item-id]', 'itemId');
    if (!order.length) return;
    // Match state to the DOM Sortable already rearranged, so the keyed each
    // block doesn't fight the drop while the request is in flight.
    const byId = new Map((itemsBySlot[slotId] || []).map((i) => [i.id, i]));
    itemsBySlot = {
      ...itemsBySlot,
      [slotId]: order.map((id) => byId.get(id)!).filter(Boolean),
    };
    await postFileOrder(slotId, order);
  }

  async function loadPlaylist(slotId: string) {
    playlistLoading = { ...playlistLoading, [slotId]: true };
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slotId)}/playlist`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      playlistBySlot = { ...playlistBySlot, [slotId]: await res.json() };
    } catch (e: any) {
      error = e?.message || 'Failed to load playlist';
    } finally {
      playlistLoading = { ...playlistLoading, [slotId]: false };
    }
  }

  function togglePlaylist(slotId: string) {
    const open = !playlistOpen[slotId];
    playlistOpen = { ...playlistOpen, [slotId]: open };
    if (!open) return;
    loadPlaylist(slotId);
    // On a phone the button that opens the playlist is above a screenful of
    // file rows, so the playlist lands off-screen. Bring its top into view
    // once it exists (scroll-margin-top clears the sticky section map).
    if (window.matchMedia('(max-width: 640px)').matches) {
      requestAnimationFrame(() => {
        document
          .querySelector(`.playlist[data-slot-id="${slotId}"]`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
  }

  /** Move one track a step through the playlist (the arrow-button path). */
  async function nudgeTrack(slotId: string, index: number, delta: number) {
    const pl = playlistBySlot[slotId];
    if (!pl) return;
    const target = index + delta;
    if (target < 0 || target >= pl.tracks.length) return;
    const tracks = [...pl.tracks];
    [tracks[index], tracks[target]] = [tracks[target], tracks[index]];
    playlistBySlot = { ...playlistBySlot, [slotId]: { ...pl, tracks } };
    await putPlaylistOrder(
      slotId,
      tracks.map((t) => t.media_item_id),
    );
  }

  /** Move one file row a step through the slot's file order. */
  async function nudgeFile(slotId: string, index: number, delta: number) {
    const items = itemsBySlot[slotId] || [];
    const target = index + delta;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    [next[index], next[target]] = [next[target], next[index]];
    itemsBySlot = { ...itemsBySlot, [slotId]: next };
    await postFileOrder(
      slotId,
      next.map((i) => i.id),
    );
  }

  /** Send a playlist order. Shared by the drag handles and the arrow buttons. */
  async function putPlaylistOrder(slotId: string, order: string[]) {
    if (!order.length) return;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slotId)}/playlist`,
        {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      playlistBySlot = { ...playlistBySlot, [slotId]: await res.json() };
    } catch (e: any) {
      error = e?.message || 'Failed to reorder playlist';
      loadPlaylist(slotId); // resync on failure
    }
  }

  async function persistPlaylistOrder(slotId: string, list: HTMLElement) {
    const order = domOrder(list, '.track-row[data-media-id]', 'mediaId');
    if (!order.length) return;
    const current = playlistBySlot[slotId];
    if (current) {
      const byMedia = new Map(current.tracks.map((t) => [t.media_item_id, t]));
      playlistBySlot = {
        ...playlistBySlot,
        [slotId]: {
          ...current,
          tracks: order.map((id) => byMedia.get(id)!).filter(Boolean),
        },
      };
    }
    await putPlaylistOrder(slotId, order);
  }

  function playPlaylist(slotId: string, startIndex = 0) {
    const pl = playlistBySlot[slotId];
    if (!pl?.tracks.length) return;
    queueMedia(
      pl.tracks.map((t) => ({
        id: t.media_item_id,
        media_type: t.media_type || 'audio',
        filename: t.filename,
        duration_seconds: t.duration_seconds,
      })),
      startIndex,
    );
  }

  /** Sortable over a slot's file rows. */
  function sortableFiles(node: HTMLElement, slotId: string) {
    const s = Sortable.create(node, {
      ...DRAG_OPTS,
      handle: '.file-row__drag',
      draggable: '.file-row', // never the expanded session-children row
      ghostClass: 'file-row--ghost',
      onEnd: () => persistFileOrder(slotId, node),
    });
    return { destroy: () => s.destroy() };
  }

  /** Sortable over a slot's playlist rows. */
  function sortableTracks(node: HTMLElement, slotId: string) {
    const s = Sortable.create(node, {
      ...DRAG_OPTS,
      handle: '.track-row__drag',
      draggable: '.track-row',
      ghostClass: 'track-row--ghost',
      onEnd: () => persistPlaylistOrder(slotId, node),
    });
    return { destroy: () => s.destroy() };
  }

  // --- Session bundles: extraction status + extracted children -------------
  // Session rows poll while extraction is pending/processing; once done, the
  // harvested audio appears both as an expandable child list on the session
  // row and as peer rows carrying a "from session" chip.
  /* A slot card arrives collapsed to its summary line at EVERY width — a
     6-slot latent was several screens of card before you could see the shape
     of it. What "open" means still differs: a phone shows one tab at a time,
     desktop stacks everything it has room for. Same markup, different
     visibility conditions. */
  let cardOpen = $state<Record<string, boolean>>({});
  let statusOpen = $state<Record<string, boolean>>({});
  let toolsOpen = $state<Record<string, boolean>>({});
  let slotTab = $state<Record<string, string>>({});
  const TAB_FILES = 'files';
  function tabOf(id: string): string {
    return slotTab[id] || TAB_FILES;
  }
  /** True when a block should render: always on desktop, per-tab on a phone. */
  function shows(id: string, tab: string): boolean {
    return !isPhone() || tabOf(id) === tab;
  }
  function isOpen(id: string): boolean {
    return !!cardOpen[id];
  }
  /** Counts live in the label so a closed tab still reports its contents. */
  function tabsFor(slot: Slot): { key: string; label: string }[] {
    const audio = audioCount(slot.id);
    const tabs = [{ key: 'files', label: `Files(${slot.item_count ?? 0})` }];
    if (audio > 0) tabs.push({ key: 'playlist', label: `Playlist(${audio})` });
    tabs.push({ key: 'add', label: 'Add files' });
    tabs.push({ key: 'links', label: 'Links' });
    tabs.push({ key: 'notes', label: 'Notes' });
    tabs.push({
      key: 'threads',
      label: slot.thread_count ? `Threads(${slot.thread_count})` : 'Threads',
    });
    /* Shown whenever the latent has a repo, not just when this slot is
       already linked: the link/run/unlink controls live in this tab, so
       gating it on repo_path left no way to link a slot from a phone. */
    if (repoMeta)
      tabs.push({ key: 'runs', label: slot.repo_path ? 'Runs' : 'Repo' });
    return tabs;
  }

  function pickTab(id: string, tab: string) {
    slotTab[id] = tab;
    if (tab === 'playlist' && !playlistBySlot[id]) loadPlaylist(id);
  }

  let expandedSessions = $state<Record<string, boolean>>({});
  let childrenByMedia = $state<Record<string, SessionChild[]>>({});
  let childrenLoading = $state<Record<string, boolean>>({});
  let sessionErrors = $state<Record<string, string>>({});
  let slotsRootEl: HTMLElement | null = $state(null);
  const sessionPollers = new Set<string>();
  const sessionErrorFetches = new Set<string>();

  function watchSessions(slotId: string) {
    for (const it of itemsBySlot[slotId] || []) {
      if (it.media?.media_type !== 'session') continue;
      const st = it.media.session_extraction_status;
      if (st === 'pending' || st === 'processing') {
        ensureSessionPoll(slotId, it.media_item_id);
      } else if (st === 'failed' && !sessionErrors[it.media_item_id]) {
        void fetchSessionError(it.media_item_id);
      }
    }
  }

  function ensureSessionPoll(slotId: string, mediaId: string) {
    if (sessionPollers.has(mediaId)) return;
    sessionPollers.add(mediaId);
    void pollSession(slotId, mediaId);
  }

  async function pollSession(slotId: string, mediaId: string) {
    try {
      // ~30 min at 3s — extraction of a big bundle can take a while.
      for (let attempt = 0; attempt < 600; attempt++) {
        await new Promise((r) => setTimeout(r, 3000));
        const res = await fetch(`/api/media/${encodeURIComponent(mediaId)}`, {
          credentials: 'include',
        });
        if (!res.ok) return;
        const body = await res.json();
        const meta = body?.session_meta;
        const st = meta?.extraction_status ?? null;
        if (st === 'done' || st === 'failed') {
          if (st === 'failed' && meta?.extraction_error) {
            sessionErrors = {
              ...sessionErrors,
              [mediaId]: meta.extraction_error,
            };
          }
          updateSessionItem(slotId, mediaId, st, meta?.extracted_count ?? 0);
          // Children attach as slot peers — refetch so they (and the slot's
          // file count) show up without a manual reload.
          await loadItems(slotId);
          await load();
          return;
        }
      }
    } catch {
    } finally {
      sessionPollers.delete(mediaId);
    }
  }

  function updateSessionItem(
    slotId: string,
    mediaId: string,
    status: string,
    count: number,
  ) {
    itemsBySlot = {
      ...itemsBySlot,
      [slotId]: (itemsBySlot[slotId] || []).map((i) =>
        i.media_item_id === mediaId && i.media
          ? {
              ...i,
              media: {
                ...i.media,
                session_extraction_status: status,
                session_extracted_count: count,
              },
            }
          : i,
      ),
    };
  }

  async function fetchSessionError(mediaId: string) {
    if (sessionErrorFetches.has(mediaId)) return;
    sessionErrorFetches.add(mediaId);
    try {
      const res = await fetch(`/api/media/${encodeURIComponent(mediaId)}`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      const body = await res.json();
      const msg = body?.session_meta?.extraction_error;
      if (msg) sessionErrors = { ...sessionErrors, [mediaId]: msg };
    } catch {
    } finally {
      sessionErrorFetches.delete(mediaId);
    }
  }

  async function toggleSessionChildren(mediaId: string) {
    const open = !expandedSessions[mediaId];
    expandedSessions = { ...expandedSessions, [mediaId]: open };
    if (!open || childrenByMedia[mediaId]) return;
    childrenLoading = { ...childrenLoading, [mediaId]: true };
    try {
      const res = await fetch(
        `/api/media/${encodeURIComponent(mediaId)}/children`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      childrenByMedia = { ...childrenByMedia, [mediaId]: body.children || [] };
      // Extracted children can carry their own annotations (+ inherited
      // session cues) — badge them too.
      loadAnnotationCounts(
        (body.children || []).map((c: SessionChild) => c.id),
      );
    } catch (e: any) {
      error = e?.message || 'Failed to load extracted files';
    } finally {
      childrenLoading = { ...childrenLoading, [mediaId]: false };
    }
  }

  function parentInSlot(slotId: string, parentId: string): Item | undefined {
    return (itemsBySlot[slotId] || []).find(
      (i) => i.media_item_id === parentId,
    );
  }

  function scrollToParent(mediaId: string) {
    const el = slotsRootEl?.querySelector(
      `[data-media-id="${CSS.escape(mediaId)}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('file-row--flash');
    setTimeout(() => el.classList.remove('file-row--flash'), 1600);
  }

  function fmtDuration(s: number | null | undefined): string {
    if (s == null || !isFinite(s)) return '';
    const total = Math.round(s);
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${mins}:${String(secs).padStart(2, '0')}`;
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

  async function renameMediaItem(slot: Slot, item: Item) {
    const current = item.media?.filename || '';
    const name = prompt('Rename file:', current);
    if (!name || name === current) return;
    try {
      const res = await fetch(
        `/api/media/${encodeURIComponent(item.media_item_id)}`,
        {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: name }),
        },
      );
      if (!res.ok) {
        if (res.status === 409)
          throw new Error('A file with that name already exists');
        throw new Error(`Failed (${res.status})`);
      }
      const body = await res.json();
      itemsBySlot = {
        ...itemsBySlot,
        [slot.id]: (itemsBySlot[slot.id] || []).map((i) =>
          i.id === item.id && i.media
            ? { ...i, media: { ...i.media, filename: body.filename } }
            : i,
        ),
      };
    } catch (e: any) {
      error = e?.message || 'Failed to rename';
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
    queueMedia([{ id: mediaId, media_type: mediaType, filename: title }]);
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
      ...DRAG_OPTS,
      handle: '.slot__drag',
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

<section class="slots" bind:this={slotsRootEl}>
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
            <!-- Collapsed, the summary IS the status: label, counts, state.
                 The whole line is the open/close target at every width, so
                 renaming can't also live on it — a phone reaches Rename
                 through Slot tools, desktop through the Rename button in the
                 controls row below. -->
            <button
              class="slot__summary"
              type="button"
              aria-expanded={!!cardOpen[slot.id]}
              onclick={() => (cardOpen[slot.id] = !cardOpen[slot.id])}
            >
              <span class="slot__caret" aria-hidden="true"
                >{cardOpen[slot.id] ? '▾' : '▸'}</span
              >
              <span class="slot__label-text">{slot.label}</span>
              <span class="slot__summary-meta">
                {slot.item_count ?? 0} file{(slot.item_count ?? 0) === 1
                  ? ''
                  : 's'}{#if audioCount(slot.id) > 0}
                  · {audioCount(slot.id)} audio{/if}
              </span>
              <span
                class="slot__summary-state"
                style="--c: {statusColor(slot.status)}">{slot.status}</span
              >
            </button>
          </div>
          <div class="slot__controls-row">
            {#if isPhone()}
              {#if cardOpen[slot.id]}
                <!-- One control that says its own value, instead of three
                     0.7rem pills that never met the touch rule. -->
                <button
                  class="action-btn slot__tool-btn"
                  type="button"
                  aria-expanded={!!statusOpen[slot.id]}
                  onclick={() => (statusOpen[slot.id] = !statusOpen[slot.id])}
                  >Status: {slot.status}
                  {statusOpen[slot.id] ? '▴' : '▾'}</button
                >
                <LatentStyleButton
                  {projectId}
                  scope="slot"
                  slotId={slot.id}
                  accent={slotAccent(slot)}
                  noInherit
                />
                <button
                  class="action-btn slot__tool-btn"
                  type="button"
                  aria-expanded={!!toolsOpen[slot.id]}
                  onclick={() => (toolsOpen[slot.id] = !toolsOpen[slot.id])}
                  >Slot tools {toolsOpen[slot.id] ? '▴' : '▾'}</button
                >
              {/if}
            {:else if cardOpen[slot.id]}
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
                <!-- The summary line is the open/close target now, so the
                     click-the-title-to-rename affordance needs its own door.
                     Removing it outright would delete a capability. -->
                <button
                  class="action-btn"
                  type="button"
                  title="Rename this slot"
                  onclick={() => renameSlot(slot)}>Rename</button
                >
                {#if audioCount(slot.id) > 0}
                  <button
                    class="action-btn playlist-toggle"
                    type="button"
                    aria-expanded={!!playlistOpen[slot.id]}
                    title="The audio in this slot, in an order you set"
                    onclick={() => togglePlaylist(slot.id)}
                    >{playlistOpen[slot.id] ? '▾' : '▶'} Playlist ({audioCount(
                      slot.id,
                    )})</button
                  >
                {/if}
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
            {/if}
          </div>

          {#if isPhone() && cardOpen[slot.id]}
            {#if statusOpen[slot.id]}
              <div class="slot__menu" role="group" aria-label="Slot status">
                {#each ['forming', 'developing', 'fixed'] as st}
                  <button
                    class="slot__menu-item"
                    type="button"
                    aria-pressed={slot.status === st}
                    onclick={() => {
                      setStatus(slot, st);
                      statusOpen[slot.id] = false;
                    }}>{st}{slot.status === st ? ' · current' : ''}</button
                  >
                {/each}
              </div>
            {/if}
            {#if toolsOpen[slot.id]}
              <div class="slot__menu" role="group" aria-label="Slot tools">
                <button
                  class="slot__menu-item"
                  type="button"
                  onclick={() => {
                    toolsOpen[slot.id] = false;
                    renameSlot(slot);
                  }}>Rename slot</button
                >
                {#if (slot.item_count ?? 0) > 0}
                  <button
                    class="slot__menu-item slot__menu-item--danger"
                    type="button"
                    title="Detach + permanently delete every Emulsion file on this slot"
                    onclick={() => {
                      toolsOpen[slot.id] = false;
                      clearSlotItems(slot);
                    }}>Clear all files</button
                  >
                {/if}
                <button
                  class="slot__menu-item slot__menu-item--danger"
                  type="button"
                  onclick={() => {
                    toolsOpen[slot.id] = false;
                    deleteSlot(slot);
                  }}>Delete slot</button
                >
              </div>
            {/if}

            <!-- Every job keeps a visible, labeled, counted door. Boxed and
                 wrapping — never a scroll strip, never shrink-to-fit. -->
            <div
              class="slot__tabs"
              role="tablist"
              aria-label={`${slot.label} sections`}
            >
              {#each tabsFor(slot) as t (t.key)}
                <button
                  class="slot__tab"
                  type="button"
                  role="tab"
                  aria-selected={tabOf(slot.id) === t.key}
                  onclick={() => pickTab(slot.id, t.key)}>{t.label}</button
                >
              {/each}
            </div>
          {/if}
        </div>

        {#if repoMeta && isOpen(slot.id) && (!isPhone() || shows(slot.id, 'runs'))}
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
        {#if isOpen(slot.id) && (shows(slot.id, 'files') || shows(slot.id, 'playlist') || shows(slot.id, 'add') || shows(slot.id, 'links'))}
          <div class="slot__panel">
            {#if shows(slot.id, 'files')}
              {#if (itemsBySlot[slot.id] || []).length > 0}
                <ul class="file-list" use:sortableFiles={slot.id}>
                  {#each itemsBySlot[slot.id] as it, i (it.id)}
                    <li
                      class="file-row"
                      class:file-row--primary={it.is_primary}
                      data-type={it.media?.media_type}
                      data-media-id={it.media_item_id}
                      data-item-id={it.id}
                    >
                      <RowMove
                        label={it.media?.filename || 'file'}
                        handleClass="file-row__drag"
                        upDisabled={i === 0}
                        downDisabled={i ===
                          (itemsBySlot[slot.id] || []).length - 1}
                        onUp={() => nudgeFile(slot.id, i, -1)}
                        onDown={() => nudgeFile(slot.id, i, 1)}
                      />
                      <div class="file-row__main">
                        <button
                          class="file-row__star"
                          type="button"
                          aria-label={it.is_primary
                            ? `${it.media?.filename || 'This file'} is the card image — tap to unstar`
                            : `Use ${it.media?.filename || 'this file'} as the card image`}
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
                              >{it.media?.media_type?.[0]?.toUpperCase() ||
                                '?'}</span
                            >
                          {/if}
                        </a>
                        <span class="file-row__name-wrap">
                          <a
                            class="file-row__name"
                            href={`/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`}
                            title={it.media?.filename}
                          >
                            {it.media?.filename || '(unknown)'}
                          </a>
                          {#if it.media?.media_type === 'session'}
                            {@const st = it.media.session_extraction_status}
                            {@const count =
                              it.media.session_extracted_count ?? 0}
                            {#if st === 'pending' || st === 'processing'}
                              <span class="session-pill session-pill--busy"
                                >Extracting audio…</span
                              >
                            {:else if st === 'failed'}
                              <span
                                class="session-pill session-pill--error"
                                title={sessionErrors[it.media_item_id] ||
                                  'Audio extraction failed'}
                                >Extraction failed</span
                              >
                            {:else if count > 0}
                              <button
                                class="session-pill session-pill--toggle"
                                type="button"
                                aria-expanded={!!expandedSessions[
                                  it.media_item_id
                                ]}
                                onclick={() =>
                                  toggleSessionChildren(it.media_item_id)}
                                >{expandedSessions[it.media_item_id]
                                  ? '▾'
                                  : '▸'}
                                {count} extracted file{count === 1
                                  ? ''
                                  : 's'}</button
                              >
                            {:else if st === 'done'}
                              <span
                                class="session-pill"
                                title="No audio files found in this bundle"
                                >0 extracted</span
                              >
                            {/if}
                          {:else if it.media?.parent_media_item_id}
                            {@const parent = parentInSlot(
                              slot.id,
                              it.media.parent_media_item_id,
                            )}
                            {#if parent}
                              <button
                                class="session-chip"
                                type="button"
                                title={`Extracted from ${parent.media?.filename || 'session bundle'} — click to jump to it`}
                                onclick={() =>
                                  scrollToParent(parent.media_item_id)}
                                >from session</button
                              >
                            {:else}
                              <span
                                class="session-chip"
                                title="Extracted from a session bundle"
                                >extracted</span
                              >
                            {/if}
                          {/if}
                        </span>
                        <span class="file-row__type" title={it.media?.mime_type}
                          >{fileExt(it.media?.filename) ||
                            it.media?.media_type ||
                            'file'}</span
                        >
                        <span class="file-row__size"
                          >{fmtBytes(it.media?.file_size_bytes)}</span
                        >
                        <div class="file-row__actions">
                          {#if it.media}
                            <MarginaliaBadge
                              mediaId={it.media_item_id}
                              mediaType={it.media.media_type}
                              filename={it.media.filename || ''}
                              counts={annotationCounts[it.media_item_id] ||
                                null}
                              showEmpty={isPhone()}
                            />
                          {/if}
                          {#if it.media?.media_type === 'audio' || it.media?.media_type === 'video' || it.media?.media_type === 'midi'}
                            <button
                              class="action-btn file-row__play"
                              type="button"
                              aria-label={`Play ${it.media?.filename || 'file'}`}
                              title="Play (queues in the persistent Player)"
                              onclick={() =>
                                playInPlayer(
                                  it.media_item_id,
                                  it.media!.media_type,
                                  it.media?.filename || '',
                                )}>{isPhone() ? '▶ Play' : '▶'}</button
                            >
                          {/if}
                          <RowActions
                            label={it.media?.filename || 'this file'}
                            meta={[
                              fileExt(it.media?.filename) ||
                                it.media?.media_type ||
                                'file',
                              fmtBytes(it.media?.file_size_bytes),
                              fmtDuration(it.media?.duration_seconds),
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                            actions={[
                              {
                                label: 'Rename',
                                title: 'Rename this file',
                                onClick: () => renameMediaItem(slot, it),
                              },
                              {
                                label: 'Download',
                                href: `/api/media/${encodeURIComponent(it.media_item_id)}/file`,
                                download: it.media?.filename || undefined,
                              },
                              {
                                label: 'Open in Stacks',
                                href: `/admin/search/detail?id=${encodeURIComponent(it.media_item_id)}`,
                              },
                              {
                                label: 'Remove from slot',
                                danger: true,
                                title:
                                  'Remove from slot. File stays in Emulsion.',
                                onClick: () => detachItem(slot, it),
                              },
                              {
                                label: 'Delete permanently',
                                danger: true,
                                title:
                                  'Permanently delete from Emulsion. Cannot be undone.',
                                onClick: () => deleteMediaItem(slot, it),
                              },
                            ]}
                          />
                        </div>
                      </div>
                    </li>
                    {#if it.media?.media_type === 'session' && expandedSessions[it.media_item_id]}
                      <li class="session-children">
                        {#if childrenLoading[it.media_item_id]}
                          <div class="muted session-children__status">
                            Loading extracted files…
                          </div>
                        {:else if (childrenByMedia[it.media_item_id] || []).length === 0}
                          <div class="muted session-children__status">
                            No extracted files found.
                          </div>
                        {:else}
                          <ul class="session-children__list">
                            {#each childrenByMedia[it.media_item_id] || [] as child (child.id)}
                              <li class="child-row">
                                <a
                                  class="child-row__name"
                                  href={`/admin/search/detail?id=${encodeURIComponent(child.id)}`}
                                  title={child.filename}>{child.filename}</a
                                >
                                <span class="child-row__dur"
                                  >{fmtDuration(child.duration_seconds)}</span
                                >
                                <span class="child-row__size"
                                  >{fmtBytes(child.file_size_bytes)}</span
                                >
                                <MarginaliaBadge
                                  mediaId={child.id}
                                  mediaType={child.media_type}
                                  filename={child.filename || ''}
                                  counts={annotationCounts[child.id] || null}
                                />
                                {#if child.media_type === 'audio' || child.media_type === 'midi'}
                                  <button
                                    class="action-btn"
                                    type="button"
                                    aria-label={`Play ${child.filename || 'extracted file'}`}
                                    title="Play (queues in the persistent Player)"
                                    onclick={() =>
                                      playInPlayer(
                                        child.id,
                                        child.media_type,
                                        child.filename,
                                      )}>▶</button
                                  >
                                {/if}
                              </li>
                            {/each}
                          </ul>
                        {/if}
                      </li>
                    {/if}
                  {/each}
                </ul>
              {/if}
            {/if}
            <!--
            The playlist: this slot's audio, in an order of its own. Expands
            inline (the ColorPicker accordion technique) rather than as a
            popover, so it inherits the card's layout on every width.
          -->
            {#if isPhone() ? shows(slot.id, 'playlist') : playlistOpen[slot.id]}
              {@const pl = playlistBySlot[slot.id]}
              <div class="playlist" data-slot-id={slot.id}>
                <div class="playlist__head">
                  <button
                    class="action-btn playlist__play-all"
                    type="button"
                    disabled={!pl?.tracks.length}
                    onclick={() => playPlaylist(slot.id)}>▷ Play all</button
                  >
                  {#if pl}
                    <span class="muted playlist__meta"
                      >{pl.tracks.length} track{pl.tracks.length === 1
                        ? ''
                        : 's'}{pl.total_seconds
                        ? ` · ${fmtDuration(pl.total_seconds)}`
                        : ''}</span
                    >
                  {/if}
                </div>
                {#if playlistLoading[slot.id] && !pl}
                  <div class="muted playlist__status">Loading playlist…</div>
                {:else if pl && pl.tracks.length === 0}
                  <div class="muted playlist__status">
                    No audio in this slot yet. Uploaded audio joins the playlist
                    automatically.
                  </div>
                {:else if pl}
                  <ul class="playlist__list" use:sortableTracks={slot.id}>
                    {#each pl.tracks as t, i (t.media_item_id)}
                      <li class="track-row" data-media-id={t.media_item_id}>
                        <RowMove
                          label={t.filename || 'track'}
                          handleClass="track-row__drag"
                          upDisabled={i === 0}
                          downDisabled={i === pl.tracks.length - 1}
                          onUp={() => nudgeTrack(slot.id, i, -1)}
                          onDown={() => nudgeTrack(slot.id, i, 1)}
                        />
                        <div class="track-row__main">
                          <span class="track-row__pos">{i + 1}</span>
                          <span class="track-row__name"
                            >{t.filename || '—'}</span
                          >
                          <span class="track-row__dur"
                            >{fmtDuration(t.duration_seconds)}</span
                          >
                          <button
                            class="action-btn track-row__play"
                            type="button"
                            aria-label={`Play ${t.filename || 'track'} from here`}
                            title="Play from here"
                            onclick={() => playPlaylist(slot.id, i)}
                            >{isPhone() ? '▶ Play' : '▶'}</button
                          >
                          <MarginaliaBadge
                            mediaId={t.media_item_id}
                            mediaType={t.media_type || 'audio'}
                            filename={t.filename || ''}
                            counts={annotationCounts[t.media_item_id] || null}
                            showEmpty={isPhone()}
                          />
                        </div>
                      </li>
                    {/each}
                  </ul>
                {/if}
              </div>
            {/if}
            {#if shows(slot.id, 'links')}
              <LatentLinks
                {projectId}
                slotId={slot.id}
                title="Slot links"
                compact={true}
              />
            {/if}
            {#if shows(slot.id, 'add')}
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
            {/if}
          </div>
        {/if}

        {#if isOpen(slot.id) && (isPhone() ? shows(slot.id, 'notes') : openSlot === slot.id && openSection === 'notes')}
          <div class="slot__panel">
            <textarea
              rows="6"
              placeholder="Slot notes — lyrics, mix notes, ideas. Autosaves."
              bind:value={slot.notes}
              oninput={() => scheduleNotesSave(slot)}
            ></textarea>
          </div>
        {/if}

        {#if isOpen(slot.id) && (isPhone() ? shows(slot.id, 'threads') : openSlot === slot.id && openSection === 'threads')}
          <div class="slot__panel">
            <Threads
              anchorType="slot"
              anchorId={slot.id}
              title="Slot discussion"
              compact={true}
            />
          </div>
        {/if}

        {#if isOpen(slot.id) && (isPhone() ? shows(slot.id, 'runs') : openSlot === slot.id && openSection === 'runs')}
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
    touch-action: none;
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
    /* The name column needs a real floor. As a bare `1fr` (min-width:0 via
       .file-row__name) it collapsed to zero the moment the actions column got
       wide — which is exactly what happened when row actions became words, and
       desktop rows showed no filename at all. */
    grid-template-columns: 20px 24px 32px minmax(14ch, 1fr) auto auto auto;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border);
    font-size: var(--text-sm);
  }
  /* Drag handles: the ONLY element that swallows touch gestures. Putting
     `touch-action: none` on the row would stop the list scrolling. */
  .drag-handle {
    background: transparent;
    border: 0;
    color: var(--color-muted);
    cursor: grab;
    font-size: 0.9rem;
    line-height: 1;
    padding: 0 2px;
    touch-action: none;
    align-self: stretch;
  }
  .drag-handle:active {
    cursor: grabbing;
  }
  /* Acknowledge a long-press before anything moves — without this a touch
     drag reads as a dead tap. Applied by Sortable's chosenClass. */
  :global(.drag-chosen) {
    outline: 2px solid var(--color-accent);
    outline-offset: -2px;
    box-shadow: 0 2px 8px var(--color-overlay-soft);
  }
  .file-row--ghost,
  .track-row--ghost {
    opacity: 0.4;
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
  /* Wrap, never truncate — the tail of a generated filename is the part that
     tells them apart (…-seed-4471-v2.png). Loose tiles got this in #541; slot
     rows were the deferred half of it. */
  .file-row__name {
    color: var(--color-text);
    text-decoration: none;
    overflow-wrap: anywhere;
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
  /* Transparent on desktop: children stay direct grid items of the row. */
  .file-row__main,
  .track-row__main {
    display: contents;
  }
  .file-row__actions {
    display: flex;
    gap: 2px;
  }
  .file-row__actions .action-btn {
    padding: 2px 6px;
    font-size: 0.75rem;
  }
  .file-row__name-wrap {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }
  .file-row__name-wrap .file-row__name {
    flex: 0 1 auto;
  }
  /* --- Playlist accordion ------------------------------------------------ */
  .playlist {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    margin-top: var(--space-sm);
  }
  .playlist__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex-wrap: wrap;
    padding: 6px 8px;
    border-bottom: 1px solid var(--color-border);
    background: var(--color-surface);
  }
  .playlist__meta {
    font-family: var(--font-mono);
    font-size: 0.7rem;
  }
  .playlist__status {
    padding: 8px;
    font-size: var(--text-sm);
  }
  .playlist__list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .track-row {
    display: grid;
    grid-template-columns: 20px 2ch 1fr auto auto;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border);
    font-size: var(--text-sm);
  }
  .track-row:last-child {
    border-bottom: 0;
  }
  .track-row:hover {
    background: var(--color-surface);
  }
  .track-row__pos {
    font-family: var(--font-mono);
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .track-row__name {
    min-width: 0;
    overflow-wrap: anywhere; /* session filenames are long; never truncate */
  }
  .track-row__dur {
    color: var(--color-muted);
    font-family: var(--font-mono);
    font-size: 0.7rem;
    white-space: nowrap;
  }
  .track-row__play {
    padding: 2px 6px;
    font-size: 0.75rem;
  }
  .playlist {
    /* Cleared past the sticky section map when scrolled into view on open. */
    scroll-margin-top: 72px;
  }
  .session-pill,
  .session-chip {
    border: 1px solid var(--color-border);
    background: transparent;
    color: var(--color-muted);
    font: inherit;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    padding: 1px 6px;
    white-space: nowrap;
  }
  .session-pill--busy {
    color: var(--color-accent);
    border-color: var(--color-accent);
  }
  .session-pill--error {
    color: #c00;
    border-color: #c00;
  }
  button.session-pill,
  button.session-chip {
    cursor: pointer;
  }
  button.session-pill:hover,
  button.session-chip:hover {
    color: var(--color-accent);
    border-color: var(--color-accent);
  }
  .file-row--flash {
    animation: row-flash 1.6s ease-out;
  }
  @keyframes row-flash {
    0% {
      background: rgba(184, 134, 11, 0.35);
    }
    100% {
      background: transparent;
    }
  }
  .session-children {
    border-bottom: 1px solid var(--color-border);
    background: var(--color-surface);
    padding: 2px 8px 2px 80px;
  }
  .session-children__status {
    padding: 4px 0;
    font-size: var(--text-sm);
  }
  .session-children__list {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .child-row {
    display: grid;
    grid-template-columns: 1fr auto auto auto auto;
    align-items: center;
    gap: 8px;
    padding: 3px 0;
    font-size: var(--text-sm);
    border-bottom: 1px dashed var(--color-border);
  }
  .child-row:last-child {
    border-bottom: 0;
  }
  .child-row__name {
    color: var(--color-text);
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .child-row__name:hover {
    color: var(--color-accent);
  }
  .child-row__dur,
  .child-row__size {
    color: var(--color-muted);
    font-family: var(--font-mono);
    font-size: 0.7rem;
    white-space: nowrap;
  }
  .child-row .action-btn {
    padding: 2px 6px;
    font-size: 0.75rem;
  }
  /* ── Phone card chrome (summary, menus, tabs) ───────────────────────── */
  /* The label owns its own line — the same lesson as .slot__title-row above.
     On one shared row a long slot name got squeezed between the caret, the
     counts and the status chip and wrapped into several lines. */
  .slot__summary {
    display: grid;
    grid-template-columns: auto 1fr auto;
    grid-template-areas:
      'caret label label'
      '.     meta  state';
    column-gap: 6px;
    row-gap: 2px;
    align-items: center;
    flex: 1 1 auto;
    min-width: 0;
    font: inherit;
    background: none;
    border: 0;
    /* inherit, like the .slot__label it replaced: .slot__head carries the
       face-aware colour (--slot-text / --color-on-overlay) and the summary
       must ride it, or a faced card's title goes dark-on-dark. */
    color: inherit;
    padding: 4px 0;
    min-height: 44px;
    text-align: left;
    cursor: pointer;
  }
  .slot__caret {
    grid-area: caret;
    color: var(--color-muted);
    align-self: start;
    padding-top: 2px;
  }
  .slot__label-text {
    grid-area: label;
    font-weight: 700;
    /* Wraps only when the name genuinely needs two lines, not because a chip
       is competing for the same row. */
    overflow-wrap: anywhere;
    line-height: 1.25;
  }
  .slot__summary-meta {
    grid-area: meta;
    font-size: 0.65rem;
    color: var(--color-muted);
    white-space: nowrap;
  }
  .slot__summary-state {
    grid-area: state;
    justify-self: end;
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    border: 1px solid var(--c);
    color: var(--c);
    padding: 0 5px;
  }
  .slot__tool-btn {
    min-height: 44px;
  }
  .slot__menu {
    display: flex;
    flex-direction: column;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    margin-top: 4px;
  }
  .slot__menu-item {
    font: inherit;
    font-size: 0.75rem;
    text-align: left;
    background: none;
    border: 0;
    border-bottom: 1px dotted var(--color-border);
    color: var(--color-text);
    padding: 0 8px;
    min-height: 44px;
    cursor: pointer;
  }
  .slot__menu-item:last-child {
    border-bottom: 0;
  }
  .slot__menu-item--danger {
    color: var(--color-status-fail);
  }
  /* Boxed and wrapping: a scroll strip hides tabs off the card edge, and
     letting them shrink below their labels made them overlap. */
  .slot__tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
    border-bottom: 2px solid var(--color-text);
    margin-top: 6px;
    padding-bottom: 4px;
  }
  .slot__tab {
    font: inherit;
    font-size: 0.66rem;
    text-transform: uppercase;
    background: var(--color-bg);
    border: 1px solid var(--color-border);
    border-bottom: 3px solid var(--color-border);
    color: var(--color-muted);
    padding: 3px 5px;
    line-height: 1.3;
    white-space: nowrap;
    flex: 0 0 auto;
    cursor: pointer;
  }
  .slot__tab[aria-selected='true'] {
    background: var(--color-text);
    color: var(--color-bg);
    font-weight: 700;
    border-color: var(--color-text);
    border-bottom-color: var(--color-accent);
  }
  .slot__summary:focus-visible,
  .slot__tab:focus-visible,
  .slot__menu-item:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: -2px;
  }

  @media (max-width: 640px) {
    /* The card's own grip had no touch target at any width. */
    .slot__drag {
      min-height: 44px;
      min-width: 32px;
    }
    /* Flex, not grid: as a grid the reorder block spanned both rows, and the
       browser split its leftover height between them — filename pinned to the
       top, actions pushed to the bottom, ~60px of dead air in between. As a
       wrapping flex row the identity line and the actions line simply stack
       at their own heights next to the block. */
    .file-row {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      column-gap: 6px;
      row-gap: 4px;
      padding: 6px 8px;
    }
    :global(.file-row .row-move) {
      flex: 0 0 68px;
    }
    /* Beside the reorder block, not below it: while the actions were a bare
       wrapping flex item they pushed onto a third line, so every row cost the
       block's height *plus* its own. As a column next to the block the row is
       just the taller of the two. */
    .file-row__main {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      flex: 1 1 0;
      min-width: 0;
      gap: 4px 6px;
    }
    .file-row__star,
    .file-row__thumb {
      flex: 0 0 auto;
    }
    .file-row__name-wrap {
      flex: 1 1 140px;
      min-width: 0;
    }
    /* Its own line inside the block, full width of it. */
    .file-row__actions {
      flex: 1 0 100%;
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      align-items: center;
    }
    .file-row__actions .action-btn {
      min-height: 44px;
    }
    /* The star used to carry a 44px minimum, which set the height of the
       whole identity line. Size it to the text instead. */
    .file-row__star {
      min-height: 0;
      min-width: 0;
      padding: 0 3px;
      line-height: 1.25;
    }
    /* Same reason: the 32px square was taller than the line it sat in. */
    .file-row__thumb {
      width: 20px;
      height: 20px;
    }
    .file-row__thumb .icon {
      font-size: 0.5rem;
    }
    .file-row__type,
    .file-row__size {
      display: none;
    }
    .file-row__name-wrap {
      flex-wrap: wrap;
    }
    /* With the extension chip and size hidden, a truncated name loses its own
       tail too — wrap instead. (Reverses the desktop-dense choice in #541,
       which doesn't hold at this width.) */
    .file-row__name,
    .file-row__name-wrap .file-row__name {
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
      overflow-wrap: anywhere;
    }
    /* Same failure and same fix as .file-row above: the content sits in a
       block *beside* the reorder control, so the row is the taller of the two
       rather than the sum of them. */
    .track-row {
      display: flex;
      align-items: flex-start;
      column-gap: 6px;
      padding: 6px 8px;
    }
    :global(.track-row .row-move) {
      flex: 0 0 68px;
    }
    .track-row__main {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      flex: 1 1 0;
      min-width: 0;
      gap: 4px 6px;
    }
    .track-row__pos,
    .track-row__dur {
      flex: 0 0 auto;
    }
    .track-row__name {
      flex: 1 1 140px;
      min-width: 0;
    }
    .track-row__play {
      flex: 1 1 auto;
      min-height: 44px;
    }
    .playlist__play-all,
    .playlist-toggle {
      min-height: 44px;
    }
    .session-pill,
    .session-chip {
      min-height: 44px;
      display: inline-flex;
      align-items: center;
    }
    .session-children {
      padding-left: 12px;
    }
    .child-row {
      grid-template-columns: 1fr auto auto;
    }
    .child-row__dur,
    .child-row__size {
      display: none;
    }
    .child-row .action-btn {
      min-height: 44px;
      min-width: 44px;
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
