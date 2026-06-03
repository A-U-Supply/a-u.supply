<script lang="ts">
  interface Props {
    masterVolume: number;
    compressorThreshold: number;
    compressorRatio: number;
    onVolumeChange: (v: number) => void;
    onThresholdChange: (v: number) => void;
    onRatioChange: (v: number) => void;
    onShare: () => void;
    shareSuccess: boolean;
  }

  let {
    masterVolume,
    compressorThreshold,
    compressorRatio,
    onVolumeChange,
    onThresholdChange,
    onRatioChange,
    onShare,
    shareSuccess,
  }: Props = $props();

  let compOpen = $state(false);
</script>

<div class="master">
  <span class="master-label">MASTER</span>

  <label class="master-control">
    <span>VOL</span>
    <input
      type="range"
      min="0"
      max="1"
      step="0.01"
      value={masterVolume}
      oninput={(e) =>
        onVolumeChange(parseFloat((e.target as HTMLInputElement).value))}
    />
  </label>

  <button
    class="brutalist-control comp-btn"
    aria-pressed={compOpen}
    onclick={() => (compOpen = !compOpen)}
  >
    COMP {compOpen ? '▴' : '▾'}
  </button>

  {#if compOpen}
    <label class="master-control">
      <span>THR</span>
      <input
        type="range"
        min="-60"
        max="0"
        step="1"
        value={compressorThreshold}
        oninput={(e) =>
          onThresholdChange(parseFloat((e.target as HTMLInputElement).value))}
      />
      <span class="val">{compressorThreshold}dB</span>
    </label>
    <label class="master-control">
      <span>RATIO</span>
      <input
        type="range"
        min="1"
        max="20"
        step="0.5"
        value={compressorRatio}
        oninput={(e) =>
          onRatioChange(parseFloat((e.target as HTMLInputElement).value))}
      />
      <span class="val">{compressorRatio}:1</span>
    </label>
  {/if}

  <button class="brutalist-control share-btn" onclick={onShare}>
    {shareSuccess ? '✓ COPIED' : '🔗 SHARE'}
  </button>
</div>

<style>
  .master {
    display: flex;
    align-items: center;
    gap: 10px;
    border-top: 1px solid var(--lit-border);
    padding: 12px 0 6px;
    flex-wrap: wrap;
    overflow: hidden;
  }

  .master-label {
    font-size: 0.7rem;
    color: var(--lit-text-dim);
    letter-spacing: 0.1em;
    flex-shrink: 0;
  }

  .master-control {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 0.65rem;
    color: var(--lit-text-dim);
    flex: 1;
    min-width: 120px;
  }

  .master-control span {
    flex-shrink: 0;
    width: 2.5rem;
  }

  .master-control input[type='range'] {
    flex: 1;
    min-width: 0;
  }

  .val {
    width: 3rem;
    text-align: right;
    color: var(--lit-text-faint);
  }

  .comp-btn,
  .share-btn {
    font-size: 0.65rem;
    padding: 3px 8px;
    cursor: pointer;
    flex-shrink: 0;
  }

  .share-btn {
    margin-left: auto;
  }
</style>
