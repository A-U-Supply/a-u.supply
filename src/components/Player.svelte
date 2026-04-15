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
  let muted = $state(false);
  let prevVolume = $state(0.8);
  let repeatMode = $state('off');
  let queueOpen = $state(false);

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
    let loaded = false;
    if (shuffleOn) {
      if (shuffledIndices.length === 0) buildShuffledIndices();
      if (shuffledIndices.length > 0) {
        const nextIdx = shuffledIndices.shift();
        if (nextIdx !== undefined) { loadTrack(nextIdx); loaded = true; }
      }
    } else {
      const newIdx = currentIndex + 1;
      if (newIdx < queue.length) { loadTrack(newIdx); loaded = true; }
    }
    if (!loaded && repeatMode === 'all') {
      if (shuffleOn) { buildShuffledIndices(); if (shuffledIndices.length > 0) loadTrack(shuffledIndices.shift()); }
      else loadTrack(0);
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

  function toggleMute() {
    if (muted) {
      volume = prevVolume;
      muted = false;
    } else {
      prevVolume = volume;
      volume = 0;
      muted = true;
    }
  }

  function cycleRepeat() {
    if (repeatMode === 'off') repeatMode = 'all';
    else if (repeatMode === 'all') repeatMode = 'one';
    else repeatMode = 'off';
  }

  function onEnded() {
    if (repeatMode === 'one') {
      if (mediaEl) { mediaEl.currentTime = 0; mediaEl.play().catch(() => {}); }
    } else {
      next();
    }
  }

  function removeTrack(idx) {
    if (idx < 0 || idx >= queue.length) return;
    const wasPlaying = idx === currentIndex;
    queue = queue.filter((_, i) => i !== idx);
    if (queue.length === 0) { currentIndex = -1; visible = false; queueOpen = false; if (mediaEl) mediaEl.pause(); return; }
    if (wasPlaying) {
      const newIdx = idx < queue.length ? idx : 0;
      loadTrack(newIdx);
    } else if (idx < currentIndex) {
      currentIndex--;
    }
    if (shuffleOn) buildShuffledIndices();
  }

  function clearQueue() {
    queue = [];
    currentIndex = -1;
    visible = false;
    queueOpen = false;
    if (mediaEl) mediaEl.pause();
  }

  function toggleQueue() {
    queueOpen = !queueOpen;
  }

  function onAdd(e) {
    const { tracks } = e.detail;
    queue = [...queue, ...tracks];
    if (shuffleOn) buildShuffledIndices();
    if (currentIndex === -1) {
      visible = true;
      loadTrack(queue.length - tracks.length);
    }
  }

  function onQueue(e) {
    const { tracks, startIndex } = e.detail;
    queue = tracks;
    visible = true;
    if (shuffleOn) buildShuffledIndices();
    loadTrack(startIndex ?? 0);
  }

  let handler = null;
  let bookmarked = $state(false);
  let hasBookmarks = $state(false);

  function getBookmarkInfo(track) {
    if (!track) return null;
    // Determine target type: media_item for search items, track for release tracks
    if (track.media_type) return { type: 'media_item', id: String(track.track_id) };
    if (track.release_code) return { type: 'track', id: String(track.track_id) };
    return null;
  }

  async function checkBookmark() {
    const bm = window.__bookmarks;
    if (!bm) { hasBookmarks = false; return; }
    hasBookmarks = true;
    const info = getBookmarkInfo(currentTrack);
    if (!info) { bookmarked = false; return; }
    const set = await bm.check(info.type, [info.id]);
    bookmarked = set.has(info.id);
  }

  async function toggleBookmark() {
    const bm = window.__bookmarks;
    if (!bm) return;
    const info = getBookmarkInfo(currentTrack);
    if (!info) return;
    bookmarked = await bm.toggle(info.type, info.id);
  }

  $effect(() => {
    if (currentTrack) checkBookmark();
  });

  $effect(() => {
    if (volume > 0 && muted) muted = false;
  });

  $effect(() => {
    if (visible) {
      document.body.classList.add('player-active');
    } else {
      document.body.classList.remove('player-active');
    }
  });

  $effect(() => {
    if (!currentTrack || !('mediaSession' in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: currentTrack.title || '',
      artist: currentTrack.entity_name || '',
      album: currentTrack.release_title || '',
      artwork: currentTrack.cover_url
        ? [{ src: currentTrack.cover_url, sizes: '256x256', type: 'image/jpeg' }]
        : [],
    });
  });

  function onKeyDown(e) {
    if (!visible) return;
    const el = document.activeElement;
    const tag = el?.tagName?.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable) return;
    switch (e.key) {
      case ' ':
        e.preventDefault();
        togglePlay();
        break;
      case 'ArrowLeft':
        e.preventDefault();
        prev();
        break;
      case 'ArrowRight':
        e.preventDefault();
        next();
        break;
      case 'm':
      case 'M':
        e.preventDefault();
        toggleMute();
        break;
    }
  }

  let addHandler = null;

  onMount(() => {
    handler = (e) => onQueue(e);
    addHandler = (e) => onAdd(e);
    document.addEventListener('player:queue', handler);
    document.addEventListener('player:add', addHandler);
    document.addEventListener('keydown', onKeyDown);
    if ('mediaSession' in navigator) {
      navigator.mediaSession.setActionHandler('play', () => { if (mediaEl) mediaEl.play(); });
      navigator.mediaSession.setActionHandler('pause', () => { if (mediaEl) mediaEl.pause(); });
      navigator.mediaSession.setActionHandler('previoustrack', prev);
      navigator.mediaSession.setActionHandler('nexttrack', next);
    }
  });

  onDestroy(() => {
    if (handler) document.removeEventListener('player:queue', handler);
    if (addHandler) document.removeEventListener('player:add', addHandler);
    document.removeEventListener('keydown', onKeyDown);
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
    onended={onEnded}
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
      onended={onEnded}
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
      {#if hasBookmarks}
        <button
          class="player__star {bookmarked ? 'bookmarked' : ''}"
          onclick={toggleBookmark}
          title={bookmarked ? 'Remove bookmark' : 'Bookmark'}
        ></button>
      {/if}
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
      <button class="player__btn {repeatMode !== 'off' ? 'player__btn--active' : ''}" onclick={cycleRepeat} title="Repeat: {repeatMode}">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M2 5h10l-2-2M14 11H4l2 2" stroke-linecap="square"/>
          <path d="M2 5v4a2 2 0 002 2M14 11V7a2 2 0 00-2-2"/>
        </svg>
        {#if repeatMode === 'one'}
          <span class="player__repeat-one">1</span>
        {/if}
      </button>
      <button class="player__btn player__btn--mute-mobile" onclick={toggleMute} title={muted ? 'Unmute' : 'Mute'}>
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <polygon points="1,6 1,10 4,10 8,14 8,2 4,6"/>
          {#if muted || volume === 0}
            <line x1="10" y1="5" x2="15" y2="11" stroke="currentColor" stroke-width="1.5"/>
            <line x1="15" y1="5" x2="10" y2="11" stroke="currentColor" stroke-width="1.5"/>
          {/if}
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
      <button class="player__mute-btn" onclick={toggleMute} title={muted ? 'Unmute' : 'Mute'}>
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <polygon points="1,6 1,10 4,10 8,14 8,2 4,6"/>
          {#if muted || volume === 0}
            <line x1="10" y1="5" x2="15" y2="11" stroke="currentColor" stroke-width="1.5"/>
            <line x1="15" y1="5" x2="10" y2="11" stroke="currentColor" stroke-width="1.5"/>
          {:else}
            {#if volume > 0}
              <path d="M10 4.5c1.5 1.5 1.5 5.5 0 7" stroke="currentColor" stroke-width="1.5" fill="none"/>
            {/if}
            {#if volume > 0.5}
              <path d="M12 2.5c2.5 2.5 2.5 8.5 0 11" stroke="currentColor" stroke-width="1.5" fill="none"/>
            {/if}
          {/if}
        </svg>
      </button>
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

    {#if currentTrack?.stream_url}
      <a class="player__btn player__btn--download" href={currentTrack.stream_url} download title="Download">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 1v9M4 7l4 4 4-4" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="square"/>
          <rect x="2" y="13" width="12" height="1.5"/>
        </svg>
      </a>
    {/if}

    <button class="player__btn player__btn--queue {queueOpen ? 'player__btn--active' : ''}" onclick={toggleQueue} title="Queue">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <rect x="1" y="2" width="10" height="1.5"/>
        <rect x="1" y="6" width="10" height="1.5"/>
        <rect x="1" y="10" width="7" height="1.5"/>
        <polygon points="11,9 11,14 15,11.5"/>
      </svg>
    </button>
  </div>
</div>

{#if queueOpen}
<div class="queue-backdrop" onclick={() => queueOpen = false}></div>
<div class="queue-panel">
  <div class="queue-panel__header">
    <span class="queue-panel__title">Queue ({queue.length})</span>
    <button class="queue-panel__clear" onclick={clearQueue}>Clear</button>
    <button class="queue-panel__close" onclick={() => queueOpen = false}>&times;</button>
  </div>
  <div class="queue-panel__list">
    {#each queue as track, i}
      <button
        class="queue-panel__item {i === currentIndex ? 'queue-panel__item--active' : ''}"
        onclick={() => loadTrack(i)}
      >
        <span class="queue-panel__num">{i + 1}</span>
        {#if i === currentIndex && !paused}
          <span class="queue-panel__playing">&#9654;</span>
        {/if}
        <span class="queue-panel__track-title">{track.title}</span>
        <span class="queue-panel__dur">{fmt(track.duration)}</span>
        <span
          class="queue-panel__remove"
          role="button"
          tabindex="0"
          onclick={(e) => { e.stopPropagation(); removeTrack(i); }}
          onkeydown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); removeTrack(i); } }}
        >&times;</span>
      </button>
    {/each}
  </div>
</div>
{/if}
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

  .player__star {
    background: none;
    border: none;
    cursor: pointer;
    padding: 0;
    font-size: 1.1rem;
    line-height: 1;
    color: #555;
    transition: color 0.15s;
    flex-shrink: 0;
  }
  .player__star::before { content: '\2606'; }
  .player__star:hover { color: #b8860b; }
  .player__star.bookmarked { color: #b8860b; }
  .player__star.bookmarked::before { content: '\2605'; }

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

  .player__mute-btn {
    background: none;
    border: none;
    color: #888;
    cursor: pointer;
    padding: 0;
    display: flex;
    align-items: center;
  }
  .player__mute-btn:hover { color: #fff; }

  .player__btn--mute-mobile {
    display: none;
  }

  .player__btn--download {
    text-decoration: none;
    flex-shrink: 0;
  }

  .player__btn--queue {
    flex-shrink: 0;
  }

  .player__repeat-one {
    position: absolute;
    font-size: 0.5rem;
    font-weight: bold;
    bottom: 1px;
    right: 1px;
    line-height: 1;
  }

  .player__btn:has(.player__repeat-one) {
    position: relative;
  }

  /* Queue panel */
  .queue-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 9997;
  }

  .queue-panel {
    position: fixed;
    bottom: 72px;
    left: 0;
    right: 0;
    z-index: 9998;
    background: #1a1a1a;
    border-top: 1px solid #333;
    max-height: 50vh;
    display: flex;
    flex-direction: column;
    font-family: 'Courier New', Courier, monospace;
    color: #e0e0e0;
    animation: queue-slide-up 0.2s ease-out;
  }

  @keyframes queue-slide-up {
    from { transform: translateY(100%); }
    to { transform: translateY(0); }
  }

  .queue-panel__header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 1rem;
    border-bottom: 1px solid #333;
    flex-shrink: 0;
  }

  .queue-panel__title {
    font-size: 0.8125rem;
    font-weight: bold;
    color: #fff;
    margin-right: auto;
  }

  .queue-panel__clear {
    background: none;
    border: 1px solid #555;
    color: #888;
    font-family: inherit;
    font-size: 0.6875rem;
    padding: 2px 8px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
  }
  .queue-panel__clear:hover { color: #fff; border-color: #b8860b; }

  .queue-panel__close {
    background: none;
    border: none;
    color: #888;
    font-size: 1.2rem;
    cursor: pointer;
    padding: 0 4px;
  }
  .queue-panel__close:hover { color: #fff; }

  .queue-panel__list {
    overflow-y: auto;
    flex: 1;
  }

  .queue-panel__item {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.375rem 1rem;
    border: none;
    background: none;
    color: #ccc;
    width: 100%;
    text-align: left;
    font-family: inherit;
    font-size: 0.8125rem;
    cursor: pointer;
    border-bottom: 1px solid #222;
  }
  .queue-panel__item:hover { background: #222; }
  .queue-panel__item--active { color: #b8860b; }

  .queue-panel__num {
    width: 2ch;
    text-align: right;
    flex-shrink: 0;
    color: #555;
    font-size: 0.75rem;
  }
  .queue-panel__item--active .queue-panel__num { color: #b8860b; }

  .queue-panel__playing {
    flex-shrink: 0;
    font-size: 0.6rem;
  }

  .queue-panel__track-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .queue-panel__dur {
    flex-shrink: 0;
    color: #555;
    font-size: 0.75rem;
    font-variant-numeric: tabular-nums;
  }

  .queue-panel__remove {
    background: none;
    border: none;
    color: #555;
    cursor: pointer;
    font-size: 0.9rem;
    padding: 0 4px;
    flex-shrink: 0;
  }
  .queue-panel__remove:hover { color: #c00; }

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
    .player__btn--mute-mobile { display: flex; }
    .player__btn--queue { order: 4; }

    .player__pip {
      width: 200px;
      bottom: 96px;
    }

    .queue-panel {
      max-height: calc(100vh - 96px);
      bottom: 96px;
    }
  }
</style>
