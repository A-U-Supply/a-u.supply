/**
 * Shared Sortable options for every reorderable list in the Latents UI.
 *
 * On touch a drag and a page scroll are the same gesture, so a drag needs a
 * long press, a tolerant threshold, edge auto-scroll, and visible
 * acknowledgement of the press. These values were tuned on a real phone for
 * the playlists work (docs/plans/2026-07-24-latent-playlists.md) — they were
 * then copy-pasted into a second component, which is what this module ends.
 *
 * `handle` is deliberately NOT set here: it differs per list, and putting
 * `touch-action: none` on anything wider than the handle kills scrolling
 * through the list.
 */
export const DRAG_OPTS = {
  animation: 120,
  delay: 180,
  delayOnTouchOnly: true,
  touchStartThreshold: 6,
  scroll: true,
  bubbleScroll: true,
  scrollSensitivity: 60,
  scrollSpeed: 12,
  chosenClass: 'drag-chosen',
} as const;
