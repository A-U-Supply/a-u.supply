<!--
  Threads — discussion view for any anchor (project / slot / media_item).

  Talks only to /api/threads (which proxies to Lemmy).

  Props:
    - anchorType: 'project' | 'slot' | 'media_item'
    - anchorId: the id of the anchored entity
    - title: optional section heading (defaults to "Threads")
    - compact: render without the heading row (slot panels)
    - singleThread: collapse to "one canonical thread" mode (media_item).
        Renders the (single) thread inline always-open. Empty state shows a
        comment composer that auto-creates the thread on submit.
    - defaultTitle: title used when auto-creating in singleThread mode.
        Falls back to "Untitled discussion".
    - currentUserId: local user id, used to gate the edit/delete affordances.
-->
<script lang="ts">
  type Props = {
    anchorType: 'project' | 'slot' | 'media_item';
    anchorId: string;
    title?: string;
    compact?: boolean;
    singleThread?: boolean;
    defaultTitle?: string;
    currentUserId?: number | null;
  };

  let {
    anchorType,
    anchorId,
    title = 'Threads',
    compact = false,
    singleThread = false,
    defaultTitle = 'Untitled discussion',
    currentUserId = null,
  }: Props = $props();

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
    lemmy_url?: string | null;
    community_name?: string | null;
    community_url?: string | null;
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
  let lemmyConfigured = $state(true);
  let lemmyLinked = $state(true);
  let lemmyUrl = $state<string | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  let composerOpen = $state(false);
  let composerTitle = $state('');
  let composerBody = $state('');
  let composerUrl = $state('');
  let posting = $state(false);

  let expandedId = $state<string | null>(null);
  let expandedComments = $state<CommentRow[]>([]);
  let replyBody = $state('');
  let replyParent = $state<number | null>(null);

  let singleComments = $state<CommentRow[]>([]);
  let singleReply = $state('');
  let singlePosting = $state(false);

  let editingTitle = $state(false);
  let editTitleDraft = $state('');
  let savingTitle = $state(false);

  let useCompactHeader = $derived(compact || singleThread);

  let primaryThread = $derived(
    singleThread && threads.length > 0
      ? [...threads].sort((a, b) =>
          (a.created_at || '').localeCompare(b.created_at || ''),
        )[0]
      : null,
  );

  let canEditPrimary = $derived(
    !!primaryThread &&
      currentUserId != null &&
      primaryThread.created_by === currentUserId,
  );

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
      lemmyConfigured = body.configured !== false;
      lemmyLinked = body.linked !== false;
      lemmyUrl = body.lemmy_url || null;
      if (singleThread && primaryThread) {
        await loadSingleComments();
      }
    } catch (e: any) {
      error = e?.message || 'Failed to load threads';
    } finally {
      loading = false;
    }
  }

  async function loadSingleComments() {
    if (!primaryThread) return;
    try {
      const res = await fetch(
        `/api/threads/${encodeURIComponent(primaryThread.id)}`,
        { credentials: 'include' },
      );
      if (!res.ok) return;
      const body = await res.json();
      singleComments = body.comments || [];
      const idx = threads.findIndex((x) => x.id === primaryThread!.id);
      if (idx >= 0) {
        threads[idx] = {
          ...threads[idx],
          title: body.title ?? threads[idx].title,
          body: body.body ?? threads[idx].body,
          url: body.url ?? threads[idx].url,
        };
        threads = [...threads];
      }
    } catch {
      // Silent — comments are nice to have
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
        throw new Error(
          err?.detail?.message || err?.detail || `Failed (${res.status})`,
        );
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

  async function postSingleReply() {
    if (!singleReply.trim() || singlePosting) return;
    singlePosting = true;
    try {
      let thread = primaryThread;
      if (!thread) {
        const createRes = await fetch('/api/threads', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            anchor_type: anchorType,
            anchor_id: anchorId,
            title: defaultTitle,
          }),
        });
        if (!createRes.ok) {
          const err = await createRes.json().catch(() => ({}));
          throw new Error(
            err?.detail?.message || err?.detail || 'Failed to start discussion',
          );
        }
        thread = (await createRes.json()) as ThreadRow;
        threads = [thread, ...threads];
      }
      const commentRes = await fetch(
        `/api/threads/${encodeURIComponent(thread.id)}/comments`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            body: singleReply.trim(),
            parent_comment_id: null,
          }),
        },
      );
      if (!commentRes.ok) {
        const err = await commentRes.json().catch(() => ({}));
        throw new Error(
          err?.detail?.message || err?.detail || 'Failed to post comment',
        );
      }
      singleReply = '';
      await load();
    } catch (e: any) {
      error = e?.message || 'Failed to post reply';
    } finally {
      singlePosting = false;
    }
  }

  function startTitleEdit() {
    if (!primaryThread) return;
    editTitleDraft = primaryThread.title || '';
    editingTitle = true;
  }

  async function saveTitle() {
    if (!primaryThread || !editTitleDraft.trim() || savingTitle) return;
    savingTitle = true;
    try {
      const res = await fetch(
        `/api/threads/${encodeURIComponent(primaryThread.id)}`,
        {
          method: 'PATCH',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: editTitleDraft.trim() }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      editingTitle = false;
      await load();
    } catch (e: any) {
      error = e?.message || 'Failed to save title';
    } finally {
      savingTitle = false;
    }
  }

  async function expand(t: ThreadRow) {
    if (expandedId === t.id) {
      expandedId = null;
      expandedComments = [];
      return;
    }
    expandedId = t.id;
    expandedComments = [];
    replyParent = null;
    try {
      const res = await fetch(`/api/threads/${encodeURIComponent(t.id)}`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      expandedComments = body.comments || [];
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
        throw new Error(
          err?.detail?.message || err?.detail || `Failed (${res.status})`,
        );
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
    const sorted = [...flat].sort((a, b) => a.path.localeCompare(b.path));
    return sorted.map((c) => {
      const parts = c.path.split('.').filter(Boolean);
      return { c, depth: Math.max(0, parts.length - 2) };
    });
  }

  let nested = $derived(nestComments(expandedComments));
  let nestedSingle = $derived(nestComments(singleComments));

  let communityLabel = $derived(
    primaryThread?.community_name ||
      threads[0]?.community_name ||
      (anchorType === 'media_item' ? 'stacks' : null),
  );
  let communityUrl = $derived(
    primaryThread?.community_url ||
      threads[0]?.community_url ||
      (lemmyUrl && communityLabel ? `${lemmyUrl}/c/${communityLabel}` : null),
  );

  $effect(() => {
    if (anchorId) load();
  });
</script>

<section class="threads" class:compact={useCompactHeader}>
  {#if !useCompactHeader}
    <header class="threads__head">
      <h2>{title}</h2>
      {#if !singleThread}
        <button
          class="action-btn"
          onclick={() => (composerOpen = !composerOpen)}
          type="button"
          disabled={!lemmyConfigured || !lemmyLinked}
          >{composerOpen ? 'Cancel' : '+ New thread'}</button
        >
      {/if}
    </header>
  {:else if !singleThread}
    <div class="threads__head threads__head--compact">
      <button
        class="action-btn"
        onclick={() => (composerOpen = !composerOpen)}
        type="button"
        disabled={!lemmyConfigured || !lemmyLinked}
        >{composerOpen ? 'Cancel' : '+ New thread'}</button
      >
    </div>
  {/if}

  {#if communityLabel}
    <div class="threads__community">
      in
      {#if communityUrl}
        <a href={communityUrl} target="_blank" rel="noopener"
          >c/{communityLabel} ↗</a
        >
      {:else}
        <span>c/{communityLabel}</span>
      {/if}
    </div>
  {/if}

  {#if !lemmyConfigured}
    <div class="notice notice--warn">Discussion temporarily unavailable.</div>
  {:else if !lemmyLinked}
    <div class="notice notice--warn">
      Link your fold account in <a href="/admin/settings">Settings</a> to post.
    </div>
  {/if}

  {#if error}
    <div class="notice notice--error">{error}</div>
  {/if}

  {#if composerOpen && !singleThread}
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
        placeholder="Paste a SoundCloud / Drive / YouTube URL for a link post (optional)"
        bind:value={composerUrl}
      />
      <div class="composer__help">
        Posts as you in c/{communityLabel || 'stacks'}.
      </div>
      <div class="composer__actions">
        <button class="btn-primary" type="submit" disabled={posting}
          >{posting ? 'Posting…' : 'Post thread'}</button
        >
      </div>
    </form>
  {/if}

  {#if singleThread}
    {#if loading && !primaryThread}
      <div class="muted">Loading discussion…</div>
    {:else if primaryThread}
      <div class="single">
        <div class="single__head">
          {#if editingTitle}
            <input
              class="single__title-input"
              type="text"
              bind:value={editTitleDraft}
            />
            <button
              class="action-btn"
              type="button"
              disabled={savingTitle || !editTitleDraft.trim()}
              onclick={saveTitle}>{savingTitle ? 'Saving…' : 'Save'}</button
            >
            <button
              class="action-btn"
              type="button"
              onclick={() => (editingTitle = false)}>Cancel</button
            >
          {:else}
            <h3 class="single__title">
              {primaryThread.title || defaultTitle}
            </h3>
            {#if canEditPrimary}
              <button
                class="icon-btn"
                type="button"
                title="Edit title"
                onclick={startTitleEdit}>✏️</button
              >
            {/if}
            {#if primaryThread.lemmy_url}
              <a
                class="single__lemmy"
                href={primaryThread.lemmy_url}
                target="_blank"
                rel="noopener"
                title="View on fold">↗ fold</a
              >
            {/if}
          {/if}
        </div>
        {#if primaryThread.url}
          <a
            class="link-card"
            href={primaryThread.url}
            target="_blank"
            rel="noopener">{primaryThread.url}</a
          >
        {/if}
        {#if primaryThread.body}
          <div class="markdown">{primaryThread.body}</div>
        {/if}
        <ul class="comments">
          {#each nestedSingle as { c, depth } (c.id)}
            <li
              class="comment"
              style="margin-left: {Math.min(depth, 6) * 16}px"
            >
              <div class="comment__meta">
                <span>user #{c.creator_id}</span>
                <span>{fmtTime(c.published)}</span>
              </div>
              <div class="comment__body">
                {c.deleted ? '(deleted)' : c.content}
              </div>
            </li>
          {/each}
          {#if nestedSingle.length === 0}
            <li class="muted">No replies yet.</li>
          {/if}
        </ul>
        <form
          class="reply"
          onsubmit={(e) => {
            e.preventDefault();
            postSingleReply();
          }}
        >
          <textarea
            rows="2"
            placeholder="Reply…"
            bind:value={singleReply}
            disabled={!lemmyConfigured || !lemmyLinked}
          ></textarea>
          <button
            class="action-btn"
            type="submit"
            disabled={!singleReply.trim() || singlePosting || !lemmyLinked}
            >{singlePosting ? 'Posting…' : 'Post reply'}</button
          >
        </form>
      </div>
    {:else}
      <div class="single single--empty">
        <div class="single__empty-head">Start the discussion</div>
        <form
          class="reply"
          onsubmit={(e) => {
            e.preventDefault();
            postSingleReply();
          }}
        >
          <textarea
            rows="2"
            placeholder="Leave the first comment…"
            bind:value={singleReply}
            disabled={!lemmyConfigured || !lemmyLinked}
          ></textarea>
          <div class="composer__help">
            Posts as you in c/{communityLabel || 'stacks'}. Thread auto-titled “{defaultTitle}”
            — you can rename it after.
          </div>
          <button
            class="action-btn"
            type="submit"
            disabled={!singleReply.trim() || singlePosting || !lemmyLinked}
            >{singlePosting ? 'Posting…' : 'Post first comment'}</button
          >
        </form>
      </div>
    {/if}
  {:else if loading && threads.length === 0}
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
              {#if t.lemmy_url}
                <a
                  class="thread__lemmy"
                  href={t.lemmy_url}
                  target="_blank"
                  rel="noopener">↗ View on fold</a
                >
              {/if}
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
                    style="margin-left: {Math.min(depth, 6) * 16}px"
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
                <button
                  class="action-btn"
                  type="submit"
                  disabled={!replyBody.trim()}>Reply</button
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
    gap: var(--space-sm);
  }
  .threads__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }
  .threads__head--compact {
    justify-content: flex-end;
  }
  .threads__head h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
    padding-bottom: var(--space-xs);
    border-bottom: 2px solid var(--color-text);
    flex: 1;
  }
  .threads__community {
    font-size: var(--text-sm);
    color: var(--color-muted);
  }
  .threads__community a {
    color: var(--color-accent);
    text-decoration: none;
  }
  .threads__community a:hover {
    text-decoration: underline;
  }
  .composer {
    display: flex;
    flex-direction: column;
    gap: 6px;
    border: 1px solid var(--color-border);
    padding: var(--space-sm);
    background: var(--color-bg);
  }
  .composer input,
  .composer textarea {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 6px 10px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .composer__help {
    font-size: 0.7rem;
    color: var(--color-muted);
  }
  .composer__actions {
    display: flex;
    justify-content: flex-end;
  }
  .single {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
    border: 1px solid var(--color-border);
    padding: var(--space-sm);
    background: var(--color-bg);
  }
  .single--empty {
    background: var(--color-surface);
  }
  .single__head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .single__title {
    margin: 0;
    font-size: var(--text-md);
    flex: 1;
  }
  .single__title-input {
    flex: 1;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 4px 8px;
    font-family: var(--font-mono);
    font-size: var(--text-md);
  }
  .single__lemmy,
  .thread__lemmy {
    font-size: 0.75rem;
    color: var(--color-accent);
    text-decoration: none;
    white-space: nowrap;
  }
  .single__lemmy:hover,
  .thread__lemmy:hover {
    text-decoration: underline;
  }
  .single__empty-head {
    font-size: var(--text-md);
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
  }
  .icon-btn {
    background: transparent;
    border: 0;
    cursor: pointer;
    padding: 2px 6px;
    font-size: 0.85rem;
  }
  .thread-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .thread {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .thread__head {
    width: 100%;
    background: transparent;
    border: 0;
    color: inherit;
    text-align: left;
    padding: var(--space-sm);
    display: flex;
    justify-content: space-between;
    gap: var(--space-sm);
    cursor: pointer;
    font: inherit;
    font-size: var(--text-sm);
  }
  .thread.expanded .thread__head {
    border-bottom: 1px dashed var(--color-border);
    background: var(--color-surface);
  }
  .thread__title {
    font-weight: bold;
  }
  .thread__meta {
    color: var(--color-muted);
    font-size: 0.7rem;
    white-space: nowrap;
  }
  .thread__body {
    padding: var(--space-sm);
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .link-card {
    display: inline-block;
    padding: 6px 10px;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    word-break: break-all;
    color: var(--color-accent);
    font-size: var(--text-sm);
    text-decoration: none;
  }
  .markdown {
    white-space: pre-wrap;
    font-size: var(--text-sm);
  }
  .comments {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .comment {
    border-left: 2px solid var(--color-border);
    padding: 4px 8px;
  }
  .comment__meta {
    color: var(--color-muted);
    font-size: 0.65rem;
    display: flex;
    gap: 8px;
    align-items: center;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
  }
  .comment__body {
    white-space: pre-wrap;
    font-size: var(--text-sm);
  }
  .reply {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .reply textarea {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 6px 10px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .muted {
    color: var(--color-muted);
    font-size: var(--text-sm);
  }
  .notice {
    padding: 6px 10px;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    font-size: var(--text-sm);
  }
  .notice--warn {
    border-color: var(--color-accent);
    color: var(--color-accent);
  }
  .notice--error {
    border-color: #c00;
    color: #c00;
  }
  .link {
    background: transparent;
    border: 0;
    color: var(--color-accent);
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
    font: inherit;
  }
  @media (max-width: 640px) {
    .thread__head {
      flex-direction: column;
      align-items: flex-start;
      gap: 2px;
    }
  }
</style>
