<script lang="ts">
  interface Props {
    submode: 'normal' | 'euclid' | 'pool' | 'fx' | 'env';
    voiceIndex: number;
    voiceCount: number;
  }

  let { submode, voiceIndex, voiceCount }: Props = $props();

  const MODE = $derived(
    submode === 'normal'
      ? 'NORMAL'
      : submode === 'euclid'
        ? 'EUCLIDEAN'
        : submode === 'pool'
          ? 'POOL'
          : submode === 'fx'
            ? 'FX'
            : 'ENV',
  );
</script>

<div class="vimode-hud">
  <div class="vimode-label">VI — {MODE}</div>
  <div class="vimode-voice">voice {voiceIndex + 1}/{voiceCount}</div>
  <div class="vimode-keys">
    {#if submode === 'normal'}
      <div class="vimode-row"><kbd>Space</kbd><kbd>P</kbd> play/stop</div>
      <div class="vimode-row"><kbd>j</kbd><kbd>k</kbd> voice</div>
      <div class="vimode-row"><kbd>h</kbd><kbd>l</kbd> step</div>
      <div class="vimode-row"><kbd>i</kbd> toggle <kbd>x</kbd> clear</div>
      <div class="vimode-row"><kbd>%</kbd> prob</div>
      <div class="vimode-row"><kbd>c</kbd><kbd>C</kbd> cadence</div>
      <div class="vimode-row"><kbd>y</kbd> pick mode</div>
      <div class="vimode-row"><kbd>-</kbd><kbd>=</kbd> vol</div>
      <div class="vimode-row"><kbd>_</kbd><kbd>+</kbd> pitch</div>
      <div class="vimode-row"><kbd>m</kbd> mute <kbd>z</kbd> solo</div>
      <div class="vimode-row"><kbd>r</kbd> re-roll</div>
      <div class="vimode-row"><kbd>o</kbd><kbd>O</kbd> add voice</div>
      <div class="vimode-row"><kbd>dd</kbd> delete</div>
      <div class="vimode-row"><kbd>1</kbd>-<kbd>9</kbd> jump</div>
      <div class="vimode-row"><kbd>w</kbd> pool <kbd>f</kbd> fx</div>
      <div class="vimode-row"><kbd>v</kbd> env</div>
      <div class="vimode-row"><kbd>e</kbd> euclid <kbd>p</kbd> pool</div>
      <div class="vimode-row"><kbd>[</kbd><kbd>]</kbd> bpm</div>
      <div class="vimode-row"><kbd>u</kbd> undo <kbd>^R</kbd> redo</div>
    {:else if submode === 'euclid'}
      <div class="vimode-row"><kbd>Space</kbd><kbd>P</kbd> play/stop</div>
      <div class="vimode-row"><kbd>k</kbd><kbd>j</kbd> pulses</div>
      <div class="vimode-row"><kbd>l</kbd><kbd>h</kbd> length</div>
      <div class="vimode-row"><kbd>.</kbd><kbd>,</kbd> offset</div>
      <div class="vimode-row"><kbd>Esc</kbd> back</div>
    {:else if submode === 'pool'}
      <div class="vimode-row"><kbd>Space</kbd><kbd>P</kbd> play/stop</div>
      <div class="vimode-row"><kbd>j</kbd><kbd>k</kbd> entry</div>
      <div class="vimode-row"><kbd>l</kbd> lock</div>
      <div class="vimode-row"><kbd>Enter</kbd> preview</div>
      <div class="vimode-row"><kbd>x</kbd> remove</div>
      <div class="vimode-row"><kbd>/</kbd> search</div>
      <div class="vimode-row"><kbd>r</kbd> +4 more</div>
      <div class="vimode-row"><kbd>Esc</kbd> back</div>
    {/if}
    {#if submode === 'fx'}
      <div class="vimode-row"><kbd>Space</kbd><kbd>P</kbd> play/stop</div>
      <div class="vimode-row"><kbd>j</kbd><kbd>k</kbd> delay time</div>
      <div class="vimode-row"><kbd>h</kbd><kbd>l</kbd> feedback</div>
      <div class="vimode-row"><kbd>-</kbd><kbd>=</kbd> delay wet</div>
      <div class="vimode-row"><kbd>_</kbd><kbd>+</kbd> reverb wet</div>
      <div class="vimode-row"><kbd>J</kbd><kbd>K</kbd> filter freq</div>
      <div class="vimode-row"><kbd>H</kbd><kbd>L</kbd> filter Q</div>
      <div class="vimode-row"><kbd>c</kbd> filter type</div>
      <div class="vimode-row"><kbd>Esc</kbd> back</div>
    {/if}
    {#if submode === 'env'}
      <div class="vimode-row"><kbd>Space</kbd><kbd>P</kbd> play/stop</div>
      <div class="vimode-row"><kbd>j</kbd><kbd>k</kbd> attack</div>
      <div class="vimode-row"><kbd>l</kbd><kbd>h</kbd> release</div>
      <div class="vimode-row"><kbd>c</kbd> atk curve</div>
      <div class="vimode-row"><kbd>C</kbd> rel curve</div>
      <div class="vimode-row"><kbd>Esc</kbd> back</div>
    {/if}
  </div>
</div>

<style>
  .vimode-hud {
    position: fixed;
    bottom: 16px;
    right: 16px;
    z-index: 500;
    background: #0c1012;
    border: 1px solid var(--lit-accent, #e6a817);
    padding: 8px 10px;
    min-width: 150px;
    font-family: var(--lit-font);
    font-size: 0.6rem;
    color: var(--lit-text-dim);
    user-select: none;
    opacity: 0.92;
  }

  .vimode-label {
    font-size: 0.65rem;
    font-weight: 700;
    color: var(--lit-accent, #e6a817);
    letter-spacing: 0.08em;
    margin-bottom: 2px;
  }

  .vimode-voice {
    font-size: 0.55rem;
    color: var(--lit-text-faint);
    margin-bottom: 6px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--lit-border);
  }

  .vimode-keys {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .vimode-row {
    display: flex;
    align-items: center;
    gap: 3px;
    white-space: nowrap;
  }

  .vimode-row kbd {
    display: inline-block;
    background: var(--lit-cell);
    color: var(--lit-text);
    border: 1px solid var(--lit-border);
    padding: 0 3px;
    font-size: 0.55rem;
    font-family: var(--lit-font);
    min-width: 12px;
    text-align: center;
  }

  .vimode-row kbd:last-of-type {
    margin-right: 4px;
  }
</style>
