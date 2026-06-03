<script lang="ts">
  import type { FxParams } from '../../lib/litany/state.ts';

  interface Props {
    fx: FxParams;
    onChange: (fx: FxParams) => void;
    onBeforeDrag: () => void;
  }

  let { fx, onChange, onBeforeDrag }: Props = $props();

  function update(key: keyof FxParams, value: number | string) {
    onChange({ ...fx, [key]: value });
  }
</script>

<div class="fx-panel">
  <div class="fx-row">
    <span class="fx-label">DELAY</span>
    <label class="fx-field">
      <span>TIME</span>
      <input
        type="range"
        min="0.01"
        max="1"
        step="0.01"
        value={fx.delayTime}
        onpointerdown={onBeforeDrag}
        oninput={(e) =>
          update('delayTime', parseFloat((e.target as HTMLInputElement).value))}
      />
    </label>
    <label class="fx-field">
      <span>FB</span>
      <input
        type="range"
        min="0"
        max="0.95"
        step="0.01"
        value={fx.delayFeedback}
        onpointerdown={onBeforeDrag}
        oninput={(e) =>
          update(
            'delayFeedback',
            parseFloat((e.target as HTMLInputElement).value),
          )}
      />
    </label>
    <label class="fx-field">
      <span>WET</span>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={fx.delayWet}
        onpointerdown={onBeforeDrag}
        oninput={(e) =>
          update('delayWet', parseFloat((e.target as HTMLInputElement).value))}
      />
    </label>
  </div>

  <div class="fx-row">
    <span class="fx-label">REVERB</span>
    <label class="fx-field fx-field--wide">
      <span>WET</span>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={fx.reverbWet}
        onpointerdown={onBeforeDrag}
        oninput={(e) =>
          update('reverbWet', parseFloat((e.target as HTMLInputElement).value))}
      />
    </label>
  </div>

  <div class="fx-row">
    <span class="fx-label">FILTER</span>
    <label class="fx-field">
      <span>TYPE</span>
      <select
        class="brutalist-control"
        value={fx.filterType}
        onchange={(e) =>
          update('filterType', (e.target as HTMLSelectElement).value)}
      >
        <option value="lowpass">LP</option>
        <option value="highpass">HP</option>
        <option value="bandpass">BP</option>
        <option value="notch">NOTCH</option>
      </select>
    </label>
    <label class="fx-field">
      <span>FREQ</span>
      <input
        type="range"
        min="80"
        max="20000"
        step="10"
        value={fx.filterFreq}
        onpointerdown={onBeforeDrag}
        oninput={(e) =>
          update(
            'filterFreq',
            parseFloat((e.target as HTMLInputElement).value),
          )}
      />
    </label>
    <label class="fx-field">
      <span>Q</span>
      <input
        type="range"
        min="0.1"
        max="20"
        step="0.1"
        value={fx.filterQ}
        onpointerdown={onBeforeDrag}
        oninput={(e) =>
          update('filterQ', parseFloat((e.target as HTMLInputElement).value))}
      />
    </label>
  </div>
</div>

<style>
  .fx-panel {
    border-top: 1px solid var(--lit-border);
    padding: 8px 0 4px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .fx-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .fx-label {
    font-size: 0.65rem;
    color: var(--lit-text-dim);
    width: 3rem;
    flex-shrink: 0;
  }

  .fx-field {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 0.65rem;
    color: var(--lit-text-dim);
    flex: 1;
    min-width: 80px;
  }

  .fx-field--wide {
    flex: 2;
  }

  .fx-field span {
    flex-shrink: 0;
    width: 2.5rem;
  }

  .fx-field input[type='range'] {
    flex: 1;
    accent-color: var(--lit-accent);
    cursor: pointer;
  }

  .fx-field select {
    padding: 1px 3px;
    font-size: 0.65rem;
    background: var(--lit-panel);
    color: var(--lit-text);
  }
</style>
