<script>
  import { onMount, onDestroy } from 'svelte';
  import LatentDesktopTaskbar from './LatentDesktopTaskbar.svelte';
  import LatentDesktopScreensaver from './LatentDesktopScreensaver.svelte';

  let { projectId } = $props();

  // ── State ──────────────────────────────────────────────────────────────────

  let latent = $state(null);
  let slots = $state([]);
  let items = $state([]);      // all project items (loose + slotted)
  let loading = $state(true);
  let error = $state(null);

  // Open modals: array of { type: 'slot'|'docs'|'threads'|'links'|'repo'|'file', id?, label? }
  let openModals = $state([]);

  // Context menu
  let ctxVisible = $state(false);
  let ctxX = $state(0);
  let ctxY = $state(0);
  let ctxTarget = $state(null); // null = canvas, else icon id

  // Background
  let bgUrl = $state('');
  let bgPickerOpen = $state(false);

  // Screensaver
  let screensaverActive = $state(false);
  let inactivityTimer = null;
  const INACTIVITY_MS = 10 * 60 * 1000; // 10 minutes

  // System icon positions (docs, threads, links, repo)
  let systemIconPos = $state({ docs: null, threads: null, links: null, repo: null });

  // Drag state
  let dragging = $state(null); // { kind: 'slot'|'system'|'file', id, startX, startY, origX, origY }
  let canvasEl = $state(null);

  // ── Data fetching ──────────────────────────────────────────────────────────

  async function load() {
    loading = true;
    error = null;
    try {
      const [latentRes, itemsRes] = await Promise.all([
        fetch(`/api/latents/${projectId}`, { credentials: 'include' }),
        fetch(`/api/latents/${projectId}/items`, { credentials: 'include' }),
      ]);
      if (!latentRes.ok) throw new Error('Failed to load latent');
      latent = await latentRes.json();
      slots = latent.slots ?? [];

      // Parse desktop metadata
      const meta = latent.metadata ?? {};
      const bgId = meta.desktop_bg_media_id;
      if (bgId) {
        const imgItem = (latent.items ?? []).find(i => i.media?.id === bgId);
        if (imgItem?.media?.file_path) bgUrl = `/api/media/${bgId}/raw`;
      }
      const posJson = meta.desktop_icon_positions;
      if (posJson) {
        try { systemIconPos = { ...systemIconPos, ...JSON.parse(posJson) }; } catch {}
      }

      if (itemsRes.ok) {
        const d = await itemsRes.json();
        items = d.items ?? d ?? [];
      }
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  // ── Screensaver ────────────────────────────────────────────────────────────

  function resetInactivityTimer() {
    clearTimeout(inactivityTimer);
    if (screensaverActive) screensaverActive = false;
    inactivityTimer = setTimeout(() => { screensaverActive = true; }, INACTIVITY_MS);
  }

  function dismissScreensaver() {
    screensaverActive = false;
    resetInactivityTimer();
  }

  // ── Drag (freeform 2D) ─────────────────────────────────────────────────────

  function onIconPointerDown(e, kind, id) {
    if (e.button !== 0) return;
    e.stopPropagation();
    const icon = getIconPos(kind, id);
    dragging = { kind, id, startX: e.clientX, startY: e.clientY, origX: icon.x, origY: icon.y };
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
  }

  function onPointerMove(e) {
    if (!dragging || !canvasEl) return;
    const rect = canvasEl.getBoundingClientRect();
    const dx = ((e.clientX - dragging.startX) / rect.width) * 100;
    const dy = ((e.clientY - dragging.startY) / rect.height) * 100;
    const newX = Math.max(0, Math.min(95, dragging.origX + dx));
    const newY = Math.max(0, Math.min(90, dragging.origY + dy));
    setIconPos(dragging.kind, dragging.id, newX, newY, false);
  }

  async function onPointerUp(e) {
    if (!dragging || !canvasEl) { dragging = null; return; }
    const rect = canvasEl.getBoundingClientRect();
    const dx = ((e.clientX - dragging.startX) / rect.width) * 100;
    const dy = ((e.clientY - dragging.startY) / rect.height) * 100;
    const newX = Math.max(0, Math.min(95, dragging.origX + dx));
    const newY = Math.max(0, Math.min(90, dragging.origY + dy));
    setIconPos(dragging.kind, dragging.id, newX, newY, true);
    const { kind, id } = dragging;
    dragging = null;
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerUp);
    await persistIconPos(kind, id, newX, newY);
  }

  function getIconPos(kind, id) {
    if (kind === 'slot') {
      const s = slots.find(s => s.id === id);
      return { x: s?.desktop_x ?? defaultSlotX(id), y: s?.desktop_y ?? defaultSlotY(id) };
    }
    if (kind === 'file') {
      const item = items.find(i => i.id === id);
      return { x: item?._desktop_x ?? 70, y: item?._desktop_y ?? 10 };
    }
    return systemIconPos[id] ?? defaultSystemPos(id);
  }

  function setIconPos(kind, id, x, y, save) {
    if (kind === 'slot') {
      slots = slots.map(s => s.id === id ? { ...s, desktop_x: x, desktop_y: y } : s);
    } else if (kind === 'system') {
      systemIconPos = { ...systemIconPos, [id]: { x, y } };
    } else if (kind === 'file') {
      items = items.map(i => i.id === id ? { ...i, _desktop_x: x, _desktop_y: y } : i);
    }
  }

  async function persistIconPos(kind, id, x, y) {
    if (kind === 'slot') {
      await fetch(`/api/latents/${projectId}/slots/${id}`, {
        method: 'PATCH', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ desktop_x: x, desktop_y: y }),
      });
    } else if (kind === 'system') {
      const updated = { ...systemIconPos, [id]: { x, y } };
      await saveMetadata({ desktop_icon_positions: JSON.stringify(updated) });
    }
    // Loose file positions are ephemeral (not persisted yet)
  }

  async function saveMetadata(patch) {
    const current = latent?.metadata ?? {};
    await fetch(`/api/latents/${projectId}`, {
      method: 'PATCH', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ metadata: { ...current, ...patch } }),
    });
  }

  // ── Default positions ──────────────────────────────────────────────────────

  function defaultSlotX(id) {
    const idx = slots.findIndex(s => s.id === id);
    return 5 + (idx % 4) * 18;
  }
  function defaultSlotY(id) {
    const idx = slots.findIndex(s => s.id === id);
    return 15 + Math.floor(idx / 4) * 22;
  }
  function defaultSystemPos(key) {
    const order = ['docs', 'threads', 'links', 'repo'];
    const i = order.indexOf(key);
    return { x: 5 + i * 18, y: 78 };
  }

  // ── Context menu ───────────────────────────────────────────────────────────

  function onCanvasContextMenu(e) {
    e.preventDefault();
    ctxX = e.clientX; ctxY = e.clientY;
    ctxTarget = null;
    ctxVisible = true;
  }

  function closeCtx() { ctxVisible = false; }

  async function ctxChangeBg() {
    bgPickerOpen = true;
    closeCtx();
  }

  async function ctxNewFolder() {
    closeCtx();
    const label = prompt('Folder name:');
    if (!label) return;
    const res = await fetch(`/api/latents/${projectId}/slots`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    if (res.ok) await load();
  }

  // ── Modals ─────────────────────────────────────────────────────────────────

  function openModal(type, id = null, label = '') {
    if (!openModals.find(m => m.type === type && m.id === id)) {
      openModals = [...openModals, { type, id, label, x: 80 + openModals.length * 20, y: 60 + openModals.length * 20 }];
    }
  }

  function closeModal(type, id) {
    openModals = openModals.filter(m => !(m.type === type && m.id === id));
  }

  // ── Background picker ──────────────────────────────────────────────────────

  function imageItems() {
    return items.filter(i => i.media?.media_type === 'image' || i.media?.mime_type?.startsWith('image/'));
  }

  async function selectBg(mediaId, url) {
    bgUrl = url;
    bgPickerOpen = false;
    await saveMetadata({ desktop_bg_media_id: mediaId });
  }

  async function clearBg() {
    bgUrl = '';
    bgPickerOpen = false;
    await saveMetadata({ desktop_bg_media_id: null });
  }

  // ── Folder icon SVGs ───────────────────────────────────────────────────────

  function folderSvg(kind) {
    if (kind === 'album') return `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="24" cy="24" r="20" fill="#222" stroke="#888" stroke-width="1.5"/>
      <circle cx="24" cy="24" r="8" fill="#444" stroke="#888" stroke-width="1"/>
      <circle cx="24" cy="24" r="2" fill="#aaa"/>
      <path d="M24 4 A20 20 0 0 1 44 24" stroke="#c8a" stroke-width="2" fill="none"/>
    </svg>`;
    if (kind === 'video') return `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="6" y="14" width="36" height="20" rx="2" fill="#222" stroke="#888" stroke-width="1.5"/>
      <circle cx="12" cy="36" r="4" fill="#444" stroke="#888" stroke-width="1"/>
      <circle cx="36" cy="36" r="4" fill="#444" stroke="#888" stroke-width="1"/>
      <rect x="10" y="10" width="28" height="4" rx="1" fill="#555"/>
      <polygon points="20,19 20,29 31,24" fill="#888"/>
    </svg>`;
    if (kind === 'zine') return `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="10" y="8" width="22" height="30" rx="1" fill="#333" stroke="#888" stroke-width="1.5"/>
      <rect x="14" y="6" width="22" height="30" rx="1" fill="#444" stroke="#888" stroke-width="1.5"/>
      <rect x="18" y="4" width="22" height="30" rx="1" fill="#222" stroke="#888" stroke-width="1.5"/>
      <line x1="22" y1="12" x2="36" y2="12" stroke="#666" stroke-width="1"/>
      <line x1="22" y1="16" x2="36" y2="16" stroke="#666" stroke-width="1"/>
      <line x1="22" y1="20" x2="32" y2="20" stroke="#666" stroke-width="1"/>
    </svg>`;
    // default: folder
    return `<svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 14 Q6 10 10 10 L18 10 L22 14 L42 14 Q42 14 42 18 L42 38 Q42 42 38 42 L10 42 Q6 42 6 38 Z" fill="#c8940a" stroke="#a07008" stroke-width="1.5"/>
      <path d="M6 18 L42 18 L42 38 Q42 42 38 42 L10 42 Q6 42 6 38 Z" fill="#e8b020"/>
    </svg>`;
  }

  const SYSTEM_ICONS = {
    docs: { label: 'Documents', emoji: '📄' },
    threads: { label: 'Threads', emoji: '💬' },
    links: { label: 'Links', emoji: '🔗' },
    repo: { label: 'Repo', emoji: '{}' },
  };

  function fileIcon(item) {
    const mt = item.media?.media_type;
    if (mt === 'image') return '🖼';
    if (mt === 'audio') return '🎵';
    if (mt === 'video') return '🎬';
    return '📎';
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  onMount(() => {
    load();
    resetInactivityTimer();
    document.addEventListener('pointermove', resetInactivityTimer, { passive: true });
    document.addEventListener('keydown', resetInactivityTimer, { passive: true });
    document.addEventListener('touchstart', resetInactivityTimer, { passive: true });
    document.addEventListener('click', closeCtx);
  });

  onDestroy(() => {
    clearTimeout(inactivityTimer);
    document.removeEventListener('pointermove', resetInactivityTimer);
    document.removeEventListener('keydown', resetInactivityTimer);
    document.removeEventListener('touchstart', resetInactivityTimer);
    document.removeEventListener('click', closeCtx);
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerUp);
  });

  let looseItems = $derived(items.filter(i => !i.slot_id));
</script>

{#if screensaverActive}
  <LatentDesktopScreensaver ondismiss={dismissScreensaver} />
{/if}

{#if latent}
  <LatentDesktopTaskbar {latent} {projectId} onreload={load} />
{/if}

<!-- Desktop canvas -->
<div
  class="desktop"
  class:desktop--bg={!!bgUrl}
  style={bgUrl ? `background-image: url('${bgUrl}')` : ''}
  bind:this={canvasEl}
  oncontextmenu={onCanvasContextMenu}
>
  {#if loading}
    <div class="desktop__loading">Loading…</div>
  {:else if error}
    <div class="desktop__error">{error}</div>
  {:else}
    <!-- Slot folders -->
    {#each slots as slot (slot.id)}
      {@const pos = { x: slot.desktop_x ?? defaultSlotX(slot.id), y: slot.desktop_y ?? defaultSlotY(slot.id) }}
      <div
        class="desktop-icon"
        class:desktop-icon--dragging={dragging?.kind === 'slot' && dragging?.id === slot.id}
        style="left:{pos.x}%; top:{pos.y}%"
        onpointerdown={(e) => onIconPointerDown(e, 'slot', slot.id)}
        onclick={() => openModal('slot', slot.id, slot.label)}
        role="button"
        tabindex="0"
        onkeydown={(e) => e.key === 'Enter' && openModal('slot', slot.id, slot.label)}
      >
        <div class="desktop-icon__img">
          {@html folderSvg(latent?.kind ?? 'other')}
          {#if slot.item_count > 0}
            <span class="desktop-icon__badge">{slot.item_count}</span>
          {/if}
        </div>
        <span class="desktop-icon__label">{slot.label}</span>
      </div>
    {/each}

    <!-- System icons -->
    {#each Object.entries(SYSTEM_ICONS) as [key, meta]}
      {@const pos = systemIconPos[key] ?? defaultSystemPos(key)}
      <div
        class="desktop-icon desktop-icon--system"
        class:desktop-icon--dragging={dragging?.kind === 'system' && dragging?.id === key}
        style="left:{pos.x}%; top:{pos.y}%"
        onpointerdown={(e) => onIconPointerDown(e, 'system', key)}
        onclick={() => openModal(key)}
        role="button"
        tabindex="0"
        onkeydown={(e) => e.key === 'Enter' && openModal(key)}
      >
        <div class="desktop-icon__img desktop-icon__img--system">{meta.emoji}</div>
        <span class="desktop-icon__label">{meta.label}</span>
      </div>
    {/each}

    <!-- Loose file icons -->
    {#each looseItems as item, i (item.id)}
      {@const fx = item._desktop_x ?? (72 + (i % 3) * 8)}
      {@const fy = item._desktop_y ?? (15 + Math.floor(i / 3) * 18)}
      <div
        class="desktop-icon desktop-icon--file"
        class:desktop-icon--dragging={dragging?.kind === 'file' && dragging?.id === item.id}
        style="left:{fx}%; top:{fy}%"
        onpointerdown={(e) => onIconPointerDown(e, 'file', item.id)}
        onclick={() => openModal('file', item.id, item.media?.filename ?? 'File')}
        role="button"
        tabindex="0"
        onkeydown={(e) => e.key === 'Enter' && openModal('file', item.id, item.media?.filename)}
      >
        <div class="desktop-icon__img desktop-icon__img--system">
          {#if item.media?.media_type === 'image'}
            <img src="/api/media/{item.media_item_id}/thumbnail" alt="" class="desktop-icon__thumb" />
          {:else}
            {fileIcon(item)}
          {/if}
        </div>
        <span class="desktop-icon__label">{(item.media?.filename ?? 'File').slice(0, 18)}</span>
      </div>
    {/each}
  {/if}
</div>

<!-- Context menu -->
{#if ctxVisible}
  <div class="ctx-menu" style="left:{ctxX}px; top:{ctxY}px" role="menu">
    <button class="ctx-menu__item" onclick={ctxChangeBg}>Change Background…</button>
    <button class="ctx-menu__item" onclick={ctxNewFolder}>New Folder</button>
  </div>
{/if}

<!-- Background picker -->
{#if bgPickerOpen}
  <div class="dialog-overlay" style="display:flex" role="dialog" aria-label="Change Background">
    <div class="dialog bg-picker">
      <h3 class="bg-picker__title">Choose Background</h3>
      <div class="bg-picker__grid">
        {#each imageItems() as item (item.id)}
          <button
            class="bg-picker__thumb-btn"
            onclick={() => selectBg(item.media_item_id, `/api/media/${item.media_item_id}/raw`)}
          >
            <img src="/api/media/{item.media_item_id}/thumbnail" alt={item.media?.filename} />
          </button>
        {/each}
        {#if imageItems().length === 0}
          <p class="bg-picker__empty">No images in this latent yet.</p>
        {/if}
      </div>
      <div class="dialog-actions">
        <button class="btn-cancel" onclick={clearBg}>Clear background</button>
        <button class="btn-cancel" onclick={() => bgPickerOpen = false}>Cancel</button>
      </div>
    </div>
  </div>
{/if}

<!-- Open modals (stacked) -->
{#each openModals as modal (modal.type + (modal.id ?? ''))}
  <div
    class="desktop-window"
    style="left:{modal.x}px; top:{modal.y}px"
    role="dialog"
    aria-label={modal.label || modal.type}
  >
    <div class="desktop-window__titlebar">
      <span class="desktop-window__title">{modal.label || modal.type}</span>
      <button class="desktop-window__close" onclick={() => closeModal(modal.type, modal.id)}>✕</button>
    </div>
    <div class="desktop-window__body">
      {#if modal.type === 'slot'}
        <p class="window-placeholder">Slot: <strong>{modal.label}</strong></p>
        <p class="window-placeholder muted">Full slot UI coming — files, notes, links, threads.</p>
      {:else if modal.type === 'docs'}
        <p class="window-placeholder">Documents editor coming.</p>
      {:else if modal.type === 'threads'}
        <p class="window-placeholder">Threads coming.</p>
      {:else if modal.type === 'links'}
        <p class="window-placeholder">Links coming.</p>
      {:else if modal.type === 'repo'}
        <p class="window-placeholder">Repo link coming.</p>
      {:else if modal.type === 'file'}
        <p class="window-placeholder">File: <strong>{modal.label}</strong></p>
      {/if}
    </div>
  </div>
{/each}

<style>
  /* ── Desktop canvas ───────────────────────────────────────────────────── */
  .desktop {
    position: fixed;
    inset: 36px 0 0 0; /* below taskbar */
    background: #1a1a1a;
    background-size: cover;
    background-position: center;
    overflow: hidden;
    user-select: none;
    -webkit-user-select: none;
  }

  .desktop__loading,
  .desktop__error {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: #888;
    font-family: var(--font-mono, monospace);
    font-size: 0.9rem;
  }

  /* ── Icons ────────────────────────────────────────────────────────────── */
  .desktop-icon {
    position: absolute;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    width: 72px;
    cursor: pointer;
    padding: 6px;
    border-radius: 6px;
    touch-action: none;
    transition: background 0.1s;
  }

  .desktop-icon:hover {
    background: rgba(255, 255, 255, 0.12);
  }

  .desktop-icon--dragging {
    opacity: 0.6;
    cursor: grabbing;
  }

  .desktop-icon__img {
    width: 48px;
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
  }

  .desktop-icon__img svg {
    width: 48px;
    height: 48px;
    display: block;
  }

  .desktop-icon__img--system {
    font-size: 36px;
    line-height: 1;
  }

  .desktop-icon__thumb {
    width: 48px;
    height: 48px;
    object-fit: cover;
    border-radius: 4px;
  }

  .desktop-icon__badge {
    position: absolute;
    top: -4px;
    right: -4px;
    background: #e8b020;
    color: #000;
    font-size: 10px;
    font-weight: 700;
    font-family: var(--font-mono, monospace);
    border-radius: 8px;
    padding: 1px 5px;
    min-width: 16px;
    text-align: center;
  }

  .desktop-icon__label {
    color: #fff;
    font-size: 11px;
    font-family: var(--font-mono, monospace);
    text-align: center;
    text-shadow: 0 1px 3px rgba(0,0,0,0.9), 0 0 8px rgba(0,0,0,0.8);
    max-width: 70px;
    word-break: break-word;
    line-height: 1.2;
  }

  /* ── Context menu ─────────────────────────────────────────────────────── */
  .ctx-menu {
    position: fixed;
    z-index: 9000;
    background: #2a2a2a;
    border: 1px solid #444;
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    overflow: hidden;
    min-width: 160px;
  }

  .ctx-menu__item {
    display: block;
    width: 100%;
    padding: 8px 14px;
    background: none;
    border: none;
    color: #ddd;
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }

  .ctx-menu__item:hover {
    background: #3a3a3a;
    color: #fff;
  }

  /* ── Floating windows ─────────────────────────────────────────────────── */
  .desktop-window {
    position: fixed;
    z-index: 8000;
    width: 420px;
    max-width: calc(100vw - 32px);
    background: #1e1e1e;
    border: 1px solid #444;
    border-radius: 8px;
    box-shadow: 0 16px 48px rgba(0,0,0,0.7);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .desktop-window__titlebar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: #2a2a2a;
    border-bottom: 1px solid #444;
    cursor: move;
  }

  .desktop-window__title {
    font-family: var(--font-mono, monospace);
    font-size: 13px;
    color: #ddd;
    font-weight: 600;
  }

  .desktop-window__close {
    background: none;
    border: none;
    color: #888;
    cursor: pointer;
    font-size: 14px;
    padding: 2px 6px;
    border-radius: 4px;
  }
  .desktop-window__close:hover { color: #fff; background: #444; }

  .desktop-window__body {
    padding: 16px;
    max-height: 60vh;
    overflow-y: auto;
    color: #ccc;
  }

  .window-placeholder {
    font-size: 13px;
    color: #aaa;
    font-family: var(--font-mono, monospace);
  }
  .window-placeholder.muted { color: #666; font-size: 12px; margin-top: 4px; }

  /* ── Background picker ────────────────────────────────────────────────── */
  .bg-picker {
    max-width: 480px;
    width: 90vw;
  }
  .bg-picker__title {
    margin: 0 0 12px;
    font-size: 14px;
    font-family: var(--font-mono, monospace);
    color: var(--color-text, #ddd);
  }
  .bg-picker__grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    gap: 8px;
    max-height: 300px;
    overflow-y: auto;
    margin-bottom: 12px;
  }
  .bg-picker__thumb-btn {
    border: 2px solid transparent;
    border-radius: 4px;
    padding: 0;
    cursor: pointer;
    overflow: hidden;
    background: #222;
  }
  .bg-picker__thumb-btn:hover { border-color: #e8b020; }
  .bg-picker__thumb-btn img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }
  .bg-picker__empty { color: #666; font-size: 13px; grid-column: 1/-1; }

  /* ── Mobile grid layout ───────────────────────────────────────────────── */
  @media (max-width: 767px) {
    .desktop {
      overflow-y: auto;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      padding: 16px 8px;
      align-content: start;
    }

    .desktop-icon {
      position: static !important;
      width: auto;
    }

    .desktop-window {
      position: fixed;
      left: 50% !important;
      top: 50% !important;
      transform: translate(-50%, -50%);
      width: calc(100vw - 24px);
    }
  }
</style>
