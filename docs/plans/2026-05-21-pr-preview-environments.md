# PR-linked dev environments on Dokku

## Context

Until now, the only way to test changes against real auth and real integrations (fold etc.) has been to merge to `master`, which auto-deploys to prod. That forces "test in prod" for any non-trivial change. This plan adds an ephemeral Dokku app per open PR so each branch can be exercised end-to-end at a stable URL before it lands.

**Decisions:**

- **Opt-in per PR via the `preview` label.** No label = no build, no preview. Eliminates spend on PRs that don't need one.
- One ephemeral Dokku app per labeled PR (`au-supply-pr-<N>` → `pr-<N>.dev.a-u.supply`).
- Images built on GitHub runners (not the Dokku host). Dokku just pulls.
- External integrations (fold, etc.) point at prod. A buggy PR can write into prod fold; accepted, with `STAGING=1` set as a future-proof escape hatch.
- Wildcard scoped under `*.dev.a-u.supply` so it can't shadow `fold`, `@`, `www`, or future first-level subdomains.
- Per-host HTTP-01 Let's Encrypt cert per PR app (no DNS-01 / Namecheap API integration needed).
- Worker process disabled in PR envs (`worker=0`) to keep RAM cost down; web only.
- Prod model cache mounted **read-only** into PR envs so they don't re-download ML models.
- **Previews start with an empty user DB.** Register a test account in the preview to exercise login flows. (The prod-DB snapshot mechanism was removed after it raced uvicorn startup and corrupted SQLite locks.)

## How to use it

| Action | Result |
|---|---|
| Add `preview` label to a PR | Build the image on GH runners, deploy to `pr-<N>.dev.a-u.supply` |
| Push to a PR that has `preview` | Rebuild + redeploy |
| Remove `preview` label | Destroy the Dokku app + delete the GHCR image |
| Close a PR that has `preview` | Same as removing the label |
| Open / push without the label | Nothing happens |

The preview URL is posted as a sticky comment on the PR once the deploy completes.

## Architecture

```
PR labeled `preview`   ──▶ GH Action: build  ──▶ docker build on GH runner
                                                 push to ghcr.io/a-u-supply/au-supply-preview:pr-N-<sha>
                       ──▶ GH Action: deploy ──▶ scripts/dokku-pr-provision.sh
                                                 ├─ apps:create (idempotent)
                                                 ├─ domains:set pr-<N>.dev.a-u.supply
                                                 ├─ storage:ensure + storage:mount
                                                 ├─ storage:mount au-supply-model-cache:ro
                                                 ├─ config:set SECRET_KEY STAGING=1 PR_NUMBER
                                                 ├─ ps:scale worker=0 web=1
                                                 ├─ git:from-image (Dokku pulls the prebuilt image)
                                                 ├─ wait up to 150s for web to be running
                                                 └─ letsencrypt:enable
                                             ▼
                              https://pr-<N>.dev.a-u.supply
PR label removed / closed ──▶ GH Action: destroy ──▶ apps:destroy --force
                                                     docker rmi the PR's image
                                                     delete the GHCR package version
Daily host cron ──▶ docker image prune -af --filter "until=24h"  (safety net)
```

## Why the build happens on GitHub runners, not the Dokku host

The host has 7.6 GB RAM, already loaded with prod + the fold stack (~4 GB steady). A `git push dokku` triggers a full image build (Astro + Python + ffmpeg + deno + ML deps, ~2 GB peak), which on the first attempt blew through swap and OOM-ed prod into a 502.

The new flow builds the image on GitHub's runners (16 GB+, ephemeral) and only pulls it on Dokku. Pull and start use ~250 MB peak — far below what prod needs. Builds and prod runtime no longer compete for the same RAM.

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

On the dokku host:

```
sudo crontab -e
# add:
0 4 * * *  docker image prune -af --filter "until=24h"
```

Runs daily at 04:00. The `until=24h` filter prevents pruning any image less than a day old (safety margin around rebuilds); `-a` lets it remove tagged images that have no running container. The PR destroy script also runs `docker image prune -f` immediately on teardown, so this cron is the safety net rather than the primary mechanism.

### 3. Let's Encrypt email (one-time, global)

If not already set:

```
ssh dokku letsencrypt:set --global email <ops@email>
```

### 4. GHCR pull credentials on Dokku (one-time)

The build job pushes to `ghcr.io/a-u-supply/au-supply-preview` as a private package. Dokku needs credentials to pull it:

1. Create a Personal Access Token (classic) with `read:packages` scope at https://github.com/settings/tokens
2. On the Dokku host:
   ```
   ssh dokku registry:login ghcr.io <github-username> <token>
   ```

This persists in the host's docker config; future PR previews pull without further setup.

(Alternative: make the `au-supply-preview` package public via the GitHub package settings page, in which case no credentials are needed. Choose based on whether the built image is considered sensitive.)

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

- **Per-PR:** `apps:destroy --force` on PR close removes container, config, domain, cert, storage volume. The destroy script then runs `docker image prune -f` to immediately reclaim the orphan layers (no wait for cron).
- **Image bloat:** daily host cron mops up anything the per-PR prune missed.
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

## Triggering a preview on an already-open PR

The workflow listens for `opened` / `synchronize` / `reopened` / `closed` events and does not fire on PRs that were open before it was merged. To get a preview for an existing PR, push an empty commit:

```
git commit --allow-empty -m "trigger preview" && git push
```

Or close and reopen the PR.

## Open items deferred to first real deploy

- Confirm `dokku:dokku` ownership of prod `au.db` (see step 4 above).
- If any PR env's `MODEL_CACHE_DIR` needs write access for a particular code path, drop the `:ro` from the storage mount.
- If concurrent PR pushes start backing up the Dokku build queue, swap the concurrency group to `cancel-in-progress: false` and serialize.
