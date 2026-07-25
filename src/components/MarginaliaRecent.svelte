<!--
  MarginaliaRecent — "Latest comments & markers" strip for the Latent
  detail page. Reads the marginalia search index for the 10 most recent
  annotations across every item in the project. Each row links to the
  item's Stacks detail page and can queue it in the player at the
  annotated position. Collapsible <details>, default open when non-empty.

  Props:
    - projectId: the Latent
-->
<script lang="ts">
  import { isPhone } from '../lib/viewport.svelte.ts';
  import {
    seekAnnotation,
    fmtTimestamp,
    excerpt,
    relTime,
    sourceLabel,
    type MarginaliaDoc,
  } from './marginalia.ts';

  type Props = {
    projectId: string;
  };

  let { projectId }: Props = $props();

  let items = $state<MarginaliaDoc[]>([]);
  let total = $state(0);
  let loading = $state(true);
  let error = $state<string | null>(null);

  async function load() {
    loading = true;
    error = null;
    try {
      const res = await fetch(
        `/api/annotations?project_id=${encodeURIComponent(projectId)}&sort=created_at:desc&per_page=10`,
        { credentials: 'include' },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const body = await res.json();
      items = body.annotations || [];
      total = body.total ?? items.length;
    } catch (e: any) {
      error = e?.message || 'Failed to load annotations';
      items = [];
      total = 0;
    } finally {
      loading = false;
    }
  }

  function who(d: MarginaliaDoc): string {
    return d.author_name || sourceLabel(d.source);
  }

  $effect(() => {
    if (projectId) load();
  });
</script>

<!-- Open by default on desktop; closed on a phone, where ten rows is ~520px
     of page before you reach anything you came for. The summary carries the
     count either way. -->
<details class="mgl-recent" open={items.length > 0 && !isPhone()}>
  <summary class="mgl-recent__summary latent-band">
    <h2>Latest comments &amp; markers</h2>
    <span class="muted">{total}</span>
  </summary>
  {#if error}
    <div class="notice notice--error">{error}</div>
  {:else if loading}
    <div class="muted">Loading…</div>
  {:else if items.length === 0}
    <div class="muted">
      No comments or markers yet — annotate from the player or an item's detail
      page.
    </div>
  {:else}
    <ul class="mgl-recent__list">
      {#each items as d (d.id)}
        <li
          class="mgl-recent__row"
          class:mgl-recent__row--resolved={d.resolved}
        >
          <span class="mgl-recent__icon" aria-hidden="true"
            >{d.kind === 'comment' ? '💬' : '◆'}</span
          >
          <a
            class="mgl-recent__file"
            href={`/admin/search/detail?id=${encodeURIComponent(d.media_item_id)}`}
            title={d.filename || ''}>{d.filename || '(unknown)'}</a
          >
          <button
            class="mgl-recent__seek"
            type="button"
            onclick={() =>
              seekAnnotation(
                d.media_item_id,
                d.media_type || 'audio',
                d.filename || '',
                d.position_seconds,
              )}
            aria-label="Play {d.filename || 'item'} from {fmtTimestamp(
              d.position_seconds,
            )}">[{fmtTimestamp(d.position_seconds)}]</button
          >
          <span class="mgl-recent__excerpt" title={excerpt(d, 200)}
            >{excerpt(d) || '(no text)'}{#if d.resolved}
              <span class="mgl-recent__done" title="Resolved">✓</span
              >{/if}</span
          >
          <span class="mgl-recent__meta"
            >{who(d)} · {relTime(d.created_at)}</span
          >
        </li>
      {/each}
    </ul>
  {/if}
</details>

<style>
  .mgl-recent {
    display: block;
  }
  .mgl-recent__summary {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    border-bottom: 2px solid var(--color-text);
    padding: 2px 6px 6px 2px;
    cursor: pointer;
    list-style: none;
  }
  .mgl-recent__summary::-webkit-details-marker {
    display: none;
  }
  .mgl-recent__summary::before {
    content: '▸';
    color: var(--color-muted);
  }
  details[open] > .mgl-recent__summary::before {
    content: '▾';
  }
  .mgl-recent__summary h2 {
    margin: 0;
    font-size: var(--text-lg);
    text-transform: uppercase;
    letter-spacing: 1pt;
  }
  .mgl-recent__list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }
  .mgl-recent__row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
    border-bottom: 1px dashed var(--color-border);
    font-size: var(--text-sm);
    min-width: 0;
  }
  .mgl-recent__row:last-child {
    border-bottom: 0;
  }
  .mgl-recent__row--resolved {
    opacity: 0.55;
  }
  .mgl-recent__icon {
    flex-shrink: 0;
    font-size: 0.7rem;
  }
  .mgl-recent__file {
    color: var(--color-text);
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 0 1 auto;
  }
  .mgl-recent__file:hover {
    color: var(--color-accent);
  }
  .mgl-recent__seek {
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
  .mgl-recent__seek:hover {
    border-color: var(--color-accent);
  }
  .mgl-recent__excerpt {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mgl-recent__done {
    color: var(--color-status-ok);
  }
  .mgl-recent__meta {
    color: var(--color-muted);
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    white-space: nowrap;
    flex-shrink: 0;
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
    .mgl-recent__row {
      flex-wrap: wrap;
    }
    .mgl-recent__excerpt {
      flex-basis: 100%;
      order: 5;
    }
    .mgl-recent__seek {
      min-height: 44px;
      display: inline-flex;
      align-items: center;
    }
  }
</style>
