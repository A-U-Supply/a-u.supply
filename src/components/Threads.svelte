<!--
  Threads — generic discussion view for any anchor (project / slot / media_item).

  Talks only to /api/threads (which proxies to Lemmy). Never to fold directly.

  Props:
    - anchorType: 'project' | 'slot' | 'media_item'
    - anchorId: the id of the anchored entity
    - title: optional section heading (defaults to "Threads")
-->
<script lang="ts">
  type Props = {
    anchorType: 'project' | 'slot' | 'media_item';
    anchorId: string;
    title?: string;
  };

  let { anchorType, anchorId, title = 'Threads' }: Props = $props();

  type ThreadRow = {
    id: string;
    anchor_type: string;
    anchor_id: string;
    lemmy_post_id: number;
    lemmy_community_id: number;
    created_by: number;
    created_at: string | null;
    title?: string;
    body?: string | null;
    url?: string | null;
    published?: string | null;
  };

  type CommentRow = {
    id: number;
    content: string;
    creator_id: number;
    parent_id: number | null;
    path: string;
    published: string | null;
    deleted: boolean;
  };

  let threads = $state<ThreadRow[]>([]);
  let lemmyAvailable = $state(true);
  let loading = $state(false);
  let error = $state<string | null>(null);

  let composerOpen = $state(false);
  let composerTitle = $state('');
  let composerBody = $state('');
  let composerUrl = $state('');
  let posting = $state(false);

  let expandedId = $state<string | null>(null);
  let expandedComments = $state<CommentRow[]>([]);
  let expandedPost = $state<ThreadRow | null>(null);
  let replyBody = $state('');
  let replyParent = $state<number | null>(null);

  async function load() {
    loading = true;
    error = null;
    try {
      const res = await fetch(
        `/api/threads?anchor_type=${encodeURIComponent(anchorType)}&anchor_id=${encodeURIComponent(anchorId)}`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed to load (${res.status})`);
      const body = await res.json();
      threads = body.threads || [];
      lemmyAvailable = body.lemmy_available !== false;
    } catch (e: any) {
      error = e?.message || 'Failed to load threads';
    } finally {
      loading = false;
    }
  }

  async function createThread() {
    if (!composerTitle.trim() || posting) return;
    posting = true;
    try {
      const res = await fetch('/api/threads', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          anchor_type: anchorType,
          anchor_id: anchorId,
          title: composerTitle.trim(),
          body: composerBody || null,
          url: composerUrl || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || `Failed (${res.status})`);
      }
      composerOpen = false;
      composerTitle = '';
      composerBody = '';
      composerUrl = '';
      await load();
    } catch (e: any) {
      error = e?.message || 'Failed to create thread';
    } finally {
      posting = false;
    }
  }

  async function expand(t: ThreadRow) {
    if (expandedId === t.id) {
      expandedId = null;
      expandedComments = [];
      expandedPost = null;
      return;
    }
    expandedId = t.id;
    expandedPost = t;
    expandedComments = [];
    replyParent = null;
    try {
      const res = await fetch(`/api/threads/${encodeURIComponent(t.id)}`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      expandedComments = body.comments || [];
      // Update merged thread record with latest title/body
      const idx = threads.findIndex((x) => x.id === t.id);
      if (idx >= 0) {
        threads[idx] = {
          ...threads[idx],
          title: body.title,
          body: body.body,
          url: body.url,
        };
        threads = [...threads];
      }
    } catch (e: any) {
      error = e?.message || 'Failed to load thread';
    }
  }

  async function postReply() {
    if (!expandedId || !replyBody.trim()) return;
    try {
      const res = await fetch(
        `/api/threads/${encodeURIComponent(expandedId)}/comments`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            body: replyBody.trim(),
            parent_comment_id: replyParent,
          }),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || `Failed (${res.status})`);
      }
      replyBody = '';
      replyParent = null;
      const t = threads.find((x) => x.id === expandedId);
      if (t) await expand(t);
    } catch (e: any) {
      error = e?.message || 'Failed to post reply';
    }
  }

  function fmtTime(iso: string | null | undefined): string {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  function nestComments(
    flat: CommentRow[],
  ): { c: CommentRow; depth: number }[] {
    // Lemmy comment.path = "0.<root>.<child>...". Depth = path segments - 2 (root and "0").
    const out: { c: CommentRow; depth: number }[] = [];
    const byId: Record<number, CommentRow> = {};
    for (const c of flat) byId[c.id] = c;
    const sorted = [...flat].sort((a, b) => a.path.localeCompare(b.path));
    for (const c of sorted) {
      const parts = c.path.split('.').filter(Boolean);
      const depth = Math.max(0, parts.length - 2);
      out.push({ c, depth });
    }
    return out;
  }

  let nested = $derived(nestComments(expandedComments));

  $effect(() => {
    if (anchorId) load();
  });
</script>

<section class="threads">
  <header class="threads__head">
    <h2>{title}</h2>
    <button
      class="btn"
      onclick={() => (composerOpen = !composerOpen)}
      type="button"
      disabled={!lemmyAvailable}
      >{composerOpen ? 'Cancel' : '+ New thread'}</button
    >
  </header>

  {#if !lemmyAvailable}
    <div class="warn">Discussion temporarily unavailable.</div>
  {/if}

  {#if error}
    <div class="error">{error}</div>
  {/if}

  {#if composerOpen}
    <form
      class="composer"
      onsubmit={(e) => {
        e.preventDefault();
        createThread();
      }}
    >
      <input
        type="text"
        placeholder="Thread title"
        bind:value={composerTitle}
        required
      />
      <textarea
        rows="3"
        placeholder="Body (markdown, optional)"
        bind:value={composerBody}
      ></textarea>
      <input
        type="url"
        placeholder="Link (paste a SoundCloud / Drive / YouTube URL for a link post — optional)"
        bind:value={composerUrl}
      />
      <div class="composer__actions">
        <button class="btn btn--primary" type="submit" disabled={posting}
          >{posting ? 'Posting…' : 'Post thread'}</button
        >
      </div>
    </form>
  {/if}

  {#if loading && threads.length === 0}
    <div class="muted">Loading threads…</div>
  {:else if threads.length === 0}
    <div class="muted">No threads yet.</div>
  {:else}
    <ul class="thread-list">
      {#each threads as t (t.id)}
        <li class="thread" class:expanded={expandedId === t.id}>
          <button class="thread__head" onclick={() => expand(t)} type="button">
            <span class="thread__title">{t.title || '(untitled)'}</span>
            <span class="thread__meta">{fmtTime(t.created_at)}</span>
          </button>
          {#if expandedId === t.id}
            <div class="thread__body">
              {#if t.url}
                <a class="link-card" href={t.url} target="_blank" rel="noopener"
                  >{t.url}</a
                >
              {/if}
              {#if t.body}
                <div class="markdown">{t.body}</div>
              {/if}
              <ul class="comments">
                {#each nested as { c, depth } (c.id)}
                  <li
                    class="comment"
                    style="margin-left: {Math.min(depth, 6) * 18}px"
                  >
                    <div class="comment__meta">
                      <span>user #{c.creator_id}</span>
                      <span>{fmtTime(c.published)}</span>
                      <button
                        class="link"
                        type="button"
                        onclick={() => (replyParent = c.id)}>Reply</button
                      >
                    </div>
                    <div class="comment__body">
                      {c.deleted ? '(deleted)' : c.content}
                    </div>
                  </li>
                {/each}
              </ul>
              <form
                class="reply"
                onsubmit={(e) => {
                  e.preventDefault();
                  postReply();
                }}
              >
                {#if replyParent}
                  <div class="muted">
                    Replying to comment #{replyParent}
                    <button
                      class="link"
                      type="button"
                      onclick={() => (replyParent = null)}>cancel</button
                    >
                  </div>
                {/if}
                <textarea
                  rows="2"
                  placeholder="Write a reply…"
                  bind:value={replyBody}
                ></textarea>
                <button class="btn" type="submit" disabled={!replyBody.trim()}
                  >Reply</button
                >
              </form>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .threads {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 0.5rem);
  }
  .threads__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .threads__head h2 {
    margin: 0;
  }
  .btn {
    padding: 6px 12px;
    background: #1a1a1a;
    color: #fff;
    border: 2px solid var(--color-border, #333);
    box-shadow: 2px 2px 0 #000;
    font-family: var(--font-mono, monospace);
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1pt;
    font-size: var(--text-sm, 0.85rem);
    cursor: pointer;
  }
  .btn--primary {
    background: var(--color-accent, #b8860b);
  }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .composer {
    display: flex;
    flex-direction: column;
    gap: 6px;
    border: 2px solid var(--color-border, #333);
    padding: 10px;
    background: rgba(255, 255, 255, 0.02);
  }
  .composer input,
  .composer textarea {
    background: var(--color-bg-input, #111);
    color: inherit;
    border: 2px solid var(--color-border, #333);
    padding: 6px 10px;
    font-family: inherit;
  }
  .composer__actions {
    display: flex;
    justify-content: flex-end;
  }
  .thread-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .thread {
    border: 2px solid var(--color-border, #333);
    background: rgba(255, 255, 255, 0.02);
  }
  .thread__head {
    width: 100%;
    background: transparent;
    border: 0;
    color: inherit;
    text-align: left;
    padding: 10px 12px;
    display: flex;
    justify-content: space-between;
    gap: 12px;
    cursor: pointer;
    font: inherit;
  }
  .thread.expanded .thread__head {
    border-bottom: 1px dashed var(--color-border, #333);
  }
  .thread__title {
    font-weight: bold;
  }
  .thread__meta {
    color: var(--color-muted, #888);
    font-size: var(--text-sm, 0.85rem);
  }
  .thread__body {
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .link-card {
    display: inline-block;
    padding: 6px 10px;
    border: 2px solid var(--color-border, #333);
    background: rgba(0, 0, 0, 0.3);
    word-break: break-all;
  }
  .markdown {
    white-space: pre-wrap;
  }
  .comments {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .comment {
    border-left: 2px solid var(--color-border, #333);
    padding: 4px 8px;
  }
  .comment__meta {
    color: var(--color-muted, #888);
    font-size: var(--text-sm, 0.85rem);
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .comment__body {
    white-space: pre-wrap;
  }
  .reply {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .reply textarea {
    background: var(--color-bg-input, #111);
    color: inherit;
    border: 2px solid var(--color-border, #333);
    padding: 6px 10px;
    font-family: inherit;
  }
  .muted {
    color: var(--color-muted, #888);
    font-size: var(--text-sm, 0.85rem);
  }
  .warn {
    padding: 8px 10px;
    border: 2px solid #d97706;
    color: #fbbf24;
    font-size: var(--text-sm, 0.85rem);
  }
  .error {
    padding: 8px 10px;
    border: 2px solid #ef4444;
    color: #fca5a5;
    font-size: var(--text-sm, 0.85rem);
  }
  .link {
    background: transparent;
    border: 0;
    color: var(--color-accent, #b8860b);
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
    font: inherit;
  }
</style>
