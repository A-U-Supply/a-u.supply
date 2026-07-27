/**
 * Per-latent collapse state for the tall page sections (Loose files,
 * Playlists, Slideshows).
 *
 * Those three are the ones that grow without bound — a few hundred loose
 * files, or a slideshow with fifty slides, and everything below them is a
 * long scroll away. A slot card collapses to its summary line already
 * (LatentSlots); this is the same move one level up.
 *
 * Two deliberate choices:
 *
 * - **Sections arrive OPEN.** Only a section you explicitly collapsed is
 *   remembered, so nothing changes for a latent you've never touched. That
 *   is also why the store holds the collapsed keys rather than the open
 *   ones — no entry means open, and forgetting the entry fails safe.
 * - **State is per latent**, not global. Collapsing Loose files on a photo
 *   dump shouldn't hide it on a latent where it's the whole point.
 *
 * localStorage is wrapped because Safari private mode throws on write, and
 * a thrown setItem inside a click handler would swallow the toggle.
 */

/** The sections that can be collapsed. Others are short enough to stay put. */
export type CollapsibleSection = 'loose' | 'playlists' | 'slideshow';

/** Fired to open a collapsed section — see `revealSection`. */
export const SECTION_REVEAL_EVENT = 'latent-section-reveal';

function storeKey(projectId: string): string {
  return `latent:collapsed:${projectId}`;
}

function readCollapsed(projectId: string): string[] {
  if (typeof localStorage === 'undefined') return [];
  try {
    const raw = localStorage.getItem(storeKey(projectId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((k) => typeof k === 'string')
      : [];
  } catch {
    return [];
  }
}

/** Is this section open? True unless it was explicitly collapsed before. */
export function readSectionOpen(
  projectId: string,
  section: CollapsibleSection,
): boolean {
  if (!projectId) return true;
  return !readCollapsed(projectId).includes(section);
}

/** Remember the new state. Open sections are dropped from the store. */
export function writeSectionOpen(
  projectId: string,
  section: CollapsibleSection,
  open: boolean,
): void {
  if (!projectId || typeof localStorage === 'undefined') return;
  const next = readCollapsed(projectId).filter((k) => k !== section);
  if (!open) next.push(section);
  try {
    if (next.length) {
      localStorage.setItem(storeKey(projectId), JSON.stringify(next));
    } else {
      localStorage.removeItem(storeKey(projectId));
    }
  } catch {
    /* private mode — the toggle still works, it just won't survive a reload */
  }
}

/**
 * Ask a section to open itself, if it's collapsed.
 *
 * The section map jumps to `#<key>-island`; landing on a collapsed header
 * would be a dead end, so the map fires this first and the section listens.
 */
export function revealSection(section: string): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(
    new CustomEvent(SECTION_REVEAL_EVENT, { detail: { section } }),
  );
}
