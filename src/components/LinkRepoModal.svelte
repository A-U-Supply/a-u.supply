<!--
  LinkRepoModal — link a GitHub repo to a Latent. Accepts a clone/blob URL
  and an optional PAT chosen from the user's stored tokens. After link, an
  initial sync runs server-side.
-->
<script lang="ts">
  import { portal } from '../lib/portal.ts';

  type Props = {
    open: boolean;
    projectId: string;
    onLinked?: () => void;
    onClose?: () => void;
  };

  let { open = $bindable(), projectId, onLinked, onClose }: Props = $props();

  type TokenRow = {
    id: string;
    label: string;
    github_login: string | null;
  };

  let url = $state('');
  let branch = $state('');
  let tokenId = $state('');
  let tokens = $state<TokenRow[]>([]);
  let submitting = $state(false);
  let error = $state<string | null>(null);

  async function loadTokens() {
    try {
      const res = await fetch('/api/github/tokens', { credentials: 'include' });
      if (!res.ok) return;
      const body = await res.json();
      tokens = body.tokens || [];
    } catch {}
  }

  function close() {
    open = false;
    onClose?.();
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') close();
  }

  async function submit() {
    if (!url.trim() || submitting) return;
    submitting = true;
    error = null;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/repo`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: url.trim(),
            default_branch: branch.trim() || null,
            github_token_id: tokenId || null,
          }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      url = '';
      branch = '';
      tokenId = '';
      open = false;
      onLinked?.();
      onClose?.();
    } catch (e: any) {
      error = e?.message || 'Failed to link';
    } finally {
      submitting = false;
    }
  }

  $effect(() => {
    if (open) loadTokens();
  });

  // Lock the page behind the modal — see PullFromIndex for the same pattern.
  $effect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  });
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <!--
    Portaled to <body>: LatentRepoStrip renders in the FIRST .latent-section,
    and every section below it paints over this at any z-index. See
    src/lib/portal.ts.
  -->
  <div use:portal class="overlay" onclick={close} role="presentation">
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-label="Link GitHub repo"
      onclick={(e) => e.stopPropagation()}
    >
      <header class="modal__head">
        <h2>Link GitHub repo</h2>
        <button class="action-btn" type="button" onclick={close}>×</button>
      </header>

      <form
        class="form"
        onsubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label class="field">
          <span class="field__label">Repo URL</span>
          <input
            type="text"
            bind:value={url}
            placeholder="https://github.com/A-U-Supply/regular-expression"
            required
          />
        </label>
        <label class="field">
          <span class="field__label">Default branch (optional)</span>
          <input type="text" bind:value={branch} placeholder="main" />
        </label>
        <label class="field">
          <span class="field__label">Personal access token (optional)</span>
          <select bind:value={tokenId}>
            <option value="">none (public repos only)</option>
            {#each tokens as t (t.id)}
              <option value={t.id}
                >{t.label}{t.github_login
                  ? ` — @${t.github_login}`
                  : ''}</option
              >
            {/each}
          </select>
          <small class="hint"
            >Manage tokens in
            <a class="link" href="/admin/settings#github">Settings</a></small
          >
        </label>

        {#if error}
          <div class="notice notice--error">{error}</div>
        {/if}

        <div class="actions">
          <button class="btn-primary" type="submit" disabled={submitting}
            >{submitting ? 'Linking…' : 'Link repo'}</button
          >
          <button class="action-btn" type="button" onclick={close}
            >Cancel</button
          >
        </div>
      </form>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: var(--space-sm);
  }
  .modal {
    background: var(--color-bg);
    border: 2px solid var(--color-text);
    box-shadow: 4px 4px 0 var(--color-text);
    width: min(560px, 100%);
    padding: var(--space-md);
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .modal__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .modal__head h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .form {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .field__label {
    color: var(--color-muted);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .field input,
  .field select {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 6px 10px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .hint {
    color: var(--color-muted);
    font-size: 0.7rem;
  }
  .link {
    color: var(--color-accent);
  }
  .notice {
    padding: 6px 10px;
    border: 1px solid var(--color-border);
    font-size: var(--text-sm);
  }
  .notice--error {
    border-color: #c00;
    color: #c00;
  }
  .actions {
    display: flex;
    gap: var(--space-sm);
  }
</style>
