<!--
  LatentStyleButton — the labeled [▪ Style] affordance on every section head
  band and slot card head (2026-07-17-latent-section-styles). The swatch
  square shows the current accent, so the button doubles as the legend for
  the color system. Deliberately a labeled word, not an icon: the UI must
  read for someone who doesn't already know the feature.

  Clicking dispatches a `latent-style-open` document event; the single
  LatentStylePanel host on the detail page listens and opens anchored here.
-->
<script lang="ts">
  import { safeHex } from '../lib/latentStyles.ts';

  type Props = {
    projectId: string;
    scope: 'section' | 'slot';
    sectionKey?: string | null;
    slotId?: string | null;
    /** Explicit swatch hex (slots pass their effective accent). */
    accent?: string | null;
    /** Don't fall back to the inherited --sec-accent (un-styled slots would
     * otherwise wrongly wear their parent section's hue). */
    noInherit?: boolean;
    /** Push to the right end of a flex header. */
    push?: boolean;
  };

  let {
    projectId,
    scope,
    sectionKey = null,
    slotId = null,
    accent = null,
    noInherit = false,
    push = false,
  }: Props = $props();

  const swatchStyle = $derived.by(() => {
    const hex = safeHex(accent);
    if (hex) return `background:${hex}`;
    return noInherit
      ? 'background:var(--color-muted)'
      : 'background:var(--sec-accent, var(--color-muted))';
  });

  function openPanel(e: MouseEvent) {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    document.dispatchEvent(
      new CustomEvent('latent-style-open', {
        detail: {
          projectId,
          scope,
          sectionKey,
          slotId,
          anchorRect: {
            top: rect.top,
            left: rect.left,
            right: rect.right,
            bottom: rect.bottom,
          },
        },
      }),
    );
  }
</script>

<button
  class="style-btn"
  class:style-btn--push={push}
  type="button"
  title="Style this {scope === 'slot' ? 'slot' : 'section'}"
  onclick={openPanel}
>
  <span class="style-btn__swatch" style={swatchStyle}></span>Style
</button>

<style>
  .style-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 2px 8px;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    cursor: pointer;
  }
  .style-btn:hover {
    border-color: var(--color-text);
  }
  .style-btn:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 1px;
  }
  .style-btn--push {
    margin-left: auto;
  }
  .style-btn__swatch {
    width: 10px;
    height: 10px;
    flex: 0 0 10px;
    border: 1px solid var(--color-border);
  }
</style>
