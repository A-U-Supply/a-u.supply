# Plans

Every non-trivial change starts with a plan here, before any code gets written.

## Naming

```
docs/plans/YYYY-MM-DD-<slug>.md
```

The date is the day the plan was written (not the day work started). The slug is a short, dash-separated description — match it to the feature branch name when possible (e.g. `2026-04-24-slack-activity-log.md` → `feature/slack-activity-log`).

## What a plan contains

Minimum viable plan:

- **Goal** — one sentence. What are we trying to achieve?
- **Approach** — the shape of the solution. Not code; the handful of decisions that make this approach different from the next one.
- **Event taxonomy / data model / API surface** — whatever the equivalent is for this feature.
- **Open questions** — things the plan author wasn't sure about and wants confirmed before coding.

Keep it tight. A plan that takes an hour to read is a design doc, not a plan.

## Lifecycle

1. Write the plan, open a PR that only adds the plan file.
2. Review happens on the plan PR — cheaper than reviewing code after the fact.
3. Merge the plan PR.
4. Implement in a separate branch; link the plan from the implementation PR's description.
5. If the implementation diverges from the plan, either update the plan (new PR) or note the divergence in the implementation PR. Don't let the plan silently rot.

Lightweight plans (one-line description changes, typo fixes, obvious bug fixes) don't need a doc.

### Status banner

Each plan carries a status line near the top:

```
**Branch:** `<branch>` → PR #N
**Status:** plan-only | in progress | shipped — PR #N [follow-ups: …]
```

When a plan ships, update its status to `shipped — PR #N` and link any follow-up plans. Don't move shipped plans out of this directory — they stay as design archaeology.

## Why

- **Searchable history.** Six months from now, "why did we pick threshold rollups every 30m" is one `git log docs/plans/` away.
- **Cheap course correction.** Catching a bad assumption on the plan beats catching it in the implementation.
- **Claude gets a foothold.** When a future session opens this repo, it can read past plans instead of re-deriving context from scratch.
