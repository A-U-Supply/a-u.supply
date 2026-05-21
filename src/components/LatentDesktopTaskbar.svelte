<script>
  let { latent, projectId, onreload } = $props();

  let menuOpen = $state(false);
  let saving = $state(false);
  let name = $derived(latent?.name ?? '');

  const STATUS_COLORS = {
    forming: '#4a9',
    developing: '#a84',
    fixing: '#48a',
    abandoned: '#666',
  };

  async function patchLatent(body) {
    saving = true;
    await fetch(`/api/latents/${projectId}`, {
      method: 'PATCH', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    saving = false;
  }

  async function setStatus(status) {
    await patchLatent({ status });
    onreload?.();
  }

  const NAV_LINKS = [
    { href: '/admin/dashboard', label: 'Auspices' },
    { href: '/admin/latents', label: 'Latents' },
    { href: '/admin/catalog', label: 'Releases' },
    { href: '/admin/search', label: 'The Stacks' },
    { href: '/admin/jobs', label: 'The Queue' },
    { href: '/admin/settings', label: 'Settings' },
  ];
</script>

<div class="taskbar">
  <!-- Left: nav menu -->
  <div class="taskbar__left">
    <button class="taskbar__menu-btn" onclick={() => menuOpen = !menuOpen} aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
    <a href="/admin/latents/{projectId}" class="taskbar__detail-link" title="Standard view">⊞</a>
  </div>

  <!-- Center: latent identity -->
  <div class="taskbar__center">
    <span class="taskbar__name">{name}</span>
    {#if latent?.kind}
      <span class="taskbar__pill">{latent.kind}</span>
    {/if}
    {#if latent?.status}
      <span class="taskbar__pill taskbar__pill--status" style="background:{STATUS_COLORS[latent.status] ?? '#555'}">
        {latent.status}
      </span>
    {/if}
  </div>

  <!-- Right: counts -->
  <div class="taskbar__right">
    {#if latent}
      <span class="taskbar__count">{latent.slot_count ?? 0} slots</span>
      <span class="taskbar__count">{latent.item_count ?? 0} files</span>
    {/if}
    {#if saving}<span class="taskbar__saving">saving…</span>{/if}
  </div>
</div>

<!-- Nav overlay -->
{#if menuOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="nav-overlay" onclick={() => menuOpen = false}>
    <div class="nav-drawer" onclick|stopPropagation>
      <div class="nav-drawer__header">A-U.SUPPLY</div>
      <ul class="nav-drawer__list">
        {#each NAV_LINKS as link}
          <li><a href={link.href} class="nav-drawer__link" onclick={() => menuOpen = false}>{link.label}</a></li>
        {/each}
      </ul>
      <div class="nav-drawer__footer">
        <a href="/admin/latents/{projectId}" class="nav-drawer__link">← Standard view</a>
      </div>
    </div>
  </div>
{/if}

<style>
  .taskbar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: 36px;
    background: rgba(10, 10, 10, 0.92);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid #333;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 12px;
    z-index: 7000;
    font-family: var(--font-mono, monospace);
    font-size: 12px;
    color: #ccc;
  }

  .taskbar__left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .taskbar__menu-btn {
    background: none;
    border: none;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 4px;
  }
  .taskbar__menu-btn span {
    display: block;
    width: 16px;
    height: 2px;
    background: #aaa;
    border-radius: 1px;
  }
  .taskbar__menu-btn:hover span { background: #fff; }

  .taskbar__detail-link {
    color: #666;
    text-decoration: none;
    font-size: 14px;
    padding: 2px 4px;
    border-radius: 3px;
  }
  .taskbar__detail-link:hover { color: #aaa; background: #222; }

  .taskbar__center {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-width: 0;
  }

  .taskbar__name {
    font-weight: 700;
    color: #eee;
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 200px;
  }

  .taskbar__pill {
    padding: 1px 7px;
    border-radius: 10px;
    background: #333;
    color: #aaa;
    font-size: 10px;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .taskbar__pill--status { color: #fff; }

  .taskbar__right {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }

  .taskbar__count { color: #555; font-size: 11px; }
  .taskbar__saving { color: #888; font-size: 11px; font-style: italic; }

  /* ── Nav drawer ─────────────────────────────────────────────────────────── */
  .nav-overlay {
    position: fixed;
    inset: 0;
    z-index: 9500;
    background: rgba(0,0,0,0.5);
  }

  .nav-drawer {
    position: absolute;
    top: 0;
    left: 0;
    bottom: 0;
    width: 220px;
    background: #111;
    border-right: 1px solid #333;
    display: flex;
    flex-direction: column;
    padding: 16px 0;
  }

  .nav-drawer__header {
    font-family: var(--font-mono, monospace);
    font-weight: 700;
    font-size: 13px;
    color: #888;
    padding: 0 16px 16px;
    border-bottom: 1px solid #222;
    margin-bottom: 8px;
    letter-spacing: 0.05em;
  }

  .nav-drawer__list {
    list-style: none;
    margin: 0;
    padding: 0;
    flex: 1;
    overflow-y: auto;
  }

  .nav-drawer__list li { margin: 0; }

  .nav-drawer__link {
    display: block;
    padding: 9px 16px;
    color: #aaa;
    text-decoration: none;
    font-family: var(--font-mono, monospace);
    font-size: 13px;
  }
  .nav-drawer__link:hover { background: #1e1e1e; color: #fff; }

  .nav-drawer__footer {
    padding: 12px 0 0;
    border-top: 1px solid #222;
    margin-top: 8px;
  }
</style>
