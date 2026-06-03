<script lang="ts">
  import type { EnvelopeParams, PlayStyle } from '../../lib/litany/state.ts';

  interface Props {
    envelope: EnvelopeParams;
    playStyle: PlayStyle;
    onChange: (envelope: EnvelopeParams, playStyle: PlayStyle) => void;
    onPreview: () => void;
  }

  let { envelope, playStyle, onChange, onPreview }: Props = $props();

  let autoPreview = $state(false);

  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  function handleEnvelopeChange(key: keyof EnvelopeParams, value: number) {
    const next = { ...envelope, [key]: value };
    onChange(next, playStyle);
    scheduleAutoPreview();
  }

  function handleStyleChange(value: string) {
    onChange(envelope, value as PlayStyle);
    scheduleAutoPreview();
  }

  function scheduleAutoPreview() {
    if (!autoPreview) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => onPreview(), 120);
  }
</script>

<div class="env-panel">
  <div class="env-row">
    <span class="env-label">ATTACK</span>
    <input
      type="range"
      min="0"
      max="2"
      step="0.01"
      value={envelope.attack}
      oninput={(e) =>
        handleEnvelopeChange(
          'attack',
          parseFloat((e.target as HTMLInputElement).value),
        )}
    />
    <span class="env-val">{envelope.attack.toFixed(2)}s</span>
  </div>

  <div class="env-row">
    <span class="env-label">RELEASE</span>
    <input
      type="range"
      min="0"
      max="3"
      step="0.01"
      value={envelope.release}
      oninput={(e) =>
        handleEnvelopeChange(
          'release',
          parseFloat((e.target as HTMLInputElement).value),
        )}
    />
    <span class="env-val">{envelope.release.toFixed(2)}s</span>
  </div>

  <div class="env-row env-row--controls">
    <label class="env-field">
      <span>STYLE</span>
      <select
        class="brutalist-control"
        value={playStyle}
        onchange={(e) =>
          handleStyleChange((e.target as HTMLSelectElement).value)}
      >
        <option value="one-shot">one-shot</option>
        <option value="cut">cut</option>
        <option value="gate">gate</option>
        <option value="legato">legato</option>
      </select>
    </label>

    <label class="env-auto">
      <input
        type="checkbox"
        checked={autoPreview}
        onchange={(e) => (autoPreview = (e.target as HTMLInputElement).checked)}
      />
      <span>auto</span>
    </label>

    <button class="brutalist-control env-preview-btn" onclick={onPreview}
      >▶</button
    >
  </div>
</div>

<style>
  .env-panel {
    border-top: 1px solid var(--lit-border);
    padding: 8px 0 4px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    overflow: hidden;
  }

  .env-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .env-label {
    font-size: 0.65rem;
    color: var(--lit-text-dim);
    width: 3.5rem;
    flex-shrink: 0;
  }

  .env-row input[type='range'] {
    flex: 1;
    min-width: 0;
  }

  .env-val {
    font-size: 0.6rem;
    color: var(--lit-text-faint);
    width: 2.5rem;
    text-align: right;
    flex-shrink: 0;
  }

  .env-row--controls {
    justify-content: flex-start;
  }

  .env-field {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 0.65rem;
    color: var(--lit-text-dim);
  }

  .env-field span {
    flex-shrink: 0;
    width: 2.5rem;
  }

  .env-field select {
    padding: 1px 3px;
    font-size: 0.65rem;
    background: var(--lit-cell);
    color: var(--lit-text);
  }

  .env-auto {
    display: flex;
    align-items: center;
    gap: 3px;
    font-size: 0.6rem;
    color: var(--lit-text-faint);
    cursor: pointer;
    margin-left: 8px;
  }

  .env-auto input[type='checkbox'] {
    width: 10px;
    height: 10px;
    accent-color: var(--lit-accent);
    cursor: pointer;
    margin: 0;
  }

  .env-preview-btn {
    padding: 2px 8px;
    font-size: 0.65rem;
    margin-left: auto;
    color: var(--lit-green);
    border-color: var(--lit-green);
    background: transparent;
  }

  .env-preview-btn:hover {
    background: var(--lit-green);
    color: var(--lit-bg);
    border-color: var(--lit-green);
  }

  @media (pointer: coarse) {
    .env-auto input[type='checkbox'] {
      width: 16px;
      height: 16px;
    }

    .env-field select {
      min-height: 28px;
    }
  }
</style>
