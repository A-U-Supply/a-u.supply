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

  function handleTouchStart(e) {
    touchStartY = e.touches[0].clientY;
  }

  function handleTouchEnd(e) {
    const dy = e.changedTouches[0].clientY - touchStartY;
    if (Math.abs(dy) > 30) {
      dy > 0 ? flipUp() : flipDown();
    }
  }

  function handleMousemove(e) {
    const dot = document.createElement('div');
    dot.className = 'trail-dot';
    const colors = ['#ff00ff', '#00ffff', '#ffff00', '#00ff00', '#ff6600'];
    const size = 4 + Math.random() * 8;
    dot.style.cssText = `
      left: ${e.clientX - size / 2}px;
      top: ${e.clientY - size / 2}px;
      width: ${size}px;
      height: ${size}px;
      background: ${colors[Math.floor(Math.random() * colors.length)]};
      opacity: 0.8;
    `;
    document.body.appendChild(dot);
    setTimeout(() => {
      dot.style.transition = 'opacity 0.5s';
      dot.style.opacity = '0';
      setTimeout(() => dot.remove(), 500);
    }, 100);
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
  let touchStartY = 0;
  let visitorCount = '0000000';

  // Visitor counter
  (function () {
    let count = parseInt(localStorage.getItem('pukebox-visits') || '0') + 1;
    localStorage.setItem('pukebox-visits', count);
    count += 8675309;
    visitorCount = count.toString().padStart(7, '0');
  })();

  loadManifest();
</script>

<svelte:window on:keydown={handleKeydown} on:mousemove={handleMousemove} />

<div class="pukebox-root">
  <a href="/" class="home-link">&laquo; HOME</a>

  <!-- Top rainbow marquee -->
  <div class="top-marquees">
    <marquee scrollamount="4">
      &#127925; WELCOME TO THE PUKE BOX &#127925; YOUR #1 SOURCE FOR
      AI-GENERATED MIDI SLOP &#127925; INSERT COIN TO CONTINUE &#127925; NOW
      WITH MORE SINE WAVES &#127925; JUKEBOX HITS FROM THE FUTURE &#127925; 100%
      ROBOT-COMPOSED &#127925;
    </marquee>
  </div>

  <!-- Scattered blinking text -->
  <span class="scatter-text blink" style="top: 15%; left: 3%; color: #ff00ff;"
    >NOW PLAYING</span
  >
  <span
    class="scatter-text blink-slow"
    style="top: 35%; right: 3%; color: #00ffff;">HOT TRACKS</span
  >
  <span class="scatter-text blink" style="top: 65%; left: 2%; color: #ffff00;"
    >COOL TUNEZ</span
  >
  <span
    class="scatter-text blink-slow"
    style="bottom: 20%; right: 4%; color: #00ff00;">MIDI 4 EVER</span
  >
  <span class="scatter-text blink" style="top: 80%; left: 5%; color: #ff6600;"
    >ROBO JAM</span
  >

  <!-- Title -->
  <div class="page-title">
    <h1>PUKE BOX</h1>
    <div class="subtitle blink-slow">
      &#9834; AI-generated MIDI jukebox from the year 3000 &#9834;
    </div>
  </div>

  <!-- Side marquees -->
  <div class="side-marquee left">
    <marquee
      scrollamount="2"
      direction="up"
      style="color: #ff00ff; height: 300px;"
    >
      &#9733; PLAY &#9733; THAT &#9733; FUNKY &#9733; MIDI &#9733; ROBOT &#9733;
      BOY &#9733;
    </marquee>
  </div>
  <div class="side-marquee right">
    <marquee
      scrollamount="2"
      direction="down"
      style="color: #00ffff; height: 300px;"
    >
      &#9733; BEEP &#9733; BOOP &#9733; SINE &#9733; WAVE &#9733; CITY &#9733;
    </marquee>
  </div>

  <!-- Jukebox -->
  <div class="jukebox-wrapper">
    <div class="jukebox-container">
      <img src="/assets/puke-box.png" alt="THE PUKE BOX" />

      <!-- Overlay: amber marquee display -->
      <div class="overlay-zone" id="marquee-zone">
        <marquee scrollamount="2">{descriptionMarquee}</marquee>
      </div>

      <!-- Overlay: card flipper -->
      <div
        class="overlay-zone"
        id="flipper-zone"
        on:touchstart={handleTouchStart}
        on:touchend={handleTouchEnd}
      >
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

      <!-- Overlay: controls -->
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
            on:click={toggleShuffle}
            title="Shuffle">&#x1F500; SHUFFLE</button
          >
          <button
            class="mode-btn"
            class:active={loopOn}
            on:click={toggleLoop}
            title="Loop">&#x1F501; LOOP</button
          >
        </div>
      </div>
    </div>
  </div>

  <!-- Another marquee -->
  <marquee
    scrollamount="3"
    style="color: #ff6600; font-size: 0.9rem; padding: 5px 0;"
  >
    &#128165; EVERY TRACK IS GENERATED FRESH DAILY BY AN AI THAT READS THE NEWS
    AND FEELS THINGS &#128165; SCALES FROM AROUND THE WORLD &#127758; MAQAM
    &#8226; RAGA &#8226; BLUES &#8226; GAMELAN &#8226; KLEZMER &#8226; PELOG
    &#128165;
  </marquee>

  <!-- Visitor counter -->
  <div class="visitor-counter">
    <div>You are visitor number:</div>
    <div class="counter-box">{visitorCount}</div>
    <div style="margin-top: 6px; font-size: 0.7rem; color: #666;">
      best viewed with Netscape Navigator 4.0 at 800x600
    </div>
  </div>

  <!-- Bottom rainbow marquee -->
  <div class="bottom-marquee">
    <marquee scrollamount="5" direction="right">
      &#127911; SINE WAVES ARE THE NEW VINYL &#127911; DOWNLOAD MIDI &#127911;
      LOAD INTO YOUR DAW &#127911; MAKE IT SLAP &#127911; THANK THE ROBOTS
      &#127911;
    </marquee>
  </div>
</div>

<style>
  .pukebox-root {
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
      ),
      url("data:image/svg+xml,%3Csvg width='40' height='40' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M20 5 L25 15 L35 15 L27 22 L30 32 L20 26 L10 32 L13 22 L5 15 L15 15Z' fill='none' stroke='%23220044' stroke-width='0.5'/%3E%3C/svg%3E");
    color: #00ff00;
    font-family: 'Comic Sans MS', 'Chalkboard SE', cursive;
    min-height: 100vh;
    overflow-x: hidden;
    cursor:
      url("data:image/svg+xml,%3Csvg width='32' height='32' xmlns='http://www.w3.org/2000/svg'%3E%3Ccircle cx='16' cy='16' r='8' fill='%23ff00ff' opacity='0.7'/%3E%3C/svg%3E")
        16 16,
      auto;
  }

  .home-link {
    position: fixed;
    top: 70px;
    left: 10px;
    z-index: 10;
    color: #ff00ff;
    font-family: 'Courier New', monospace;
    font-size: 1.3rem;
    text-decoration: none;
    text-shadow: 0 0 8px #ff00ff;
  }
  .home-link:hover {
    color: #00ffff;
  }

  /* === MARQUEES === */
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
  .top-marquees :global(marquee) {
    font-size: 1.1rem;
    font-weight: bold;
    color: #000;
    text-shadow: 1px 1px 0 rgba(255, 255, 255, 0.5);
  }

  /* === BLINK === */
  @keyframes blink-neon {
    0%,
    49% {
      opacity: 1;
    }
    50%,
    100% {
      opacity: 0;
    }
  }
  @keyframes blink-slow {
    0%,
    69% {
      opacity: 1;
    }
    70%,
    100% {
      opacity: 0.2;
    }
  }
  .blink {
    animation: blink-neon 0.8s infinite;
  }
  .blink-slow {
    animation: blink-slow 1.5s infinite;
  }

  .scatter-text {
    position: fixed;
    font-size: 1rem;
    font-weight: bold;
    z-index: 2;
    pointer-events: none;
    text-shadow: 0 0 10px currentColor;
  }

  /* === TITLE === */
  .page-title {
    text-align: center;
    padding: 20px 10px 5px;
  }
  .page-title h1 {
    font-family: 'Bungee Shade', 'Impact', sans-serif;
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

  /* === JUKEBOX CONTAINER === */
  .jukebox-wrapper {
    display: flex;
    justify-content: center;
    padding: 140px 0 20px;
    position: relative;
  }
  .jukebox-container {
    position: relative;
    width: min(85vw, 500px);
    aspect-ratio: 1;
  }
  .jukebox-container img {
    width: 170%;
    height: 170%;
    object-fit: contain;
    display: block;
    position: absolute;
    top: -35%;
    left: -35%;
    pointer-events: none;
    filter: drop-shadow(0 0 30px rgba(0, 150, 255, 0.4))
      drop-shadow(0 0 60px rgba(255, 0, 128, 0.2));
  }

  /* === OVERLAY ZONES === */
  .overlay-zone {
    position: absolute;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }

  #marquee-zone {
    top: 24%;
    left: 40.25%;
    width: 37%;
    height: 9%;
    background: rgba(30, 15, 0, 0.85);
    border: 1px solid #8b6914;
    box-shadow: inset 0 0 15px rgba(200, 150, 0, 0.3);
  }
  #marquee-zone :global(marquee) {
    color: #ffb000;
    font-family: 'Courier New', monospace;
    font-size: clamp(0.55rem, 1.4vw, 0.85rem);
    font-weight: bold;
    text-shadow: 0 0 8px #ffb000;
    white-space: nowrap;
  }

  #flipper-zone {
    top: 42%;
    left: 36.25%;
    width: 44%;
    height: 21%;
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
    animation: blink-slow 1s infinite;
  }

  #controls-zone {
    top: 64%;
    left: 38.25%;
    width: 40%;
    height: 13%;
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
    width: 0%;
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

  /* === SIDE MARQUEES === */
  .side-marquee {
    position: fixed;
    z-index: 5;
    pointer-events: none;
  }
  .side-marquee.left {
    left: 5px;
    top: 50%;
    transform: rotate(-90deg) translateX(-50%);
    transform-origin: left center;
  }
  .side-marquee.right {
    right: 5px;
    top: 50%;
    transform: rotate(90deg) translateX(50%);
    transform-origin: right center;
  }
  .side-marquee :global(marquee) {
    font-size: 0.8rem;
    white-space: nowrap;
  }

  /* === VISITOR COUNTER === */
  .visitor-counter {
    text-align: center;
    padding: 15px 10px 30px;
    font-size: 0.85rem;
    color: #aaa;
  }
  .counter-box {
    display: inline-block;
    background: #000;
    border: 2px inset #555;
    padding: 3px 12px;
    font-family: 'Courier New', monospace;
    color: #00ff00;
    font-size: 1.1rem;
    letter-spacing: 3px;
    margin-top: 4px;
  }

  /* === BOTTOM MARQUEE === */
  .bottom-marquee {
    background: linear-gradient(90deg, #00ffff, #ff00ff, #ffff00, #00ffff);
    background-size: 300% 100%;
    animation: rainbow-scroll 4s linear infinite;
    padding: 4px 0;
  }
  .bottom-marquee :global(marquee) {
    color: #000;
    font-weight: bold;
    font-size: 0.9rem;
  }

  /* === CURSOR TRAIL === */
  :global(.trail-dot) {
    position: fixed;
    pointer-events: none;
    border-radius: 50%;
    z-index: 9999;
  }

  /* === RESPONSIVE === */
  @media (max-width: 500px) {
    .scatter-text {
      display: none;
    }
    .side-marquee {
      display: none;
    }
  }
</style>
