/**
 * Move an element to the end of <body> for as long as it's rendered.
 *
 * Latents sections set `isolation: isolate` (they paint face/veil layers
 * behind their content), so every section is its own stacking context and a
 * `z-index` inside one cannot escape it — a later section paints over an
 * overlay in an earlier one no matter how high its z-index. detail.astro
 * already notes this at LatentStylePanel's mount point, which dodges the
 * problem by living outside the sections entirely.
 *
 * An island that owns its own overlay can't be hoisted out like that, so it
 * portals the overlay instead. Svelte's scoped class travels with the node,
 * so component styles keep applying.
 */
export function portal(node: HTMLElement) {
  document.body.appendChild(node);
  return {
    destroy() {
      node.remove();
    },
  };
}
