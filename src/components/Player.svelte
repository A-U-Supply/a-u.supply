<script>
  import { onMount, onDestroy } from 'svelte';

  let queue = $state([]);
  let currentIndex = $state(-1);
  let shuffleOn = $state(false);
  let shuffledIndices = $state([]);

  let currentTime = $state(0);
  let duration = $state(0);
  let paused = $state(true);
  let volume = $state(0.8);
  let visible = $state(false);
  let pipOpen = $state(true);

  let mediaEl = $state(undefined);

  let currentTrack = $derived(currentIndex >= 0 && currentIndex < queue.length ? queue[currentIndex] : null);
  let isVideo = $derived(currentTrack?.media_type === 'video');

  function fmt(secs) {
    if (!secs || !isFinite(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  function buildShuffledIndices() {
    const remaining = [];
    for (let i = 0; i < queue.length; i++) {
      if (i !== currentIndex) remaining.push(i);
    }
    for (let i = remaining.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [remaining[i], remaining[j]] = [remaining[j], remaining[i]];
    }
    shuffledIndices = remaining;
  }

  function loadTrack(idx) {
    if (idx < 0 || idx >= queue.length) return;
    currentIndex = idx;
    pipOpen = true;
    requestAnimationFrame(() => {
      if (mediaEl) {
        mediaEl.load();
        mediaEl.play().catch(() => {});
      }
    });
  }

  function togglePlay() {
    if (!mediaEl || !currentTrack) return;
    if (paused) {
      mediaEl.play().catch(() => {});
    } else {
      mediaEl.pause();
    }
  }

  function prev() {
    if (queue.length === 0) return;
    if (currentTime > 3) {
      currentTime = 0;
      return;
    }
    const newIdx = currentIndex - 1;
    if (newIdx >= 0) loadTrack(newIdx);
  }

  function next() {
    if (queue.length === 0) return;
    if (shuffleOn) {
      if (shuffledIndices.length === 0) buildShuffledIndices();
      if (shuffledIndices.length > 0) {
        const nextIdx = shuffledIndices.shift();
        if (nextIdx !== undefined) loadTrack(nextIdx);
      }
    } else {
      const newIdx = currentIndex + 1;
      if (newIdx < queue.length) loadTrack(newIdx);
    }
  }

  function toggleShuffle() {
    shuffleOn = !shuffleOn;
    if (shuffleOn) buildShuffledIndices();
  }

  function onSeek(e) {
    currentTime = parseFloat(e.target.value);
  }

  function onVolume(e) {
    volume = parseFloat(e.target.value);
  }

  function onQueue(e) {
    const { tracks, startIndex } = e.detail;
    queue = tracks;
    visible = true;
    if (shuffleOn) buildShuffledIndices();
    loadTrack(startIndex ?? 0);
  }

  let handler = null;

  $effect(() => {
    if (visible) {
      document.body.classList.add('player-active');
    } else {
      document.body.classList.remove('player-active');
    }
  });

  onMount(() => {
    handler = (e) => onQueue(e);
    document.addEventListener('player:queue', handler);
  });

  onDestroy(() => {
    if (handler) document.removeEventListener('player:queue', handler);
    document.body.classList.remove('player-active');
  });
</script>

{#if visible}

{#if isVideo && pipOpen}
<div class="player__pip">
  <button class="player__pip-close" onclick={() => pipOpen = false} title="Close video">&times;</button>
  <!-- svelte-ignore a11y_media_has_caption -->
  <video
    bind:this={mediaEl}
    bind:currentTime
    bind:duration
    bind:paused
    bind:volume
    onended={next}
    src={currentTrack?.stream_url}
    poster={currentTrack?.cover_url}
    preload="metadata"
  ></video>
</div>
{/if}

<div class="player">
  {#if !isVideo || !pipOpen}
    <audio
      bind:this={mediaEl}
      bind:currentTime
      bind:duration
      bind:paused
      bind:volume
      onended={next}
      src={currentTrack?.stream_url}
      preload="metadata"
    ></audio>
  {/if}

  <div class="player__inner">
    <div class="player__info">
      {#if isVideo && !pipOpen}
        <button class="player__pip-reopen" onclick={() => pipOpen = true} title="Show video">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <rect x="1" y="2" width="14" height="11" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/>
            <rect x="8" y="7" width="6" height="5" rx="0.5"/>
          </svg>
        </button>
      {/if}
      {#if currentTrack?.cover_url}
        <img
          class="player__cover"
          src={currentTrack.cover_url}
          alt="{currentTrack.release_title} cover"
          width="48"
          height="48"
        />
      {/if}
      <div class="player__meta">
        <div class="player__title">{currentTrack?.title ?? ''}</div>
        <div class="player__sub">
          {currentTrack?.release_title ?? ''}
          {#if currentTrack?.entity_name}&mdash; {currentTrack.entity_name}{/if}
        </div>
      </div>
    </div>

    <div class="player__controls">
      <button class="player__btn {shuffleOn ? 'player__btn--active' : ''}" onclick={toggleShuffle} title="Shuffle">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path d="M11 2l3 3-3 3M11 8l3 3-3 3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="square"/>
          <path d="M1 5h6l5 6h2M1 11h6l2-2.5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="square"/>
        </svg>
      </button>
      <button class="player__btn" onclick={prev} title="Previous">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <rect x="1" y="2" width="2" height="12"/>
          <polygon points="14,2 14,14 4,8"/>
        </svg>
      </button>
      <button class="player__btn player__btn--play" onclick={togglePlay} title={paused ? 'Play' : 'Pause'}>
        {#if paused}
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
            <polygon points="4,2 18,10 4,18"/>
          </svg>
        {:else}
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
            <rect x="3" y="2" width="5" height="16"/>
            <rect x="12" y="2" width="5" height="16"/>
          </svg>
        {/if}
      </button>
      <button class="player__btn" onclick={next} title="Next">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <polygon points="2,2 2,14 12,8"/>
          <rect x="13" y="2" width="2" height="12"/>
        </svg>
      </button>
    </div>

    <div class="player__scrubber">
      <span class="player__time">{fmt(currentTime)}</span>
      <input
        class="player__range player__range--seek"
        type="range"
        min="0"
        max={duration || 0}
        step="0.1"
        value={currentTime}
        oninput={onSeek}
      />
      <span class="player__time">{fmt(duration)}</span>
    </div>

    <div class="player__volume">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <polygon points="1,6 1,10 4,10 8,14 8,2 4,6"/>
        {#if volume > 0}
          <path d="M10 4.5c1.5 1.5 1.5 5.5 0 7" stroke="currentColor" stroke-width="1.5" fill="none"/>
        {/if}
        {#if volume > 0.5}
          <path d="M12 2.5c2.5 2.5 2.5 8.5 0 11" stroke="currentColor" stroke-width="1.5" fill="none"/>
        {/if}
      </svg>
      <input
        class="player__range player__range--vol"
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={volume}
        oninput={onVolume}
      />
    </div>
  </div>
</div>
{/if}

<style>
  .player {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 9999;
    background: #1a1a1a;
    border-top: 1px solid #333;
    color: #e0e0e0;
    font-family: 'Courier New', Courier, monospace;
    font-size: 0.8125rem;
    padding: 0.5rem 1rem;
  }

  .player__inner {
    max-width: 1440px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    gap: 1rem;
  }

  .player__info {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    min-width: 0;
    flex: 0 1 260px;
  }

  .player__cover {
    width: 48px;
    height: 48px;
    object-fit: cover;
    flex-shrink: 0;
    border: 1px solid #333;
  }

  .player__meta {
    min-width: 0;
    overflow: hidden;
  }

  .player__title {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: #fff;
    font-weight: bold;
  }

  .player__sub {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: #888;
    font-size: 0.75rem;
  }

  .player__controls {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    flex-shrink: 0;
  }

  .player__btn {
    background: none;
    border: 1px solid transparent;
    color: #ccc;
    cursor: pointer;
    padding: 0.25rem;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: color 0.15s;
  }

  .player__btn:hover { color: #fff; }
  .player__btn--active { color: #b8860b; }

  .player__btn--play {
    border: 1px solid #555;
    padding: 0.375rem;
  }

  .player__btn--play:hover { border-color: #b8860b; }

  .player__scrubber {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex: 1 1 auto;
    min-width: 0;
  }

  .player__time {
    font-size: 0.6875rem;
    color: #888;
    flex-shrink: 0;
    min-width: 2.75rem;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }

  .player__range {
    -webkit-appearance: none;
    appearance: none;
    background: transparent;
    cursor: pointer;
    height: 1rem;
  }

  .player__range::-webkit-slider-runnable-track {
    height: 2px;
    background: #444;
  }

  .player__range::-moz-range-track {
    height: 2px;
    background: #444;
    border: none;
  }

  .player__range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 10px;
    height: 10px;
    background: #b8860b;
    border: none;
    margin-top: -4px;
  }

  .player__range::-moz-range-thumb {
    width: 10px;
    height: 10px;
    background: #b8860b;
    border: none;
    border-radius: 0;
  }

  .player__range--seek { width: 100%; }

  .player__volume {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    flex-shrink: 0;
    color: #888;
  }

  .player__range--vol { width: 80px; }

  /* Video PiP panel */
  .player__pip {
    position: fixed;
    bottom: 72px;
    right: 1rem;
    z-index: 9998;
    width: 320px;
    background: #000;
    border: 1px solid #333;
    box-shadow: 0 4px 24px rgba(0,0,0,0.6);
  }

  .player__pip video {
    display: block;
    width: 100%;
    height: auto;
  }

  .player__pip-close {
    position: absolute;
    top: 4px;
    right: 6px;
    z-index: 1;
    background: rgba(0,0,0,0.6);
    border: none;
    color: #fff;
    font-size: 1.1rem;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1.2;
    opacity: 0;
    transition: opacity 0.15s;
  }

  .player__pip:hover .player__pip-close { opacity: 1; }
  .player__pip-close:hover { background: rgba(0,0,0,0.9); }

  .player__pip-reopen {
    background: none;
    border: 1px solid #555;
    color: #ccc;
    cursor: pointer;
    padding: 0.25rem;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .player__pip-reopen:hover { color: #fff; border-color: #b8860b; }

  @media (max-width: 639px) {
    .player { padding: 0.375rem 0.5rem; }
    .player__inner { flex-wrap: wrap; gap: 0.375rem; }
    .player__info { flex: 1 1 100%; order: 1; }
    .player__controls { order: 2; flex: 0 0 auto; }
    .player__scrubber { order: 3; flex: 1 1 auto; min-width: 0; }
    .player__volume { display: none; }

    .player__pip {
      width: 200px;
      bottom: 96px;
    }
  }
</style>
