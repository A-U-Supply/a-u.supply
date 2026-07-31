/**
 * Per-latent section arrangement — which detail-page sections are shown, and
 * in what order (docs/plans/2026-07-31-latent-section-arrange.md).
 *
 * Stored server-side on `projects.section_layout` as
 * `{ order: [key…], hidden: [key…] }`, because this describes the LATENT, not
 * the person looking at it: a latent that doesn't use Playlists doesn't use
 * them for anyone. (Contrast src/lib/latentCollapse.ts, which is a per-browser
 * preference and lives in localStorage.)
 *
 * Hiding never touches content. The island stays mounted with its data loaded
 * and simply stops being painted, so un-hiding brings back the same paragraph,
 * the same files, the same everything.
 *
 * `resolveLayout` is the single reader — the section map, the Arrange dialog
 * and detail.astro all go through it, so the three cannot disagree about what
 * a stored layout means.
 */

import { SECTION_KEYS, SECTION_LABELS } from './latentStyles.ts';

/**
 * Every arrangeable section, in default page order — the styleable ones plus
 * Marginalia. Marginalia is deliberately NOT in SECTION_KEYS: it carries no
 * colour of its own (it borrows the threads accent) and is not a valid
 * section_styles key on the server. It is still a section a user sees, so it
 * can be moved and hidden. Mirrors VALID_LAYOUT_KEYS in server/latents_api.py.
 */
export const LAYOUT_KEYS = [...SECTION_KEYS, 'marginalia'] as const;
export type LayoutKey = (typeof LAYOUT_KEYS)[number];

export const LAYOUT_LABELS: Record<LayoutKey, string> = {
  ...SECTION_LABELS,
  marginalia: 'Comments & markers',
};

/** Broadcast after every successful layout write; carries the raw stored
 * object so listeners resolve it themselves. */
export const LAYOUT_EVENT = 'latent-layout-changed';

export type StoredLayout = {
  order?: unknown;
  hidden?: unknown;
};

export type ResolvedLayout = {
  /** Every layout key exactly once, in the order the page should show them. */
  order: LayoutKey[];
  hidden: Set<LayoutKey>;
};

function isLayoutKey(v: unknown): v is LayoutKey {
  return (
    typeof v === 'string' && (LAYOUT_KEYS as readonly string[]).includes(v)
  );
}

/**
 * Turn a stored layout into something renderable.
 *
 * The stored `order` may be partial or stale: unknown keys are dropped, and
 * every known key it didn't mention is appended in default page order. That is
 * what lets a layout saved today survive a section being ADDED later — the new
 * one arrives at the end, visible, instead of vanishing. A key absent from
 * `hidden` reads as shown, so a forgotten entry fails safe the same way.
 */
export function resolveLayout(
  raw: StoredLayout | null | undefined,
): ResolvedLayout {
  const stored = Array.isArray(raw?.order) ? raw.order : [];
  const order: LayoutKey[] = [];
  for (const key of stored) {
    if (isLayoutKey(key) && !order.includes(key)) order.push(key);
  }
  for (const key of LAYOUT_KEYS) {
    if (!order.includes(key)) order.push(key);
  }
  const hiddenRaw = Array.isArray(raw?.hidden) ? raw.hidden : [];
  return { order, hidden: new Set(hiddenRaw.filter(isLayoutKey)) };
}

/** The object to PATCH back. Always writes the FULL order — a partial order is
 * legal to store but there's no reason to author one. */
export function serializeLayout(l: ResolvedLayout): {
  order: LayoutKey[];
  hidden: LayoutKey[];
} {
  return {
    order: [...l.order],
    hidden: l.order.filter((k) => l.hidden.has(k)),
  };
}
