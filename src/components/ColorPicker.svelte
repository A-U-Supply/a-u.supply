<!--
  ColorPicker — saturation/value square + hue slider + RGB fields, replacing
  native <input type="color"> in LatentStylePanel. The native input renders
  wildly different OS-supplied UI per platform (macOS's system color panel
  on desktop vs. a small preset grid on mobile Chrome) — this draws the same
  widget everywhere so mobile and desktop match.

  Expands inline below its swatch (an accordion within the parent .row),
  rather than a floating popover, so it inherits the host panel's existing
  popover/bottom-sheet handling for free.
-->
<script lang="ts">
  import {
    safeHex,
    hexToRgb,
    rgbToHex,
    rgbToHsv,
    hsvToRgb,
  } from '../lib/latentStyles.ts';

  type Props = {
    value: string;
    onInput: (hex: string) => void;
    label: string;
    title?: string;
  };
  let { value, onInput, label, title }: Props = $props();

  let expanded = $state(false);
  let h = $state(0);
  let s = $state(0);
  let v = $state(0);
  let dragging = $state<'square' | 'hue' | null>(null);
  let squareEl = $state<HTMLDivElement | null>(null);
  let hueEl = $state<HTMLDivElement | null>(null);

  const eyedropperSupported =
    typeof window !== 'undefined' && 'EyeDropper' in window;

  const rgb = $derived(hsvToRgb([h, s, v]));
  const currentHex = $derived(rgbToHex(rgb));
  const swatchHex = $derived(safeHex(value) || '#888888');

  function syncFromValue() {
    const [hh, ss, vv] = rgbToHsv(hexToRgb(swatchHex));
    h = hh;
    s = ss;
    v = vv;
  }

  function toggle() {
    if (!expanded) syncFromValue();
    expanded = !expanded;
  }

  function updateSquare(e: PointerEvent) {
    if (!squareEl) return;
    const r = squareEl.getBoundingClientRect();
    s = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    v = 1 - Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    onInput(currentHex);
  }

  function updateHue(e: PointerEvent) {
    if (!hueEl) return;
    const r = hueEl.getBoundingClientRect();
    h = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * 360;
    onInput(currentHex);
  }

  function squareDown(e: PointerEvent) {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging = 'square';
    updateSquare(e);
  }

  function hueDown(e: PointerEvent) {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging = 'hue';
    updateHue(e);
  }

  function pointerMove(e: PointerEvent) {
    if (dragging === 'square') updateSquare(e);
    else if (dragging === 'hue') updateHue(e);
  }

  function pointerUp() {
    dragging = null;
  }

  function rgbField(i: 0 | 1 | 2, raw: string) {
    const n = Math.min(255, Math.max(0, parseInt(raw, 10) || 0));
    const next: [number, number, number] = [...rgb];
    next[i] = n;
    const [hh, ss, vv] = rgbToHsv(next);
    h = hh;
    s = ss;
    v = vv;
    onInput(currentHex);
  }

  async function pickEyedropper() {
    try {
      // @ts-ignore — EyeDropper isn't in lib.dom.d.ts yet
      const result = await new window.EyeDropper().open();
      const hex = safeHex(result.sRGBHex);
      if (!hex) return;
      const [hh, ss, vv] = rgbToHsv(hexToRgb(hex));
      h = hh;
      s = ss;
      v = vv;
      onInput(currentHex);
    } catch {
      // user cancelled the pick — no-op
    }
  }
</script>

<svelte:window
  onpointermove={dragging ? pointerMove : undefined}
  onpointerup={dragging ? pointerUp : undefined}
/>

<button
  class="swatch"
  type="button"
  style="background:{swatchHex}"
  aria-label="{label} color, {swatchHex}"
  aria-expanded={expanded}
  {title}
  onclick={toggle}
></button>

{#if expanded}
  <div class="picker">
    <div
      class="square"
      bind:this={squareEl}
      style="--hue:{h}"
      role="slider"
      tabindex="0"
      aria-label="{label} saturation and brightness"
      aria-valuenow={Math.round(v * 100)}
      onpointerdown={squareDown}
    >
      <div
        class="square__thumb"
        style="left:{s * 100}%; top:{(1 - v) * 100}%"
      ></div>
    </div>

    <div
      class="hue"
      bind:this={hueEl}
      role="slider"
      tabindex="0"
      aria-label="{label} hue"
      aria-valuenow={Math.round(h)}
      aria-valuemax={360}
      onpointerdown={hueDown}
    >
      <div class="hue__thumb" style="left:{(h / 360) * 100}%"></div>
    </div>

    <div class="picker__row">
      {#if eyedropperSupported}
        <button
          class="eyedropper"
          type="button"
          title="Pick a color from the screen"
          aria-label="Pick a color from the screen"
          onclick={pickEyedropper}
        >
          <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
            <path
              d="M18.5 2.5a2.6 2.6 0 0 1 3 3l-1.9 1.9-3-3zM19 8.1l-3-3-9.6 9.6-1.2 4.2 4.2-1.2z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.4"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      {/if}
      <div class="rgb-fields">
        {#each ['R', 'G', 'B'] as ch, i (ch)}
          <label class="rgb-field">
            <input
              type="number"
              min="0"
              max="255"
              value={Math.round(rgb[i])}
              onchange={(e) => rgbField(i as 0 | 1 | 2, e.currentTarget.value)}
            />
            <span>{ch}</span>
          </label>
        {/each}
      </div>
    </div>
  </div>
{/if}

<style>
  .swatch {
    width: 28px;
    height: 28px;
    border-radius: 9999px;
    border: 1px solid var(--color-border);
    padding: 0;
    cursor: pointer;
  }
  .swatch:hover {
    border-color: var(--color-text);
  }
  .picker {
    flex: 1 0 100%;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px;
    margin-top: 4px;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .square {
    position: relative;
    width: 100%;
    height: 130px;
    touch-action: none;
    cursor: crosshair;
    background:
      linear-gradient(to top, #000, transparent),
      linear-gradient(to right, #fff, transparent),
      hsl(calc(var(--hue) * 1deg) 100% 50%);
  }
  .square__thumb {
    position: absolute;
    width: 12px;
    height: 12px;
    border-radius: 9999px;
    border: 2px solid #fff;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.6);
    transform: translate(-50%, -50%);
    pointer-events: none;
  }
  .hue {
    position: relative;
    width: 100%;
    height: 14px;
    touch-action: none;
    cursor: pointer;
    background: linear-gradient(
      to right,
      hsl(0 100% 50%),
      hsl(60 100% 50%),
      hsl(120 100% 50%),
      hsl(180 100% 50%),
      hsl(240 100% 50%),
      hsl(300 100% 50%),
      hsl(360 100% 50%)
    );
  }
  .hue__thumb {
    position: absolute;
    top: 50%;
    width: 14px;
    height: 14px;
    border-radius: 9999px;
    border: 2px solid #fff;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.6);
    transform: translate(-50%, -50%);
    pointer-events: none;
  }
  .picker__row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .eyedropper {
    flex: 0 0 auto;
    width: 26px;
    height: 26px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    cursor: pointer;
  }
  .eyedropper:hover {
    border-color: var(--color-text);
  }
  .rgb-fields {
    display: flex;
    gap: 6px;
  }
  .rgb-field {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    font-size: 0.6rem;
    color: var(--color-muted);
    text-transform: uppercase;
  }
  .rgb-field input {
    width: 44px;
    text-align: center;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 2px 0;
  }
</style>
