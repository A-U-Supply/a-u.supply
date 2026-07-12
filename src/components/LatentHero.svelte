<!--
  LatentHero — compact hero-card controls mounted in the Latent detail
  header. One row: hero thumb chip · Set/Replace · Remove · style chips ·
  accent swatch (+ auto reset when an override exists).

  All writes are single-field PATCHes, safe alongside the header's
  debounced name/slug/description autosaves. Other islands can change the
  hero too (loose-files quick action) — they announce it with a
  `latent-hero-changed` window event carrying the fresh project summary.
-->
<script lang="ts">
  import PullFromIndex from './PullFromIndex.svelte';

  type Props = {
    projectId: string;
    heroMediaItemId?: string | null;
    heroStyle?: string;
    heroAccentAuto?: string | null;
    heroAccentOverride?: string | null;
    onAccentChange?: (accent: string | null) => void;
  };

  let {
    projectId,
    heroMediaItemId = null,
    heroStyle = 'scrim',
    heroAccentAuto = null,
    heroAccentOverride = null,
    onAccentChange,
  }: Props = $props();

  const STYLES = ['scrim', 'plate', 'treat'];

  let hero = $state(heroMediaItemId);
  let style = $state(heroStyle);
  let accentAuto = $state(heroAccentAuto);
  let accentOverride = $state(heroAccentOverride);
  let pickerOpen = $state(false);
  let error = $state<string | null>(null);

  const accent = $derived(accentOverride || accentAuto);

  $effect(() => {
    onAccentChange?.(accent || null);
  });

  // The loose-files quick action PATCHes the hero directly; pick up the
  // fresh summary it broadcasts so this row never goes stale.
  $effect(() => {
    const handler = (e: Event) => {
      const s = (e as CustomEvent).detail;
      if (s?.id === projectId) applySummary(s);
    };
    window.addEventListener('latent-hero-changed', handler);
    return () => window.removeEventListener('latent-hero-changed', handler);
  });

  function applySummary(s: any) {
    hero = s.hero_media_item_id;
    style = s.hero_style || 'scrim';
    accentAuto = s.hero_accent_auto;
    accentOverride = s.hero_accent_override;
  }

  async function patch(payload: Record<string, string>) {
    error = null;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || `Save failed (${res.status})`);
      }
      applySummary(await res.json());
    } catch (e: any) {
      error = e?.message || 'Save failed';
    }
  }

  let accentTimer: any = null;
  function onAccentInput(e: Event) {
    const v = (e.target as HTMLInputElement).value;
    accentOverride = v; // optimistic — the header spine follows the drag
    if (accentTimer) clearTimeout(accentTimer);
    accentTimer = setTimeout(() => patch({ hero_accent_override: v }), 500);
  }

  function thumbUrl(id: string): string {
    return `/api/media/${encodeURIComponent(id)}/thumbnail?size=sm`;
  }
</script>

<div class="hero-row">
  <span class="label">Card image</span>
  {#if hero}
    <a
      class="thumb"
      href={`/admin/search/detail?id=${encodeURIComponent(hero)}`}
      title="Open in search"
    >
      <img src={thumbUrl(hero)} alt="Card image" />
    </a>
  {:else}
    <span class="thumb thumb--empty">no card image</span>
  {/if}
  <button class="action-btn" type="button" onclick={() => (pickerOpen = true)}>
    {hero ? 'Replace' : 'Set'}
  </button>
  {#if hero}
    <button
      class="action-btn action-btn--danger"
      type="button"
      onclick={() => patch({ hero_media_item_id: '' })}
    >
      Remove
    </button>
    <div class="style-chips" role="radiogroup" aria-label="Card style">
      {#each STYLES as v (v)}
        <button
          class="chip"
          class:active={style === v}
          type="button"
          role="radio"
          aria-checked={style === v}
          onclick={() => patch({ hero_style: v })}
        >
          {v}
        </button>
      {/each}
    </div>
    <label class="swatch" title="Card accent color">
      <input type="color" value={accent || '#888888'} oninput={onAccentInput} />
      accent
    </label>
    {#if accentOverride}
      <button
        class="chip"
        type="button"
        title="Reset to the color extracted from the image"
        onclick={() => patch({ hero_accent_override: '' })}
      >
        auto
      </button>
    {/if}
  {/if}
  {#if error}
    <span class="error">{error}</span>
  {/if}
</div>

<PullFromIndex
  bind:open={pickerOpen}
  {projectId}
  selectMode={true}
  onSelect={(id) => patch({ hero_media_item_id: id })}
/>

<style>
  .hero-row {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex-wrap: wrap;
    border: 1px dashed var(--color-border);
    padding: var(--space-xs) var(--space-sm);
  }
  .label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
  }
  .thumb {
    width: 64px;
    height: 64px;
    flex: 0 0 64px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--color-border);
    overflow: hidden;
  }
  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .thumb--empty {
    border-style: dashed;
    color: var(--color-muted);
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    text-align: center;
  }
  .style-chips {
    display: flex;
    gap: 4px;
  }
  .chip {
    padding: 3px 10px;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    text-transform: lowercase;
  }
  .chip:hover {
    border-color: var(--color-text);
  }
  .chip.active {
    background: var(--color-text);
    color: var(--color-bg);
    border-color: var(--color-text);
  }
  .swatch {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
    cursor: pointer;
  }
  .swatch input[type='color'] {
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    cursor: pointer;
  }
  .error {
    color: var(--color-status-fail);
    font-size: var(--text-sm);
  }
</style>
