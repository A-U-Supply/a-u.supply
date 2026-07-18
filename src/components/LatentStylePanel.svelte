<!--
  LatentStylePanel — the single style-settings host for the Latent detail
  page (2026-07-17-latent-section-styles). Style buttons don't own panels;
  they dispatch `latent-style-open` and this component opens anchored to
  them — a popover on desktop, a bottom sheet under 640px.

  Sections and slots share one control stack (accent · background · border ·
  text · head band · Reset all); the only difference is the background-mode
  row (slots add `auto` = inherit the ★ starred image). Every successful
  PATCH broadcasts `latent-style-changed` {projectId, scope, summary} so
  spines, bands, swatches, and the section map repaint live.

  Resets never touch content — they only clear saved style choices.
-->
<script lang="ts">
  import PullFromIndex from './PullFromIndex.svelte';
  import {
    SECTION_LABELS,
    safeHex,
    effectiveGround,
    autoTextColor,
    contrastRatio,
    type SectionKey,
    type ThemeName,
  } from '../lib/latentStyles.ts';

  type Props = { projectId: string };
  let { projectId }: Props = $props();

  let open = $state(false);
  let scope = $state<'section' | 'slot'>('section');
  let sectionKey = $state<string | null>(null);
  let slotId = $state<string | null>(null);
  let title = $state('');
  let current = $state<Record<string, string>>({});
  let accentAuto = $state<string | null>(null);
  let anchor = $state<{
    top: number;
    left: number;
    right: number;
    bottom: number;
  } | null>(null);
  let error = $state<string | null>(null);
  let pickerOpen = $state(false);
  let panelEl = $state<HTMLElement | null>(null);

  const PANEL_WIDTH = 300;

  const bgModes = $derived(
    scope === 'slot'
      ? ['auto', 'image', 'solid', 'none']
      : ['image', 'solid', 'none'],
  );
  const effBgMode = $derived(
    current.bg_mode || (scope === 'slot' ? 'auto' : 'none'),
  );
  const effAccent = $derived(safeHex(current.accent) || safeHex(accentAuto));
  const accentResetLabel = $derived(scope === 'slot' ? 'auto' : 'default');

  // Warn (never block) when the current text choice fails WCAG AA against
  // the blended card ground in either theme. Auto text is contrast-computed,
  // so this fires almost exclusively on explicit picks — in exactly the
  // theme that's bad.
  const contrastWarnings = $derived.by(() => {
    const washHex = effBgMode === 'solid' ? safeHex(current.bg_color) : null;
    const bad: ThemeName[] = [];
    for (const theme of ['light', 'dark'] as ThemeName[]) {
      const ground = effectiveGround(washHex, theme);
      const text = safeHex(current.text) || autoTextColor(ground, theme);
      if (contrastRatio(text, ground) < 4.5) bad.push(theme);
    }
    return bad;
  });

  const panelStyle = $derived.by(() => {
    if (!anchor || typeof window === 'undefined') return '';
    if (window.innerWidth < 640) return ''; // bottom sheet — CSS positions it
    const left = Math.max(
      8,
      Math.min(anchor.right - PANEL_WIDTH, window.innerWidth - PANEL_WIDTH - 8),
    );
    const top = Math.min(anchor.bottom + 6, window.innerHeight - 80);
    return `position:fixed;top:${top}px;left:${left}px;width:${PANEL_WIDTH}px;`;
  });

  async function onOpen(e: Event) {
    const d = (e as CustomEvent).detail;
    if (!d || d.projectId !== projectId) return;
    scope = d.scope;
    sectionKey = d.sectionKey || null;
    slotId = d.slotId || null;
    anchor = d.anchorRect || null;
    error = null;
    // Always fetch fresh state on open — buttons carry no style payload, so
    // there's nothing to go stale between islands.
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
      const p = await res.json();
      if (scope === 'section' && sectionKey) {
        current = (p.section_styles || {})[sectionKey] || {};
        accentAuto = null;
        title = SECTION_LABELS[sectionKey as SectionKey] || sectionKey;
      } else if (scope === 'slot' && slotId) {
        const slot = (p.slots || []).find((s: any) => s.id === slotId);
        if (!slot) throw new Error('Slot not found');
        current = slot.style || {};
        accentAuto = slot.accent_auto || null;
        title = slot.label || `Slot ${slot.position}`;
      } else {
        return;
      }
      open = true;
    } catch (err: any) {
      error = err?.message || 'Failed to load style';
      open = true;
    }
  }

  function close() {
    open = false;
    pickerOpen = false;
  }

  function onPointerDown(e: PointerEvent) {
    if (!open || pickerOpen) return;
    const t = e.target as HTMLElement;
    if (panelEl && !panelEl.contains(t) && !t.closest('.style-btn')) close();
  }

  $effect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !pickerOpen) close();
    };
    document.addEventListener('latent-style-open', onOpen);
    document.addEventListener('keydown', esc);
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      document.removeEventListener('latent-style-open', onOpen);
      document.removeEventListener('keydown', esc);
      document.removeEventListener('pointerdown', onPointerDown);
    };
  });

  async function patchStyle(patch: Record<string, string>) {
    error = null;
    try {
      const url =
        scope === 'section'
          ? `/api/projects/${encodeURIComponent(projectId)}`
          : `/api/projects/${encodeURIComponent(projectId)}/slots/${encodeURIComponent(slotId!)}`;
      const body =
        scope === 'section'
          ? { section_styles: { [sectionKey!]: patch } }
          : { style: patch };
      const res = await fetch(url, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || `Save failed (${res.status})`);
      }
      const summary = await res.json();
      if (scope === 'section') {
        current = (summary.section_styles || {})[sectionKey!] || {};
      } else {
        current = summary.style || {};
        accentAuto = summary.accent_auto ?? null;
      }
      window.dispatchEvent(
        new CustomEvent('latent-style-changed', {
          detail: { projectId, scope, summary },
        }),
      );
    } catch (e: any) {
      error = e?.message || 'Save failed';
    }
  }

  // Debounced color inputs — optimistic locally so everything wearing the
  // color follows the drag once the PATCH lands (500ms, hero precedent).
  let timers: Record<string, any> = {};
  function colorInput(key: string, e: Event) {
    const v = (e.target as HTMLInputElement).value;
    current = { ...current, [key]: v };
    if (timers[key]) clearTimeout(timers[key]);
    timers[key] = setTimeout(() => patchStyle({ [key]: v }), 500);
  }

  function resetKey(key: string) {
    patchStyle({ [key]: '' });
  }

  function resetAll() {
    // {} = clear the whole style object (server contract). Style only —
    // never touches files, notes, or anything attached.
    patchStyle({});
  }

  function pickBgImage(id: string) {
    patchStyle({ bg_mode: 'image', bg_media_item_id: id });
  }

  function thumbUrl(id: string): string {
    return `/api/media/${encodeURIComponent(id)}/thumbnail?size=sm`;
  }
</script>

{#if open}
  <div class="backdrop" onclick={close} aria-hidden="true"></div>
  <div
    class="panel"
    bind:this={panelEl}
    style={panelStyle}
    role="dialog"
    aria-label="Style settings for {title}"
  >
    <header class="panel__head">
      <span class="panel__title">Style — {title}</span>
      <button
        class="panel__close"
        type="button"
        aria-label="Close"
        onclick={close}>×</button
      >
    </header>

    <div class="row">
      <span class="row__label">Accent</span>
      <input
        type="color"
        value={effAccent || '#888888'}
        oninput={(e) => colorInput('accent', e)}
      />
      {#if current.accent}
        <button
          class="chip"
          type="button"
          title="Reset to the {accentResetLabel} color"
          onclick={() => resetKey('accent')}>{accentResetLabel}</button
        >
      {/if}
    </div>

    <div class="row">
      <span class="row__label">Background</span>
      <div class="chips" role="radiogroup" aria-label="Background mode">
        {#each bgModes as m (m)}
          <button
            class="chip"
            class:active={effBgMode === m}
            type="button"
            role="radio"
            aria-checked={effBgMode === m}
            onclick={() => patchStyle({ bg_mode: m })}>{m}</button
          >
        {/each}
      </div>
    </div>

    {#if effBgMode === 'image'}
      <div class="row row--indent">
        {#if current.bg_media_item_id}
          <img
            class="bg-thumb"
            src={thumbUrl(current.bg_media_item_id)}
            alt="Background"
          />
        {/if}
        <button class="chip" type="button" onclick={() => (pickerOpen = true)}
          >{current.bg_media_item_id ? 'Replace image…' : 'Pick image…'}</button
        >
      </div>
    {:else if effBgMode === 'solid'}
      <div class="row row--indent">
        <input
          type="color"
          value={safeHex(current.bg_color) || '#888888'}
          oninput={(e) => colorInput('bg_color', e)}
        />
        <span class="muted-note">washed into the card, never full-strength</span
        >
      </div>
    {:else if effBgMode === 'auto'}
      <div class="row row--indent">
        <span class="muted-note">uses this slot's ★ starred image</span>
      </div>
    {/if}

    <div class="row">
      <span class="row__label">Border</span>
      <input
        type="color"
        value={safeHex(current.border) || effAccent || '#888888'}
        oninput={(e) => colorInput('border', e)}
      />
      {#if current.border}
        <button
          class="chip"
          type="button"
          title="Reset to the accent color"
          onclick={() => resetKey('border')}>accent</button
        >
      {/if}
    </div>

    <div class="row">
      <span class="row__label">Text</span>
      <input
        type="color"
        value={safeHex(current.text) || '#888888'}
        oninput={(e) => colorInput('text', e)}
      />
      {#if current.text}
        <button
          class="chip"
          type="button"
          title="Reset to automatic contrast"
          onclick={() => resetKey('text')}>auto</button
        >
      {/if}
    </div>
    {#each contrastWarnings as theme (theme)}
      <div class="warn">▲ low contrast in {theme} theme</div>
    {/each}

    <div class="row">
      <span class="row__label">Head band</span>
      <input
        type="color"
        value={safeHex(current.head_tint) || effAccent || '#888888'}
        oninput={(e) => colorInput('head_tint', e)}
      />
      {#if current.head_tint}
        <button
          class="chip"
          type="button"
          title="Reset to the accent color"
          onclick={() => resetKey('head_tint')}>accent</button
        >
      {/if}
    </div>

    <footer class="panel__foot">
      <button
        class="chip"
        type="button"
        title="Clear every style choice here — back to defaults. Content is untouched."
        onclick={resetAll}>Reset all</button
      >
      {#if error}
        <span class="error">{error}</span>
      {/if}
    </footer>
  </div>
{/if}

<PullFromIndex
  bind:open={pickerOpen}
  {projectId}
  selectMode={true}
  onSelect={pickBgImage}
/>

<style>
  .backdrop {
    display: none;
  }
  .panel {
    position: fixed;
    z-index: 120;
    background: var(--color-bg);
    color: var(--color-text);
    border: 2px solid var(--color-text);
    box-shadow: 3px 3px 0 var(--color-text);
    padding: var(--space-sm);
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-size: var(--text-sm);
  }
  .panel__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    border-bottom: 1px solid var(--color-border);
    padding-bottom: 6px;
  }
  .panel__title {
    font-weight: 700;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .panel__close {
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--color-border);
    color: var(--color-muted);
    font-size: 1rem;
    line-height: 1;
    padding: 0 6px;
    cursor: pointer;
  }
  .panel__close:hover {
    color: var(--color-text);
    border-color: var(--color-text);
  }
  .row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .row--indent {
    padding-left: 78px;
  }
  .row__label {
    flex: 0 0 70px;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    color: var(--color-muted);
  }
  input[type='color'] {
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    cursor: pointer;
  }
  .chips {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
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
  .bg-thumb {
    width: 28px;
    height: 28px;
    object-fit: cover;
    border: 1px solid var(--color-border);
  }
  .muted-note {
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .warn {
    color: var(--color-status-warn);
    font-size: 0.7rem;
    padding-left: 78px;
  }
  .panel__foot {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    border-top: 1px solid var(--color-border);
    padding-top: 6px;
  }
  .error {
    color: var(--color-status-fail);
    font-size: 0.7rem;
  }
  @media (max-width: 639px) {
    .backdrop {
      display: block;
      position: fixed;
      inset: 0;
      background: var(--color-overlay-soft);
      z-index: 110;
    }
    .panel {
      /* Bottom sheet — inline popover coords don't apply (panelStyle is
         empty under 640px). */
      left: 0 !important;
      right: 0 !important;
      top: auto !important;
      bottom: 0 !important;
      width: auto !important;
      max-height: 70vh;
      overflow-y: auto;
      box-shadow: 0 -3px 0 var(--color-text);
    }
  }
</style>
