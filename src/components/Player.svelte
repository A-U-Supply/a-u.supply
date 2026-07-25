<script>
  import { onMount, onDestroy } from 'svelte';
  import {
    fetchAnnotations,
    createAnnotation,
    updateAnnotation,
    toggleResolveAnnotation,
    deleteAnnotation,
    fmtTimestamp,
    parseTimestamp,
    linkifyTimestamps,
    whoLabel,
    sourceLabel,
    excerpt,
  } from './marginalia.ts';

  let queue = $state([]);
  let currentIndex = $state(-1);
  let shuffleOn = $state(false);
  let shuffledIndices = $state([]);

  let currentTime = $state(0);
  let duration = $state(0);
  /* Why a track isn't playing. Previously play() rejections and media errors
     were both swallowed, so a missing file or a blocked autoplay looked
     identical to "the button does nothing". */
  let mediaError = $state(null);
  let paused = $state(true);
  let volume = $state(0.8);
  let visible = $state(false);
  let pipOpen = $state(true);
  let muted = $state(false);
  let prevVolume = $state(0.8);
  let repeatMode = $state('off');
  let queueOpen = $state(false);

  // ── Marginalia (timestamped comments + cue markers) ─────────────────────
  // Only active for media-item tracks (they carry media_type; catalog
  // release tracks don't and skip all of this).
  let panelOpen = $state(false);
  let annotations = $state([]);
  let inherited = $state([]);
  let parentSession = $state(null);
  let showInherited = $state(true);
  let peaks = $state(null);
  let pendingSeek = $state(null);
  let waveEl = $state(undefined);
  let expandedId = $state(null);
  let expandedCardEl = $state(undefined);
  let composerOpen = $state(false);
  let composerText = $state('');
  let composerEl = $state(undefined);
  let replyFor = $state(null);
  let replyText = $state('');
  let editId = $state(null);
  let editText = $state('');
  let editPos = $state('');
  let margError = $state(null);
  let liveMsg = $state('');
  let liveTimer = null;
  // Non-reactive: which track id the annotation state belongs to. Read
  // inside the track-change $effect without becoming a dependency of it.
  let annotatedTrackId = null;

  let mediaEl = $state(undefined);
  let pipEl = $state(undefined);
  let isFullscreen = $state(false);

  let currentTrack = $derived(
    currentIndex >= 0 && currentIndex < queue.length
      ? queue[currentIndex]
      : null,
  );
  let isVideo = $derived(currentTrack?.media_type === 'video');
  let isMediaTrack = $derived(!!currentTrack?.media_type);

  // Own annotations + (optionally) inherited session cues, position-sorted
  // for the waveform markers, the seek-bar ticks and the [ ] jump keys.
  let markers = $derived(
    [
      ...annotations.map((a) => ({ ...a, inh: false })),
      ...(showInherited ? inherited.map((a) => ({ ...a, inh: true })) : []),
    ].sort((a, b) => a.position_seconds - b.position_seconds),
  );

  // Prefetch the next audio track so it's buffered before the user skips to it.
  // Only applies to audio (not video) and only when not in shuffle mode (shuffle
  // picks a random next index, so we can't know which track to prefetch).
  let nextAudioUrl = $derived(
    !shuffleOn &&
      !isVideo &&
      currentIndex >= 0 &&
      currentIndex + 1 < queue.length &&
      queue[currentIndex + 1]?.media_type !== 'video'
      ? queue[currentIndex + 1].stream_url
      : null,
  );

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
    mediaError = null;
    live(`Now playing: ${queue[idx]?.title || ''}`);
    const url = queue[idx]?.stream_url;
    if (mediaEl && url) {
      /* Set src and play in THIS task, not a later frame: iOS Safari only
         honours play() while the user gesture is still active, and a
         requestAnimationFrame callback is too late. Svelte's own src binding
         lands on the same URL a moment later, so this is a no-op for it. */
      mediaEl.src = url;
      mediaEl.load();
      startPlayback();
      return;
    }
    // First queue of the session: the element doesn't exist until `visible`
    // renders it, so wait a frame.
    requestAnimationFrame(() => {
      if (mediaEl) {
        mediaEl.load();
        startPlayback();
      }
    });
  }

  function startPlayback() {
    if (!mediaEl) return;
    mediaEl.play().catch((err) => {
      if (err?.name === 'NotAllowedError') {
        // Autoplay policy — the gesture didn't carry. The track is loaded, so
        // the transport button works.
        mediaError = 'Tap play to start';
      } else if (err?.name !== 'AbortError') {
        mediaError = 'Playback failed';
      }
      if (mediaError) live(mediaError);
    });
  }

  function onMediaError() {
    // The element couldn't fetch/decode the source at all — most often the
    // file is missing on disk behind an otherwise-valid media item.
    mediaError = "Couldn't load this file";
    live(mediaError);
  }

  function onPlaying() {
    mediaError = null;
  }

  // Visually-hidden aria-live announcer. Clear-then-set so identical
  // consecutive messages ("Marker resolved" twice in a row) re-announce.
  function live(msg) {
    liveMsg = '';
    if (liveTimer) clearTimeout(liveTimer);
    liveTimer = setTimeout(() => (liveMsg = msg), 40);
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
        if (nextIdx !== undefined) {
          loadTrack(nextIdx);
          loaded = true;
        }
      }
    } else {
      const newIdx = currentIndex + 1;
      if (newIdx < queue.length) {
        loadTrack(newIdx);
        loaded = true;
      }
    }
    if (!loaded && repeatMode === 'all') {
      if (shuffleOn) {
        buildShuffledIndices();
        if (shuffledIndices.length > 0) loadTrack(shuffledIndices.shift());
      } else loadTrack(0);
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
      if (mediaEl) {
        mediaEl.currentTime = 0;
        mediaEl.play().catch(() => {});
      }
    } else {
      next();
    }
  }

  function removeTrack(idx) {
    if (idx < 0 || idx >= queue.length) return;
    const wasPlaying = idx === currentIndex;
    queue = queue.filter((_, i) => i !== idx);
    if (queue.length === 0) {
      currentIndex = -1;
      visible = false;
      queueOpen = false;
      if (mediaEl) mediaEl.pause();
      return;
    }
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

  // ── Marginalia: data, waveform, markers, composer ───────────────────────

  async function loadAnnotations(id) {
    try {
      const bundle = await fetchAnnotations(id);
      // Guard against a stale response landing after another track change.
      if (id !== annotatedTrackId) return;
      annotations = bundle.annotations;
      inherited = bundle.inherited;
      parentSession = bundle.parent;
    } catch {
      if (id !== annotatedTrackId) return;
      annotations = [];
      inherited = [];
      parentSession = null;
    }
  }

  async function loadPeaks(id) {
    try {
      const res = await fetch(`/api/media/${encodeURIComponent(id)}/peaks`, {
        credentials: 'include',
      });
      if (!res.ok) return; // 404: no peaks — UI still works with ticks only
      const body = await res.json();
      if (id !== annotatedTrackId) return;
      peaks = Array.isArray(body?.peaks) ? body.peaks : null;
    } catch {
      // Peaks are a nice-to-have; the progress strip covers their absence.
    }
  }

  async function refreshMarginalia() {
    if (annotatedTrackId) await loadAnnotations(annotatedTrackId);
  }

  // DPR-aware canvas 2D: min/max peaks as vertical lines, played portion
  // tinted accent vs unplayed gray, plus a playhead line. Without peaks a
  // plain progress strip stands in.
  function drawWave(now, dur, peakData) {
    const canvas = waveEl;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvas.clientWidth || 0;
    const cssHeight = canvas.clientHeight || 0;
    if (!cssWidth || !cssHeight) return;
    canvas.width = Math.floor(cssWidth * dpr);
    canvas.height = Math.floor(cssHeight * dpr);
    const g = canvas.getContext('2d');
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, cssWidth, cssHeight);
    const mid = cssHeight / 2;
    const progress = dur > 0 ? Math.min(1, now / dur) : 0;

    if (peakData && peakData.length) {
      const step = cssWidth / peakData.length;
      const renderPeaks = (color) => {
        g.strokeStyle = color;
        g.lineWidth = Math.max(1, step * 0.8);
        g.beginPath();
        for (let i = 0; i < peakData.length; i++) {
          const [mn, mx] = peakData[i];
          const x = (i + 0.5) * step;
          g.moveTo(x, mid - mx * (mid - 1));
          g.lineTo(x, mid - mn * (mid - 1) + 0.5);
        }
        g.stroke();
      };
      renderPeaks('#555');
      g.save();
      g.beginPath();
      g.rect(0, 0, cssWidth * progress, cssHeight);
      g.clip();
      renderPeaks('#b8860b');
      g.restore();
    } else {
      g.fillStyle = '#333';
      g.fillRect(0, mid - 1.5, cssWidth, 3);
      g.fillStyle = '#b8860b';
      g.fillRect(0, mid - 1.5, cssWidth * progress, 3);
    }

    if (dur > 0) {
      g.fillStyle = '#e0e0e0';
      g.fillRect(cssWidth * progress - 0.5, 0, 1, cssHeight);
    }
  }

  function onWaveClick(e) {
    if (!duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    currentTime = frac * duration;
  }

  function markerLabel(m) {
    const t = fmt(m.position_seconds);
    const text = excerpt(m, 60);
    if (m.kind === 'comment') {
      return `Comment${m.author?.name ? ' by ' + m.author.name : ''} at ${t}${text ? ': ' + text : ''}`;
    }
    return `${m.inh ? 'Session marker' : 'Marker'} at ${t}${text ? ': ' + text : ''}`;
  }

  // Clicking a waveform marker seeks AND opens its card (inherited cues
  // only seek — they belong to the parent session and have no card here).
  function openMarker(m) {
    currentTime = m.position_seconds;
    if (m.inh) return;
    expandedId = expandedId === m.id ? null : m.id;
    replyFor = null;
    editId = null;
    if (expandedId) {
      setTimeout(() => {
        expandedCardEl?.focus();
        expandedCardEl?.scrollIntoView({ block: 'nearest' });
      }, 0);
    }
  }

  function toggleExpand(a) {
    expandedId = expandedId === a.id ? null : a.id;
    replyFor = null;
    editId = null;
    if (expandedId) {
      setTimeout(() => {
        expandedCardEl?.focus();
        expandedCardEl?.scrollIntoView({ block: 'nearest' });
      }, 0);
    }
  }

  function jumpMarker(dir) {
    if (!markers.length || !duration) return;
    const t = currentTime;
    let target = null;
    if (dir < 0) {
      for (let i = markers.length - 1; i >= 0; i--) {
        if (markers[i].position_seconds < t - 0.5) {
          target = markers[i];
          break;
        }
      }
      if (!target) target = markers[markers.length - 1];
    } else {
      for (let i = 0; i < markers.length; i++) {
        if (markers[i].position_seconds > t + 0.5) {
          target = markers[i];
          break;
        }
      }
      if (!target) target = markers[0];
    }
    if (target) {
      currentTime = target.position_seconds;
      live(
        `${target.kind === 'comment' ? 'Comment' : 'Marker'} at ${fmt(target.position_seconds)}`,
      );
    }
  }

  function openComposer() {
    if (!isMediaTrack) return;
    panelOpen = true;
    composerOpen = true;
    setTimeout(() => composerEl?.focus(), 0);
  }

  async function postComment() {
    const body = composerText.trim();
    if (!body || !annotatedTrackId) return;
    margError = null;
    try {
      await createAnnotation(annotatedTrackId, {
        kind: 'comment',
        position_seconds: currentTime,
        body,
      });
      composerText = '';
      composerOpen = false;
      live(`Comment added at ${fmt(currentTime)}`);
      await refreshMarginalia();
    } catch (e) {
      margError = e?.message || 'Failed to post comment';
    }
  }

  async function postCue() {
    if (!annotatedTrackId) return;
    margError = null;
    try {
      await createAnnotation(annotatedTrackId, {
        kind: 'cue',
        position_seconds: currentTime,
        label: composerText.trim() || undefined,
      });
      composerText = '';
      composerOpen = false;
      live(`Marker added at ${fmt(currentTime)}`);
      await refreshMarginalia();
    } catch (e) {
      margError = e?.message || 'Failed to add marker';
    }
  }

  async function postReply(a) {
    const body = replyText.trim();
    if (!body || !annotatedTrackId) return;
    margError = null;
    try {
      await createAnnotation(annotatedTrackId, {
        kind: 'comment',
        position_seconds: a.position_seconds,
        body,
        parent_id: a.id,
      });
      replyText = '';
      replyFor = null;
      live('Reply added');
      await refreshMarginalia();
      expandedId = a.id;
    } catch (e) {
      margError = e?.message || 'Failed to post reply';
    }
  }

  async function toggleResolved(a) {
    margError = null;
    try {
      await toggleResolveAnnotation(a.id);
      live(a.resolved ? 'Annotation reopened' : 'Annotation resolved');
      await refreshMarginalia();
      expandedId = a.id;
    } catch (e) {
      margError = e?.message || 'Failed to update';
    }
  }

  function startEdit(a) {
    editId = a.id;
    editText = a.kind === 'comment' ? a.body || '' : a.label || '';
    editPos = fmtTimestamp(a.position_seconds);
    replyFor = null;
  }

  async function saveEdit(a) {
    margError = null;
    try {
      const seconds = parseTimestamp(editPos);
      await updateAnnotation(a.id, {
        ...(a.kind === 'comment'
          ? { body: editText.trim() }
          : { label: editText.trim() }),
        ...(seconds != null ? { position_seconds: seconds } : {}),
      });
      editId = null;
      live('Annotation updated');
      await refreshMarginalia();
      expandedId = a.id;
    } catch (e) {
      margError = e?.message || 'Failed to save';
    }
  }

  async function removeAnnotation(a) {
    if (!confirm('Delete this annotation? Replies go with it.')) return;
    margError = null;
    try {
      await deleteAnnotation(a.id);
      if (expandedId === a.id) expandedId = null;
      live('Annotation deleted');
      await refreshMarginalia();
    } catch (e) {
      margError = e?.message || 'Failed to delete';
    }
  }

  function toggleFullscreen() {
    if (!pipEl) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      pipEl.requestFullscreen().catch(() => {});
    }
  }

  function onFullscreenChange() {
    isFullscreen = !!document.fullscreenElement;
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
    const { tracks, startIndex, start_time } = e.detail;
    queue = tracks;
    visible = true;
    // Seek target applied once metadata loads (initial load of the track
    // only — replays/repeats don't re-seek).
    pendingSeek =
      typeof start_time === 'number' && isFinite(start_time) && start_time > 0
        ? start_time
        : null;
    if (shuffleOn) buildShuffledIndices();
    loadTrack(startIndex ?? 0);
  }

  function onMediaLoaded() {
    if (pendingSeek == null || !mediaEl) return;
    const t = pendingSeek;
    pendingSeek = null;
    mediaEl.currentTime = t;
  }

  let handler = null;
  let bookmarked = $state(false);
  let hasBookmarks = $state(false);

  function getBookmarkInfo(track) {
    if (!track) return null;
    // Determine target type: media_item for search items, track for release tracks
    if (track.media_type)
      return { type: 'media_item', id: String(track.track_id) };
    if (track.release_code)
      return { type: 'track', id: String(track.track_id) };
    return null;
  }

  async function checkBookmark() {
    const bm = window.__bookmarks;
    if (!bm) {
      hasBookmarks = false;
      return;
    }
    hasBookmarks = true;
    const info = getBookmarkInfo(currentTrack);
    if (!info) {
      bookmarked = false;
      return;
    }
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

  // Track change → load that media item's annotations + peaks. Catalog
  // release tracks (no media_type) skip marginalia entirely.
  $effect(() => {
    const t = currentTrack;
    if (!t || !t.media_type) {
      annotatedTrackId = null;
      annotations = [];
      inherited = [];
      parentSession = null;
      peaks = null;
      panelOpen = false;
      expandedId = null;
      composerOpen = false;
      return;
    }
    const id = String(t.track_id);
    if (id === annotatedTrackId) return;
    annotatedTrackId = id;
    peaks = null;
    expandedId = null;
    composerOpen = false;
    margError = null;
    loadAnnotations(id);
    loadPeaks(id);
  });

  // Redraw the waveform while the panel is open: playback progress, peaks
  // arriving, metadata loading.
  $effect(() => {
    if (!panelOpen || !waveEl) return;
    drawWave(currentTime, duration, peaks);
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
        ? [
            {
              src: currentTrack.cover_url,
              sizes: '256x256',
              type: 'image/jpeg',
            },
          ]
        : [],
    });
  });

  function onKeyDown(e) {
    if (!visible) return;
    // Escape closes innermost-first: annotation card → composer → panel.
    // Handled before the typing guard so it also works from panel inputs.
    if (e.key === 'Escape') {
      if (expandedId) {
        e.preventDefault();
        expandedId = null;
      } else if (composerOpen) {
        e.preventDefault();
        composerOpen = false;
      } else if (panelOpen) {
        e.preventDefault();
        panelOpen = false;
      }
      return;
    }
    const el = document.activeElement;
    const tag = el?.tagName?.toLowerCase();
    if (
      tag === 'input' ||
      tag === 'textarea' ||
      tag === 'select' ||
      el?.isContentEditable
    )
      return;
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
      case '[':
        if (isMediaTrack) {
          e.preventDefault();
          jumpMarker(-1);
        }
        break;
      case ']':
        if (isMediaTrack) {
          e.preventDefault();
          jumpMarker(1);
        }
        break;
      case 'c':
      case 'C':
        if (isMediaTrack) {
          e.preventDefault();
          openComposer();
        }
        break;
    }
  }

  let addHandler = null;
  let seekHandler = null;
  let timeRequestHandler = null;
  let resizeHandler = null;

  onMount(() => {
    handler = (e) => onQueue(e);
    addHandler = (e) => onAdd(e);
    // Annotation seeks from elsewhere: only ever dispatched by components
    // that verified (via player:time) that their item is the current track.
    seekHandler = (e) => {
      const s = e.detail?.seconds;
      if (typeof s === 'number' && isFinite(s)) currentTime = Math.max(0, s);
    };
    // Answer player:time-request synchronously so other islands can read
    // the playhead without touching player state directly.
    timeRequestHandler = () => {
      document.dispatchEvent(
        new CustomEvent('player:time', {
          detail: {
            track_id: currentTrack ? String(currentTrack.track_id) : null,
            media_type: currentTrack?.media_type || null,
            currentTime,
            duration,
          },
        }),
      );
    };
    resizeHandler = () => {
      if (panelOpen) drawWave(currentTime, duration, peaks);
    };
    document.addEventListener('player:queue', handler);
    document.addEventListener('player:add', addHandler);
    document.addEventListener('player:seek', seekHandler);
    document.addEventListener('player:time-request', timeRequestHandler);
    window.addEventListener('resize', resizeHandler);
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('fullscreenchange', onFullscreenChange);
    if ('mediaSession' in navigator) {
      navigator.mediaSession.setActionHandler('play', () => {
        if (mediaEl) mediaEl.play();
      });
      navigator.mediaSession.setActionHandler('pause', () => {
        if (mediaEl) mediaEl.pause();
      });
      navigator.mediaSession.setActionHandler('previoustrack', prev);
      navigator.mediaSession.setActionHandler('nexttrack', next);
    }
  });

  onDestroy(() => {
    if (handler) document.removeEventListener('player:queue', handler);
    if (addHandler) document.removeEventListener('player:add', addHandler);
    if (seekHandler) document.removeEventListener('player:seek', seekHandler);
    if (timeRequestHandler)
      document.removeEventListener('player:time-request', timeRequestHandler);
    if (resizeHandler) window.removeEventListener('resize', resizeHandler);
    document.removeEventListener('keydown', onKeyDown);
    document.removeEventListener('fullscreenchange', onFullscreenChange);
    if (liveTimer) clearTimeout(liveTimer);
    document.body.classList.remove('player-active');
  });
</script>

<div class="player__sr" aria-live="polite">{liveMsg}</div>

<div class="player__spacer" class:player__spacer--active={visible}></div>

{#if visible}
  {#if isVideo && pipOpen}
    <div
      class="player__pip"
      class:player__pip--fs={isFullscreen}
      bind:this={pipEl}
    >
      <div class="player__pip-controls">
        <button
          class="player__pip-btn"
          onclick={toggleFullscreen}
          title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          aria-label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
        >
          {#if isFullscreen}
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
            >
              <path d="M5 1v4H1M11 1v4h4M5 15v-4H1M11 15v-4h4" />
            </svg>
          {:else}
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
            >
              <path d="M1 5V1h4M15 5V1h-4M1 11v4h4M15 11v4h-4" />
            </svg>
          {/if}
        </button>
        <button
          class="player__pip-btn"
          onclick={() => (pipOpen = false)}
          title="Close video"
          aria-label="Close video">&times;</button
        >
      </div>
      <!-- svelte-ignore a11y_media_has_caption -->
      <video
        bind:this={mediaEl}
        bind:currentTime
        bind:duration
        bind:paused
        bind:volume
        onended={onEnded}
        onloadedmetadata={onMediaLoaded}
        onerror={onMediaError}
        onplaying={onPlaying}
        ondblclick={toggleFullscreen}
        src={currentTrack?.stream_url}
        poster={currentTrack?.cover_url}
        preload="auto"
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
        onloadedmetadata={onMediaLoaded}
        onerror={onMediaError}
        onplaying={onPlaying}
        src={currentTrack?.stream_url}
        preload="auto"
      ></audio>
      {#if nextAudioUrl}
        <audio src={nextAudioUrl} preload="auto" style="display:none"></audio>
      {/if}
    {/if}

    <div class="player__inner">
      <div class="player__info">
        {#if isVideo && !pipOpen}
          <button
            class="player__pip-reopen"
            onclick={() => (pipOpen = true)}
            title="Show video"
            aria-label="Show video"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <rect
                x="1"
                y="2"
                width="14"
                height="11"
                rx="1"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
              />
              <rect x="8" y="7" width="6" height="5" rx="0.5" />
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
            {#if mediaError}
              <span class="player__error">{mediaError}</span>
            {:else}
              {currentTrack?.release_title ?? ''}
              {#if currentTrack?.entity_name}&mdash; {currentTrack.entity_name}{/if}
            {/if}
          </div>
        </div>
        {#if hasBookmarks}
          <button
            class="player__star {bookmarked ? 'bookmarked' : ''}"
            onclick={toggleBookmark}
            title={bookmarked ? 'Remove bookmark' : 'Bookmark'}
            aria-label={bookmarked ? 'Remove bookmark' : 'Bookmark'}
            aria-pressed={bookmarked}
          ></button>
        {/if}
      </div>

      <div class="player__controls">
        <button
          class="player__btn {shuffleOn ? 'player__btn--active' : ''}"
          onclick={toggleShuffle}
          title="Shuffle"
          aria-label="Shuffle"
          aria-pressed={shuffleOn}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <path
              d="M11 2l3 3-3 3M11 8l3 3-3 3"
              stroke="currentColor"
              stroke-width="1.5"
              fill="none"
              stroke-linecap="square"
            />
            <path
              d="M1 5h6l5 6h2M1 11h6l2-2.5"
              stroke="currentColor"
              stroke-width="1.5"
              fill="none"
              stroke-linecap="square"
            />
          </svg>
        </button>
        <button
          class="player__btn"
          onclick={prev}
          title="Previous"
          aria-label="Previous track"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <rect x="1" y="2" width="2" height="12" />
            <polygon points="14,2 14,14 4,8" />
          </svg>
        </button>
        <button
          class="player__btn player__btn--play"
          onclick={togglePlay}
          title={paused ? 'Play' : 'Pause'}
          aria-label={paused ? 'Play' : 'Pause'}
        >
          {#if paused}
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <polygon points="4,2 18,10 4,18" />
            </svg>
          {:else}
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <rect x="3" y="2" width="5" height="16" />
              <rect x="12" y="2" width="5" height="16" />
            </svg>
          {/if}
        </button>
        <button
          class="player__btn"
          onclick={next}
          title="Next"
          aria-label="Next track"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <polygon points="2,2 2,14 12,8" />
            <rect x="13" y="2" width="2" height="12" />
          </svg>
        </button>
        <button
          class="player__btn {repeatMode !== 'off'
            ? 'player__btn--active'
            : ''}"
          onclick={cycleRepeat}
          title="Repeat: {repeatMode}"
          aria-label="Repeat: {repeatMode}"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
          >
            <path d="M2 5h10l-2-2M14 11H4l2 2" stroke-linecap="square" />
            <path d="M2 5v4a2 2 0 002 2M14 11V7a2 2 0 00-2-2" />
          </svg>
          {#if repeatMode === 'one'}
            <span class="player__repeat-one">1</span>
          {/if}
        </button>
        <button
          class="player__btn player__btn--mute-mobile"
          onclick={toggleMute}
          title={muted ? 'Unmute' : 'Mute'}
          aria-label={muted ? 'Unmute' : 'Mute'}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <polygon points="1,6 1,10 4,10 8,14 8,2 4,6" />
            {#if muted || volume === 0}
              <line
                x1="10"
                y1="5"
                x2="15"
                y2="11"
                stroke="currentColor"
                stroke-width="1.5"
              />
              <line
                x1="15"
                y1="5"
                x2="10"
                y2="11"
                stroke="currentColor"
                stroke-width="1.5"
              />
            {/if}
          </svg>
        </button>
      </div>

      <div class="player__scrubber">
        <span class="player__time">{fmt(currentTime)}</span>
        <div class="player__seek-wrap">
          {#if !panelOpen && markers.length > 0 && duration > 0}
            <!-- Collapsed-state tick strip: supplementary seek dots above
                 the range input (which stays the primary seek control). -->
            <div class="player__ticks">
              {#each markers as m (m.id)}
                <button
                  type="button"
                  class="player__tick {m.kind === 'comment'
                    ? 'player__tick--comment'
                    : 'player__tick--cue'} {m.inh ? 'player__tick--inh' : ''}"
                  style="left: {(m.position_seconds / duration) * 100}%"
                  onclick={() => (currentTime = m.position_seconds)}
                  aria-label={markerLabel(m)}
                  title={markerLabel(m)}
                ></button>
              {/each}
            </div>
          {/if}
          <input
            class="player__range player__range--seek"
            type="range"
            min="0"
            max={duration || 0}
            step="0.1"
            value={currentTime}
            oninput={onSeek}
            aria-label="Seek"
            aria-valuetext={fmt(currentTime)}
          />
        </div>
        <span class="player__time">{fmt(duration)}</span>
      </div>

      <div class="player__volume">
        <button
          class="player__mute-btn"
          onclick={toggleMute}
          title={muted ? 'Unmute' : 'Mute'}
          aria-label={muted ? 'Unmute' : 'Mute'}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <polygon points="1,6 1,10 4,10 8,14 8,2 4,6" />
            {#if muted || volume === 0}
              <line
                x1="10"
                y1="5"
                x2="15"
                y2="11"
                stroke="currentColor"
                stroke-width="1.5"
              />
              <line
                x1="15"
                y1="5"
                x2="10"
                y2="11"
                stroke="currentColor"
                stroke-width="1.5"
              />
            {:else}
              {#if volume > 0}
                <path
                  d="M10 4.5c1.5 1.5 1.5 5.5 0 7"
                  stroke="currentColor"
                  stroke-width="1.5"
                  fill="none"
                />
              {/if}
              {#if volume > 0.5}
                <path
                  d="M12 2.5c2.5 2.5 2.5 8.5 0 11"
                  stroke="currentColor"
                  stroke-width="1.5"
                  fill="none"
                />
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
          aria-label="Volume"
          aria-valuetext="{Math.round(volume * 100)}%"
        />
      </div>

      {#if currentTrack?.stream_url}
        <a
          class="player__btn player__btn--download"
          href={currentTrack.stream_url}
          download={currentTrack.title || 'download'}
          title="Download"
          aria-label="Download"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <path
              d="M8 1v9M4 7l4 4 4-4"
              stroke="currentColor"
              stroke-width="1.5"
              fill="none"
              stroke-linecap="square"
            />
            <rect x="2" y="13" width="12" height="1.5" />
          </svg>
        </a>
      {/if}

      <button
        class="player__btn player__btn--queue {queueOpen
          ? 'player__btn--active'
          : ''}"
        onclick={toggleQueue}
        title="Queue"
        aria-label="Queue"
        aria-expanded={queueOpen}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <rect x="1" y="2" width="10" height="1.5" />
          <rect x="1" y="6" width="10" height="1.5" />
          <rect x="1" y="10" width="7" height="1.5" />
          <polygon points="11,9 11,14 15,11.5" />
        </svg>
      </button>

      {#if isMediaTrack}
        <button
          class="player__btn player__btn--marginalia {panelOpen
            ? 'player__btn--active'
            : ''}"
          onclick={() => (panelOpen = !panelOpen)}
          title="Comments and markers"
          aria-label="Comments and markers"
          aria-expanded={panelOpen}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
          >
            <path
              d={panelOpen ? 'M3 10l5-5 5 5' : 'M3 6l5 5 5-5'}
              stroke-linecap="square"
            />
          </svg>
          {#if annotations.length > 0}
            <span class="player__marg-count">{annotations.length}</span>
          {/if}
        </button>
      {/if}

      <button
        class="player__btn player__btn--close"
        onclick={clearQueue}
        title="Close"
        aria-label="Close player"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
        >
          <line x1="3" y1="3" x2="13" y2="13" />
          <line x1="13" y1="3" x2="3" y2="13" />
        </svg>
      </button>
    </div>
  </div>

  {#if queueOpen}
    <div class="queue-backdrop" onclick={() => (queueOpen = false)}></div>
    <div class="queue-panel">
      <div class="queue-panel__header">
        <span class="queue-panel__title">Queue ({queue.length})</span>
        <button class="queue-panel__clear" onclick={clearQueue}>Clear</button>
        <button class="queue-panel__close" onclick={() => (queueOpen = false)}
          >&times;</button
        >
      </div>
      <div class="queue-panel__list">
        {#each queue as track, i}
          <button
            class="queue-panel__item {i === currentIndex
              ? 'queue-panel__item--active'
              : ''}"
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
              onclick={(e) => {
                e.stopPropagation();
                removeTrack(i);
              }}
              onkeydown={(e) => {
                if (e.key === 'Enter') {
                  e.stopPropagation();
                  removeTrack(i);
                }
              }}>&times;</span
            >
          </button>
        {/each}
      </div>
    </div>
  {/if}

  {#if panelOpen && isMediaTrack}
    <div class="marginalia" role="region" aria-label="Comments and markers">
      <div class="marginalia__scroll">
        <!-- Click-to-seek waveform; keyboard seeking stays on the range
             input + [ ] marker jumps, so this is pointer-supplementary. -->
        <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
        <div class="marginalia__wave" onclick={onWaveClick}>
          <canvas bind:this={waveEl} aria-hidden="true"></canvas>
          {#if duration > 0}
            {#each markers as m (m.id)}
              <button
                type="button"
                class="marginalia__marker {m.kind === 'comment'
                  ? 'marginalia__marker--comment'
                  : 'marginalia__marker--cue'} {m.inh
                  ? 'marginalia__marker--inh'
                  : ''} {m.resolved ? 'marginalia__marker--resolved' : ''}"
                style="left: {(m.position_seconds / duration) * 100}%"
                onclick={(e) => {
                  e.stopPropagation();
                  openMarker(m);
                }}
                aria-label={markerLabel(m)}
                title={markerLabel(m)}
                >{#if m.kind === 'comment'}{(m.author?.name || '?')
                    .charAt(0)
                    .toUpperCase()}{/if}</button
              >
            {/each}
          {/if}
        </div>

        <div class="marginalia__bar">
          <button
            class="marginalia__btn"
            type="button"
            onclick={() => {
              composerOpen = !composerOpen;
              if (composerOpen) setTimeout(() => composerEl?.focus(), 0);
            }}
            aria-expanded={composerOpen}
            >💬 Comment at {fmt(currentTime)}</button
          >
          <button
            class="marginalia__btn"
            type="button"
            onclick={postCue}
            title="Drop a marker at the current position (uses the comment text as its label, if any)"
            >◆ Marker</button
          >
          {#if inherited.length > 0}
            <button
              class="marginalia__btn marginalia__btn--inh"
              type="button"
              aria-pressed={showInherited}
              onclick={() => (showInherited = !showInherited)}
              >◇ session markers ({inherited.length})</button
            >
          {/if}
          {#if parentSession}
            <span
              class="marginalia__session"
              title="Cues inherited from the parent session bundle"
              >from session: {parentSession.filename}</span
            >
          {/if}
        </div>

        {#if composerOpen}
          <form
            class="marginalia__composer"
            onsubmit={(e) => {
              e.preventDefault();
              postComment();
            }}
          >
            <textarea
              rows="2"
              placeholder="Comment… (Enter posts, Shift+Enter for a newline)"
              aria-label="Comment text"
              bind:value={composerText}
              bind:this={composerEl}
              onkeydown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  postComment();
                }
              }}
            ></textarea>
            <div class="marginalia__composer-actions">
              <button
                class="marginalia__btn"
                type="submit"
                disabled={!composerText.trim()}
                >Comment at {fmt(currentTime)}</button
              >
              <button class="marginalia__btn" type="button" onclick={postCue}
                >Marker</button
              >
            </div>
          </form>
        {/if}
        {#if margError}
          <div class="marginalia__error">{margError}</div>
        {/if}

        <div class="marginalia__list">
          {#if annotations.length === 0 && (!showInherited || inherited.length === 0)}
            <div class="marginalia__empty">
              No comments or markers yet — press C to add one.
            </div>
          {/if}
          {#each annotations as a (a.id)}
            <div
              class="marginalia__row"
              class:marginalia__row--resolved={a.resolved}
            >
              <div class="marginalia__row-head">
                <button
                  class="marginalia__seek"
                  type="button"
                  onclick={() => (currentTime = a.position_seconds)}
                  aria-label="Seek to {fmt(a.position_seconds)}"
                  >{fmt(a.position_seconds)}</button
                >
                <button
                  class="marginalia__row-toggle"
                  type="button"
                  onclick={() => toggleExpand(a)}
                  aria-expanded={expandedId === a.id}
                >
                  <span class="marginalia__icon" aria-hidden="true"
                    >{a.kind === 'comment' ? '💬' : '◆'}</span
                  >
                  <span class="marginalia__who">{whoLabel(a)}</span>
                  <span class="marginalia__text"
                    >{excerpt(a) || '(no text)'}</span
                  >
                  {#if a.replies?.length}
                    <span class="marginalia__replies-count"
                      >{a.replies.length}↩</span
                    >
                  {/if}
                  {#if a.resolved}
                    <span class="marginalia__done" title="Resolved">✓</span>
                  {/if}
                </button>
              </div>
              {#if expandedId === a.id}
                <div
                  class="marginalia__card"
                  tabindex="-1"
                  bind:this={expandedCardEl}
                >
                  <div class="marginalia__card-meta">
                    {whoLabel(a)} · {fmt(a.position_seconds)}
                  </div>
                  {#if a.label}
                    <div class="marginalia__card-label">{a.label}</div>
                  {/if}
                  {#if a.body}
                    <div class="marginalia__body">
                      {#each linkifyTimestamps(a.body) as part}
                        {#if 'seconds' in part}
                          <button
                            class="marginalia__ts"
                            type="button"
                            onclick={() => (currentTime = part.seconds)}
                            >{part.label}</button
                          >
                        {:else}{part.text}{/if}
                      {/each}
                    </div>
                  {/if}
                  {#if a.replies?.length}
                    <ul class="marginalia__replies">
                      {#each a.replies as r (r.id)}
                        <li>
                          <span class="marginalia__who"
                            >{r.author?.name || 'reply'}</span
                          >
                          <span class="marginalia__body">
                            {#each linkifyTimestamps(r.body || '') as part}
                              {#if 'seconds' in part}
                                <button
                                  class="marginalia__ts"
                                  type="button"
                                  onclick={() => (currentTime = part.seconds)}
                                  >{part.label}</button
                                >
                              {:else}{part.text}{/if}
                            {/each}
                          </span>
                        </li>
                      {/each}
                    </ul>
                  {/if}
                  {#if editId === a.id}
                    <form
                      class="marginalia__edit"
                      onsubmit={(e) => {
                        e.preventDefault();
                        saveEdit(a);
                      }}
                    >
                      <textarea
                        rows="2"
                        bind:value={editText}
                        aria-label={a.kind === 'comment'
                          ? 'Comment text'
                          : 'Marker label'}
                      ></textarea>
                      <div class="marginalia__edit-row">
                        <input
                          class="marginalia__pos"
                          type="text"
                          bind:value={editPos}
                          aria-label="Position (mm:ss)"
                        />
                        <button class="marginalia__btn" type="submit"
                          >Save</button
                        >
                        <button
                          class="marginalia__btn"
                          type="button"
                          onclick={() => (editId = null)}>Cancel</button
                        >
                      </div>
                    </form>
                  {:else if replyFor === a.id}
                    <form
                      class="marginalia__edit"
                      onsubmit={(e) => {
                        e.preventDefault();
                        postReply(a);
                      }}
                    >
                      <textarea
                        rows="2"
                        placeholder="Reply…"
                        aria-label="Reply text"
                        bind:value={replyText}
                      ></textarea>
                      <div class="marginalia__edit-row">
                        <button
                          class="marginalia__btn"
                          type="submit"
                          disabled={!replyText.trim()}>Reply</button
                        >
                        <button
                          class="marginalia__btn"
                          type="button"
                          onclick={() => (replyFor = null)}>Cancel</button
                        >
                      </div>
                    </form>
                  {:else}
                    <div class="marginalia__actions">
                      <button
                        class="marginalia__btn"
                        type="button"
                        onclick={() => {
                          replyFor = a.id;
                          replyText = '';
                          editId = null;
                        }}>Reply</button
                      >
                      <button
                        class="marginalia__btn"
                        type="button"
                        onclick={() => toggleResolved(a)}
                        >{a.resolved ? 'Unresolve' : 'Resolve'}</button
                      >
                      <button
                        class="marginalia__btn"
                        type="button"
                        onclick={() => startEdit(a)}>Edit</button
                      >
                      <button
                        class="marginalia__btn marginalia__btn--danger"
                        type="button"
                        onclick={() => removeAnnotation(a)}>Delete</button
                      >
                    </div>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
          {#if showInherited && inherited.length > 0}
            {#each inherited as a (a.id)}
              <div class="marginalia__row marginalia__row--inh">
                <div class="marginalia__row-head">
                  <button
                    class="marginalia__seek"
                    type="button"
                    onclick={() => (currentTime = a.position_seconds)}
                    aria-label="Seek to {fmt(a.position_seconds)}"
                    >{fmt(a.position_seconds)}</button
                  >
                  <span class="marginalia__row-static">
                    <span class="marginalia__icon" aria-hidden="true">◇</span>
                    <span class="marginalia__who">{sourceLabel(a.source)}</span>
                    <span class="marginalia__text" title={a.label || ''}
                      >{a.label || ''}</span
                    >
                  </span>
                </div>
              </div>
            {/each}
          {/if}
        </div>
      </div>
    </div>
  {/if}
{/if}

<style>
  .player__spacer {
    height: 0;
    background: #1a1a1a;
  }
  .player__spacer--active {
    height: 72px;
  }

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

  .player__error {
    color: var(--color-status-fail);
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
  .player__star::before {
    content: '\2606';
  }
  .player__star:hover {
    color: #b8860b;
  }
  .player__star.bookmarked {
    color: #b8860b;
  }
  .player__star.bookmarked::before {
    content: '\2605';
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

  .player__btn:hover {
    color: #fff;
  }
  .player__btn--active {
    color: #b8860b;
  }

  .player__btn--play {
    border: 1px solid #555;
    padding: 0.375rem;
  }

  .player__btn--play:hover {
    border-color: #b8860b;
  }

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

  .player__range--seek {
    width: 100%;
  }

  .player__volume {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    flex-shrink: 0;
    color: #888;
  }

  .player__range--vol {
    width: 80px;
  }

  .player__mute-btn {
    background: none;
    border: none;
    color: #888;
    cursor: pointer;
    padding: 0;
    display: flex;
    align-items: center;
  }
  .player__mute-btn:hover {
    color: #fff;
  }

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

  .player__btn--close {
    flex-shrink: 0;
    color: #666;
  }
  .player__btn--close:hover {
    color: #c00;
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
    from {
      transform: translateY(100%);
    }
    to {
      transform: translateY(0);
    }
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
  .queue-panel__clear:hover {
    color: #fff;
    border-color: #b8860b;
  }

  .queue-panel__close {
    background: none;
    border: none;
    color: #888;
    font-size: 1.2rem;
    cursor: pointer;
    padding: 0 4px;
  }
  .queue-panel__close:hover {
    color: #fff;
  }

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
  .queue-panel__item:hover {
    background: #222;
  }
  .queue-panel__item--active {
    color: #b8860b;
  }

  .queue-panel__num {
    width: 2ch;
    text-align: right;
    flex-shrink: 0;
    color: #555;
    font-size: 0.75rem;
  }
  .queue-panel__item--active .queue-panel__num {
    color: #b8860b;
  }

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
  .queue-panel__remove:hover {
    color: #c00;
  }

  /* Video PiP panel */
  .player__pip {
    position: fixed;
    bottom: 72px;
    right: 1rem;
    z-index: 9998;
    width: 320px;
    background: #000;
    border: 1px solid #333;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.6);
  }

  .player__pip video {
    display: block;
    width: 100%;
    height: auto;
  }

  .player__pip-controls {
    position: absolute;
    top: 4px;
    right: 6px;
    z-index: 1;
    display: flex;
    gap: 4px;
    opacity: 0;
    transition: opacity 0.15s;
  }

  .player__pip:hover .player__pip-controls {
    opacity: 1;
  }

  .player__pip-btn {
    background: rgba(0, 0, 0, 0.6);
    border: none;
    color: #fff;
    font-size: 1.1rem;
    cursor: pointer;
    padding: 4px 6px;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .player__pip-btn:hover {
    background: rgba(0, 0, 0, 0.9);
  }

  /* Fullscreen mode */
  .player__pip--fs {
    position: fixed;
    inset: 0;
    width: 100%;
    height: 100%;
    border: none;
    box-shadow: none;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .player__pip--fs video {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

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

  .player__pip-reopen:hover {
    color: #fff;
    border-color: #b8860b;
  }

  /* Visually-hidden live announcer ("Comment added at 2:34", track changes). */
  .player__sr {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  /* Seek wrap + collapsed-state annotation ticks. The ticks protrude above
     the bar's top edge, SoundCloud-style; the range input stays primary. */
  .player__seek-wrap {
    position: relative;
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    align-items: center;
  }
  .player__ticks {
    position: absolute;
    left: 0;
    right: 0;
    top: -10px;
    height: 8px;
    pointer-events: none;
  }
  .player__tick {
    position: absolute;
    top: 0;
    transform: translateX(-50%);
    width: 8px;
    height: 8px;
    padding: 0;
    border: none;
    cursor: pointer;
    pointer-events: auto;
  }
  .player__tick--comment {
    border-radius: 50%;
    background: #e0e0e0;
  }
  .player__tick--cue {
    transform: translateX(-50%) rotate(45deg);
    background: #b8860b;
  }
  .player__tick--inh {
    background: transparent;
    border: 1px solid #b8860b;
  }
  .player__tick--comment.player__tick--inh {
    border-color: #888;
  }
  .player__tick:focus-visible {
    outline: 2px solid #b8860b;
    outline-offset: 1px;
  }

  .player__marg-count {
    font-size: 0.55rem;
    color: #b8860b;
    margin-left: 2px;
    font-variant-numeric: tabular-nums;
  }

  /* ── Marginalia panel (waveform + annotations above the bar) ────────── */
  .marginalia {
    position: fixed;
    bottom: 72px;
    left: 0;
    right: 0;
    z-index: 9998;
    background: #1a1a1a;
    border-top: 1px solid #333;
    max-height: 60vh;
    display: flex;
    flex-direction: column;
    font-family: 'Courier New', Courier, monospace;
    color: #e0e0e0;
    animation: queue-slide-up 0.2s ease-out;
  }
  .marginalia__scroll {
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }
  .marginalia__wave {
    position: relative;
    height: 72px;
    flex-shrink: 0;
    cursor: crosshair;
    border-bottom: 1px solid #333;
  }
  .marginalia__wave canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
  .marginalia__marker {
    position: absolute;
    top: 50%;
    transform: translate(-50%, -50%);
    padding: 0;
    cursor: pointer;
    z-index: 1;
  }
  .marginalia__marker--comment {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: #e0e0e0;
    color: #1a1a1a;
    border: 1px solid #000;
    font-size: 0.6rem;
    font-weight: bold;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .marginalia__marker--cue {
    width: 11px;
    height: 11px;
    background: #b8860b;
    border: none;
    transform: translate(-50%, -50%) rotate(45deg);
  }
  .marginalia__marker--inh {
    background: transparent;
    border: 1px solid #b8860b;
  }
  .marginalia__marker--resolved {
    opacity: 0.4;
  }
  .marginalia__marker:focus-visible {
    outline: 2px solid #b8860b;
    outline-offset: 1px;
  }

  .marginalia__bar {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.75rem;
    border-bottom: 1px solid #333;
    flex-wrap: wrap;
    flex-shrink: 0;
  }
  .marginalia__session {
    color: #888;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .marginalia__btn {
    background: none;
    border: 1px solid #555;
    color: #ccc;
    font-family: inherit;
    font-size: 0.6875rem;
    padding: 2px 8px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
  }
  .marginalia__btn:hover {
    color: #fff;
    border-color: #b8860b;
  }
  .marginalia__btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .marginalia__btn--inh[aria-pressed='true'] {
    color: #b8860b;
    border-color: #b8860b;
  }
  .marginalia__btn--danger {
    border-color: #c00;
    color: #c00;
  }
  .marginalia__btn--danger:hover {
    background: #c00;
    color: #fff;
    border-color: #c00;
  }

  .marginalia__composer {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid #333;
    flex-shrink: 0;
  }
  .marginalia__composer textarea {
    background: #111;
    color: #e0e0e0;
    border: 1px solid #444;
    font-family: inherit;
    font-size: 0.8125rem;
    padding: 6px 8px;
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
  }
  .marginalia__composer-actions {
    display: flex;
    gap: 6px;
  }
  .marginalia__error {
    padding: 0.375rem 0.75rem;
    color: #c00;
    font-size: 0.75rem;
    border-bottom: 1px solid #333;
  }
  .marginalia__empty {
    padding: 0.75rem;
    color: #888;
    font-size: 0.75rem;
  }

  .marginalia__list {
    display: flex;
    flex-direction: column;
  }
  .marginalia__row {
    border-bottom: 1px solid #222;
  }
  .marginalia__row--resolved {
    opacity: 0.5;
  }
  .marginalia__row-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.25rem 0.75rem;
  }
  .marginalia__seek {
    background: none;
    border: 1px solid #555;
    color: #b8860b;
    font-family: inherit;
    font-size: 0.6875rem;
    padding: 1px 6px;
    cursor: pointer;
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }
  .marginalia__seek:hover {
    border-color: #b8860b;
  }
  .marginalia__row-toggle {
    background: none;
    border: none;
    color: #ccc;
    font-family: inherit;
    font-size: 0.75rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    flex: 1;
    text-align: left;
    padding: 2px 0;
  }
  .marginalia__row-toggle:hover {
    color: #fff;
  }
  .marginalia__row-static {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    flex: 1;
    font-size: 0.75rem;
    color: #888;
    padding: 2px 0;
  }
  .marginalia__icon {
    flex-shrink: 0;
    font-size: 0.65rem;
  }
  .marginalia__who {
    color: #888;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .marginalia__text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .marginalia__replies-count {
    color: #555;
    font-size: 0.65rem;
    flex-shrink: 0;
  }
  .marginalia__done {
    color: #b8860b;
    flex-shrink: 0;
  }

  .marginalia__card {
    margin: 0 0.75rem 0.5rem 2.75rem;
    padding: 0.5rem;
    border: 1px solid #444;
    background: #161616;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .marginalia__card:focus {
    outline: 2px solid #b8860b;
    outline-offset: -2px;
  }
  .marginalia__card-meta {
    color: #888;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
  }
  .marginalia__card-label {
    font-size: 0.8125rem;
    font-weight: bold;
  }
  .marginalia__body {
    white-space: pre-wrap;
    font-size: 0.8125rem;
  }
  .marginalia__ts {
    background: none;
    border: none;
    color: #b8860b;
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
    font: inherit;
  }
  .marginalia__replies {
    list-style: none;
    margin: 0;
    padding: 0 0 0 12px;
    border-left: 2px solid #333;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .marginalia__replies li {
    display: flex;
    gap: 8px;
    align-items: baseline;
  }
  .marginalia__edit {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .marginalia__edit textarea {
    background: #111;
    color: #e0e0e0;
    border: 1px solid #444;
    font-family: inherit;
    font-size: 0.8125rem;
    padding: 6px 8px;
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
  }
  .marginalia__edit-row {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .marginalia__pos {
    width: 8ch;
    background: #111;
    color: #e0e0e0;
    border: 1px solid #444;
    font-family: inherit;
    font-size: 0.8125rem;
    padding: 4px 6px;
  }
  .marginalia__actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  @media (max-width: 639px) {
    .player__spacer--active {
      height: 100px;
    }
    .player {
      padding: 0.375rem 0.5rem;
    }
    .player__inner {
      flex-wrap: wrap;
      gap: 0.375rem;
    }
    .player__info {
      flex: 1 1 100%;
      order: 1;
    }
    .player__controls {
      order: 2;
      flex: 0 0 auto;
    }
    .player__scrubber {
      order: 3;
      flex: 1 1 auto;
      min-width: 0;
    }
    .player__volume {
      display: none;
    }
    .player__btn--mute-mobile {
      display: flex;
    }
    .player__btn--queue {
      order: 4;
    }

    .player__pip {
      width: 200px;
      bottom: 96px;
    }

    .queue-panel {
      max-height: calc(100vh - 96px);
      bottom: 96px;
    }

    /* Marginalia panel → bottom sheet. */
    .marginalia {
      bottom: 96px;
      max-height: 70vh;
    }
    /* ≥44px hit areas without blowing up the visuals. */
    .marginalia__marker::before {
      content: '';
      position: absolute;
      inset: -12px;
    }
    .marginalia__btn,
    .marginalia__seek {
      min-height: 44px;
      display: inline-flex;
      align-items: center;
    }
    .marginalia__row-toggle {
      min-height: 44px;
    }
    .marginalia__card {
      margin-left: 0.75rem;
    }
    .marginalia__composer textarea,
    .marginalia__edit textarea,
    .marginalia__pos {
      /* iOS Safari zooms on focus below 16px. */
      font-size: 16px;
    }
    .player__tick {
      width: 10px;
      height: 10px;
    }
  }
</style>
