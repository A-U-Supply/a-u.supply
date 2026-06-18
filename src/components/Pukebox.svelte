<script>
  let manifest = [];
  let currentIndex = 0;
  let isPlaying = false;
  let shuffleOn = false;
  let loopOn = false;
  let loading = true;
  let error = null;

  const audio = new Audio();

  async function loadManifest() {
    try {
      const resp = await fetch('/api/pukebox/manifest?limit=1000');
      if (!resp.ok) throw new Error(resp.status);
      const data = await resp.json();
      manifest = data.entries || [];
    } catch (e) {
      error = 'NO TRACKS YET... CHECK BACK TOMORROW';
      loading = false;
      return;
    }

    if (!manifest.length) {
      error = 'NO TRACKS YET';
      loading = false;
      return;
    }

    const hash = location.hash.slice(1);
    if (hash) {
      const idx = manifest.findIndex((e) => e.entry_id === hash);
      if (idx >= 0) currentIndex = idx;
    }

    loading = false;
    renderCard();
    loadTrack(currentIndex);
  }

  function formatDate(dateStr) {
    const months = [
      'JAN',
      'FEB',
      'MAR',
      'APR',
      'MAY',
      'JUN',
      'JUL',
      'AUG',
      'SEP',
      'OCT',
      'NOV',
      'DEC',
    ];
    const [y, m, d] = dateStr.split('-');
    return `${months[parseInt(m) - 1]}\u2022${d}\u2022${y}`;
  }

  function renderCard() {
    const entry = manifest[currentIndex];
    if (!entry) return;
    const teaser =
      entry.description.length > 60
        ? entry.description.slice(0, 57) + '...'
        : entry.description;
    cardContent = `
      <div class="card-date">${formatDate(entry.date)}</div>
      <div class="card-scale">${entry.scale} in ${entry.root} — ${entry.tempo} BPM</div>
      <div class="card-teaser">${teaser}</div>
      <div class="card-counter">${currentIndex + 1} / ${manifest.length}</div>
    `;
    descriptionMarquee = `\u266B ${entry.scale} in ${entry.root} (${entry.tempo} BPM) \u2014 ${entry.description} \u266B`;
  }

  function loadTrack(index) {
    const entry = manifest[index];
    if (!entry) return;
    audio.src = entry.preview_url;
    location.hash = entry.entry_id;

    dlOgg = entry.preview_url;
    dlMelody = entry.midi_urls?.melody || null;
    dlDrums = entry.midi_urls?.drums || null;
    dlBass = entry.midi_urls?.bass || null;
    dlChords = entry.midi_urls?.chords || null;

    audio
      .play()
      .then(() => {
        isPlaying = true;
        playBtn = '\u23F8';
      })
      .catch(() => {
        isPlaying = false;
      });
  }

  function flipUp() {
    if (!manifest.length) return;
    currentIndex = (currentIndex - 1 + manifest.length) % manifest.length;
    renderCard();
    loadTrack(currentIndex);
  }

  function flipDown() {
    if (!manifest.length) return;
    currentIndex = (currentIndex + 1) % manifest.length;
    renderCard();
    loadTrack(currentIndex);
  }

  function selectCurrentTrack() {
    if (!manifest.length) return;
    loadTrack(currentIndex);
    audio.play().catch(() => {});
  }

  function togglePlay() {
    if (!audio.src) return;
    if (audio.paused) {
      audio.play().catch(() => {});
      isPlaying = true;
      playBtn = '\u23F8';
    } else {
      audio.pause();
      isPlaying = false;
      playBtn = '\u25B6';
    }
  }

  function seekAudio(e) {
    if (!audio.duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    audio.currentTime = pct * audio.duration;
  }

  function playRandomTrack() {
    if (manifest.length < 2) return;
    let next;
    do {
      next = Math.floor(Math.random() * manifest.length);
    } while (next === currentIndex);
    currentIndex = next;
    renderCard();
    loadTrack(currentIndex);
  }

  function toggleShuffle() {
    shuffleOn = !shuffleOn;
    if (shuffleOn && manifest.length) playRandomTrack();
  }

  function toggleLoop() {
    loopOn = !loopOn;
  }

  function formatTime(s) {
    if (isNaN(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  audio.addEventListener('timeupdate', () => {
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    progressFill = pct + '%';
    trackTime = `${formatTime(audio.currentTime)}/${formatTime(audio.duration)}`;
  });

  audio.addEventListener('ended', () => {
    if (shuffleOn && manifest.length > 1) {
      playRandomTrack();
      return;
    }
    if (loopOn) {
      audio.currentTime = 0;
      audio.play().catch(() => {});
      return;
    }
    isPlaying = false;
    playBtn = '\u25B6';
    progressFill = '0%';
  });

  function handleKeydown(e) {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      flipUp();
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      flipDown();
    }
    if (e.key === ' ') {
      e.preventDefault();
      togglePlay();
    }
  }

  let cardContent = '';
  let descriptionMarquee = 'Loading the jukebox...';
  let playBtn = '\u25B6';
  let progressFill = '0%';
  let trackTime = '0:00';
  let dlOgg = '#';
  let dlMelody = null;
  let dlDrums = null;
  let dlBass = null;
  let dlChords = null;

  loadManifest();
</script>

<svelte:window on:keydown={handleKeydown} />

<div class="pukebox">
  <div class="top-marquees">
    <marquee scrollamount="4">
      &#127925; WELCOME TO THE PUKE BOX &#127925; YOUR #1 SOURCE FOR
      AI-GENERATED MIDI SLOP &#127925; INSERT COIN TO CONTINUE &#127925; NOW
      WITH MORE SINE WAVES &#127925; JUKEBOX HITS FROM THE FUTURE &#127925; 100%
      ROBOT-COMPOSED &#127925;
    </marquee>
  </div>

  <div class="page-title">
    <h1>PUKE BOX</h1>
    <div class="subtitle">
      &#9834; AI-generated MIDI jukebox from the year 3000 &#9834;
    </div>
  </div>

  <div class="jukebox-wrapper">
    <div class="jukebox-container">
      <div class="overlay-zone" id="marquee-zone">
        <marquee scrollamount="2">{descriptionMarquee}</marquee>
      </div>

      <div class="overlay-zone" id="flipper-zone">
        <div class="flipper-nav">
          <button class="flip-btn" on:click={flipUp} title="Previous track"
            >&#9650;</button
          >
          <div class="card-content" on:click={selectCurrentTrack}>
            {#if loading}
              <div class="loading-msg">LOADING...</div>
            {:else if error}
              <div class="loading-msg">{error}</div>
            {:else}
              {@html cardContent}
            {/if}
          </div>
          <button class="flip-btn" on:click={flipDown} title="Next track"
            >&#9660;</button
          >
        </div>
      </div>

      <div class="overlay-zone" id="controls-zone">
        <div class="player-row">
          <button class="play-btn" on:click={togglePlay}>{playBtn}</button>
          <div class="progress-bar" on:click={seekAudio}>
            <div class="progress-fill" style="width: {progressFill}"></div>
          </div>
          <span class="track-time">{trackTime}</span>
        </div>
        <div class="download-row">
          <a class="dl-btn" href={dlOgg} download>GET OGG</a>
          {#if dlMelody}<a class="dl-btn" href={dlMelody} download>MELODY</a
            >{/if}
          {#if dlDrums}<a class="dl-btn" href={dlDrums} download>DRUMS</a>{/if}
          {#if dlBass}<a class="dl-btn" href={dlBass} download>BASS</a>{/if}
          {#if dlChords}<a class="dl-btn" href={dlChords} download>CHORDS</a
            >{/if}
        </div>
        <div class="mode-row">
          <button
            class="mode-btn"
            class:active={shuffleOn}
            on:click={toggleShuffle}>&#x1F500; SHUFFLE</button
          >
          <button class="mode-btn" class:active={loopOn} on:click={toggleLoop}
            >&#x1F501; LOOP</button
          >
        </div>
      </div>
    </div>
  </div>

  <div class="bottom-marquee">
    <marquee scrollamount="5" direction="right">
      &#127911; SINE WAVES ARE THE NEW VINYL &#127911; DOWNLOAD MIDI &#127911;
      LOAD INTO YOUR DAW &#127911; MAKE IT SLAP &#127911; THANK THE ROBOTS
      &#127911;
    </marquee>
  </div>
</div>

<style>
  .pukebox {
    background-color: #000033;
    background-image:
      radial-gradient(
        circle at 20% 50%,
        rgba(255, 0, 128, 0.08) 0%,
        transparent 50%
      ),
      radial-gradient(
        circle at 80% 50%,
        rgba(0, 255, 255, 0.08) 0%,
        transparent 50%
      );
    color: #00ff00;
    font-family: 'Comic Sans MS', 'Chalkboard SE', cursive;
    min-height: 100vh;
    overflow-x: hidden;
  }

  .top-marquees {
    padding: 8px 0;
    background: linear-gradient(90deg, #ff00ff, #00ffff, #ffff00, #ff00ff);
    background-size: 300% 100%;
    animation: rainbow-scroll 3s linear infinite;
  }
  @keyframes rainbow-scroll {
    0% {
      background-position: 0% 0;
    }
    100% {
      background-position: 300% 0;
    }
  }
  .top-marquees marquee {
    font-size: 1.1rem;
    font-weight: bold;
    color: #000;
    text-shadow: 1px 1px 0 rgba(255, 255, 255, 0.5);
  }

  .page-title {
    text-align: center;
    padding: 20px 10px 5px;
  }
  .page-title h1 {
    font-family: 'Impact', sans-serif;
    font-size: clamp(2rem, 6vw, 4rem);
    color: #ff00ff;
    text-shadow:
      3px 3px 0 #00ffff,
      -2px -2px 0 #ffff00,
      0 0 20px #ff00ff,
      0 0 40px #ff00ff;
    letter-spacing: 0.05em;
  }
  .page-title .subtitle {
    color: #ffff00;
    font-size: 1rem;
    margin-top: 4px;
  }

  .jukebox-wrapper {
    display: flex;
    justify-content: center;
    padding: 40px 0 20px;
  }
  .jukebox-container {
    position: relative;
    width: min(85vw, 500px);
    aspect-ratio: 1;
    border: 4px solid #8b6914;
    border-radius: 12px;
    background: linear-gradient(180deg, #2a1a0a 0%, #1a0a05 100%);
    box-shadow:
      0 0 40px rgba(0, 150, 255, 0.3),
      0 0 80px rgba(255, 0, 128, 0.15);
    display: flex;
    flex-direction: column;
  }

  .overlay-zone {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }

  #marquee-zone {
    flex: 0 0 12%;
    width: 90%;
    margin-top: 4%;
    background: rgba(30, 15, 0, 0.85);
    border: 1px solid #8b6914;
    box-shadow: inset 0 0 15px rgba(200, 150, 0, 0.3);
  }
  #marquee-zone marquee {
    color: #ffb000;
    font-family: 'Courier New', monospace;
    font-size: clamp(0.55rem, 1.4vw, 0.85rem);
    font-weight: bold;
    text-shadow: 0 0 8px #ffb000;
    white-space: nowrap;
  }

  #flipper-zone {
    flex: 1;
    width: 90%;
    margin: 4% 0;
    background: rgba(10, 5, 20, 0.88);
    border: 2px solid #3a2a1a;
    display: flex;
    flex-direction: column;
  }
  .flipper-nav {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    flex: 1;
    min-height: 0;
  }
  .flip-btn {
    background: linear-gradient(180deg, #8b6914 0%, #5a4510 100%);
    border: 2px outset #aa8830;
    color: #ffb000;
    font-size: clamp(0.8rem, 2vw, 1.2rem);
    cursor: pointer;
    padding: 2px 8px;
    font-family: 'Courier New', monospace;
    text-shadow: 0 0 5px #ffb000;
    flex-shrink: 0;
  }
  .flip-btn:hover {
    background: linear-gradient(180deg, #aa8830 0%, #8b6914 100%);
  }
  .flip-btn:active {
    border-style: inset;
  }
  .card-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    padding: 2px 5px;
    min-height: 0;
    overflow: hidden;
    cursor: pointer;
  }
  :global(.card-date) {
    color: #00ffff;
    font-family: 'Courier New', monospace;
    font-size: clamp(0.55rem, 1.3vw, 0.8rem);
    letter-spacing: 0.15em;
    font-weight: bold;
  }
  :global(.card-scale) {
    color: #ff00ff;
    font-size: clamp(0.5rem, 1.2vw, 0.75rem);
    margin: 1px 0;
    font-weight: bold;
  }
  :global(.card-teaser) {
    color: #aaa;
    font-size: clamp(0.45rem, 1vw, 0.65rem);
    font-style: italic;
    line-height: 1.2;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }
  :global(.card-counter) {
    color: #666;
    font-family: 'Courier New', monospace;
    font-size: clamp(0.4rem, 0.9vw, 0.55rem);
    margin-top: 1px;
  }
  .loading-msg {
    color: #ffb000;
    font-family: 'Courier New', monospace;
    text-align: center;
  }

  #controls-zone {
    flex: 0 0 20%;
    width: 90%;
    margin-bottom: 4%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
  }
  .player-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .play-btn {
    background: linear-gradient(180deg, #444 0%, #222 100%);
    border: 2px outset #666;
    color: #00ff00;
    font-size: clamp(0.8rem, 2vw, 1.2rem);
    cursor: pointer;
    width: clamp(28px, 5vw, 38px);
    height: clamp(22px, 4vw, 30px);
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: monospace;
  }
  .play-btn:active {
    border-style: inset;
  }
  .progress-bar {
    width: clamp(60px, 12vw, 120px);
    height: clamp(8px, 1.5vw, 14px);
    background: #111;
    border: 1px solid #444;
    overflow: hidden;
    cursor: pointer;
  }
  .progress-fill {
    height: 100%;
    background: #00ff00;
    transition: width 0.3s;
  }
  .track-time {
    color: #00ff00;
    font-family: 'Courier New', monospace;
    font-size: clamp(0.4rem, 1vw, 0.6rem);
  }
  .download-row {
    display: flex;
    gap: 4px;
  }
  .dl-btn {
    background: linear-gradient(180deg, #333 0%, #111 100%);
    border: 1px outset #555;
    color: #ffff00;
    font-family: 'Courier New', monospace;
    font-size: clamp(0.4rem, 0.9vw, 0.55rem);
    cursor: pointer;
    padding: 1px 5px;
    text-decoration: none;
    white-space: nowrap;
  }
  .dl-btn:hover {
    color: #fff;
    background: #444;
  }
  .mode-row {
    display: flex;
    gap: 4px;
  }
  .mode-btn {
    background: linear-gradient(180deg, #333 0%, #111 100%);
    border: 1px outset #555;
    color: #888;
    font-family: 'Courier New', monospace;
    font-size: clamp(0.4rem, 0.9vw, 0.55rem);
    cursor: pointer;
    padding: 1px 5px;
    white-space: nowrap;
  }
  .mode-btn:hover {
    color: #fff;
    background: #444;
  }
  .mode-btn.active {
    color: #00ff00;
    border-color: #00ff00;
    text-shadow: 0 0 5px #00ff00;
  }

  .bottom-marquee {
    background: linear-gradient(90deg, #00ffff, #ff00ff, #ffff00, #00ffff);
    background-size: 300% 100%;
    animation: rainbow-scroll 4s linear infinite;
    padding: 4px 0;
  }
  .bottom-marquee marquee {
    color: #000;
    font-weight: bold;
    font-size: 0.9rem;
  }
</style>
