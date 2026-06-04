<script lang="ts">
  interface Props {
    playing: boolean;
    poolsLoading: boolean;
    bpm: number;
    undoCount: number;
    redoCount: number;
    layout: 'grid' | 'rows';
    onPlay: () => void;
    onStop: () => void;
    onUndo: () => void;
    onRedo: () => void;
    onLayoutToggle: () => void;
    onBpmChange: (bpm: number) => void;
    onRandomizeSteps: () => void;
    onRandomizeQuery: () => void;
    onRandomizeBpm: () => void;
    onRandomizeVoices: () => void;
    onRandomizeAll: () => void;
    onChaos: () => void;
    onAddVoice: () => void;
  }

  let {
    playing,
    poolsLoading,
    bpm,
    undoCount,
    redoCount,
    layout,
    onPlay,
    onStop,
    onUndo,
    onRedo,
    onLayoutToggle,
    onBpmChange,
    onRandomizeSteps,
    onRandomizeQuery,
    onRandomizeBpm,
    onRandomizeVoices,
    onRandomizeAll,
    onChaos,
    onAddVoice,
  }: Props = $props();

  function handleKey(e: KeyboardEvent) {
    const tag = (e.target as HTMLElement).tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if ((e.key === 'p' || e.key === ' ') && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      playing ? onStop() : onPlay();
    }
    if (e.key === 'z' && (e.metaKey || e.ctrlKey) && !e.shiftKey) {
      e.preventDefault();
      onUndo();
    }
    if (e.key === 'z' && (e.metaKey || e.ctrlKey) && e.shiftKey) {
      e.preventDefault();
      onRedo();
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      onBpmChange(Math.min(bpm + 1, 240));
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      onBpmChange(Math.max(bpm - 1, 40));
    }
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      onBpmChange(Math.min(bpm + 5, 240));
    }
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      onBpmChange(Math.max(bpm - 5, 40));
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
      class:transport-btn--loading={poolsLoading && !playing}
      disabled={poolsLoading && !playing}
      onclick={playing ? onStop : onPlay}
    >
      {playing ? '◼ STOP' : poolsLoading ? '… LOADING' : '▶ PLAY'}
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

  <div class="history-group">
    <button
      class="brutalist-control hist-btn"
      disabled={undoCount === 0}
      onclick={onUndo}
      title="Undo (⌘Z)">↩{undoCount > 0 ? ` ${undoCount}` : ''}</button
    >
    <button
      class="brutalist-control hist-btn"
      disabled={redoCount === 0}
      onclick={onRedo}
      title="Redo (⌘⇧Z)">↪{redoCount > 0 ? ` ${redoCount}` : ''}</button
    >
  </div>

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
    <button class="brutalist-control rnd-btn rnd-btn--chaos" onclick={onChaos}
      >💥</button
    >
  </div>

  <div class="right-group">
    <button
      class="brutalist-control layout-btn"
      aria-pressed={layout === 'rows'}
      onclick={onLayoutToggle}
      title="Toggle layout">{layout === 'grid' ? '▦ GRID' : '≡ ROWS'}</button
    >
    <button class="brutalist-control add-btn" onclick={onAddVoice}
      >+ VOICE</button
    >
  </div>
</div>

<style>
  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 0 12px;
    border-bottom: 1px solid var(--lit-border);
    flex-wrap: wrap;
  }

  .app-title {
    font-size: 0.95rem;
    font-weight: 700;
    letter-spacing: 0.15em;
    color: var(--lit-text);
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
    background: var(--lit-playing-bg) !important;
    color: var(--lit-accent) !important;
  }

  .transport-btn--loading {
    color: var(--lit-text-faint) !important;
    cursor: wait !important;
  }

  .bpm-label {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 0.7rem;
    color: var(--lit-text-dim);
    flex-shrink: 0;
  }

  .bpm-input {
    width: 4rem;
    padding: 3px 5px;
    font-size: 0.8rem;
    background: var(--lit-cell);
    color: var(--lit-text);
    text-align: center;
  }

  .history-group {
    display: flex;
    gap: 4px;
    flex-shrink: 0;
  }

  .hist-btn {
    font-size: 0.7rem;
    padding: 3px 8px;
    cursor: pointer;
    min-width: 2.5rem;
  }

  .hist-btn:disabled {
    color: var(--lit-text-faint) !important;
    cursor: default;
    border-color: var(--lit-border) !important;
    box-shadow: none !important;
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
    background: var(--lit-solo-bg) !important;
    border-color: var(--lit-accent) !important;
    color: var(--lit-accent) !important;
  }

  .rnd-btn--chaos {
    background: transparent !important;
    border-color: var(--lit-red) !important;
    color: var(--lit-red) !important;
    font-size: 0.8rem;
    padding: 2px 6px;
  }

  .rnd-btn--chaos:hover {
    background: var(--lit-red) !important;
    color: var(--lit-bg) !important;
  }

  .right-group {
    display: flex;
    gap: 4px;
    margin-left: auto;
    flex-shrink: 0;
  }

  .layout-btn {
    font-size: 0.65rem;
    padding: 3px 8px;
    cursor: pointer;
  }

  .layout-btn[aria-pressed='true'] {
    background: var(--lit-accent) !important;
    color: var(--lit-bg) !important;
    border-color: var(--lit-accent) !important;
  }

  .add-btn {
    font-size: 0.7rem;
    padding: 3px 10px;
    cursor: pointer;
  }
</style>
