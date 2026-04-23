/**
 * Image viewer — a lightbox-style overlay for images.
 *
 * Opens via `openImageViewer(items, startIndex, actions?)`. One viewer
 * instance at a time; opening a new one closes any existing. The module
 * owns its DOM and cleans up on close.
 *
 * v1: images only. Video / audio land in a follow-up.
 *
 * Mobile support:
 * - Pinch-to-zoom via two-finger gestures
 * - Swipe-left/right to navigate (when not zoomed in)
 * - Drag-to-pan when zoomed
 * - Touch targets are ≥ 44px, safe-area insets respected
 */

export interface ViewerItem {
  id: string;
  kind: 'media_item' | 'job_output';
  large_url: string;
  thumbnail_url?: string;
  download_url: string;
  job_id?: string;
  indexed?: boolean;
  filename?: string;
  width?: number;
  height?: number;
  dominant_colors?: string[];
  media_type?: string;
}

export interface ViewerActions {
  // Each callback, if defined, adds a button to the toolbar and binds its
  // keyboard shortcut. Omit a callback to hide that action entirely.
  onBookmark?: (item: ViewerItem) => Promise<boolean>;
  isBookmarked?: (item: ViewerItem) => Promise<boolean> | boolean;
  onAddToWorkspace?: (item: ViewerItem) => Promise<void>;
  onIndex?: (item: ViewerItem) => Promise<void>;
  onDiscard?: (item: ViewerItem) => Promise<void>;
  onDetails?: (item: ViewerItem) => void;
}

// ---------------------------------------------------------------------------
// Module-scoped singleton state
// ---------------------------------------------------------------------------

let activeViewer: ViewerInstance | null = null;

// ---------------------------------------------------------------------------
// Public entrypoint
// ---------------------------------------------------------------------------

export function openImageViewer(
  items: ViewerItem[],
  startIndex: number,
  actions: ViewerActions = {},
): void {
  if (!items || items.length === 0) return;
  if (activeViewer) activeViewer.close();
  activeViewer = new ViewerInstance(
    items,
    Math.max(0, Math.min(startIndex, items.length - 1)),
    actions,
  );
  activeViewer.open();
}

export function closeImageViewer(): void {
  if (activeViewer) activeViewer.close();
}

// ---------------------------------------------------------------------------
// Icons (inlined SVG — stays consistent with the rest of the admin UI)
// ---------------------------------------------------------------------------

const ICONS = {
  close:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>',
  prev: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>',
  next: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.59 16.59L10 18l6-6-6-6-1.41 1.41L13.17 12z"/></svg>',
  zoomIn:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14zm-2.5-4h5v1H7v-1z"/><path d="M9 7h1v2h2v1h-2v2H9v-2H7V9h2z"/></svg>',
  zoomOut:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14zM7 9h5v1H7z"/></svg>',
  fullscreen:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>',
  help: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17h-2v-2h2v2zm2.07-7.75l-.9.92C13.45 12.9 13 13.5 13 15h-2v-.5c0-1.1.45-2.1 1.17-2.83l1.24-1.26c.37-.36.59-.86.59-1.41 0-1.1-.9-2-2-2s-2 .9-2 2H8c0-2.21 1.79-4 4-4s4 1.79 4 4c0 .88-.36 1.68-.93 2.25z"/></svg>',
  download:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>',
  bookmarkOff:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27zm0-2.1l-4.21 2.54 1.12-4.82L5.17 9.65l4.94-.42L12 4.68l1.9 4.56 4.94.42-3.74 3.24 1.12 4.82L12 15.17z"/></svg>',
  bookmarkOn:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/></svg>',
  workspace:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7h-4V5c0-1.1-.9-2-2-2h-4c-1.1 0-2 .9-2 2v2H4c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V9c0-1.1-.9-2-2-2zM10 5h4v2h-4V5z"/></svg>',
  index:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-9 14l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>',
  discard:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>',
  details:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 17h2v-6h-2v6zm1-15C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8 8zm-1-11h2V7h-2v2z"/></svg>',
};

// ---------------------------------------------------------------------------
// Styles (injected once)
// ---------------------------------------------------------------------------

const STYLE_ID = 'image-viewer-styles';
const STYLES = `
.iv-root {
  position: fixed;
  inset: 0;
  z-index: 10000;
  background: rgba(0, 0, 0, 0.94);
  color: #fff;
  font-family: var(--font-mono, ui-monospace, Menlo, monospace);
  display: flex;
  flex-direction: column;
  overscroll-behavior: contain;
  padding-top: env(safe-area-inset-top, 0);
  padding-bottom: env(safe-area-inset-bottom, 0);
  padding-left: env(safe-area-inset-left, 0);
  padding-right: env(safe-area-inset-right, 0);
  animation: iv-fade-in 0.15s ease;
}
@keyframes iv-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.iv-topbar, .iv-toolbar {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  z-index: 2;
}
.iv-topbar {
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.iv-toolbar {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  justify-content: center;
  flex-wrap: wrap;
}
.iv-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.8rem;
  color: #ddd;
}
.iv-counter {
  font-size: 0.75rem;
  color: #aaa;
  padding: 0 8px;
  white-space: nowrap;
}
.iv-stage {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}
.iv-img-wrap {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  /* While fit, image uses max-width/height from CSS */
  /* While zoomed, transform scale + translate is applied inline */
  transition: transform 0.12s ease-out;
  will-change: transform;
}
.iv-img-wrap.iv-panning {
  transition: none;
}
.iv-img {
  max-width: 100%;
  max-height: 100%;
  display: block;
  object-fit: contain;
  pointer-events: none;
  -webkit-user-drag: none;
  user-drag: none;
}
.iv-img-wrap.iv-zoomed .iv-img {
  max-width: none;
  max-height: none;
}
.iv-loading-dot {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  color: #888;
  pointer-events: none;
}
.iv-btn {
  appearance: none;
  background: transparent;
  border: 1px solid transparent;
  color: #fff;
  padding: 0;
  cursor: pointer;
  width: 44px;
  height: 44px;
  min-width: 44px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: background 0.1s, border-color 0.1s;
  flex-shrink: 0;
}
.iv-btn:hover {
  background: rgba(255, 255, 255, 0.08);
}
.iv-btn:active {
  background: rgba(255, 255, 255, 0.15);
}
.iv-btn[disabled] {
  opacity: 0.3;
  cursor: default;
}
.iv-btn[disabled]:hover {
  background: transparent;
}
.iv-btn.iv-btn--active {
  color: #ffd700;
}
.iv-btn svg {
  width: 22px;
  height: 22px;
  fill: currentColor;
}
.iv-nav-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 56px;
  height: 56px;
  background: rgba(0, 0, 0, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 50%;
  opacity: 0.75;
  transition: opacity 0.15s;
}
.iv-nav-btn:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.6);
}
.iv-nav-btn[disabled] {
  opacity: 0;
  pointer-events: none;
}
.iv-nav-btn--prev { left: 12px; }
.iv-nav-btn--next { right: 12px; }
.iv-nav-btn svg {
  width: 28px;
  height: 28px;
}
.iv-divider {
  width: 1px;
  height: 24px;
  background: rgba(255, 255, 255, 0.15);
  margin: 0 4px;
}
.iv-cheatsheet {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 3;
  padding: 20px;
  overflow: auto;
}
.iv-cheatsheet-inner {
  max-width: 520px;
  width: 100%;
  font-size: 0.85rem;
  line-height: 1.8;
}
.iv-cheatsheet-title {
  font-size: 1rem;
  margin-bottom: 12px;
  color: #ffd700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.iv-cheatsheet kbd {
  display: inline-block;
  min-width: 1.4em;
  text-align: center;
  padding: 2px 6px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 3px;
  font-family: inherit;
  font-size: 0.75rem;
  margin-right: 6px;
  color: #fff;
}
.iv-cheatsheet ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 6px 18px;
}
.iv-cheatsheet li {
  display: flex;
  gap: 8px;
}
.iv-cheatsheet-hint {
  margin-top: 14px;
  color: #888;
  font-size: 0.75rem;
}
/* Mobile: tighter paddings, nav buttons overlap less */
@media (max-width: 640px) {
  .iv-topbar, .iv-toolbar {
    padding: 6px 8px;
    gap: 2px;
  }
  .iv-toolbar {
    overflow-x: auto;
    justify-content: flex-start;
    -webkit-overflow-scrolling: touch;
  }
  .iv-title { font-size: 0.7rem; }
  .iv-btn {
    width: 44px;
    height: 44px;
  }
  .iv-nav-btn {
    width: 44px;
    height: 44px;
  }
  .iv-nav-btn--prev { left: 6px; }
  .iv-nav-btn--next { right: 6px; }
  .iv-nav-btn svg {
    width: 22px;
    height: 22px;
  }
}
/* Hide hover-only navs on coarse pointer; rely on swipe instead */
@media (hover: none) and (pointer: coarse) {
  .iv-nav-btn {
    opacity: 0.5;
  }
}
`;

function ensureStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = STYLES;
  document.head.appendChild(style);
}

// ---------------------------------------------------------------------------
// ViewerInstance — one open viewer at a time
// ---------------------------------------------------------------------------

class ViewerInstance {
  private items: ViewerItem[];
  private index: number;
  private actions: ViewerActions;

  private root!: HTMLElement;
  private stage!: HTMLElement;
  private imgWrap!: HTMLElement;
  private img!: HTMLImageElement;
  private loadingEl!: HTMLElement;
  private counterEl!: HTMLElement;
  private titleEl!: HTMLElement;
  private prevBtn!: HTMLButtonElement;
  private nextBtn!: HTMLButtonElement;
  private bookmarkBtn: HTMLButtonElement | null = null;
  private cheatsheetEl: HTMLElement | null = null;

  // Zoom state
  private scale = 1;
  private translateX = 0;
  private translateY = 0;

  // Pointer/touch tracking
  private pointers = new Map<number, { x: number; y: number }>();
  private panStart: { x: number; y: number; tx: number; ty: number } | null =
    null;
  private pinchStart: {
    distance: number;
    scale: number;
    midX: number;
    midY: number;
  } | null = null;
  private swipeStart: { x: number; y: number; time: number } | null = null;

  private keyHandler = (e: KeyboardEvent) => this.handleKey(e);
  private wheelHandler = (e: WheelEvent) => this.handleWheel(e);
  private navCloseHandler = () => this.close();
  private resizeHandler = () => this.resetZoom();

  constructor(items: ViewerItem[], index: number, actions: ViewerActions) {
    this.items = items;
    this.index = index;
    this.actions = actions;
  }

  open() {
    ensureStyles();
    this.buildDom();
    document.body.appendChild(this.root);
    // Prevent background scroll
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', this.keyHandler);
    document.addEventListener('astro:before-preparation', this.navCloseHandler);
    window.addEventListener('resize', this.resizeHandler);
    this.render();
  }

  close() {
    if (!this.root) return;
    document.removeEventListener('keydown', this.keyHandler);
    document.removeEventListener(
      'astro:before-preparation',
      this.navCloseHandler,
    );
    window.removeEventListener('resize', this.resizeHandler);
    this.root.remove();
    document.body.style.overflow = '';
    if (activeViewer === this) activeViewer = null;
  }

  private get currentItem(): ViewerItem {
    return this.items[this.index];
  }

  // -------------------------------------------------------------------------
  // DOM construction
  // -------------------------------------------------------------------------

  private buildDom() {
    this.root = document.createElement('div');
    this.root.className = 'iv-root';
    this.root.setAttribute('role', 'dialog');
    this.root.setAttribute('aria-modal', 'true');

    // --- Top bar ---
    const topbar = document.createElement('div');
    topbar.className = 'iv-topbar';

    this.titleEl = document.createElement('div');
    this.titleEl.className = 'iv-title';

    this.counterEl = document.createElement('div');
    this.counterEl.className = 'iv-counter';

    const helpBtn = this.makeBtn(ICONS.help, 'Help (?)', () =>
      this.toggleCheatsheet(),
    );
    const closeBtn = this.makeBtn(ICONS.close, 'Close (Esc)', () =>
      this.close(),
    );

    topbar.append(this.titleEl, this.counterEl, helpBtn, closeBtn);

    // --- Stage ---
    this.stage = document.createElement('div');
    this.stage.className = 'iv-stage';

    this.imgWrap = document.createElement('div');
    this.imgWrap.className = 'iv-img-wrap';

    this.img = document.createElement('img');
    this.img.className = 'iv-img';
    this.img.alt = '';
    this.img.decoding = 'async';

    this.loadingEl = document.createElement('div');
    this.loadingEl.className = 'iv-loading-dot';
    this.loadingEl.textContent = 'Loading…';

    this.imgWrap.append(this.img);
    this.stage.append(this.imgWrap, this.loadingEl);

    this.prevBtn = this.makeBtn(ICONS.prev, 'Previous (←)', () =>
      this.navigate(-1),
    );
    this.prevBtn.classList.add('iv-nav-btn', 'iv-nav-btn--prev');
    this.nextBtn = this.makeBtn(ICONS.next, 'Next (→)', () => this.navigate(1));
    this.nextBtn.classList.add('iv-nav-btn', 'iv-nav-btn--next');
    this.stage.append(this.prevBtn, this.nextBtn);

    // Backdrop click closes, but only when clicking the stage itself
    // (not the image or nav buttons)
    this.stage.addEventListener('click', (e) => {
      if (e.target === this.stage) this.close();
    });

    // Pointer interactions for zoom/pan/swipe
    this.stage.addEventListener('pointerdown', (e) => this.onPointerDown(e));
    this.stage.addEventListener('pointermove', (e) => this.onPointerMove(e));
    this.stage.addEventListener('pointerup', (e) => this.onPointerUp(e));
    this.stage.addEventListener('pointercancel', (e) => this.onPointerUp(e));
    this.stage.addEventListener('dblclick', (e) => this.onDoubleClick(e));
    this.stage.addEventListener('wheel', this.wheelHandler, { passive: false });

    // --- Bottom toolbar ---
    const toolbar = document.createElement('div');
    toolbar.className = 'iv-toolbar';

    const zoomOutBtn = this.makeBtn(ICONS.zoomOut, 'Zoom out (-)', () =>
      this.zoomBy(0.8),
    );
    const zoomInBtn = this.makeBtn(ICONS.zoomIn, 'Zoom in (+)', () =>
      this.zoomBy(1.25),
    );
    const fsBtn = this.makeBtn(ICONS.fullscreen, 'Fullscreen (F)', () =>
      this.toggleFullscreen(),
    );

    toolbar.append(zoomOutBtn, zoomInBtn, fsBtn, this.divider());

    if (this.actions.onBookmark) {
      this.bookmarkBtn = this.makeBtn(ICONS.bookmarkOff, 'Bookmark (B)', () =>
        this.doAction('bookmark'),
      );
      toolbar.append(this.bookmarkBtn);
    }
    if (this.actions.onAddToWorkspace) {
      toolbar.append(
        this.makeBtn(ICONS.workspace, 'Add to workspace (W)', () =>
          this.doAction('workspace'),
        ),
      );
    }
    if (this.actions.onIndex) {
      toolbar.append(
        this.makeBtn(ICONS.index, 'Index (I)', () => this.doAction('index')),
      );
    }
    if (this.actions.onDiscard) {
      toolbar.append(
        this.makeBtn(ICONS.discard, 'Discard (X)', () =>
          this.doAction('discard'),
        ),
      );
    }
    if (this.actions.onDetails) {
      toolbar.append(
        this.makeBtn(ICONS.details, 'Details (Enter)', () =>
          this.doAction('details'),
        ),
      );
    }
    toolbar.append(this.divider());
    toolbar.append(
      this.makeBtn(ICONS.download, 'Download (⇧D)', () =>
        this.doAction('download'),
      ),
    );

    this.root.append(topbar, this.stage, toolbar);
  }

  private makeBtn(
    icon: string,
    title: string,
    onClick: () => void,
  ): HTMLButtonElement {
    const btn = document.createElement('button');
    btn.className = 'iv-btn';
    btn.type = 'button';
    btn.title = title;
    btn.setAttribute('aria-label', title);
    btn.innerHTML = icon;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      onClick();
    });
    return btn;
  }

  private divider(): HTMLElement {
    const d = document.createElement('span');
    d.className = 'iv-divider';
    return d;
  }

  // -------------------------------------------------------------------------
  // Render / navigate
  // -------------------------------------------------------------------------

  private render() {
    const item = this.currentItem;
    this.titleEl.textContent = item.filename || '';
    this.counterEl.textContent = `${this.index + 1} / ${this.items.length}`;

    this.prevBtn.disabled = this.index <= 0;
    this.nextBtn.disabled = this.index >= this.items.length - 1;

    this.resetZoom();
    this.loadingEl.style.display = 'flex';
    this.img.style.background = this.backdropColor(item);

    // Preload the large; fall back to thumbnail on error.
    this.img.onload = () => {
      this.loadingEl.style.display = 'none';
    };
    this.img.onerror = () => {
      if (item.thumbnail_url && this.img.src !== item.thumbnail_url) {
        this.img.src = item.thumbnail_url;
      } else {
        this.loadingEl.textContent = 'Failed to load';
      }
    };

    // Low-quality placeholder first (if we have one), then upgrade to large.
    if (item.thumbnail_url) {
      this.img.src = item.thumbnail_url;
    }
    // Start lg load (browser will race; last write wins when it arrives)
    const preload = new Image();
    preload.onload = () => {
      this.img.src = item.large_url;
      this.loadingEl.style.display = 'none';
    };
    preload.onerror = () => {
      this.loadingEl.textContent = 'Failed to load';
    };
    preload.src = item.large_url;

    this.refreshBookmarkState();
    this.preloadNeighbors();
  }

  private preloadNeighbors() {
    for (const offset of [1, -1]) {
      const i = this.index + offset;
      if (i < 0 || i >= this.items.length) continue;
      const it = this.items[i];
      if (!it.large_url) continue;
      new Image().src = it.large_url;
    }
  }

  private async refreshBookmarkState() {
    if (!this.bookmarkBtn || !this.actions.isBookmarked) return;
    try {
      const on = await this.actions.isBookmarked(this.currentItem);
      this.bookmarkBtn.innerHTML = on ? ICONS.bookmarkOn : ICONS.bookmarkOff;
      this.bookmarkBtn.classList.toggle('iv-btn--active', !!on);
    } catch {
      /* ignore */
    }
  }

  private backdropColor(item: ViewerItem): string {
    const first = item.dominant_colors?.[0];
    return first || 'transparent';
  }

  private navigate(delta: number) {
    const next = this.index + delta;
    if (next < 0 || next >= this.items.length) return;
    this.index = next;
    this.render();
  }

  // -------------------------------------------------------------------------
  // Zoom / pan
  // -------------------------------------------------------------------------

  private resetZoom() {
    this.scale = 1;
    this.translateX = 0;
    this.translateY = 0;
    this.applyTransform();
    this.imgWrap.classList.remove('iv-zoomed');
  }

  private zoomBy(factor: number) {
    this.setZoom(this.scale * factor);
  }

  private setZoom(newScale: number, centerX?: number, centerY?: number) {
    const clamped = Math.max(1, Math.min(8, newScale));
    if (clamped === this.scale) return;

    // Keep the image point under (centerX, centerY) stationary.
    if (centerX !== undefined && centerY !== undefined) {
      const rect = this.stage.getBoundingClientRect();
      const cx = centerX - rect.left - rect.width / 2;
      const cy = centerY - rect.top - rect.height / 2;
      const ratio = clamped / this.scale;
      this.translateX = cx - ratio * (cx - this.translateX);
      this.translateY = cy - ratio * (cy - this.translateY);
    }

    this.scale = clamped;
    this.imgWrap.classList.toggle('iv-zoomed', clamped > 1.001);
    if (clamped === 1) {
      this.translateX = 0;
      this.translateY = 0;
    }
    this.applyTransform();
  }

  private applyTransform() {
    this.imgWrap.style.transform = `translate(${this.translateX}px, ${this.translateY}px) scale(${this.scale})`;
  }

  // -------------------------------------------------------------------------
  // Pointer handlers (mouse + touch via Pointer Events)
  // -------------------------------------------------------------------------

  private onPointerDown(e: PointerEvent) {
    if (
      e.target !== this.stage &&
      e.target !== this.imgWrap &&
      e.target !== this.img
    ) {
      return;
    }
    this.stage.setPointerCapture(e.pointerId);
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (this.pointers.size === 1) {
      if (this.scale > 1.001) {
        this.panStart = {
          x: e.clientX,
          y: e.clientY,
          tx: this.translateX,
          ty: this.translateY,
        };
        this.imgWrap.classList.add('iv-panning');
      } else {
        this.swipeStart = { x: e.clientX, y: e.clientY, time: Date.now() };
      }
    } else if (this.pointers.size === 2) {
      const pts = Array.from(this.pointers.values());
      this.pinchStart = {
        distance: dist(pts[0], pts[1]),
        scale: this.scale,
        midX: (pts[0].x + pts[1].x) / 2,
        midY: (pts[0].y + pts[1].y) / 2,
      };
      // Abort a pending swipe — the user is pinching.
      this.swipeStart = null;
      this.panStart = null;
    }
  }

  private onPointerMove(e: PointerEvent) {
    if (!this.pointers.has(e.pointerId)) return;
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (this.pinchStart && this.pointers.size >= 2) {
      const pts = Array.from(this.pointers.values());
      const cur = dist(pts[0], pts[1]);
      const ratio = cur / this.pinchStart.distance;
      this.setZoom(
        this.pinchStart.scale * ratio,
        this.pinchStart.midX,
        this.pinchStart.midY,
      );
    } else if (this.panStart) {
      this.translateX = this.panStart.tx + (e.clientX - this.panStart.x);
      this.translateY = this.panStart.ty + (e.clientY - this.panStart.y);
      this.applyTransform();
    }
  }

  private onPointerUp(e: PointerEvent) {
    if (!this.pointers.has(e.pointerId)) return;
    this.pointers.delete(e.pointerId);

    if (this.pointers.size < 2) {
      this.pinchStart = null;
    }

    if (this.panStart) {
      this.panStart = null;
      this.imgWrap.classList.remove('iv-panning');
    }

    // Handle swipe navigation — only if the user wasn't zoomed in
    // and lifted the final finger.
    if (this.pointers.size === 0 && this.swipeStart && this.scale <= 1.001) {
      const dx = e.clientX - this.swipeStart.x;
      const dy = e.clientY - this.swipeStart.y;
      const dt = Date.now() - this.swipeStart.time;
      const absX = Math.abs(dx);
      const absY = Math.abs(dy);
      // Horizontal swipe: mostly-horizontal, > 60px, < 500ms
      if (absX > 60 && absX > absY * 1.5 && dt < 500) {
        this.navigate(dx < 0 ? 1 : -1);
      }
      this.swipeStart = null;
    } else if (this.pointers.size === 0) {
      this.swipeStart = null;
    }
  }

  private onDoubleClick(e: MouseEvent) {
    if (this.scale > 1.001) {
      this.resetZoom();
    } else {
      this.setZoom(2.5, e.clientX, e.clientY);
    }
  }

  private handleWheel(e: WheelEvent) {
    if (!this.root.contains(e.target as Node)) return;
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.15 : 0.87;
    this.setZoom(this.scale * factor, e.clientX, e.clientY);
  }

  // -------------------------------------------------------------------------
  // Keyboard
  // -------------------------------------------------------------------------

  private handleKey(e: KeyboardEvent) {
    // If user is typing into an input somewhere, bail (shouldn't happen
    // with the viewer open but defensive).
    const tgt = e.target as HTMLElement;
    if (tgt && (tgt.tagName === 'INPUT' || tgt.tagName === 'TEXTAREA')) return;

    // Cheatsheet overlay absorbs most keys
    if (this.cheatsheetEl) {
      if (e.key === 'Escape' || e.key === '?' || e.key === '/') {
        e.preventDefault();
        this.toggleCheatsheet();
      }
      return;
    }

    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        this.close();
        break;
      case 'ArrowLeft':
        e.preventDefault();
        this.navigate(-1);
        break;
      case 'ArrowRight':
        e.preventDefault();
        this.navigate(1);
        break;
      case ' ':
      case 'Spacebar':
        e.preventDefault();
        if (this.scale > 1.001) this.resetZoom();
        else this.setZoom(2.5);
        break;
      case '+':
      case '=':
        e.preventDefault();
        this.zoomBy(1.25);
        break;
      case '-':
      case '_':
        e.preventDefault();
        this.zoomBy(0.8);
        break;
      case '0':
        e.preventDefault();
        this.resetZoom();
        break;
      case 'f':
      case 'F':
        e.preventDefault();
        this.toggleFullscreen();
        break;
      case '?':
      case '/':
        e.preventDefault();
        this.toggleCheatsheet();
        break;
      case 'b':
      case 'B':
        if (this.actions.onBookmark) {
          e.preventDefault();
          this.doAction('bookmark');
        }
        break;
      case 'w':
      case 'W':
        if (this.actions.onAddToWorkspace) {
          e.preventDefault();
          this.doAction('workspace');
        }
        break;
      case 'i':
      case 'I':
        if (this.actions.onIndex) {
          e.preventDefault();
          this.doAction('index');
        }
        break;
      case 'x':
      case 'X':
        if (this.actions.onDiscard) {
          e.preventDefault();
          this.doAction('discard');
        }
        break;
      case 'D':
        if (e.shiftKey) {
          e.preventDefault();
          this.doAction('download');
        }
        break;
      case 'Enter':
        if (this.actions.onDetails) {
          e.preventDefault();
          this.doAction('details');
        }
        break;
    }
  }

  // -------------------------------------------------------------------------
  // Fullscreen
  // -------------------------------------------------------------------------

  private toggleFullscreen() {
    const anyDoc = document as any;
    const fsEl = document.fullscreenElement || anyDoc.webkitFullscreenElement;
    if (fsEl) {
      (
        document.exitFullscreen?.bind(document) ||
        anyDoc.webkitExitFullscreen?.bind(document)
      )?.();
    } else {
      const req = (this.root as any).requestFullscreen
        ? this.root.requestFullscreen.bind(this.root)
        : (this.root as any).webkitRequestFullscreen?.bind(this.root);
      req?.();
    }
  }

  // -------------------------------------------------------------------------
  // Cheatsheet
  // -------------------------------------------------------------------------

  private toggleCheatsheet() {
    if (this.cheatsheetEl) {
      this.cheatsheetEl.remove();
      this.cheatsheetEl = null;
      return;
    }
    const el = document.createElement('div');
    el.className = 'iv-cheatsheet';
    el.addEventListener('click', () => this.toggleCheatsheet());
    const inner = document.createElement('div');
    inner.className = 'iv-cheatsheet-inner';
    inner.innerHTML = `
      <div class="iv-cheatsheet-title">Keyboard shortcuts</div>
      <ul>
        <li><kbd>←</kbd><kbd>→</kbd> prev / next</li>
        <li><kbd>Esc</kbd> close</li>
        <li><kbd>␣</kbd> toggle zoom</li>
        <li><kbd>+</kbd> <kbd>-</kbd> <kbd>0</kbd> zoom in / out / reset</li>
        <li><kbd>f</kbd> fullscreen</li>
        <li><kbd>?</kbd> this help</li>
        ${this.actions.onBookmark ? '<li><kbd>b</kbd> bookmark</li>' : ''}
        ${this.actions.onAddToWorkspace ? '<li><kbd>w</kbd> add to workspace</li>' : ''}
        ${this.actions.onIndex ? '<li><kbd>i</kbd> index</li>' : ''}
        ${this.actions.onDiscard ? '<li><kbd>x</kbd> discard</li>' : ''}
        ${this.actions.onDetails ? '<li><kbd>⏎</kbd> details page</li>' : ''}
        <li><kbd>⇧</kbd><kbd>D</kbd> download</li>
      </ul>
      <div class="iv-cheatsheet-hint">
        Touch: pinch to zoom · drag to pan when zoomed · swipe to navigate · double-tap to toggle zoom
      </div>
    `;
    el.appendChild(inner);
    this.root.appendChild(el);
    this.cheatsheetEl = el;
  }

  // -------------------------------------------------------------------------
  // Action dispatch
  // -------------------------------------------------------------------------

  private async doAction(
    kind:
      | 'bookmark'
      | 'workspace'
      | 'index'
      | 'discard'
      | 'details'
      | 'download',
  ) {
    const item = this.currentItem;
    try {
      switch (kind) {
        case 'bookmark':
          if (this.actions.onBookmark) {
            const on = await this.actions.onBookmark(item);
            if (this.bookmarkBtn) {
              this.bookmarkBtn.innerHTML = on
                ? ICONS.bookmarkOn
                : ICONS.bookmarkOff;
              this.bookmarkBtn.classList.toggle('iv-btn--active', !!on);
            }
          }
          break;
        case 'workspace':
          if (this.actions.onAddToWorkspace)
            await this.actions.onAddToWorkspace(item);
          break;
        case 'index':
          if (this.actions.onIndex) await this.actions.onIndex(item);
          break;
        case 'discard':
          if (this.actions.onDiscard) await this.actions.onDiscard(item);
          break;
        case 'details':
          if (this.actions.onDetails) this.actions.onDetails(item);
          break;
        case 'download': {
          const a = document.createElement('a');
          a.href = item.download_url;
          if (item.filename) a.download = item.filename;
          a.click();
          break;
        }
      }
    } catch {
      /* callers should toast on failure */
    }
  }
}

function dist(a: { x: number; y: number }, b: { x: number; y: number }) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy);
}
