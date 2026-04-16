"""Management CLI for a-u.supply. Runs inside the Docker container.

Usage (from host):
    ssh dokku run au-supply .venv/bin/python manage.py create-user <email> <password> <name> [role]
    ssh dokku run au-supply .venv/bin/python manage.py list-users
    ssh dokku run au-supply .venv/bin/python manage.py make-apikey <email> <label> <scope>
    ssh dokku run au-supply .venv/bin/python manage.py revoke-apikey <key-prefix>
    ssh dokku run au-supply .venv/bin/python manage.py migrate-index <old-index> <new-index>
"""

import sys

from auth import hash_password
from models import SessionLocal, User


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


def make_apikey(email: str, label: str, scope: str):
    from auth import generate_api_key, hash_api_key
    from models import ApiKey

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
    from models import ApiKey

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
    from models import MediaItem
    from search_client import configure_indexes, sync_media_item

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


def color_histogram():
    import json
    from models import MediaImageMeta
    from search_client import _hex_to_color_groups

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
    from models import MediaImageMeta

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
    from models import MediaItem, MediaSource

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
    from models import MediaItem, MediaImageMeta, MediaAudioMeta, MediaVideoMeta, ExtractionFailure
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
    from models import MediaItem, MediaAudioMeta, MediaVideoMeta
    from extraction import (
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


def backfill_ocr():
    """Find images missing OCR text and run tesseract on them."""
    import os
    from models import MediaItem, MediaImageMeta
    from extraction import extract_text_ocr, _upsert_meta, SEARCH_MEDIA_DIR

    db = SessionLocal()

    images_missing = (
        db.query(MediaItem)
        .outerjoin(MediaImageMeta)
        .filter(
            MediaItem.media_type == "image",
            (MediaImageMeta.caption.is_(None)) | (MediaImageMeta.media_item_id.is_(None)),
        )
        .all()
    )

    total = len(images_missing)
    log(f"Found {total} images missing OCR text")

    if total == 0:
        log("Nothing to do!")
        db.close()
        return

    done = 0
    errors = 0
    for i, item in enumerate(images_missing):
        full_path = os.path.join(SEARCH_MEDIA_DIR, item.file_path)
        if not os.path.exists(full_path):
            log(f"  SKIP {item.id} — file not found: {full_path}")
            errors += 1
            continue

        log(f"  [{i + 1}/{total}] {item.filename}")

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

    log(f"Done! OCR processed: {done}, Errors: {errors}")
    db.close()

    log("Re-indexing all items to populate has_text filter...")
    reindex_search()


def list_users():
    db = SessionLocal()
    users = db.query(User).all()
    for u in users:
        print(f"{u.id} | {u.email} | {u.name} | {u.role}")
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
        from slack_scraper import backfill_posters
        result = backfill_posters()
        print(f"Updated: {result['updated']}, Errors: {result['errors']}")

    elif cmd == "backfill-text":
        from slack_scraper import backfill_message_text
        result = backfill_message_text()
        print(f"Updated: {result['updated']}, Errors: {result['errors']}")

    elif cmd == "backfill-transcripts":
        backfill_transcripts()

    elif cmd == "backfill-ocr":
        backfill_ocr()

    elif cmd == "migrate-index":
        if len(sys.argv) < 4:
            print("Usage: manage.py migrate-index <old-index> <new-index>")
            sys.exit(1)
        old_idx, new_idx = sys.argv[2], sys.argv[3]
        from models import MediaItem, MediaTag
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

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
