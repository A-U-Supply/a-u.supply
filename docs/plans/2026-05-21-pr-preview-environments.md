# PR-linked dev environments on Dokku

## Context

Until now, the only way to test changes against real auth and real integrations (fold etc.) has been to merge to `master`, which auto-deploys to prod. That forces "test in prod" for any non-trivial change. This plan adds an ephemeral Dokku app per open PR so each branch can be exercised end-to-end at a stable URL before it lands.

**Decisions:**

- One ephemeral Dokku app per open PR (`au-supply-pr-<N>` → `pr-<N>.dev.a-u.supply`).
- User DB snapshotted from prod on first deploy of each PR. Subsequent pushes to the same PR preserve the snapshot, so login sessions survive across pushes.
- External integrations (fold, etc.) point at prod. A buggy PR can write into prod fold; accepted, with `STAGING=1` set as a future-proof escape hatch.
- Wildcard scoped under `*.dev.a-u.supply` so it can't shadow `fold`, `@`, `www`, or future first-level subdomains.
- Per-host HTTP-01 Let's Encrypt cert per PR app (no DNS-01 / Namecheap API integration needed).
- Worker process disabled in PR envs (`worker=0`) to keep RAM cost down; web only.
- Prod model cache mounted **read-only** into PR envs so they don't re-download ML models.

## Architecture

```
PR opened/sync ──▶ GH Action ──▶ scripts/dokku-pr-provision.sh
                                  ├─ apps:create (idempotent)
                                  ├─ domains:set pr-<N>.dev.a-u.supply
                                  ├─ storage:ensure-directory + cp prod au.db (first run only)
                                  ├─ storage:mount au-supply-model-cache:ro
                                  ├─ config:set SECRET_KEY=<random> STAGING=1 PR_NUMBER=<N>
                                  ├─ ps:scale worker=0 web=1
                                  ├─ git push → master
                                  └─ letsencrypt:enable
                                          ▼
                              https://pr-<N>.dev.a-u.supply
PR closed/merged ──▶ GH Action ──▶ scripts/dokku-pr-destroy.sh ──▶ apps:destroy --force
Weekly host cron ──▶ docker image prune -af --filter "until=168h"
```

## Files

- **`.github/workflows/pr-preview.yml`** — `pull_request` trigger (`opened`/`synchronize`/`reopened`/`closed`). Two jobs: `deploy` and `destroy`. Concurrency group keyed on PR number cancels in-flight deploys when a new commit lands. Sticky PR comment via `marocchino/sticky-pull-request-comment` posts the preview URL.
- **`scripts/dokku-pr-provision.sh`** — idempotent provisioner. Creates the app + storage + DB snapshot only on first invocation; subsequent runs just push the new commit.
- **`scripts/dokku-pr-destroy.sh`** — tears down the app with `apps:destroy --force`.
- **No changes** to `main.py`, `worker.py`, `Procfile`, `Dockerfile`, or the existing `deploy.yml`. Prod deploy flow is untouched.

## One-time setup (operator actions)

### 1. DNS (number4 / Namecheap)

Namecheap → Domain List → **Manage** next to `a-u.supply` → **Advanced DNS**.

**Do not touch existing records.** Specific records (`fold`, `@`, `www`) always win over wildcards.

Add one record:

| Field | Value |
|---|---|
| Type | `A Record` |
| Host | `*.dev` |
| Value | `204.168.201.89` |
| TTL | `5 min` |

Verify after propagation:

```
dig +short anything.dev.a-u.supply
# expected: 204.168.201.89
```

### 2. Host crontab (dokku server)

```
ssh -t dokku
sudo crontab -e
# add:
0 4 * * 0  docker image prune -af --filter "until=168h"
```

Sweeps dangling images older than a week. Tagged images for running apps stay.

### 3. Let's Encrypt email (one-time, global)

If not already set:

```
ssh dokku letsencrypt:set --global email <ops@email>
```

### 4. Verify ownership of prod storage

```
ssh dokku -- ls -la /var/lib/dokku/data/storage/au-supply/au.db
```

Expect `dokku:dokku`. If it's `root:root`, the provisioner's `cp` step needs to switch to `dokku run au-supply -- cat ... > ...` piping.

## Resource sizing

The real image (Astro build + Python + ffmpeg + deno + docker-cli + ML deps) is ~2 GB on disk, not the ~600 MB the earlier brainstorm assumed. Layer sharing with the prod image means each additional PR app adds ~50–200 MB of unique storage, not a full 2 GB. RAM per PR env with worker disabled: ~150–250 MB idle.

**Before enabling**, run pre-flight from a local terminal:

```
ssh -t dokku free -h
ssh -t dokku df -h /var/lib/docker
ssh dokku apps:list
```

For the realistic case of 1 PR open at a time: ~250 MB extra RAM, ~200 MB extra disk. Concurrent PRs scale linearly.

## Cleanup

- **Per-PR:** `apps:destroy --force` on PR close removes container, config, domain, cert, storage volume.
- **Image bloat:** weekly `docker image prune` on the host.
- **No artifacts leak into the repo** — snapshots happen server-side.

## Security notes

- Per-PR URLs are public; the prod DB snapshot includes bcrypt password hashes but no endpoint exposes them.
- Each PR env gets a fresh `SECRET_KEY`, so JWTs cannot cross between envs or into prod.
- `AU_API_KEY` is copied from prod so admin endpoints work; revoke + reissue if a PR env's config is ever leaked.
- Model cache is read-only mount — PR envs cannot poison the shared cache.

## Verification

After DNS propagates and the workflow is merged:

1. Open a throwaway PR with a visible copy change.
2. GH Action posts `https://pr-<N>.dev.a-u.supply` as a sticky comment.
3. `dig +short pr-<N>.dev.a-u.supply` → `204.168.201.89`.
4. `curl -I https://pr-<N>.dev.a-u.supply` → 200, valid Let's Encrypt cert.
5. Log in via the preview URL with a real prod account → succeeds.
6. Make a second commit to the PR → preview updates, session still valid.
7. Close PR → `ssh dokku apps:list` no longer shows `au-supply-pr-<N>`; URL stops resolving.
8. `https://a-u.supply` unaffected throughout.

## Open items deferred to first real deploy

- Confirm `dokku:dokku` ownership of prod `au.db` (see step 4 above).
- If any PR env's `MODEL_CACHE_DIR` needs write access for a particular code path, drop the `:ro` from the storage mount.
- If concurrent PR pushes start backing up the Dokku build queue, swap the concurrency group to `cancel-in-progress: false` and serialize.
