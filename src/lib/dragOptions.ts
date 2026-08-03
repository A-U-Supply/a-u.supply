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
import Sortable from 'sortablejs';

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

/** Read an order out of the DOM Sortable just produced. */
function readOrder(list: HTMLElement, rows: string, id: string): string[] {
  return Array.from(list.querySelectorAll<HTMLElement>(rows))
    .map((el) => el.dataset[id])
    .filter((v): v is string => !!v);
}

type SortableOpts = Record<string, any>;

export type SortableList = {
  rows?: string;
  id?: string;
  onDrop?: (order: string[], evt: any) => void;
  onArrive?: (evt: any) => void;
  onFinish?: (evt: any) => void;
} & SortableOpts;

/**
 * Create a Sortable over a list that Svelte renders.
 *
 * **Sortable's DOM changes must never outlive the drop.** Every reorderable
 * list here is a keyed `{#each}`, and Svelte tracks each item as a *range* of
 * nodes, not one node: a row followed by an `{#if}` block is `<li>` … anchor
 * comment. Sortable moves the `<li>` alone, so the range breaks — the row now
 * sits ahead of the comment that is supposed to end it. On the next update
 * Svelte's `move()` walks forward from the row looking for that end node,
 * never reaches it, and cycles the nodes in front of its destination for
 * ever. The tab locks up: "Page Unresponsive", reported 2026-08-02 after two
 * drags inside one slot. Short of hanging, the same divergence renders an
 * order nobody asked for — the row you dropped on lands where the row you
 * dragged came from.
 *
 * So the drop reads the order the user built, hands the DOM straight back to
 * Svelte exactly as it left it, and lets the state assignment in `onDrop` be
 * what actually moves the row. Reordering by drag then goes through the same
 * one path as the ↑/↓ buttons.
 *
 * `onDrop` fires only when the row stayed in this list; a row that left is
 * `onArrive` on the receiving list. `onFinish` runs after every drag, once the
 * DOM is Svelte's again — do not read row order there, it is the *old* order
 * by design.
 */
export function createSortable(
  node: HTMLElement,
  { rows, id, onDrop, onArrive, onFinish, ...options }: SortableList,
): Sortable {
  /** Where the dragged row sat before Sortable touched anything. */
  let origin: { parent: Node; next: ChildNode | null } | null = null;

  return Sortable.create(node, {
    ...options,
    onStart: (evt: any) => {
      origin = { parent: evt.from, next: evt.item.nextSibling };
      options.onStart?.(evt);
    },
    onAdd: (evt: any) => {
      // The row is still parked in this list; the list it came from puts it
      // back in its own `onEnd`, which Sortable fires immediately after.
      onArrive?.(evt);
    },
    onEnd: (evt: any) => {
      const order =
        rows && id && evt.to === evt.from ? readOrder(node, rows, id) : null;
      if (origin) {
        // Undo Sortable, before anything can render against a DOM Svelte does
        // not recognise. Restoring by remembered sibling rather than by index
        // is deliberate: these lists interleave rows with anchor comments and
        // expanded child rows, so Sortable's indices and the DOM's are not the
        // same count.
        try {
          origin.parent.insertBefore(evt.item, origin.next);
        } catch {
          // The origin row can be gone if the list re-rendered mid-drag; the
          // state assignment below is still the source of truth.
        }
        origin = null;
      }
      if (order?.length) onDrop?.(order, evt);
      onFinish?.(evt);
    },
  });
}
