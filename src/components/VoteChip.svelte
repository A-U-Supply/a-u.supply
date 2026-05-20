<!--
  VoteChip — per-user up/down vote on a media item (issue #318).

  Two visual sizes:
    - size="sm" (default): ▲ 12 · 3 ▼  inline chip used in tooltips/embedded slots.
    - size="lg": large Reddit-style vertical stack with arrows + score in the
      middle. Used on the detail page header.

  Hover anywhere on the chip shows a floating tooltip with the voter
  identities (denormalized from the search doc / fetched lazily on first
  hover when not supplied).

  Optimistic local state: clicking updates `myVote` + counts immediately,
  then POSTs. A failed POST reverts and surfaces a small inline error.

  The component owns no global state; it emits no events. The parent page
  (which holds the in-memory results list) reads back the response via
  `onUpdate(aggregates)` to keep the rest of the row in sync (e.g. to
  rerank the row if sorted by Acclaim).
-->
<script lang="ts">
  type Voter = { user_id: number; name: string };

  type Props = {
    mediaId: string;
    upCount: number;
    downCount: number;
    myVote?: -1 | 0 | 1;
    upvoters?: Voter[];
    downvoters?: Voter[];
    currentUserId?: number | null;
    size?: 'sm' | 'lg';
    onUpdate?: (aggregates: {
      up_count: number;
      down_count: number;
      vote_score: number;
      my_vote: number;
    }) => void;
  };

  let {
    mediaId,
    upCount,
    downCount,
    myVote = 0,
    upvoters = [],
    downvoters = [],
    currentUserId = null,
    size = 'sm',
    onUpdate,
  }: Props = $props();

  let up = $state(upCount);
  let down = $state(downCount);
  let mine = $state<-1 | 0 | 1>(myVote);
  let votersUp = $state<Voter[]>(upvoters);
  let votersDown = $state<Voter[]>(downvoters);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let tooltipOpen = $state(false);
  let votersLoaded = $state(upvoters.length > 0 || downvoters.length > 0);

  $effect(() => {
    up = upCount;
  });
  $effect(() => {
    down = downCount;
  });
  $effect(() => {
    mine = myVote;
  });
  $effect(() => {
    votersUp = upvoters;
    if (upvoters.length > 0 || downvoters.length > 0) votersLoaded = true;
  });
  $effect(() => {
    votersDown = downvoters;
  });

  async function ensureVotersLoaded() {
    if (votersLoaded) return;
    try {
      const r = await fetch(
        `/api/search/${encodeURIComponent(mediaId)}/voters`,
        {
          credentials: 'include',
        },
      );
      if (!r.ok) return;
      const body = await r.json();
      votersUp = body.upvoters || [];
      votersDown = body.downvoters || [];
      votersLoaded = true;
    } catch {
      /* swallow — tooltip just stays empty */
    }
  }

  async function cast(target: 1 | -1) {
    // Reddit-style: clicking the same arrow retracts.
    const next: -1 | 0 | 1 = mine === target ? 0 : target;
    const priorUp = up;
    const priorDown = down;
    const priorMine = mine;

    // Optimistic update
    if (mine === 1) up -= 1;
    if (mine === -1) down -= 1;
    if (next === 1) up += 1;
    if (next === -1) down += 1;
    mine = next;
    // Reflect my own vote in the local voter list so the tooltip stays accurate
    if (currentUserId != null) {
      votersUp = votersUp.filter((v) => v.user_id !== currentUserId);
      votersDown = votersDown.filter((v) => v.user_id !== currentUserId);
      if (next === 1)
        votersUp = [...votersUp, { user_id: currentUserId, name: 'you' }];
      if (next === -1)
        votersDown = [...votersDown, { user_id: currentUserId, name: 'you' }];
    }

    busy = true;
    error = null;
    try {
      const r = await fetch(`/api/search/${encodeURIComponent(mediaId)}/vote`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: next }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      up = body.up_count;
      down = body.down_count;
      mine = body.my_vote as -1 | 0 | 1;
      onUpdate?.(body);
    } catch (e: any) {
      up = priorUp;
      down = priorDown;
      mine = priorMine;
      error = e?.message || 'Vote failed';
    } finally {
      busy = false;
    }
  }

  function namesText(voters: Voter[]) {
    if (voters.length === 0) return '—';
    const names = voters.map((v) => v.name).slice(0, 12);
    const more = voters.length > 12 ? `, +${voters.length - 12} more` : '';
    return names.join(', ') + more;
  }
</script>

<div
  class="vote-chip vote-chip--{size}"
  class:is-busy={busy}
  role="group"
  aria-label="Acclaim or disavow this item"
  onmouseenter={() => {
    tooltipOpen = true;
    void ensureVotersLoaded();
  }}
  onmouseleave={() => (tooltipOpen = false)}
  onfocusin={() => {
    tooltipOpen = true;
    void ensureVotersLoaded();
  }}
  onfocusout={() => (tooltipOpen = false)}
>
  <button
    type="button"
    class="vote-chip__arrow vote-chip__arrow--up"
    class:is-active={mine === 1}
    aria-pressed={mine === 1}
    aria-label="Acclaim"
    disabled={busy}
    onclick={(e) => {
      e.stopPropagation();
      void cast(1);
    }}>▲</button
  >
  <span class="vote-chip__count vote-chip__count--up" class:is-zero={up === 0}
    >{up}</span
  >
  {#if size === 'lg'}
    <span class="vote-chip__score" aria-label="Net score">{up - down}</span>
  {/if}
  <span
    class="vote-chip__count vote-chip__count--down"
    class:is-zero={down === 0}>{down}</span
  >
  <button
    type="button"
    class="vote-chip__arrow vote-chip__arrow--down"
    class:is-active={mine === -1}
    aria-pressed={mine === -1}
    aria-label="Disavow"
    disabled={busy}
    onclick={(e) => {
      e.stopPropagation();
      void cast(-1);
    }}>▼</button
  >

  {#if tooltipOpen}
    <div class="vote-chip__tooltip" role="tooltip">
      <div><strong>Acclaimed by:</strong> {namesText(votersUp)} ({up})</div>
      <div><strong>Disavowed by:</strong> {namesText(votersDown)} ({down})</div>
      {#if error}
        <div class="vote-chip__error">{error}</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .vote-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    position: relative;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }
  .vote-chip--sm {
    font-size: 0.85rem;
    padding: 0.1rem 0.35rem;
    border: 1px solid var(--color-border, #ccc);
    border-radius: 999px;
    background: var(--color-surface, #fff);
  }
  .vote-chip--lg {
    flex-direction: column;
    gap: 0.15rem;
    font-size: 1rem;
    padding: 0.25rem 0.4rem;
    border: 2px solid var(--color-border, #222);
    border-radius: 6px;
    background: var(--color-surface, #fff);
    box-shadow: 2px 2px 0 var(--color-border, #222);
  }
  .vote-chip--lg .vote-chip__score {
    font-weight: 700;
    font-size: 1.15rem;
  }
  .vote-chip__arrow {
    background: none;
    border: none;
    cursor: pointer;
    padding: 0 0.15rem;
    font-size: 1em;
    color: var(--color-muted, #888);
    line-height: 1;
  }
  .vote-chip__arrow:hover:not(:disabled) {
    color: var(--color-text, #111);
  }
  .vote-chip__arrow:disabled {
    cursor: progress;
  }
  .vote-chip__arrow--up.is-active {
    color: var(--color-accent-up, #1f8a3a);
  }
  .vote-chip__arrow--down.is-active {
    color: var(--color-accent-down, #b03030);
  }
  .vote-chip__count.is-zero {
    opacity: 0.4;
  }
  .vote-chip__tooltip {
    position: absolute;
    bottom: calc(100% + 0.4rem);
    left: 50%;
    transform: translateX(-50%);
    z-index: 100;
    background: var(--color-surface, #fff);
    color: var(--color-text, #111);
    border: 2px solid var(--color-border, #222);
    box-shadow: 2px 2px 0 var(--color-border, #222);
    padding: 0.4rem 0.6rem;
    border-radius: 4px;
    font-size: 0.78rem;
    white-space: nowrap;
    pointer-events: none;
    line-height: 1.4;
  }
  .vote-chip__tooltip strong {
    font-weight: 600;
  }
  .vote-chip__error {
    margin-top: 0.2rem;
    color: var(--color-accent-down, #b03030);
  }
  .vote-chip.is-busy {
    opacity: 0.7;
  }
</style>
