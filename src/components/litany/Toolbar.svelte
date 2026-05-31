<script lang="ts">
  interface Props {
    playing: boolean;
    bpm: number;
    onPlay: () => void;
    onStop: () => void;
    onBpmChange: (bpm: number) => void;
    onRandomizeSteps: () => void;
    onRandomizeQuery: () => void;
    onRandomizeBpm: () => void;
    onRandomizeVoices: () => void;
    onRandomizeAll: () => void;
    onAddVoice: () => void;
  }

  let {
    playing,
    bpm,
    onPlay,
    onStop,
    onBpmChange,
    onRandomizeSteps,
    onRandomizeQuery,
    onRandomizeBpm,
    onRandomizeVoices,
    onRandomizeAll,
    onAddVoice,
  }: Props = $props();

  function handleKey(e: KeyboardEvent) {
    if (e.key === 'p' && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      playing ? onStop() : onPlay();
    }
  }
</script>

<svelte:window onkeydown={handleKey} />

<div class="toolbar">
  <span class="app-title">LITANY</span>

  <div class="transport">
    <button
      class="brutalist-control transport-btn"
      class:transport-btn--playing={playing}
      onclick={playing ? onStop : onPlay}
    >
      {playing ? '◼ STOP' : '▶ PLAY'}
    </button>
  </div>

  <label class="bpm-label">
    BPM
    <input
      class="brutalist-control bpm-input"
      type="number"
      min="40"
      max="240"
      value={bpm}
      onchange={(e) =>
        onBpmChange(parseInt((e.target as HTMLInputElement).value) || bpm)}
    />
  </label>

  <div class="rnd-group">
    <button class="brutalist-control rnd-btn" onclick={onRandomizeSteps}
      >🎲 STEPS</button
    >
    <button class="brutalist-control rnd-btn" onclick={onRandomizeQuery}
      >🎲 QUERY</button
    >
    <button class="brutalist-control rnd-btn" onclick={onRandomizeBpm}
      >🎲 BPM</button
    >
    <button class="brutalist-control rnd-btn" onclick={onRandomizeVoices}
      >🎲 VOICES</button
    >
    <button
      class="brutalist-control rnd-btn rnd-btn--all"
      onclick={onRandomizeAll}>🎲 ALL</button
    >
  </div>

  <button class="brutalist-control add-btn" onclick={onAddVoice}>+ VOICE</button
  >
</div>

<style>
  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 0 12px;
    border-bottom: 2px solid #333;
    flex-wrap: wrap;
  }

  .app-title {
    font-size: 1rem;
    font-weight: bold;
    letter-spacing: 0.15em;
    color: #ddd;
    flex-shrink: 0;
  }

  .transport {
    flex-shrink: 0;
  }

  .transport-btn {
    padding: 4px 12px;
    font-size: 0.8rem;
    cursor: pointer;
    min-width: 6rem;
  }

  .transport-btn--playing {
    background: #1a1a0a !important;
    color: #b8860b !important;
  }

  .bpm-label {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 0.7rem;
    color: #888;
    flex-shrink: 0;
  }

  .bpm-input {
    width: 4rem;
    padding: 3px 5px;
    font-size: 0.8rem;
    background: #0d0d0d;
    color: #ccc;
    text-align: center;
  }

  .rnd-group {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }

  .rnd-btn {
    font-size: 0.65rem;
    padding: 3px 7px;
    cursor: pointer;
  }

  .rnd-btn--all {
    background: #1a1200 !important;
    border-color: #b8860b !important;
    color: #b8860b !important;
  }

  .add-btn {
    margin-left: auto;
    font-size: 0.7rem;
    padding: 3px 10px;
    cursor: pointer;
  }
</style>
