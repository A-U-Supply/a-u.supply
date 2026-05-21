<script>
  import { onMount, onDestroy } from 'svelte';

  let { ondismiss } = $props();

  // Randomly pick a mode each time the screensaver activates
  const MODE = Math.random() < 0.5 ? 'bounce' : 'toasters';

  // ── Bouncing logo state ────────────────────────────────────────────────────
  let bx = $state(20);
  let by = $state(20);
  let vx = $state(0.4);
  let vy = $state(0.3);
  let hue = $state(200);
  let rafId = null;
  let containerEl = $state(null);

  const LOGO_W = 120; // px approx
  const LOGO_H = 36;

  function bounceTick() {
    if (!containerEl) { rafId = requestAnimationFrame(bounceTick); return; }
    const W = containerEl.clientWidth;
    const H = containerEl.clientHeight;
    const maxX = 100 - (LOGO_W / W * 100);
    const maxY = 100 - (LOGO_H / H * 100);

    bx += vx;
    by += vy;
    if (bx <= 0 || bx >= maxX) { vx = -vx; hue = (hue + 40) % 360; bx = Math.max(0, Math.min(maxX, bx)); }
    if (by <= 0 || by >= maxY) { vy = -vy; hue = (hue + 40) % 360; by = Math.max(0, Math.min(maxY, by)); }
    rafId = requestAnimationFrame(bounceTick);
  }

  // ── Toasters state ─────────────────────────────────────────────────────────
  let toasters = $state([]);
  let toasterTimer = null;

  const TOASTER_ICONS = ['📼', '💿', '📀', '🎵', '🎶'];

  function spawnToaster() {
    const icon = TOASTER_ICONS[Math.floor(Math.random() * TOASTER_ICONS.length)];
    const id = Math.random();
    const startY = Math.random() * 80;
    const speed = 2 + Math.random() * 3; // seconds to cross screen
    toasters = [...toasters, { id, icon, startY, speed }];
    // Remove after animation finishes
    setTimeout(() => {
      toasters = toasters.filter(t => t.id !== id);
    }, (speed + 1) * 1000);
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────
  onMount(() => {
    if (MODE === 'bounce') {
      rafId = requestAnimationFrame(bounceTick);
    } else {
      spawnToaster();
      toasterTimer = setInterval(spawnToaster, 800);
    }
  });

  onDestroy(() => {
    if (rafId) cancelAnimationFrame(rafId);
    if (toasterTimer) clearInterval(toasterTimer);
  });
</script>

<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div
  class="screensaver"
  bind:this={containerEl}
  onclick={ondismiss}
  ontouchstart={ondismiss}
>
  {#if MODE === 'bounce'}
    <div
      class="bounce-logo"
      style="left:{bx}%; top:{by}%; color: hsl({hue}, 80%, 65%)"
    >
      A-U.SUPPLY
    </div>
    <p class="screensaver__hint">Click anywhere to continue</p>

  {:else}
    <!-- Flying toasters (cassettes / records with wings) -->
    {#each toasters as t (t.id)}
      <div
        class="toaster"
        style="top:{t.startY}vh; animation-duration:{t.speed}s"
      >
        <span class="toaster__wing toaster__wing--left">🪽</span>
        <span class="toaster__icon">{t.icon}</span>
        <span class="toaster__wing toaster__wing--right">🪽</span>
      </div>
    {/each}
    <p class="screensaver__hint">Click anywhere to continue</p>
  {/if}
</div>

<style>
  .screensaver {
    position: fixed;
    inset: 0;
    z-index: 9999;
    background: #000;
    overflow: hidden;
    cursor: none;
  }

  /* ── Bounce mode ─────────────────────────────────────────────────────────── */
  .bounce-logo {
    position: absolute;
    font-family: var(--font-mono, monospace);
    font-size: 2rem;
    font-weight: 900;
    letter-spacing: 0.05em;
    white-space: nowrap;
    transition: color 0.4s;
    pointer-events: none;
  }

  /* ── Toaster mode ────────────────────────────────────────────────────────── */
  .toaster {
    position: absolute;
    left: 110vw;
    display: flex;
    align-items: center;
    gap: 2px;
    animation: fly-left linear forwards;
    font-size: 2rem;
    pointer-events: none;
  }

  @keyframes fly-left {
    from { left: 110vw; }
    to   { left: -20vw; }
  }

  .toaster__wing { font-size: 1.2rem; }
  .toaster__wing--left { transform: scaleX(-1); }

  /* ── Hint ─────────────────────────────────────────────────────────────────── */
  .screensaver__hint {
    position: absolute;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    color: #333;
    font-family: var(--font-mono, monospace);
    font-size: 12px;
    pointer-events: none;
  }
</style>
