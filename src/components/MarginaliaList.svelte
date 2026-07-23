<!--
  MarginaliaList — timestamped comments + cue markers for one media item.

  Two display modes:
    - default: brutalist card chrome + full interaction (media detail page)
    - compact + readOnly: plain list for badge popovers (no chrome, no writes)

  Seeking goes through seekAnnotation() — a bare player:seek when the item
  is already the player's current track, else player:queue with start_time.

  The composer posts at a fixed position (mm:ss input, default 0:00). When
  the item IS the player's current track the position prefills/syncs from
  the playhead via the player:time-request bridge. Composition elsewhere
  (Latent popovers) is deliberately read-only: the player panel is the
  single composition point.

  Props:
    - mediaId: the media item
    - mediaType / filename: used to build the player track for seeks
    - readOnly: hide composer + all write actions (badge popovers)
    - compact: no card chrome (the popover provides it)
    - showComposer: mm:ss-positioned composer (detail page)
    - heading: card head label (default mode)
-->
<script lang="ts">
  import {
    fetchAnnotations,
    createAnnotation,
    updateAnnotation,
    toggleResolveAnnotation,
    deleteAnnotation,
    seekAnnotation,
    queryPlayerState,
    fmtTimestamp,
    parseTimestamp,
    linkifyTimestamps,
    whoLabel,
    sourceLabel,
    excerpt,
    relTime,
    type Annotation,
  } from './marginalia.ts';

  type Props = {
    mediaId: string;
    mediaType?: string;
    filename?: string;
    readOnly?: boolean;
    compact?: boolean;
    showComposer?: boolean;
    heading?: string;
  };

  let {
    mediaId,
    mediaType = '',
    filename = '',
    readOnly = false,
    compact = false,
    showComposer = false,
    heading = 'Comments & markers',
  }: Props = $props();

  let annotations = $state<Annotation[]>([]);
  let inherited = $state<Annotation[]>([]);
  let parent = $state<{ id: string; filename: string } | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let showInherited = $state(true);

  let expandedId = $state<string | null>(null);
  let cardEl = $state<HTMLDivElement | undefined>(undefined);
  let replyFor = $state<string | null>(null);
  let replyText = $state('');
  let editId = $state<string | null>(null);
  let editText = $state('');
  let editPos = $state('');

  let composerPos = $state('0:00');
  let composerBody = $state('');
  let posting = $state(false);

  let commentCount = $derived(
    annotations.filter((a) => a.kind === 'comment').length,
  );
  let cueCount = $derived(annotations.filter((a) => a.kind === 'cue').length);

  async function load() {
    loading = true;
    error = null;
    try {
      const bundle = await fetchAnnotations(mediaId);
      annotations = bundle.annotations;
      inherited = bundle.inherited;
      parent = bundle.parent;
    } catch (e: any) {
      error = e?.message || 'Failed to load annotations';
      annotations = [];
      inherited = [];
      parent = null;
    } finally {
      loading = false;
    }
  }

  function seek(seconds: number) {
    seekAnnotation(mediaId, mediaType, filename, seconds);
  }

  function toggleExpand(id: string) {
    expandedId = expandedId === id ? null : id;
    replyFor = null;
    editId = null;
    if (expandedId) {
      setTimeout(() => {
        cardEl?.focus();
        cardEl?.scrollIntoView({ block: 'nearest' });
      }, 0);
    }
  }

  function onCardKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      expandedId = null;
    }
  }

  // Prefill the composer position from the playhead when this item is the
  // player's current track. Synchronous event-bus round-trip.
  function syncFromPlayer() {
    const st = queryPlayerState();
    if (st && st.track_id === mediaId) {
      composerPos = fmtTimestamp(st.currentTime);
    }
  }

  async function post(kind: 'comment' | 'cue') {
    if (posting) return;
    const text = composerBody.trim();
    if (kind === 'comment' && !text) return;
    const seconds = parseTimestamp(composerPos) ?? 0;
    posting = true;
    error = null;
    try {
      await createAnnotation(mediaId, {
        kind,
        position_seconds: seconds,
        ...(kind === 'comment' ? { body: text } : text ? { label: text } : {}),
      });
      composerBody = '';
      await load();
    } catch (e: any) {
      error = e?.message || 'Failed to post';
    } finally {
      posting = false;
    }
  }

  async function postReply(a: Annotation) {
    const text = replyText.trim();
    if (!text) return;
    error = null;
    try {
      await createAnnotation(mediaId, {
        kind: 'comment',
        position_seconds: a.position_seconds,
        body: text,
        parent_id: a.id,
      });
      replyText = '';
      replyFor = null;
      await load();
      expandedId = a.id;
    } catch (e: any) {
      error = e?.message || 'Failed to post reply';
    }
  }

  async function toggleResolved(a: Annotation) {
    error = null;
    try {
      await toggleResolveAnnotation(a.id);
      await load();
      expandedId = a.id;
    } catch (e: any) {
      error = e?.message || 'Failed to update';
    }
  }

  function startEdit(a: Annotation) {
    editId = a.id;
    editText = a.kind === 'comment' ? a.body || '' : a.label || '';
    editPos = fmtTimestamp(a.position_seconds);
    replyFor = null;
  }

  async function saveEdit(a: Annotation) {
    error = null;
    try {
      const seconds = parseTimestamp(editPos);
      await updateAnnotation(a.id, {
        ...(a.kind === 'comment'
          ? { body: editText.trim() }
          : { label: editText.trim() }),
        ...(seconds != null ? { position_seconds: seconds } : {}),
      });
      editId = null;
      await load();
      expandedId = a.id;
    } catch (e: any) {
      error = e?.message || 'Failed to save';
    }
  }

  async function removeAnnotation(a: Annotation) {
    if (!confirm('Delete this annotation? Replies go with it.')) return;
    error = null;
    try {
      await deleteAnnotation(a.id);
      if (expandedId === a.id) expandedId = null;
      await load();
    } catch (e: any) {
      error = e?.message || 'Failed to delete';
    }
  }

  $effect(() => {
    if (mediaId) {
      expandedId = null;
      replyFor = null;
      editId = null;
      load();
      syncFromPlayer();
    }
  });
</script>

{#snippet row(a: Annotation)}
  <li class="mgl-row" class:mgl-row--resolved={a.resolved}>
    <div class="mgl-row__line">
      <button
        class="mgl-seek"
        type="button"
        onclick={() => seek(a.position_seconds)}
        aria-label="Play from {fmtTimestamp(a.position_seconds)}"
        >[{fmtTimestamp(a.position_seconds)}]</button
      >
      <span class="mgl-icon" aria-hidden="true"
        >{a.kind === 'comment' ? '💬' : '◆'}</span
      >
      <span class="mgl-who">{whoLabel(a)}</span>
      {#if readOnly}
        <span class="mgl-text" title={excerpt(a, 200)}>{excerpt(a)}</span>
      {:else}
        <button
          class="mgl-text mgl-text--btn"
          type="button"
          onclick={() => toggleExpand(a.id)}
          aria-expanded={expandedId === a.id}
          title={excerpt(a, 200)}>{excerpt(a) || '(no text)'}</button
        >
      {/if}
      {#if a.replies?.length}
        <span class="mgl-replies-count" title="{a.replies.length} replies"
          >{a.replies.length}↩</span
        >
      {/if}
      {#if a.resolved}
        <span class="mgl-done" title="Resolved">✓</span>
      {/if}
    </div>
    {#if !readOnly && expandedId === a.id}
      <div
        class="mgl-card"
        tabindex="-1"
        bind:this={cardEl}
        onkeydown={onCardKeydown}
      >
        <div class="mgl-card__meta">
          {whoLabel(a)} · {fmtTimestamp(a.position_seconds)} · {relTime(
            a.created_at,
          )}
        </div>
        {#if a.label}
          <div class="mgl-card__label">{a.label}</div>
        {/if}
        {#if a.body}
          <div class="mgl-body">
            {#each linkifyTimestamps(a.body) as part}
              {#if 'seconds' in part}
                <button
                  class="mgl-ts"
                  type="button"
                  onclick={() => seek(part.seconds)}>{part.label}</button
                >
              {:else}{part.text}{/if}
            {/each}
          </div>
        {/if}
        {#if a.replies?.length}
          <ul class="mgl-replies">
            {#each a.replies as r (r.id)}
              <li>
                <span class="mgl-who">{r.author?.name || 'reply'}</span>
                <span class="mgl-body">
                  {#each linkifyTimestamps(r.body || '') as part}
                    {#if 'seconds' in part}
                      <button
                        class="mgl-ts"
                        type="button"
                        onclick={() => seek(part.seconds)}>{part.label}</button
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
            class="mgl-edit"
            onsubmit={(e) => {
              e.preventDefault();
              saveEdit(a);
            }}
          >
            {#if a.kind === 'comment'}
              <textarea rows="2" bind:value={editText} aria-label="Comment text"
              ></textarea>
            {:else}
              <input
                type="text"
                bind:value={editText}
                aria-label="Marker label"
                placeholder="Marker label"
              />
            {/if}
            <div class="mgl-edit__row">
              <input
                class="mgl-pos"
                type="text"
                bind:value={editPos}
                aria-label="Position (mm:ss)"
              />
              <button class="action-btn" type="submit">Save</button>
              <button
                class="action-btn"
                type="button"
                onclick={() => (editId = null)}>Cancel</button
              >
            </div>
          </form>
        {:else if replyFor === a.id}
          <form
            class="mgl-edit"
            onsubmit={(e) => {
              e.preventDefault();
              postReply(a);
            }}
          >
            <textarea
              rows="2"
              placeholder="Reply…"
              bind:value={replyText}
              aria-label="Reply text"
            ></textarea>
            <div class="mgl-edit__row">
              <button
                class="action-btn"
                type="submit"
                disabled={!replyText.trim()}>Reply</button
              >
              <button
                class="action-btn"
                type="button"
                onclick={() => (replyFor = null)}>Cancel</button
              >
            </div>
          </form>
        {:else}
          <div class="mgl-actions">
            <button
              class="action-btn"
              type="button"
              onclick={() => {
                replyFor = a.id;
                replyText = '';
                editId = null;
              }}>Reply</button
            >
            <button
              class="action-btn"
              type="button"
              onclick={() => toggleResolved(a)}
              >{a.resolved ? 'Unresolve' : 'Resolve'}</button
            >
            <button
              class="action-btn"
              type="button"
              onclick={() => startEdit(a)}>Edit</button
            >
            <button
              class="action-btn action-btn--danger"
              type="button"
              onclick={() => removeAnnotation(a)}>Delete</button
            >
          </div>
        {/if}
      </div>
    {/if}
  </li>
{/snippet}

{#snippet content()}
  {#if error}
    <div class="notice notice--error">{error}</div>
  {/if}
  {#if loading}
    <div class="muted">Loading…</div>
  {:else}
    {#if annotations.length === 0 && inherited.length === 0}
      <div class="muted">No comments or markers yet.</div>
    {/if}
    {#if annotations.length > 0}
      <ul class="mgl-list">
        {#each annotations as a (a.id)}
          {@render row(a)}
        {/each}
      </ul>
    {/if}
    {#if parent && inherited.length > 0}
      <div class="mgl-session">
        <button
          class="mgl-session__toggle"
          type="button"
          aria-pressed={showInherited}
          onclick={() => (showInherited = !showInherited)}
          >◇ session markers ({inherited.length})</button
        >
        <span class="muted">from session: {parent.filename}</span>
      </div>
      {#if showInherited}
        <ul class="mgl-list">
          {#each inherited as a (a.id)}
            <li class="mgl-row mgl-row--inh">
              <div class="mgl-row__line">
                <button
                  class="mgl-seek"
                  type="button"
                  onclick={() => seek(a.position_seconds)}
                  aria-label="Play from {fmtTimestamp(a.position_seconds)}"
                  >[{fmtTimestamp(a.position_seconds)}]</button
                >
                <span class="mgl-icon" aria-hidden="true">◇</span>
                <span class="mgl-who">{sourceLabel(a.source)}</span>
                <span class="mgl-text" title={a.label || ''}
                  >{a.label || ''}</span
                >
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
    {#if showComposer && !readOnly}
      <form
        class="mgl-composer"
        onsubmit={(e) => {
          e.preventDefault();
          post('comment');
        }}
      >
        <div class="mgl-composer__row">
          <input
            class="mgl-pos"
            type="text"
            bind:value={composerPos}
            aria-label="Position (mm:ss)"
            title="Position (mm:ss)"
          />
          <button
            class="action-btn"
            type="button"
            onclick={syncFromPlayer}
            title="Use the player's current position (when this item is playing)"
            >⌖ now</button
          >
        </div>
        <textarea
          rows="2"
          placeholder="Comment… (a [mm:ss] in the text becomes a seek link)"
          bind:value={composerBody}
          aria-label="Comment text"
          onkeydown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              post('comment');
            }
          }}
        ></textarea>
        <div class="mgl-composer__actions">
          <button
            class="btn-primary"
            type="submit"
            disabled={posting || !composerBody.trim()}
            >{posting ? 'Posting…' : 'Comment'}</button
          >
          <button
            class="action-btn"
            type="button"
            disabled={posting}
            onclick={() => post('cue')}>+ Marker</button
          >
        </div>
      </form>
    {/if}
  {/if}
{/snippet}

{#if compact}
  {@render content()}
{:else}
  <section class="mgl">
    <div class="mgl__head">
      <h2>{heading}</h2>
      <span class="mgl__head-meta">
        {#if commentCount > 0}
          {commentCount} comment{commentCount === 1 ? '' : 's'}
        {/if}
        {#if commentCount > 0 && cueCount > 0}
          ·
        {/if}
        {#if cueCount > 0}
          {cueCount} marker{cueCount === 1 ? '' : 's'}
        {/if}
      </span>
    </div>
    <div class="mgl__body">
      {@render content()}
    </div>
  </section>
{/if}

<style>
  .mgl {
    display: flex;
    flex-direction: column;
    border: 2px solid var(--color-text);
    background: var(--color-surface);
    box-shadow: 4px 4px 0 var(--color-text);
  }
  .mgl__head {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: 6px var(--space-sm);
    background: var(--color-bg);
    border-bottom: 2px solid var(--color-text);
  }
  .mgl__head h2 {
    margin: 0;
    font-size: var(--text-md);
    flex: 1;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
  }
  .mgl__head-meta {
    font-size: 0.7rem;
    color: var(--color-muted);
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    white-space: nowrap;
  }
  .mgl__body {
    padding: var(--space-sm);
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }
  .mgl-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .mgl-row {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
  }
  .mgl-row--resolved {
    opacity: 0.55;
  }
  .mgl-row--inh {
    border-style: dashed;
  }
  .mgl-row__line {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    min-width: 0;
  }
  .mgl-seek {
    background: transparent;
    border: 1px solid var(--color-border);
    color: var(--color-accent);
    font-family: var(--font-mono);
    font-size: 0.7rem;
    padding: 1px 6px;
    cursor: pointer;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .mgl-seek:hover {
    border-color: var(--color-accent);
  }
  .mgl-icon {
    flex-shrink: 0;
    font-size: 0.7rem;
  }
  .mgl-who {
    color: var(--color-muted);
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .mgl-text {
    min-width: 0;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--text-sm);
  }
  .mgl-text--btn {
    background: none;
    border: 0;
    color: var(--color-text);
    font: inherit;
    font-size: var(--text-sm);
    cursor: pointer;
    text-align: left;
    padding: 0;
  }
  .mgl-text--btn:hover {
    color: var(--color-accent);
  }
  .mgl-replies-count {
    color: var(--color-muted);
    font-size: 0.65rem;
    flex-shrink: 0;
  }
  .mgl-done {
    color: var(--color-status-ok);
    flex-shrink: 0;
  }
  .mgl-card {
    border-top: 1px dashed var(--color-border);
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .mgl-card:focus {
    outline: 2px solid var(--color-accent);
    outline-offset: -2px;
  }
  .mgl-card__meta {
    color: var(--color-muted);
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
  }
  .mgl-card__label {
    font-size: var(--text-sm);
    font-weight: bold;
  }
  .mgl-body {
    white-space: pre-wrap;
    font-size: var(--text-sm);
  }
  .mgl-ts {
    background: none;
    border: 0;
    color: var(--color-accent);
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
    font: inherit;
  }
  .mgl-replies {
    list-style: none;
    margin: 0;
    padding: 0 0 0 12px;
    border-left: 2px solid var(--color-border);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .mgl-replies li {
    display: flex;
    gap: 8px;
    align-items: baseline;
  }
  .mgl-edit {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .mgl-edit__row {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .mgl-edit textarea,
  .mgl-edit input,
  .mgl-composer textarea {
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 6px 10px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    width: 100%;
    box-sizing: border-box;
  }
  .mgl-pos {
    width: 7ch;
    flex: 0 0 auto;
    background: var(--color-bg);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 6px 8px;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .mgl-session {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .mgl-session__toggle {
    background: transparent;
    border: 1px dashed var(--color-border);
    color: var(--color-muted);
    font: inherit;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 1pt;
    padding: 1px 6px;
    cursor: pointer;
  }
  .mgl-session__toggle[aria-pressed='true'] {
    color: var(--color-accent);
    border-color: var(--color-accent);
  }
  .mgl-composer {
    display: flex;
    flex-direction: column;
    gap: 6px;
    border-top: 1px dashed var(--color-border);
    padding-top: var(--space-sm);
  }
  .mgl-composer__row {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .mgl-composer__actions {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .muted {
    color: var(--color-muted);
    font-size: var(--text-sm);
  }
  .notice {
    padding: 6px 10px;
    border: 1px solid var(--color-border);
    font-size: var(--text-sm);
  }
  .notice--error {
    border-color: var(--color-status-fail);
    color: var(--color-status-fail);
  }
  @media (max-width: 640px) {
    .mgl-row__line {
      flex-wrap: wrap;
    }
    .mgl-seek,
    .mgl-session__toggle {
      min-height: 44px;
      display: inline-flex;
      align-items: center;
    }
    .mgl-text--btn {
      min-height: 44px;
      display: inline-flex;
      align-items: center;
    }
    .mgl-pos {
      /* iOS Safari zooms on focus below 16px. */
      font-size: 16px;
    }
  }
</style>
