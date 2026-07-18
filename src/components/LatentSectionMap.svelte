<!--
  LatentSectionMap — the "Section map" strip under the Latent detail header
  (2026-07-17-latent-section-styles). One labeled chip per fixed section and
  per slot, in page order; click scrolls to the section. Chips carry the
  section's live color plus its name (truncated, full name in the tooltip),
  so the map doubles as the legend for the page's color grammar.

  Sticky on mobile only, where the page is a long scroll and the map is the
  primary way to jump around.

  Live updates ride two window events:
  - `latent-style-changed` (Style panel) — colors and, for slots, labels.
  - `latent-slots-updated` (LatentSlots) — add / remove / reorder / rename.
-->
<script lang="ts">
  import {
    SECTION_KEYS,
    SECTION_LABELS,
    SECTION_TOKENS,
    safeHex,
    effectiveAccent,
    type SectionKey,
  } from '../lib/latentStyles.ts';

  type SlotChip = {
    id: string;
    label: string;
    position: number;
    accent: string | null;
  };
  type Props = {
    projectId: string;
    sectionStyles?: Record<string, Record<string, string>>;
    initialSlots?: SlotChip[];
  };

  let { projectId, sectionStyles = {}, initialSlots = [] }: Props = $props();

  let styles = $state<Record<string, Record<string, string>>>(sectionStyles);
  let slots = $state<SlotChip[]>(initialSlots);

  function sectionSwatch(key: SectionKey): string {
    // effectiveAccent so chips track solid-face-derived accents too.
    const hex = effectiveAccent(styles[key]);
    return `background:${hex || SECTION_TOKENS[key]}`;
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
    el.scrollIntoView({
      behavior: reduced ? 'auto' : 'smooth',
      block: 'start',
    });
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

  $effect(() => {
    window.addEventListener('latent-style-changed', onStyleChanged);
    window.addEventListener('latent-slots-updated', onSlotsUpdated);
    return () => {
      window.removeEventListener('latent-style-changed', onStyleChanged);
      window.removeEventListener('latent-slots-updated', onSlotsUpdated);
    };
  });
</script>

<nav class="map" aria-label="Section map">
  <span class="map__label">Section map</span>
  {#each SECTION_KEYS as key (key)}
    <button
      class="map__chip"
      type="button"
      title={SECTION_LABELS[key]}
      aria-label="Go to {SECTION_LABELS[key]}"
      onclick={() => scrollToEl(`#${key}-island`)}
    >
      <i class="map__swatch" style={sectionSwatch(key)}></i>
      <span class="map__name">{SECTION_LABELS[key]}</span>
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
</nav>

<style>
  .map {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
    padding: 4px 0;
    margin-top: var(--space-xs);
  }
  .map__label {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
    margin-right: 4px;
    white-space: nowrap;
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
    /* Mobile: the map is the primary navigation — sticky, one scrollable
       row so a long slot list can't bury the page. */
    .map {
      position: sticky;
      top: 0;
      z-index: 30;
      background: var(--color-bg);
      flex-wrap: nowrap;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      border-bottom: 1px solid var(--color-border);
      padding: 6px 0;
    }
  }
</style>
