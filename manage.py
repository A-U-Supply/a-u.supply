"""Management CLI for a-u.supply. Runs inside the Docker container.

Usage (from host):
    ssh dokku run au-supply .venv/bin/python manage.py create-user <email> <password> <name> [role]
    ssh dokku run au-supply .venv/bin/python manage.py list-users
    ssh dokku run au-supply .venv/bin/python manage.py make-apikey <email> <label> <scope>
    ssh dokku run au-supply .venv/bin/python manage.py revoke-apikey <key-prefix>
    ssh dokku run au-supply .venv/bin/python manage.py migrate-index <old-index> <new-index>
    ssh dokku run au-supply .venv/bin/python manage.py seed-slack-mapping [--dry-run]
    ssh dokku run au-supply .venv/bin/python manage.py add-slack-mapping <slack-user-id> <user-email>
    ssh dokku run au-supply .venv/bin/python manage.py backfill-slack-uploader-id [--dry-run]
    ssh dokku run au-supply .venv/bin/python manage.py refresh-app <name>
    ssh dokku run au-supply .venv/bin/python manage.py refresh-all-apps
    ssh dokku run au-supply .venv/bin/python manage.py resync-votes [<media_id>]
    ssh dokku run au-supply .venv/bin/python manage.py backfill-ocr [--include-empty]
    ssh dokku run au-supply .venv/bin/python manage.py test-ocr <media_id>
    ssh dokku run au-supply .venv/bin/python manage.py backfill-ai-descriptions [--all] [--restart]
    ssh dokku run au-supply .venv/bin/python manage.py test-ai-description <media_id> [--write]
    ssh dokku run au-supply .venv/bin/python manage.py backfill-audio-ai-tags [--all]
    ssh dokku run au-supply .venv/bin/python manage.py index-drum-machines [--limit N]
"""

import json
import os
import sys
from pathlib import Path

from server.auth import hash_password
from server.models import SessionLocal, SlackUserMapping, User


def create_user(email: str, password: str, name: str, role: str = "member"):
    db = SessionLocal()
    if db.query(User).filter(User.email == email).first():
        print(f"ERROR: {email} already exists")
        db.close()
        sys.exit(1)
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    print(f"Created: {user.name} ({user.email}) role={user.role} id={user.id}")
    db.close()


def set_role(email: str, role: str):
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        print(f"ERROR: {email} not found")
        db.close()
        sys.exit(1)
    if role not in ("admin", "member"):
        print("ERROR: role must be admin or member")
        db.close()
        sys.exit(1)
    user.role = role
    db.commit()
    print(f"{user.name} ({user.email}) is now {user.role}")
    db.close()


def refresh_app(name: str):
    """Reload the AppDefinition row for ``name`` from ``apps/<name>.toml``.

    The ``app_definitions`` table stores a frozen copy of each app's manifest
    captured when the app was first registered. Editing ``apps/<name>.toml``
    in the repo doesn't update that row — hence this helper, so manifest
    tweaks can be applied in prod without a full re-register.
    """
    import tomllib
    from server.models import AppDefinition

    toml_path = Path(__file__).parent / "apps" / f"{name}.toml"
    if not toml_path.is_file():
        print(f"ERROR: {toml_path} not found")
        sys.exit(1)

    toml_text = toml_path.read_text()
    try:
        manifest = tomllib.loads(toml_text)
    except Exception as e:
        print(f"ERROR: invalid TOML in {toml_path}: {e}")
        sys.exit(1)

    db = SessionLocal()
    try:
        app = db.query(AppDefinition).filter(AppDefinition.name == name).first()
        if not app:
            print(f"ERROR: no app named {name!r} in the database")
            print("(register it first via POST /api/apps — this command only updates existing rows)")
            sys.exit(1)

        app.display_name = manifest.get("display_name", app.display_name)
        app.description = manifest.get("description", app.description)
        app.image = manifest.get("image", app.image)
        app.manifest = toml_text
        db.commit()
        print(f"Refreshed {name} ({toml_path.name} → DB):")
        print(f"  display_name={app.display_name}")
        print(f"  image={app.image}")
        print(f"  command={manifest.get('command') or '(none — image ENTRYPOINT)'}")
    finally:
        db.close()


def refresh_all_apps():
    """Refresh every app whose name matches an apps/<name>.toml file."""
    from server.models import AppDefinition

    apps_dir = Path(__file__).parent / "apps"
    toml_files = sorted(apps_dir.glob("*.toml")) if apps_dir.is_dir() else []
    if not toml_files:
        print("No apps/*.toml files found.")
        return

    db = SessionLocal()
    try:
        existing = {a.name for a in db.query(AppDefinition).all()}
    finally:
        db.close()

    updated = 0
    skipped = 0
    for path in toml_files:
        name = path.stem
        if name not in existing:
            print(f"  skip {name} (not registered in DB)")
            skipped += 1
            continue
        refresh_app(name)
        updated += 1
    print(f"\nRefreshed {updated} app(s), skipped {skipped}.")


def add_slack_mapping(slack_user_id: str, user_email: str):
    if not slack_user_id or not slack_user_id.startswith("U"):
        print(f"ERROR: slack_user_id {slack_user_id!r} should look like 'U...'")
        sys.exit(1)
    db = SessionLocal()
    user = db.query(User).filter(User.email == user_email).first()
    if not user:
        print(f"ERROR: no User with email {user_email}")
        db.close()
        sys.exit(1)
    existing = (
        db.query(SlackUserMapping)
        .filter(SlackUserMapping.slack_user_id == slack_user_id)
        .first()
    )
    if existing:
        if existing.user_id == user.id:
            print(f"No change: {slack_user_id} -> user {user.id} ({user.name})")
        else:
            prev = existing.user_id
            existing.user_id = user.id
            existing.email = user_email
            db.commit()
            print(f"Updated: {slack_user_id} user {prev} -> {user.id} ({user.name})")
    else:
        db.add(SlackUserMapping(slack_user_id=slack_user_id, user_id=user.id, email=user_email))
        db.commit()
        print(f"Added: {slack_user_id} -> user {user.id} ({user.name})")
    db.close()


def make_apikey(email: str, label: str, scope: str):
    from server.auth import generate_api_key, hash_api_key
    from server.models import ApiKey

    if scope not in ("read", "write", "admin"):
        print("ERROR: scope must be read, write, or admin")
        sys.exit(1)
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        print(f"ERROR: {email} not found")
        db.close()
        sys.exit(1)
    raw_key = "au_" + generate_api_key()
    ak = ApiKey(
        user_id=user.id,
        key_hash=hash_api_key(raw_key),
        key_prefix=raw_key[:11],
        label=label,
        scope=scope,
    )
    db.add(ak)
    db.commit()
    print(raw_key)
    db.close()


def revoke_apikey(prefix: str):
    from datetime import datetime, timezone
    from server.models import ApiKey

    db = SessionLocal()
    key = db.query(ApiKey).filter(
        ApiKey.key_prefix == prefix,
        ApiKey.revoked_at.is_(None),
    ).first()
    if not key:
        print(f"ERROR: no active key with prefix {prefix}")
        db.close()
        sys.exit(1)
    key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    print(f"Revoked: {key.label} ({key.key_prefix})")
    db.close()


def reindex_search():
    from server.models import MediaItem
    from server.search_client import configure_indexes, sync_media_item

    db = SessionLocal()
    configure_indexes()
    items = db.query(MediaItem).all()
    print(f"Re-indexing {len(items)} items...")
    for i, item in enumerate(items):
        sync_media_item(db, item)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(items)}")
    print(f"Done! Re-indexed {len(items)} items.")
    db.close()


def resync_votes(media_id: str | None = None):
    """Repush vote aggregates + voter lists into Meilisearch.

    Recovery for when SQLite holds the truth but Meilisearch is stale
    (process crash mid-debounce, Meili outage, hand-edited DB rows).
    """
    from server.models import MediaItem
    from server.search_client import update_vote_fields

    db = SessionLocal()
    try:
        q = db.query(MediaItem)
        if media_id:
            q = q.filter(MediaItem.id == media_id)
        items = q.all()
        if not items:
            print("No items matched.")
            return
        print(f"Resyncing vote fields for {len(items)} item(s)...")
        for i, item in enumerate(items):
            update_vote_fields(db, item)
            if (i + 1) % 200 == 0:
                print(f"  {i + 1}/{len(items)}")
        print(f"Done! Resynced {len(items)} item(s).")
    finally:
        db.close()


def color_histogram():
    import json
    from server.models import MediaImageMeta
    from server.search_client import _hex_to_color_groups

    db = SessionLocal()
    metas = db.query(MediaImageMeta).filter(MediaImageMeta.dominant_colors.isnot(None)).all()

    visual_groups = {}
    all_groups = {}
    neutrals = {"gray", "black", "white", "brown", "beige"}

    for m in metas:
        try:
            colors = json.loads(m.dominant_colors)
        except Exception:
            continue
        if not colors:
            continue
        # Visual = first chromatic color, fallback to #1 dominant
        visual = ""
        for c in colors:
            for g in _hex_to_color_groups(c):
                if g not in neutrals:
                    visual = g
                    break
            if visual:
                break
        if not visual:
            gs = _hex_to_color_groups(colors[0])
            visual = gs[0] if gs else "?"
        visual_groups[visual] = visual_groups.get(visual, 0) + 1
        for c in colors:
            for g in _hex_to_color_groups(c):
                all_groups[g] = all_groups.get(g, 0) + 1

    print(f"Total images with colors: {len(metas)}")
    print()
    print("VISUAL (first chromatic color, skip neutrals):")
    for g, count in sorted(visual_groups.items(), key=lambda x: -x[1]):
        bar = "#" * (count // 5)
        print(f"  {g:8s} {count:4d}  {bar}")
    print()
    print("ALL (all 5 colors):")
    for g, count in sorted(all_groups.items(), key=lambda x: -x[1]):
        bar = "#" * (count // 5)
        print(f"  {g:8s} {count:4d}  {bar}")
    db.close()


def color_overlap():
    import json
    from collections import Counter
    from server.models import MediaImageMeta

    db = SessionLocal()
    metas = db.query(MediaImageMeta).filter(MediaImageMeta.dominant_colors.isnot(None)).all()

    hex_to_items: dict[str, int] = Counter()
    total_colors = 0
    for m in metas:
        try:
            colors = json.loads(m.dominant_colors)
        except Exception:
            continue
        for c in colors:
            hex_to_items[c] += 1
            total_colors += 1

    unique = sum(1 for v in hex_to_items.values() if v == 1)
    shared = sum(1 for v in hex_to_items.values() if v > 1)
    print(f"Total hex colors: {total_colors}")
    print(f"Unique hex values: {len(hex_to_items)}")
    print(f"  Appear in 1 image only: {unique}")
    print(f"  Shared across 2+ images: {shared}")
    print()
    print("Most shared colors:")
    for hex_val, count in hex_to_items.most_common(20):
        print(f"  {hex_val}  appears in {count} images")
    db.close()


def source_audit():
    """Look at what source data actually exists — filenames, metadata, URLs."""
    import json
    from collections import Counter
    from server.models import MediaItem, MediaSource

    db = SessionLocal()

    # What source_types exist?
    sources = db.query(MediaSource).all()
    type_counts = Counter(s.source_type for s in sources)
    print(f"Total sources: {len(sources)}")
    print(f"Source types: {dict(type_counts)}")
    print()

    # What's in source_metadata?
    meta_keys = Counter()
    extractor_vals = Counter()
    has_url = 0
    url_domains = Counter()
    for s in sources:
        if s.source_url:
            has_url += 1
            try:
                from urllib.parse import urlparse
                domain = urlparse(s.source_url).netloc.lower()
                url_domains[domain] += 1
            except Exception:
                pass
        if s.source_metadata:
            try:
                meta = json.loads(s.source_metadata)
                if isinstance(meta, dict):
                    for k in meta.keys():
                        meta_keys[k] += 1
                    if "extractor" in meta:
                        extractor_vals[meta["extractor"]] += 1
            except Exception:
                pass

    print(f"Sources with URL: {has_url}")
    if url_domains:
        print("URL domains:")
        for d, c in url_domains.most_common(20):
            print(f"  {d}: {c}")
    print()
    print(f"Metadata keys found:")
    for k, c in meta_keys.most_common():
        print(f"  {k}: {c}")
    if extractor_vals:
        print()
        print("Extractor values:")
        for v, c in extractor_vals.most_common():
            print(f"  {v}: {c}")

    # Sample filenames for patterns
    items = db.query(MediaItem).limit(500).all()
    print()
    print(f"Sample filenames (first 30):")
    for item in items[:30]:
        print(f"  [{item.media_type}] {item.filename}")

    # Look for platform-like patterns in filenames
    patterns = Counter()
    for item in items:
        fn = (item.filename or "").lower()
        for pat in ["tiktok", "instagram", "ig_", "youtube", "yt_", "twitter", "x.com",
                     "reddit", "snapchat", "fb_", "facebook", "tumblr", "pinterest",
                     "screen shot", "screenshot", "img_", "photo-", "dsc", "dcim",
                     "dall-e", "dalle", "midjourney", "mj_", "stable", "comfyui"]:
            if pat in fn:
                patterns[pat] += 1
    if patterns:
        print()
        print("Filename patterns found:")
        for p, c in patterns.most_common():
            print(f"  '{p}': {c}")

    db.close()


def check_meta():
    from server.models import MediaItem, MediaImageMeta, MediaAudioMeta, MediaVideoMeta, ExtractionFailure
    db = SessionLocal()
    total = db.query(MediaItem).count()
    imgs = db.query(MediaItem).filter(MediaItem.media_type == "image").count()
    auds = db.query(MediaItem).filter(MediaItem.media_type == "audio").count()
    vids = db.query(MediaItem).filter(MediaItem.media_type == "video").count()
    img_meta = db.query(MediaImageMeta).count()
    aud_meta = db.query(MediaAudioMeta).count()
    vid_meta = db.query(MediaVideoMeta).count()
    failures = db.query(ExtractionFailure).filter(ExtractionFailure.resolved == False).count()
    print(f"Total items: {total} (images={imgs}, audio={auds}, video={vids})")
    print(f"Image meta: {img_meta}/{imgs}")
    print(f"Audio meta: {aud_meta}/{auds}")
    print(f"Video meta: {vid_meta}/{vids}")
    print(f"Unresolved failures: {failures}")
    db.close()


def log(msg):
    print(msg, flush=True)


def backfill_transcripts():
    """Find audio/video items missing transcripts and run whisper on them."""
    import os
    import tempfile
    from server.models import MediaItem, MediaAudioMeta, MediaVideoMeta
    from server.extraction import (
        transcribe_audio, _extract_audio_track, _has_audio_stream,
        _upsert_meta, SEARCH_MEDIA_DIR,
    )

    db = SessionLocal()

    # Audio items missing transcripts
    audio_missing = (
        db.query(MediaItem)
        .outerjoin(MediaAudioMeta)
        .filter(
            MediaItem.media_type == "audio",
            (MediaAudioMeta.transcript.is_(None)) | (MediaAudioMeta.media_item_id.is_(None)),
        )
        .all()
    )

    # Video items missing transcripts
    video_missing = (
        db.query(MediaItem)
        .outerjoin(MediaVideoMeta)
        .filter(
            MediaItem.media_type == "video",
            (MediaVideoMeta.audio_transcript.is_(None)) | (MediaVideoMeta.media_item_id.is_(None)),
        )
        .all()
    )

    total = len(audio_missing) + len(video_missing)
    log(f"Found {len(audio_missing)} audio + {len(video_missing)} video items missing transcripts ({total} total)")

    if total == 0:
        log("Nothing to do!")
        db.close()
        return

    done = 0
    skipped = 0
    errors = 0
    for i, item in enumerate(audio_missing + video_missing):
        full_path = os.path.join(SEARCH_MEDIA_DIR, item.file_path)
        if not os.path.exists(full_path):
            log(f"  SKIP {item.id} — file not found: {full_path}")
            skipped += 1
            continue

        log(f"  [{i + 1}/{total}] {item.media_type}: {item.filename}")

        try:
            if item.media_type == "audio":
                result = transcribe_audio(full_path)
                if result:
                    _upsert_meta(db, MediaAudioMeta, item.id, {
                        "transcript": result["transcript"],
                        "transcript_confidence": result["confidence"],
                    })
                    log(f"    OK ({len(result['transcript'])} chars, confidence={result['confidence']})")
                else:
                    _upsert_meta(db, MediaAudioMeta, item.id, {
                        "transcript": "",
                        "transcript_confidence": 0.0,
                    })
                    log(f"    No speech detected (marked)")
                done += 1

            elif item.media_type == "video":
                if not _has_audio_stream(full_path):
                    _upsert_meta(db, MediaVideoMeta, item.id, {
                        "audio_transcript": "",
                        "transcript_confidence": 0.0,
                    })
                    log(f"    No audio stream (marked)")
                    done += 1
                    continue
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    if not _extract_audio_track(full_path, tmp_path):
                        _upsert_meta(db, MediaVideoMeta, item.id, {
                            "audio_transcript": "",
                            "transcript_confidence": 0.0,
                        })
                        log(f"    Failed to extract audio (marked)")
                        done += 1
                        continue
                    result = transcribe_audio(tmp_path)
                    if result:
                        _upsert_meta(db, MediaVideoMeta, item.id, {
                            "audio_transcript": result["transcript"],
                            "transcript_confidence": result["confidence"],
                        })
                        log(f"    OK ({len(result['transcript'])} chars, confidence={result['confidence']})")
                    else:
                        _upsert_meta(db, MediaVideoMeta, item.id, {
                            "audio_transcript": "",
                            "transcript_confidence": 0.0,
                        })
                        log(f"    No speech detected (marked)")
                    done += 1
                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

        except Exception as exc:
            log(f"    ERROR: {exc}")
            errors += 1

    log(f"Done! Transcribed: {done}, Skipped: {skipped}, Errors: {errors}")
    db.close()

    log("Re-indexing all items to populate has_transcript filter...")
    reindex_search()


_OCR_CHECKPOINT_FILE = "/app/data/.ocr-backfill-progress"


def backfill_ocr(
    include_empty: bool = False,
    force_all: bool = False,
    restart: bool = False,
):
    """Find images missing OCR text and run OCR on them.

    By default picks up only items with caption=NULL (never tried).
    With include_empty=True, also re-processes items where caption="" —
    use this after improving the OCR pipeline to re-OCR images that
    previously returned no text under the old configuration.
    With force_all=True, re-OCRs every image regardless of current caption
    state — use this after swapping the OCR engine entirely.

    Progress is checkpointed to ``.ocr-backfill-progress`` in the data
    volume after every processed item, so deploys/SSH drops/server reboots
    can interrupt a run and a fresh invocation picks up exactly where it
    left off. Pass ``restart=True`` to wipe the checkpoint and start over.
    """
    import os
    from server.models import MediaItem, MediaImageMeta
    from server.extraction import extract_text_ocr, _upsert_meta, SEARCH_MEDIA_DIR

    db = SessionLocal()

    query = db.query(MediaItem).outerjoin(MediaImageMeta).filter(
        MediaItem.media_type == "image",
    )
    if not force_all:
        caption_filter = MediaImageMeta.caption.is_(None)
        if include_empty:
            caption_filter = caption_filter | (MediaImageMeta.caption == "")
        query = query.filter(caption_filter | (MediaImageMeta.media_item_id.is_(None)))
    images_missing = query.all()

    if restart and os.path.exists(_OCR_CHECKPOINT_FILE):
        os.remove(_OCR_CHECKPOINT_FILE)
        log("Checkpoint cleared (restart=True).")

    done_ids: set[str] = set()
    if os.path.exists(_OCR_CHECKPOINT_FILE):
        with open(_OCR_CHECKPOINT_FILE) as f:
            done_ids = {line.strip() for line in f if line.strip()}
        log(f"Resuming from checkpoint: {len(done_ids)} items already processed.")

    pending = [it for it in images_missing if it.id not in done_ids]
    total = len(images_missing)
    remaining = len(pending)
    log(
        f"Found {total} images to OCR "
        f"(include_empty={include_empty}, force_all={force_all}); "
        f"{remaining} remaining after checkpoint."
    )

    if remaining == 0:
        log("Nothing to do — clearing checkpoint.")
        if os.path.exists(_OCR_CHECKPOINT_FILE):
            os.remove(_OCR_CHECKPOINT_FILE)
        db.close()
        return

    done = 0
    errors = 0
    for i, item in enumerate(pending):
        full_path = os.path.join(SEARCH_MEDIA_DIR, item.file_path)
        if not os.path.exists(full_path):
            log(f"  SKIP {item.id} — file not found: {full_path}")
            errors += 1
            # Still mark checkpoint so we don't keep retrying a missing file.
            with open(_OCR_CHECKPOINT_FILE, "a") as f:
                f.write(item.id + "\n")
            continue

        log(f"  [{i + 1}/{remaining}] {item.filename}")

        try:
            text = extract_text_ocr(full_path)
            _upsert_meta(db, MediaImageMeta, item.id, {"caption": text or ""})
            if text:
                log(f"    OK ({len(text)} chars)")
            else:
                log(f"    No text (marked)")
            done += 1
        except Exception as exc:
            log(f"    ERROR: {exc}")
            errors += 1

        # Checkpoint after every item — survives container kills.
        with open(_OCR_CHECKPOINT_FILE, "a") as f:
            f.write(item.id + "\n")

    log(f"Done! OCR processed: {done}, Errors: {errors}")
    # Clean up checkpoint on full completion so the next backfill starts fresh.
    if os.path.exists(_OCR_CHECKPOINT_FILE):
        os.remove(_OCR_CHECKPOINT_FILE)
    db.close()

    log("Re-indexing all items to populate has_text filter...")
    reindex_search()


_AI_DESC_CHECKPOINT_FILE = "/app/data/.ai-desc-backfill-progress"


def backfill_ai_descriptions(
    force_all: bool = False,
    restart: bool = False,
):
    """Generate AI descriptions for every image that doesn't have one yet.

    By default picks up items where ai_description_generated_at IS NULL.
    With force_all=True, re-generates for every image regardless of state —
    use after the prompt version bumps and you want to roll out new fields.

    Same checkpoint pattern as backfill_ocr — progress survives deploys /
    SSH drops / server reboots. Use ``restart=True`` to wipe the checkpoint.

    Reads DEEPSEEK_API_KEY from env; aborts early with a clear error if it
    isn't set.
    """
    import os
    from server.models import MediaItem, MediaImageMeta
    from server.extraction import (
        _apply_ai_description,
        _upsert_meta,
        SEARCH_MEDIA_DIR,
    )
    from server.ai_description import DeepSeekError, generate_ai_description

    if not (os.environ.get("VISION_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        log("ERROR: VISION_API_KEY (or legacy DEEPSEEK_API_KEY) is not set in the environment.")
        log("Set it with: ssh dokku config:set au-supply VISION_API_KEY=sk-...")
        return

    db = SessionLocal()

    query = db.query(MediaItem).outerjoin(MediaImageMeta).filter(
        MediaItem.media_type == "image",
    )
    if not force_all:
        query = query.filter(
            (MediaImageMeta.ai_description_generated_at.is_(None))
            | (MediaImageMeta.media_item_id.is_(None))
        )
    images = query.all()

    if restart and os.path.exists(_AI_DESC_CHECKPOINT_FILE):
        os.remove(_AI_DESC_CHECKPOINT_FILE)
        log("Checkpoint cleared (restart=True).")

    done_ids: set[str] = set()
    if os.path.exists(_AI_DESC_CHECKPOINT_FILE):
        with open(_AI_DESC_CHECKPOINT_FILE) as f:
            done_ids = {line.strip() for line in f if line.strip()}
        log(f"Resuming from checkpoint: {len(done_ids)} items already processed.")

    pending = [it for it in images if it.id not in done_ids]
    total = len(images)
    remaining = len(pending)
    log(
        f"Found {total} images to describe (force_all={force_all}); "
        f"{remaining} remaining after checkpoint."
    )

    if remaining == 0:
        log("Nothing to do — clearing checkpoint.")
        if os.path.exists(_AI_DESC_CHECKPOINT_FILE):
            os.remove(_AI_DESC_CHECKPOINT_FILE)
        db.close()
        return

    done = 0
    errors = 0
    for i, item in enumerate(pending):
        full_path = os.path.join(SEARCH_MEDIA_DIR, item.file_path)
        if not os.path.exists(full_path):
            log(f"  SKIP {item.id} — file not found: {full_path}")
            errors += 1
            with open(_AI_DESC_CHECKPOINT_FILE, "a") as f:
                f.write(item.id + "\n")
            continue

        meta = (
            db.query(MediaImageMeta)
            .filter(MediaImageMeta.media_item_id == item.id)
            .first()
        )
        ocr_caption = meta.caption if meta else None
        overrides_json = meta.ai_overrides if meta else None
        try:
            overrides = json.loads(overrides_json) if overrides_json else {}
        except (TypeError, ValueError):
            overrides = {}

        log(f"  [{i + 1}/{remaining}] {item.filename}")

        try:
            ai = generate_ai_description(full_path, ocr_caption=ocr_caption)
            kwargs: dict = {}
            _apply_ai_description(kwargs, ai, ai_overrides=overrides)
            _upsert_meta(db, MediaImageMeta, item.id, kwargs)
            desc = ai.get("description") or ""
            log(f"    OK ({len(desc)} chars, {ai.get('tokens_in', 0)}+{ai.get('tokens_out', 0)} tokens)")
            done += 1
        except DeepSeekError as exc:
            log(f"    ERROR (deepseek): {exc}")
            errors += 1
        except Exception as exc:
            log(f"    ERROR: {exc}")
            errors += 1

        with open(_AI_DESC_CHECKPOINT_FILE, "a") as f:
            f.write(item.id + "\n")

    log(f"Done! AI descriptions: {done}, Errors: {errors}")
    if _os.path.exists(_AI_DESC_CHECKPOINT_FILE):
        _os.remove(_AI_DESC_CHECKPOINT_FILE)
    db.close()

    log("Re-indexing all items to populate AI fields in search...")
    reindex_search()


_AUDIO_AI_CHECKPOINT = "/app/data/.audio-ai-backfill-progress"


def backfill_audio_ai_tags(force_all: bool = False, restart: bool = False):
    import json as _json
    import os as _os
    import uuid as _uuid

    from server.extraction import SEARCH_MEDIA_DIR, _detect_voice_from_filename
    from server.ai_audio import generate_audio_ai_descriptions
    from server.models import (
        MediaAudioMeta,
        MediaItem,
        MediaSource,
        MediaTag,
        SessionLocal as _SL,
    )
    from server.search_client import sync_media_item

    if restart and _os.path.exists(_AUDIO_AI_CHECKPOINT):
        _os.remove(_AUDIO_AI_CHECKPOINT)
        log("Checkpoint cleared (restart=True).")

    _db = _SL()
    try:
        rows = (
            _db.query(MediaItem.id, MediaItem.file_path, MediaSource.source_metadata)
            .join(MediaSource, MediaSource.media_item_id == MediaItem.id)
            .filter(MediaSource.source_type == "sample_library")
            .all()
        )
    finally:
        _db.close()

    if not rows:
        log("No sample_library items found.")
        return

    total = len(rows)
    if not force_all:
        _db = _SL()
        try:
            already = {r[0] for r in _db.query(MediaAudioMeta.media_item_id).filter(
                MediaAudioMeta.acoustic_tags.isnot(None),
            ).all()}
        finally:
            _db.close()
        rows = [r for r in rows if r[0] not in already]
    log(f"Found {total} total, {len(rows)} need AI tagging")

    if not rows:
        log("Nothing to do.")
        return

    done_ids: set[str] = set()
    if _os.path.exists(_AUDIO_AI_CHECKPOINT):
        with open(_AUDIO_AI_CHECKPOINT) as f:
            done_ids = {line.strip() for line in f if line.strip()}
        log(f"Checkpoint: {len(done_ids)} already done, resuming")

    by_context: dict[str, list[tuple[str, str]]] = {}
    for mid, fp, smeta in rows:
        if mid in done_ids:
            continue
        ctx = "unknown"
        if smeta:
            try:
                meta = _json.loads(smeta) if isinstance(smeta, str) else smeta
                ctx = meta.get("dir") or meta.get("machine_name") or "unknown"
            except Exception:
                pass
        by_context.setdefault(ctx, []).append((mid, fp))

    if not by_context:
        log("All items already done (checkpoint).")
        return

    remaining = sum(len(v) for v in by_context.values())
    log(f"  {remaining} remaining across {len(by_context)} groups")

    tagged_total = len(done_ids)
    for ctx, items in by_context.items():
        batch = [(mid, _os.path.join(SEARCH_MEDIA_DIR, fp), ctx) for mid, fp in items]
        fnames = [_os.path.basename(fp) for _, fp, _ in batch]

        ai_results = {}
        try:
            ai_results = generate_audio_ai_descriptions(fnames, dir_name=ctx)
        except Exception as exc:
            log(f"  ERROR AI '{ctx}': {exc}")

        _db = _SL()
        try:
            for mid, fp, _ in batch:
                fn = _os.path.basename(fp)
                ai = ai_results.get(fn, {})
                ai_tags = ai.get("tags", [])
                ai_voice = ai.get("voice") or _detect_voice_from_filename(fn)
                ai_instrument = ai.get("instrument") or ctx.lower().replace(" ", "-").replace("_", "-")

                if ai.get("description"):
                    item = _db.query(MediaItem).filter(MediaItem.id == mid).first()
                    if item:
                        item.description = ai["description"]

                if ai_tags or ai_voice or ai_instrument:
                    acoustic = {}
                    if ai_voice:
                        acoustic["voice"] = ai_voice
                    if ai_instrument:
                        acoustic["instrument"] = ai_instrument
                    if ai_tags:
                        acoustic["ai_tags"] = ai_tags
                    acoustic_json = _json.dumps(acoustic)
                    existing = _db.query(MediaAudioMeta).filter(
                        MediaAudioMeta.media_item_id == mid).first()
                    if existing:
                        existing.acoustic_tags = acoustic_json
                    else:
                        _db.add(MediaAudioMeta(media_item_id=mid, acoustic_tags=acoustic_json))
                    for tag in ai_tags:
                        if not _db.query(MediaTag).filter(
                            MediaTag.media_item_id == mid, MediaTag.tag == tag).first():
                            _db.add(MediaTag(id=str(_uuid.uuid4()), media_item_id=mid, tag=tag))

                with open(_AUDIO_AI_CHECKPOINT, "a") as f:
                    f.write(mid + "\n")

            _db.commit()
            tagged_total += len(items)
            log(f"  [{tagged_total}/{remaining + len(done_ids)}] {ctx}: {len(items)} tagged ({tagged_total} total)")

            for mid, _fp in items:
                item = _db.query(MediaItem).filter(MediaItem.id == mid).first()
                if item:
                    try:
                        sync_media_item(_db, item)
                    except Exception:
                        pass
        except Exception as exc:
            log(f"  ERROR apply '{ctx}': {exc}")
            _db.rollback()
        finally:
            _db.close()

    if _os.path.exists(_AUDIO_AI_CHECKPOINT):
        _os.remove(_AUDIO_AI_CHECKPOINT)
    log(f"Done. Tagged {tagged_total} / {total} total items.")


def list_users():
    db = SessionLocal()
    users = db.query(User).all()
    for u in users:
        print(f"{u.id} | {u.email} | {u.name} | {u.role}")
    db.close()


def backfill_image_thumbnails():
    """Generate sm + md + lg WebP thumbnails for any image missing one."""
    import os
    from server.models import MediaItem
    from server.extraction import (
        SEARCH_MEDIA_DIR,
        _image_thumbnail_path,
        _image_thumbnail_sm_path,
        _image_thumbnail_lg_path,
        generate_image_thumbnail,
        generate_image_thumbnail_sm,
        generate_image_thumbnail_lg,
    )

    db = SessionLocal()
    images = db.query(MediaItem).filter(MediaItem.media_type == "image").all()

    total = len(images)
    log(f"Scanning {total} image items for missing thumbnails")

    md_done = sm_done = lg_done = fully_skipped = errors = 0
    for i, item in enumerate(images):
        full_path = os.path.join(SEARCH_MEDIA_DIR, item.file_path)
        if not os.path.exists(full_path):
            log(f"  SKIP {item.id} — source missing: {full_path}")
            errors += 1
            continue

        md_path = _image_thumbnail_path(full_path)
        sm_path = _image_thumbnail_sm_path(full_path)
        lg_path = _image_thumbnail_lg_path(full_path)
        md_exists = os.path.exists(md_path)
        sm_exists = os.path.exists(sm_path)
        lg_exists = os.path.exists(lg_path)

        if md_exists and sm_exists and lg_exists:
            fully_skipped += 1
            continue

        log(f"  [{i + 1}/{total}] {item.filename}")
        if not md_exists:
            if generate_image_thumbnail(full_path, md_path):
                md_done += 1
            else:
                errors += 1
        if not sm_exists:
            if generate_image_thumbnail_sm(full_path, sm_path):
                sm_done += 1
            else:
                errors += 1
        if not lg_exists:
            if generate_image_thumbnail_lg(full_path, lg_path):
                lg_done += 1
            else:
                errors += 1

    log(
        f"Done! md: {md_done}, sm: {sm_done}, lg: {lg_done}, "
        f"already had all: {fully_skipped}, errors: {errors}"
    )
    db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "create-user":
        if len(sys.argv) < 5:
            print("Usage: manage.py create-user <email> <password> <name> [role]")
            sys.exit(1)
        role = sys.argv[5] if len(sys.argv) > 5 else "member"
        create_user(sys.argv[2], sys.argv[3], sys.argv[4], role)

    elif cmd == "set-role":
        if len(sys.argv) < 4:
            print("Usage: manage.py set-role <email> <role>")
            sys.exit(1)
        set_role(sys.argv[2], sys.argv[3])

    elif cmd == "list-users":
        list_users()

    elif cmd == "make-apikey":
        if len(sys.argv) < 5:
            print("Usage: manage.py make-apikey <email> <label> <scope>")
            print("  scope: read, write, or admin")
            sys.exit(1)
        make_apikey(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "revoke-apikey":
        if len(sys.argv) < 3:
            print("Usage: manage.py revoke-apikey <key-prefix>")
            sys.exit(1)
        revoke_apikey(sys.argv[2])

    elif cmd == "check-meta":
        check_meta()

    elif cmd == "reindex":
        reindex_search()

    elif cmd == "color-histogram":
        color_histogram()

    elif cmd == "color-overlap":
        color_overlap()

    elif cmd == "source-audit":
        source_audit()

    elif cmd == "backfill-posters":
        from server.slack_scraper import backfill_posters
        result = backfill_posters()
        print(f"Updated: {result['updated']}, Errors: {result['errors']}")

    elif cmd == "add-slack-mapping":
        if len(sys.argv) < 4:
            print("Usage: manage.py add-slack-mapping <slack-user-id> <user-email>")
            sys.exit(1)
        add_slack_mapping(sys.argv[2], sys.argv[3])

    elif cmd == "seed-slack-mapping":
        from server.slack_scraper import seed_slack_user_mapping
        dry_run = "--dry-run" in sys.argv[2:]
        result = seed_slack_user_mapping(dry_run=dry_run)
        print(f"Inserted: {result['inserted']}")
        print(f"Updated: {result['updated']}")
        print(f"Skipped (bots/deleted): {result['skipped_bots_or_deleted']}")
        print(f"Unmatched: {len(result['unmatched'])}")
        for u in result["unmatched"]:
            print(f"  - {u.get('slack_user_id')} | {u.get('name', '')} | {u.get('email', '-')} | {u.get('reason')}")
        if result["dry_run"]:
            print("(dry-run — nothing written)")

    elif cmd == "backfill-slack-uploader-id":
        from server.slack_scraper import backfill_slack_uploader_id
        dry_run = "--dry-run" in sys.argv[2:]
        result = backfill_slack_uploader_id(dry_run=dry_run)
        print(f"Scanned: {result['scanned']}")
        print(f"Updated: {result['updated']}")
        print(f"Missing slack_user_id in metadata: {result['missing_slack_user_id_in_metadata']}")
        if result["unmapped_slack_users"]:
            print("Unmapped slack users (run seed-slack-mapping + backfill-posters first):")
            for uid, n in sorted(result["unmapped_slack_users"].items(), key=lambda x: -x[1]):
                print(f"  {uid}: {n} rows")
        if result["dry_run"]:
            print("(dry-run — nothing written)")

    elif cmd == "backfill-text":
        from server.slack_scraper import backfill_message_text
        result = backfill_message_text()
        print(f"Updated: {result['updated']}, Errors: {result['errors']}")

    elif cmd == "backfill-transcripts":
        backfill_transcripts()

    elif cmd == "backfill-ocr":
        include_empty = "--include-empty" in sys.argv[2:]
        force_all = "--all" in sys.argv[2:]
        restart = "--restart" in sys.argv[2:]
        backfill_ocr(include_empty=include_empty, force_all=force_all, restart=restart)

    elif cmd == "test-ocr":
        if len(sys.argv) < 3:
            print("Usage: manage.py test-ocr <media_id>")
            sys.exit(1)
        import os as _os
        from server.models import MediaItem, MediaImageMeta
        from server.extraction import extract_text_ocr, SEARCH_MEDIA_DIR
        db = SessionLocal()
        item = db.query(MediaItem).filter(MediaItem.id == sys.argv[2]).first()
        if not item:
            print(f"no MediaItem with id={sys.argv[2]}")
            sys.exit(1)
        full_path = _os.path.join(SEARCH_MEDIA_DIR, item.file_path)
        print(f"file: {full_path}")
        print(f"exists: {_os.path.exists(full_path)}")
        meta = db.query(MediaImageMeta).filter(MediaImageMeta.media_item_id == item.id).first()
        print(f"current caption: {meta.caption!r}" if meta else "no image_meta row")

        # Per-word breakdown — informational only; extract_text_ocr below
        # is what actually feeds the pipeline.
        from PIL import Image as _Image
        import numpy as _np
        from server.extraction import _get_easyocr_reader, _OCR_MAX_DIM
        with _Image.open(full_path) as _img:
            _img = _img.convert("RGB")
            print(f"orig size: {_img.size}")
            _max = max(_img.size)
            if _max > _OCR_MAX_DIM:
                _scale = _OCR_MAX_DIM / _max
                _img = _img.resize((int(_img.width * _scale), int(_img.height * _scale)), _Image.LANCZOS)
                print(f"downscaled to: {_img.size}")
            _arr = _np.array(_img)
        _reader = _get_easyocr_reader()
        _results = _reader.readtext(_arr, detail=1, paragraph=False)
        for _bbox, _t, _c in _results:
            print(f"  {_t!r} conf={_c:.2f}")

        text = extract_text_ocr(full_path)
        print(f"new ocr result: {text!r}")

        if "--write" in sys.argv[3:]:
            from server.extraction import _upsert_meta, _sync_to_search
            _upsert_meta(db, MediaImageMeta, item.id, {"caption": text or ""})
            print("wrote caption to DB")
            db.refresh(item)
            _sync_to_search(db, item)
            print("synced to search index")
        db.close()

    elif cmd == "backfill-ai-descriptions":
        force_all = "--all" in sys.argv[2:]
        restart = "--restart" in sys.argv[2:]
        backfill_ai_descriptions(force_all=force_all, restart=restart)

    elif cmd == "test-ai-description":
        if len(sys.argv) < 3:
            print("Usage: manage.py test-ai-description <media_id> [--write]")
            sys.exit(1)
        import os as _os
        from server.models import MediaItem, MediaImageMeta
        from server.extraction import (
            SEARCH_MEDIA_DIR,
            _apply_ai_description,
            _upsert_meta,
            _sync_to_search,
        )
        from server.ai_description import generate_ai_description

        db = SessionLocal()
        item = db.query(MediaItem).filter(MediaItem.id == sys.argv[2]).first()
        if not item:
            print(f"no MediaItem with id={sys.argv[2]}")
            sys.exit(1)
        full_path = _os.path.join(SEARCH_MEDIA_DIR, item.file_path)
        print(f"file: {full_path}")
        print(f"exists: {_os.path.exists(full_path)}")
        meta = db.query(MediaImageMeta).filter(MediaImageMeta.media_item_id == item.id).first()
        if meta is None:
            print("no image_meta row")
        else:
            print(f"current ai_description: {meta.ai_description!r}")
            print(f"current ai_tags: {meta.ai_tags!r}")
            print(f"current ai_vibe: {meta.ai_vibe!r}")

        ocr_caption = meta.caption if meta else None
        try:
            ai = generate_ai_description(full_path, ocr_caption=ocr_caption)
        except Exception as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)

        print(f"\nmodel: {ai['model']} | prompt: {ai['prompt_version']} | tokens: {ai['tokens_in']}+{ai['tokens_out']}")
        print(f"description:\n  {ai['description']}")
        print(f"tags: {ai['tags']}")
        print(f"color_temperature: {ai['color_temperature']}")
        print(f"color_character: {ai['color_character']}")
        print(f"vibe: {ai['vibe']}")
        print("flags:")
        for fname, fval in ai["flags"].items():
            print(f"  {fname}: {fval}")

        if "--write" in sys.argv[3:]:
            overrides_json = meta.ai_overrides if meta else None
            try:
                overrides = json.loads(overrides_json) if overrides_json else {}
            except (TypeError, ValueError):
                overrides = {}
            kwargs: dict = {}
            _apply_ai_description(kwargs, ai, ai_overrides=overrides)
            _upsert_meta(db, MediaImageMeta, item.id, kwargs)
            print("\nwrote AI fields to DB")
            db.refresh(item)
            _sync_to_search(db, item)
            print("synced to search index")
        db.close()

    elif cmd == "backfill-thumbnails":
        backfill_image_thumbnails()

    elif cmd == "refresh-app":
        if len(sys.argv) < 3:
            print("Usage: manage.py refresh-app <name>")
            sys.exit(1)
        refresh_app(sys.argv[2])

    elif cmd == "refresh-all-apps":
        refresh_all_apps()

    elif cmd == "resync-votes":
        target = sys.argv[2] if len(sys.argv) >= 3 else None
        resync_votes(target)

    elif cmd == "migrate-index":
        if len(sys.argv) < 4:
            print("Usage: manage.py migrate-index <old-index> <new-index>")
            sys.exit(1)
        old_idx, new_idx = sys.argv[2], sys.argv[3]
        from server.models import MediaItem, MediaTag
        db = SessionLocal()
        items = db.query(MediaItem).filter(MediaItem.output_index == old_idx).all()
        print(f"Found {len(items)} items with output_index={old_idx}")
        for item in items:
            item.output_index = new_idx
            old_tag = db.query(MediaTag).filter(
                MediaTag.media_item_id == item.id,
                MediaTag.tag == f"index:{old_idx}",
            ).first()
            if old_tag:
                old_tag.tag = f"index:{new_idx}"
            print(f"  {item.id}: {old_idx} -> {new_idx}")
        db.commit()
        db.close()
        print(f"Migrated {len(items)} items. Running reindex...")
        reindex_search()

    elif cmd == "clean-sample-orphans":
        from server.models import MediaItem, SessionLocal as _SL
        _db = _SL()
        _n = _db.query(MediaItem).filter(MediaItem.filename.is_(None)).delete()
        _db.commit()
        log(f"Deleted {_n} orphaned items with null filename")
        _db.close()

    elif cmd == "reset-samples-index":
        from server.search_client import SAMPLES_INDEX, get_client
        _c = get_client()
        try:
            _c.delete_index(SAMPLES_INDEX)
            log(f"Deleted Meilisearch index '{SAMPLES_INDEX}'")
        except Exception as _e:
            log(f"Delete failed (may not exist): {_e}")
        log("Recreating index...")
        _c.create_index(SAMPLES_INDEX, {"primaryKey": "id"})
        log(f"Created index '{SAMPLES_INDEX}'")
        from server.search_client import configure_indexes
        configure_indexes()

    elif cmd == "index-samples":
        import subprocess as _sp
        from server.models import MediaItem, MediaSource, SessionLocal as _SL
        # Clear any orphaned sample records that were never synced to Meilisearch
        _db = _SL()
        _ids = [r[0] for r in _db.query(MediaSource.media_item_id).filter(MediaSource.source_type == "sample_library").all()]
        if _ids:
            _db.query(MediaItem).filter(MediaItem.id.in_(_ids)).delete(synchronize_session=False)
            _db.commit()
            log(f"Cleared {len(_ids)} orphaned sample records")
        _db.close()
        zip_dir = os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data")
        zip_path = os.path.join(zip_dir, "music2000.zip")
        if not os.path.exists(zip_path):
            log("Downloading Music 2000 sample library zip...")
            r = _sp.run([
                "curl", "-L", "-o", zip_path,
                "https://archive.org/download/music-2000-sample-library-44k-wav-rip/Music_2000_Sample_library_44k_WAV_RIP.zip",
            ])
            if r.returncode != 0:
                print(f"Download failed (exit {r.returncode})")
                sys.exit(1)
        log("Running index_samples.py...")
        r = _sp.run([".venv/bin/python", "scripts/index_samples.py", zip_path])
        sys.exit(r.returncode)

    elif cmd == "audit-samples":
        from sqlalchemy import func
        from server.models import MediaItem, MediaSource, MediaAudioMeta, SessionLocal as _SL
        _db = _SL()
        total_srcs = _db.query(func.count(MediaSource.id)).filter(
            MediaSource.source_type == "sample_library").scalar() or 0
        unique_items = _db.query(func.count(func.distinct(MediaSource.media_item_id))).filter(
            MediaSource.source_type == "sample_library").scalar() or 0
        dupe_sources = _db.query(
            MediaSource.media_item_id, func.count(MediaSource.id).label("c")
        ).filter(MediaSource.source_type == "sample_library").group_by(
            MediaSource.media_item_id).having(func.count(MediaSource.id) > 1).count()
        with_tags = _db.query(func.count(MediaAudioMeta.media_item_id)).filter(
            MediaAudioMeta.acoustic_tags.isnot(None),
            MediaAudioMeta.media_item_id.in_(
                _db.query(MediaSource.media_item_id).filter(
                    MediaSource.source_type == "sample_library")
            )
        ).scalar() or 0
        _db.close()
        log(f"sample_library sources: {total_srcs} rows, {unique_items} unique items, {dupe_sources} items with multiple sources")
        log(f"have acoustic_tags: {with_tags} / {unique_items}")

    elif cmd == "dedup-sample-sources":
        from sqlalchemy import func
        from server.models import MediaItem, MediaSource, SessionLocal as _SL
        _db = _SL()
        dupe_rows = _db.query(
            MediaSource.media_item_id, func.count(MediaSource.id).label("c"),
            func.min(MediaSource.id).label("keep_id")
        ).filter(MediaSource.source_type == "sample_library").group_by(
            MediaSource.media_item_id).having(func.count(MediaSource.id) > 1).all()
        deleted = 0
        for mid, cnt, keep_id in dupe_rows:
            _db.query(MediaSource).filter(
                MediaSource.source_type == "sample_library",
                MediaSource.media_item_id == mid,
                MediaSource.id != keep_id,
            ).delete(synchronize_session=False)
            deleted += cnt - 1
        _db.commit()
        _db.close()
        log(f"Deleted {deleted} duplicate MediaSource rows")

    elif cmd == "export-untagged-audio":
        """Export JSON of untagged sample_library items for offline AI tagging."""
        import json
        from server.models import MediaAudioMeta, MediaItem, MediaSource, SessionLocal as _SL
        _db = _SL()
        rows = _db.query(
            MediaItem.id, MediaItem.file_path, MediaSource.source_metadata
        ).join(MediaSource, MediaSource.media_item_id == MediaItem.id).filter(
            MediaSource.source_type == "sample_library"
        ).all()
        already = {r[0] for r in _db.query(MediaAudioMeta.media_item_id).filter(
            MediaAudioMeta.acoustic_tags.isnot(None)).all()}
        _db.close()
        items = []
        for mid, fp, smeta in rows:
            if mid in already:
                continue
            ctx = "unknown"
            if smeta:
                try:
                    meta = json.loads(smeta) if isinstance(smeta, str) else smeta
                    ctx = meta.get("dir") or meta.get("machine_name") or "unknown"
                except Exception:
                    pass
            fn = fp.rsplit("/", 1)[-1] if fp else ""
            items.append({"id": mid, "filename": fn, "ctx": ctx})
        print(json.dumps(items))

    elif cmd == "import-ai-tags":
        """Apply AI tags from /app/data/ai-tags.json."""
        import json, uuid
        from server.models import MediaAudioMeta, MediaItem, MediaTag, SessionLocal as _SL
        from server.search_client import sync_media_item
        filepath = os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data") + "/ai-tags.json"
        if not os.path.exists(filepath):
            log(f"ERROR: {filepath} not found")
        else:
            data = json.load(open(filepath))
            from sqlalchemy import text
            _db = _SL()
            updated = 0
            for entry in data:
                mid = entry["id"]
                acoustic = {}
                if entry.get("voice"):
                    acoustic["voice"] = entry["voice"]
                if entry.get("instrument"):
                    acoustic["instrument"] = entry["instrument"]
                if entry.get("ai_tags"):
                    acoustic["ai_tags"] = entry["ai_tags"]
                if not acoustic:
                    continue
                acoustic_json = json.dumps(acoustic)
                _db.execute(text(
                    "INSERT INTO media_audio_meta (media_item_id, duration_seconds, sample_rate, channels, acoustic_tags) "
                    "VALUES (:mid, 0, 0, 0, :tags) "
                    "ON CONFLICT(media_item_id) DO UPDATE SET acoustic_tags = :tags2"
                ), {"mid": mid, "tags": acoustic_json, "tags2": acoustic_json})
                if entry.get("description"):
                    _db.execute(text(
                        "UPDATE media_items SET description = :desc WHERE id = :mid"
                    ), {"mid": mid, "desc": entry["description"]})
                updated += 1
                if updated % 1000 == 0:
                    _db.commit()
                    log(f"  applied {updated}/{len(data)}")
            _db.commit()
            log(f"Applied {updated} items. Syncing Meilisearch...")
            for entry in data:
                item = _db.query(MediaItem).filter(MediaItem.id == entry["id"]).first()
                if item:
                    try:
                        sync_media_item(_db, item)
                    except Exception:
                        pass
            _db.close()
            log(f"Done. {updated} items tagged and synced.")

    elif cmd == "clean-drum-machine-orphans":
        from server.models import MediaItem, MediaSource, SessionLocal as _SL
        _db = _SL()
        _ids = [r[0] for r in _db.query(MediaSource.media_item_id).filter(
            MediaSource.source_type == "sample_library",
            MediaSource.source_metadata.like('%drum-machines%'),
        ).all()]
        if _ids:
            _db.query(MediaItem).filter(MediaItem.id.in_(_ids)).delete(synchronize_session=False)
            _db.commit()
            log(f"Cleared {len(_ids)} orphaned drum-machine sample records")
        _db.close()

    elif cmd == "backfill-audio-ai-tags":
        force_all = "--all" in sys.argv
        restart = "--restart" in sys.argv
        backfill_audio_ai_tags(force_all=force_all, restart=restart)

    elif cmd == "index-drum-machines":
        import subprocess as _sp
        dl_dir = os.environ.get("SEARCH_MEDIA_DIR", "/app/search-data")
        cmd_parts = [".venv/bin/python", "scripts/index_drum_machines.py",
                     "--download-dir", os.path.join(dl_dir, "drum-machine-zips")]
        if os.environ.get("DRUM_MACHINE_LIMIT"):
            cmd_parts.extend(["--limit", os.environ["DRUM_MACHINE_LIMIT"]])
        r = _sp.run(cmd_parts)
        sys.exit(r.returncode)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
