/**
 * Placing a fixed-position panel next to the button that opened it.
 *
 * This exists because the same bug keeps coming back. A panel anchored to a
 * button's `getBoundingClientRect()` is `position: fixed`, so the viewport is
 * the only space it has — and if you place it past the bottom edge, **there is
 * nothing to scroll to**. It is simply unreachable. That is not a cosmetic
 * problem; the control is gone.
 *
 * The failure is always the same shape: clamp the panel's TOP to the viewport
 * and call it done, forgetting that the panel has HEIGHT. `top: innerHeight -
 * 80` looks like a clamp and leaves 80px of a 600px panel on screen.
 *
 * So: one implementation, used by every anchored panel, that
 *   1. prefers below the anchor, flips above when below won't fit,
 *   2. takes the roomier side when neither fits,
 *   3. and caps `max-height` to the room it actually has, so a tall panel
 *      scrolls inside itself instead of hanging off the edge.
 *
 * The caller must apply `maxHeight` (and have `overflow-y: auto`), or step 3
 * does nothing.
 *
 * Related: src/lib/portal.ts solves the *other* half of the overlay problem —
 * escaping a section's stacking context. A panel inside a `.latent-section`
 * generally needs both.
 */

export type AnchorRect = {
  top: number;
  left: number;
  right: number;
  bottom: number;
};

export type AnchoredPlacement = {
  top: number;
  left: number;
  /** Room actually available on the chosen side. Apply it. */
  maxHeight: number;
  /** Which way it ended up opening — handy for a caret or a transform-origin. */
  direction: 'down' | 'up';
};

/** Below this much room, a side isn't worth opening into — take the other. */
const MIN_ROOM = 140;

export type AnchorOptions = {
  /** Panel width in px. Used to keep it inside the right edge. */
  width: number;
  /** Measured panel height. Pass 0 on a first pass, then re-place once mounted. */
  height?: number;
  /** Gap between anchor and panel. */
  gap?: number;
  /** Minimum distance from any viewport edge. */
  margin?: number;
  /**
   * Horizontal alignment against the anchor. `'right'` lines the panel's right
   * edge up with the anchor's (the usual choice for a menu hanging off a
   * button at the end of a row); `'left'` lines up the left edges.
   */
  align?: 'left' | 'right';
};

/**
 * Where to put a panel anchored to `anchor`, clamped so all of it is on
 * screen. Coordinates are viewport-relative, for `position: fixed`.
 */
export function placeAnchored(
  anchor: AnchorRect,
  opts: AnchorOptions,
): AnchoredPlacement {
  const { width } = opts;
  const gap = opts.gap ?? 6;
  const margin = opts.margin ?? 8;
  const height = opts.height ?? 0;
  const align = opts.align ?? 'right';

  const vw = window.innerWidth;
  const vh = window.innerHeight;

  const desiredLeft = align === 'right' ? anchor.right - width : anchor.left;
  const left = Math.max(margin, Math.min(desiredLeft, vw - width - margin));

  const roomBelow = vh - anchor.bottom - gap - margin;
  const roomAbove = anchor.top - gap - margin;

  // With `height` unknown (first pass, before the node exists) "fits" means
  // there's enough room to be worth opening into at all; the caller re-places
  // once it can measure.
  const enough = (room: number) =>
    height > 0 ? height <= room : room >= MIN_ROOM;
  const direction: 'down' | 'up' = enough(roomBelow)
    ? 'down'
    : enough(roomAbove)
      ? 'up'
      : roomBelow >= roomAbove
        ? 'down'
        : 'up';

  if (direction === 'down') {
    // Capping maxHeight to roomBelow is what keeps the bottom edge on screen:
    // top + roomBelow === vh - margin, by construction.
    const maxHeight = Math.max(0, roomBelow);
    return { top: anchor.bottom + gap, left, maxHeight, direction };
  }

  const maxHeight = Math.max(0, roomAbove);
  const used = Math.min(height || maxHeight, maxHeight);
  return {
    top: Math.max(margin, anchor.top - gap - used),
    left,
    maxHeight,
    direction,
  };
}

/** The placement as an inline `style` string, for `style={...}` bindings. */
export function anchoredStyle(anchor: AnchorRect, opts: AnchorOptions): string {
  const p = placeAnchored(anchor, opts);
  return (
    `position:fixed;top:${p.top}px;left:${p.left}px;` +
    `width:${opts.width}px;max-height:${p.maxHeight}px;`
  );
}
