# Bots (App Runner)

Bots are Docker images that process media for users. They live in their **own repos** — this repo only contains TOML manifests pointing at the images.

## Runtime flow

1. A user selects media into a **workspace** (admin UI → search → workspace).
2. They pick an **app** (a bot) and submit a **job**.
3. `worker.py` polls pending jobs, pulls the app's Docker image, and runs a container.
4. Worker mounts selected input files at `/work/input/` and collects outputs from `/work/output/`.
5. Output files attach back to the workspace and appear in the admin UI.

## Adding a new bot

1. **Create a new repo** with a Dockerfile that reads from `/work/input/` and writes to `/work/output/`. Bot code does *not* live in this repo.
2. **Build and push** the image to a registry the Dokku host can reach.
3. **Add a manifest** at `apps/<bot-name>.toml` in this repo, pointing at the image. Use an existing file in `apps/` as a template — the schema is small (image reference, label, input file types, CLI option exposure).
4. **After merge**: the manifest is picked up automatically on next deploy. On the server, you may need to sync the `AppDefinition` table — see `manage.py` for the subcommand.

## Why separate repos?

Bots tend to drag in heavy dependencies (Python ML libs, audio tooling, etc.) that we don't want bloating this web-app image. Keeping each bot in its own repo and image means:

- The web-app Docker image stays lean.
- Bots can be updated independently (bump the image tag in the TOML manifest — no web-app rebuild needed).
- Contributors can work on bot code without touching web-app code.

## What belongs as a bot vs. in this repo

**Bot** — anything that transforms media: audio effects, image processing, ML inference, transcription, format conversion.

**This repo** — anything that's a page, API endpoint, or UI feature of the web app itself.

If you're unsure, ask in the PR. See the "What Belongs Here vs. a New Repo" section of [`../CLAUDE.md`](../CLAUDE.md).

## Manifest format

See the existing files in `apps/` for the authoritative format. At minimum a manifest specifies:

- Image reference (registry + tag)
- Human-readable label shown in the admin UI
- Accepted input media types
- Which CLI options are exposed in the job submission form

**Expose ALL meaningful CLI options** when integrating a tool — if a flag is useful at the command line, it should be available in the job form.
