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
  });
  $effect(() => {
    votersDown = downvoters;
  });

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

<div class="vote-block" class:is-busy={busy}>
  <div class="vote-block__row">
    <button
      type="button"
      class="vote-block__btn vote-block__btn--up"
      class:is-active={mine === 1}
      aria-pressed={mine === 1}
      aria-label="Acclaim this item"
      disabled={busy}
      onclick={(e) => {
        e.stopPropagation();
        void cast(1);
      }}
    >
      <span class="vote-block__arrow" aria-hidden="true">▲</span>
      <span class="vote-block__label">Acclaim</span>
      <span class="vote-block__count">{up}</span>
    </button>

    <button
      type="button"
      class="vote-block__btn vote-block__btn--down"
      class:is-active={mine === -1}
      aria-pressed={mine === -1}
      aria-label="Disavow this item"
      disabled={busy}
      onclick={(e) => {
        e.stopPropagation();
        void cast(-1);
      }}
    >
      <span class="vote-block__arrow" aria-hidden="true">▼</span>
      <span class="vote-block__label">Disavow</span>
      <span class="vote-block__count">{down}</span>
    </button>
  </div>

  <dl class="vote-block__voters">
    <div class="vote-block__voters-row">
      <dt>Acclaimed by</dt>
      <dd>{namesText(votersUp)}</dd>
    </div>
    <div class="vote-block__voters-row">
      <dt>Disavowed by</dt>
      <dd>{namesText(votersDown)}</dd>
    </div>
  </dl>

  {#if error}
    <div class="vote-block__error" role="alert">{error}</div>
  {/if}
</div>

<style>
  .vote-block {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 0.5rem);
  }
  .vote-block.is-busy {
    opacity: 0.7;
  }
  .vote-block__row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-sm, 0.5rem);
  }
  .vote-block__btn {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.6rem 1rem;
    background: var(--color-bg);
    color: var(--color-fg, var(--color-text));
    border: 2px solid var(--color-fg, var(--color-text));
    box-shadow: 2px 2px 0 var(--color-fg, var(--color-text));
    font-family: var(--font-mono);
    font-size: var(--text-base, 0.95rem);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    line-height: 1;
    cursor: pointer;
    user-select: none;
    min-width: 9.5rem;
    justify-content: flex-start;
    transition:
      transform 60ms ease,
      box-shadow 60ms ease,
      background 80ms,
      color 80ms;
  }
  .vote-block__btn:hover:not(:disabled) {
    transform: translate(-1px, -1px);
    box-shadow: 3px 3px 0 var(--color-fg, var(--color-text));
  }
  .vote-block__btn:active:not(:disabled) {
    transform: translate(2px, 2px);
    box-shadow: 0 0 0 var(--color-fg, var(--color-text));
  }
  .vote-block__btn.is-active {
    background: var(--color-fg, var(--color-text));
    color: var(--color-bg);
  }
  .vote-block__btn:disabled {
    cursor: progress;
    opacity: 0.7;
  }
  .vote-block__arrow {
    font-size: 1.15em;
    line-height: 1;
  }
  .vote-block__label {
    flex: 1;
  }
  .vote-block__count {
    font-variant-numeric: tabular-nums;
    min-width: 2ch;
    text-align: right;
  }
  .vote-block__voters {
    margin: 0;
    font-size: var(--text-sm, 0.85rem);
    color: var(--color-muted);
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
  }
  .vote-block__voters-row {
    display: flex;
    gap: 0.5rem;
    align-items: baseline;
  }
  .vote-block__voters dt {
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.7rem;
    letter-spacing: 0.5pt;
    color: var(--color-muted);
    min-width: 9.5rem;
    flex-shrink: 0;
  }
  .vote-block__voters dd {
    margin: 0;
    color: var(--color-text, var(--color-fg));
    word-break: break-word;
  }
  .vote-block__error {
    color: #c00;
    font-size: var(--text-sm, 0.85rem);
  }
</style>
