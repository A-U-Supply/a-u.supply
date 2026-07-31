<!--
  LatentSectionMap — the "Section map" strip under the Latent detail header
  (2026-07-17-latent-section-styles). One labeled chip per fixed section and
  per slot, in page order; click scrolls to the section. Chips carry the
  section's live color plus its name (truncated, full name in the tooltip),
  so the map doubles as the legend for the page's color grammar.

  Sticky on mobile only, where the page is a long scroll and the map is the
  primary way to jump around.

  The head carries the Arrange button (LatentArrange), which sets which
  sections are shown and in what order. A hidden section drops its chip — so
  the head also states how many are hidden, because once the chip is gone that
  button is the only way back.

  Live updates ride three window events:
  - `latent-style-changed` (Style panel) — colors and, for slots, labels.
  - `latent-slots-updated` (LatentSlots) — add / remove / reorder / rename.
  - `latent-layout-changed` (LatentArrange) — order and visibility.
-->
<script lang="ts">
  import {
    SECTION_TOKENS,
    safeHex,
    effectiveAccent,
    type SectionKey,
  } from '../lib/latentStyles.ts';
  import {
    LAYOUT_EVENT,
    LAYOUT_LABELS,
    resolveLayout,
    type LayoutKey,
    type StoredLayout,
  } from '../lib/latentLayout.ts';
  import { revealSection } from '../lib/latentCollapse.ts';
  import LatentArrange from './LatentArrange.svelte';

  type SlotChip = {
    id: string;
    label: string;
    position: number;
    accent: string | null;
  };
  type Props = {
    projectId: string;
    sectionStyles?: Record<string, Record<string, string>>;
    sectionLayout?: StoredLayout;
    initialSlots?: SlotChip[];
  };

  let {
    projectId,
    sectionStyles = {},
    sectionLayout = {},
    initialSlots = [],
  }: Props = $props();

  let styles = $state<Record<string, Record<string, string>>>(sectionStyles);
  let slots = $state<SlotChip[]>(initialSlots);
  let layoutRaw = $state<StoredLayout>(sectionLayout);
  let arrangeOpen = $state(false);

  const layout = $derived(resolveLayout(layoutRaw));
  const shown = $derived(layout.order.filter((k) => !layout.hidden.has(k)));
  const hiddenCount = $derived(layout.order.length - shown.length);

  function sectionSwatch(key: LayoutKey): string {
    // Marginalia has no styleable colour of its own — it borrows the threads
    // accent on the page, so its chip does too.
    if (key === 'marginalia') {
      const hex = effectiveAccent(styles.threads);
      return `background:${hex || SECTION_TOKENS.threads}`;
    }
    // effectiveAccent so chips track solid-face-derived accents too.
    const hex = effectiveAccent(styles[key]);
    return `background:${hex || SECTION_TOKENS[key as SectionKey]}`;
  }

  function slotSwatch(s: SlotChip): string {
    const hex = safeHex(s.accent);
    return `background:${hex || 'var(--color-muted)'}`;
  }

  function scrollToEl(selector: string) {
    const el = document.querySelector(selector);
    if (!el) return;
    const reduced = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches;
    // On a phone the admin sidebar toggle is `position: fixed` over the top
    // edge, so a plain scrollIntoView parks the section's head underneath it
    // — and for a COLLAPSED section the head is all there is to see. Measure
    // the chrome rather than guessing a constant.
    //
    // (This map declares `position: sticky` for phones but never actually
    // sticks: #map-island is only as tall as the map, so it scrolls away with
    // the page. Pre-existing; left alone here.)
    const toggle = document.querySelector<HTMLElement>('.sidebar__toggle');
    const cs = toggle && getComputedStyle(toggle);
    const offset =
      cs && cs.position === 'fixed' && cs.display !== 'none'
        ? toggle!.getBoundingClientRect().bottom + 8
        : 0;
    const top = el.getBoundingClientRect().top + window.scrollY - offset;
    window.scrollTo({
      top: Math.max(0, top),
      behavior: reduced ? 'auto' : 'smooth',
    });
  }

  /**
   * Three sections can be collapsed (see lib/latentCollapse.ts). Jumping to a
   * collapsed one would land on a head line with nothing under it, so ask it
   * to open first and scroll on the next frame, once it has rendered.
   */
  function goToSection(key: LayoutKey) {
    revealSection(key);
    requestAnimationFrame(() => scrollToEl(`#${key}-island`));
  }

  function onStyleChanged(e: Event) {
    const d = (e as CustomEvent).detail;
    if (!d || d.projectId !== projectId || !d.summary) return;
    if (d.scope === 'section' && d.summary.section_styles !== undefined) {
      styles = d.summary.section_styles || {};
    } else if (d.scope === 'slot') {
      slots = slots.map((s) =>
        s.id === d.summary.id
          ? {
              ...s,
              label: d.summary.label ?? s.label,
              accent: d.summary.accent ?? null,
            }
          : s,
      );
    }
  }

  function onSlotsUpdated(e: Event) {
    const d = (e as CustomEvent).detail;
    if (!d || d.projectId !== projectId || !Array.isArray(d.slots)) return;
    slots = d.slots;
  }

  function onLayoutChanged(e: Event) {
    const d = (e as CustomEvent).detail;
    if (!d || d.projectId !== projectId) return;
    layoutRaw = d.layout || {};
  }

  $effect(() => {
    window.addEventListener('latent-style-changed', onStyleChanged);
    window.addEventListener('latent-slots-updated', onSlotsUpdated);
    window.addEventListener(LAYOUT_EVENT, onLayoutChanged);
    return () => {
      window.removeEventListener('latent-style-changed', onStyleChanged);
      window.removeEventListener('latent-slots-updated', onSlotsUpdated);
      window.removeEventListener(LAYOUT_EVENT, onLayoutChanged);
    };
  });
</script>

<nav class="map" aria-label="Section map">
  <div class="map__head">
    <h2 class="map__title">Section map</h2>
    {#if hiddenCount > 0}
      <span class="map__hidden">{hiddenCount} hidden</span>
    {/if}
    <button
      class="map__arrange"
      type="button"
      onclick={() => (arrangeOpen = true)}
    >
      Arrange sections
    </button>
  </div>
  <div class="map__chips">
    {#each shown as key (key)}
      <button
        class="map__chip"
        type="button"
        title={LAYOUT_LABELS[key]}
        aria-label="Go to {LAYOUT_LABELS[key]}"
        onclick={() => goToSection(key)}
      >
        <i class="map__swatch" style={sectionSwatch(key)}></i>
        <span class="map__name">{LAYOUT_LABELS[key]}</span>
      </button>
      {#if key === 'slots'}
        {#each slots as s (s.id)}
          <button
            class="map__chip map__chip--slot"
            type="button"
            title={s.label}
            aria-label="Go to slot {s.label}"
            onclick={() => scrollToEl(`[data-slot-id="${CSS.escape(s.id)}"]`)}
          >
            <i class="map__swatch" style={slotSwatch(s)}></i>
            <span class="map__name">{s.label}</span>
          </button>
        {/each}
      {/if}
    {/each}
  </div>
</nav>

<LatentArrange
  {projectId}
  {slots}
  open={arrangeOpen}
  layout={layoutRaw}
  swatchFor={sectionSwatch}
  onClose={() => (arrangeOpen = false)}
/>

<style>
  .map {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px 0;
    margin-top: var(--space-xs);
  }
  .map__head {
    display: flex;
    align-items: center;
    gap: var(--space-xs);
  }
  /* Typed as a peer of the section heads it maps — the same 700-weight
     uppercase at --text-sm that .sec-summary__label uses — rather than as the
     small muted caption it was when it was a bare label. */
  .map__title {
    font-size: var(--text-sm);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-text);
    white-space: nowrap;
    margin: 0;
  }
  /* Hiding must never be silent: once a chip is gone, Arrange is the only way
     back, so the head says how much is missing. */
  .map__hidden {
    font-family: var(--font-mono);
    font-size: 0.62rem;
    color: var(--color-muted);
    white-space: nowrap;
  }
  /* A filled ochre plate, not another outlined chip. This is the only route
     back to a section you've hidden — once its chip is gone there is nothing
     else to click — so it has to read as the one control in the row rather
     than the last item in a line of them.

     Ochre is --color-accent, the house's "interactive" hue, and it is light in
     BOTH themes: the ink is a fixed near-black, exactly as .sidebar__badge in
     admin.css does it. Theme text would be #ececec on ochre in dark (2.4:1);
     this way it measures 5.35:1 light and 7.33:1 dark. */
  .map__arrange {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    padding: 5px 12px;
    min-height: 32px;
    background: var(--color-accent);
    color: #1a1a1a;
    border: 1px solid var(--color-fg);
    box-shadow: var(--shadow-sm);
    cursor: pointer;
    white-space: nowrap;
  }
  .map__arrange:hover {
    box-shadow: var(--shadow-md);
  }
  .map__arrange:active {
    box-shadow: none;
    transform: translate(1px, 1px);
  }
  .map__arrange:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 1px;
  }
  .map__chips {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
  }
  .map__chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 8px 2px 5px;
    background: var(--color-surface);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    font-family: var(--font-mono);
    font-size: 0.66rem;
    cursor: pointer;
    white-space: nowrap;
  }
  .map__chip:hover {
    border-color: var(--color-text);
  }
  .map__chip:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 1px;
  }
  .map__chip--slot {
    border-style: dashed;
  }
  .map__swatch {
    width: 9px;
    height: 9px;
    flex: 0 0 9px;
    border: 1px solid var(--color-border);
  }
  .map__name {
    max-width: 110px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  @media (max-width: 640px) {
    /* Mobile: the map is the primary navigation — sticky, and wrapping rather
       than scrolling. A scroll strip hid the later sections off the edge, and
       this is the only nav on the page. */
    .map {
      position: sticky;
      top: 0;
      z-index: 30;
      background: var(--color-bg);
      gap: 3px;
      border-bottom: 2px solid var(--color-text);
      padding: 5px 0;
    }
    .map__chips {
      gap: 3px;
    }
    .map__arrange {
      min-height: 36px;
    }
    /* Was 0.66rem with 2px padding — under the touch guidance the rest of the
       page respects, for the control you navigate with. */
    .map__chip {
      border: 1px solid var(--color-border);
      padding: 5px 6px;
      min-height: 32px;
    }
    .map__name {
      max-width: 92px;
    }
  }
</style>
