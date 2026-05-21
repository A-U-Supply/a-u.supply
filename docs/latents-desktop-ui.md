# Latents Desktop UI

## Vision

Transform the Latents detail page from a vertical scroll into a **desktop OS experience**. Each slot becomes a draggable folder icon on a freeform canvas. Documents, Threads, Links, and Repo get their own desktop icons. Loose files scatter as individual file icons. The latent's own media images serve as the wallpaper. After 10 minutes of inactivity, a screensaver kicks in. On mobile, everything collapses into a clean folder grid with the same aesthetic.

The underlying data model and API are unchanged — this is a new visual layer on top of what already exists, accessible at `/admin/latents/{id}/desktop`.

---

## Design

### Layout

- **Desktop (≥768px):** Freeform canvas. All icons are draggable to any position. Positions are persisted per-slot.
- **Mobile (<768px):** Responsive grid (3–4 columns). No drag — tap opens the same modals. Background and taskbar aesthetic preserved.

### Taskbar (top bar)

Fixed OS-style toolbar spanning the full width. The admin sidebar is hidden in desktop mode — all navigation moves here.

- **Left:** Hamburger menu → opens sidebar nav overlay
- **Center:** Latent name (editable inline) + kind pill + status pills
- **Right:** File/slot counts + Settings icon → description/metadata panel

### Desktop Icons

Every section from the old vertical layout becomes a draggable icon:

| Icon | What it represents | Click/tap |
|------|-------------------|-----------|
| Folder (themed) | Each slot | Opens slot modal |
| Document icon | Documents section | Opens documents modal |
| Speech bubble | Threads | Opens threads modal |
| Chain/link | Links | Opens links modal |
| Code bracket | Repo link | Opens repo modal |
| File/thumbnail | Each loose file | Opens file preview |

**Folder themes by latent kind:**
- `album` → record sleeve
- `video` → film canister
- `zine` → booklet/stack
- `other` → generic manila folder

File count badge shown on slot folder icons.

**Future goal:** Customizable or custom icons per slot.

### Loose Files & User-Created Folders

- Each loose file is its own draggable icon (thumbnail for images, waveform for audio, etc.)
- Right-click canvas → **New Folder** creates a new slot
- Drag a loose file onto a folder to move it into that slot

### Folder/Icon Interaction

- **Click/tap** → floating OS-style modal with a draggable title bar
- Multiple modals can be open at once on desktop
- Close via ✕ or clicking outside

### Desktop Background

- Right-click canvas → **Change Background** → picker of image media from the latent
- Stored in latent `metadata` as `desktop_bg_media_id`
- Default: dark surface color

### Screensaver

- Triggers after **10 minutes** of inactivity (`pointermove` / `keydown` / `touchstart`)
- Click, tap, or keypress to dismiss
- **Phase 1 — two modes, chosen randomly on each activation:**
  1. **Bouncing A-U logo** — DVD-logo style, changes color on wall hit
  2. **Winged cassettes/records** — After Dark tribute, A-U–themed objects drift across screen

**Future goal:** Growing library of screensavers chosen at random.

---

## Implementation

### New files

| File | Purpose |
|------|---------|
| `src/pages/admin/latents/desktop.astro` | Route `/admin/latents/{id}/desktop` |
| `src/components/LatentDesktop.svelte` | Canvas, icons, drag, context menu, background |
| `src/components/LatentDesktopTaskbar.svelte` | Taskbar |
| `src/components/LatentDesktopScreensaver.svelte` | Screensaver modes + inactivity timer |

### Modified files

| File | Change |
|------|--------|
| `server/models.py` | `desktop_x`, `desktop_y` (Float, nullable) on `ProjectSlot` |
| `server/latents_api.py` | Expose in `_slot_summary`, accept in `UpdateSlotBody` + handler |
| `main.py` | Auto-migration for the two new columns |
| `src/pages/admin/latents/detail.astro` | "Desktop view" link |

### Data storage

- **Slot position:** `desktop_x`, `desktop_y` on `ProjectSlot` (percentage 0–100, resolution-independent). Saved via `PATCH /api/latents/{id}/slots/{slot_id}` on drag end.
- **System icon positions** (Docs, Threads, Links, Repo): stored in latent `metadata` as `desktop_icon_positions: { docs: {x, y}, threads: {x, y}, … }`.
- **Background:** `desktop_bg_media_id` in latent `metadata`.

---

## Local Dev

```bash
# Terminal 1 — backend
uv run uvicorn main:app --port 5000 --reload

# Terminal 2 — frontend
npm install   # first time only
npm run dev   # → http://localhost:4321
```

Then visit: `http://localhost:4321/admin/latents/{id}/desktop`

File upload and Pull from Index won't work without a seeded local DB, but the canvas, icons, drag, screensaver, and taskbar are all fully testable.

---

## Future Goals

- [ ] Per-slot customizable folder icons (upload or emoji/icon set)
- [ ] Growing screensaver library, chosen at random
- [ ] Keyboard navigation (Tab between icons, Enter to open)
- [ ] Snap-to-grid toggle
- [ ] "Clean up Desktop" — arrange icons into a tidy grid
- [ ] Double-click to rename a folder inline
