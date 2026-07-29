"""Notifications materializers + dismissal flow.

Covers the internal sources (fallen, midden, acclaim) directly against
the in-memory test DB. Fold sources are exercised at the orchestrator
level only — they short-circuit when FOLD_DATABASE_URL is unset, which
is the test environment's default.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import notifications as notif
from server.models import (
    ExtractionFailure,
    Job,
    JobOutput,
    MediaItem,
    MediaSource,
    MediaVote,
    Notification,
    NotificationHighWater,
    User,
)
from tests.conftest import make_media_item, make_media_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(db_session, **kwargs):
    """Minimal Job row (with its AppDefinition) for JobOutput FK satisfaction."""
    import uuid

    from server.models import AppDefinition

    app = AppDefinition(
        name="test-app",
        display_name="Test App",
        image="test:latest",
        manifest="[app]\nname = \"test-app\"",
    )
    db_session.add(app)
    db_session.flush()

    defaults = {
        "id": str(uuid.uuid4()),
        "app_name": app.name,
        "status": "completed",
        "input_items": "[]",
        "params": "{}",
        "created_by": kwargs.get("created_by", 1),
    }
    defaults.update(kwargs)
    job = Job(**defaults)
    db_session.add(job)
    db_session.commit()
    return job


def _make_user(db_session, **kwargs):
    from server.auth import hash_password

    defaults = {
        "email": "other@test.com",
        "name": "Other",
        "password_hash": hash_password("x"),
        "role": "admin",
    }
    defaults.update(kwargs)
    u = User(**defaults)
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


# ---------------------------------------------------------------------------
# First-poll seeding
# ---------------------------------------------------------------------------


def test_first_poll_seeds_watermark_no_backfill(db_session, test_user):
    """A user's first materialize() must NOT pick up historical events."""

    # Historical failure (was created before the user "exists" for inbox
    # purposes — i.e. before any watermark was seeded).
    media = make_media_item(db_session)
    db_session.add(
        ExtractionFailure(
            id="hist-failure",
            media_item_id=media.id,
            extraction_type="ocr",
            error_message="boom",
            attempts=1,
            last_attempt_at=datetime.now(timezone.utc) - timedelta(days=10),
            resolved=False,
        )
    )
    db_session.commit()

    inserted = notif.materialize_for_user(test_user, db_session)

    assert inserted == 0
    assert (
        db_session.query(Notification)
        .filter_by(user_id=test_user.id, source="fallen")
        .count()
        == 0
    )
    # Watermark must be seeded.
    hw = (
        db_session.query(NotificationHighWater)
        .filter_by(user_id=test_user.id, source="fallen")
        .one()
    )
    assert hw.last_seen_at is not None


# ---------------------------------------------------------------------------
# Fallen
# ---------------------------------------------------------------------------


def test_fallen_picks_up_new_failures_after_seeding(db_session, test_user):
    # Seed the watermark by running once.
    notif.materialize_for_user(test_user, db_session)

    # Now add a *new* failure — should surface on the next run.
    media = make_media_item(db_session)
    db_session.add(
        ExtractionFailure(
            id="new-fail",
            media_item_id=media.id,
            extraction_type="meilisearch_sync",
            error_message="meili down",
            attempts=1,
            last_attempt_at=datetime.now(timezone.utc),
            resolved=False,
        )
    )
    db_session.commit()

    inserted = notif.materialize_for_user(test_user, db_session)

    assert inserted >= 1
    row = (
        db_session.query(Notification)
        .filter_by(user_id=test_user.id, source="fallen", source_ref="failure:new-fail")
        .one()
    )
    assert "meilisearch_sync" in row.title
    assert row.dismissed_at is None


def test_resolved_failures_are_not_surfaced(db_session, test_user):
    notif.materialize_for_user(test_user, db_session)

    media = make_media_item(db_session)
    db_session.add(
        ExtractionFailure(
            id="already-resolved",
            media_item_id=media.id,
            extraction_type="ocr",
            error_message="boom",
            attempts=1,
            last_attempt_at=datetime.now(timezone.utc),
            resolved=True,
        )
    )
    db_session.commit()

    notif.materialize_for_user(test_user, db_session)

    assert (
        db_session.query(Notification)
        .filter_by(source_ref="failure:already-resolved")
        .count()
        == 0
    )


# ---------------------------------------------------------------------------
# Midden
# ---------------------------------------------------------------------------


def test_midden_picks_up_discarded_outputs(db_session, test_user):
    # Unmute BEFORE the first materialize, not after.
    #
    # `_seed_default_muted` started muting "midden" for new users after this
    # test was written, and it has been failing ever since — but not for the
    # obvious reason. A muted source's materializer never runs, so it never
    # seeds a watermark either. Unmuting afterwards means midden's watermark
    # is first created on the SECOND call, i.e. after the discard below, and
    # `discarded_at >= watermark` is then false: first-poll seeding says "as
    # of now you've seen everything". Setting it here means the watermark is
    # seeded on the first call and the discard genuinely lands after it.
    test_user.muted_sources = json.dumps([])
    db_session.flush()
    notif.materialize_for_user(test_user, db_session)

    job = _make_job(db_session, created_by=test_user.id)
    db_session.add(
        JobOutput(
            id="out-1",
            job_id=job.id,
            filename="trash.png",
            file_path="output/trash.png",
            discarded_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    inserted = notif.materialize_for_user(test_user, db_session)
    assert inserted >= 1

    row = (
        db_session.query(Notification)
        .filter_by(user_id=test_user.id, source="midden", source_ref="job_output:out-1")
        .one()
    )
    assert row.snippet == "trash.png"


# ---------------------------------------------------------------------------
# Acclaim
# ---------------------------------------------------------------------------


def test_acclaim_surfaces_plus_one_from_others(db_session, test_user):
    notif.materialize_for_user(test_user, db_session)

    media = make_media_item(db_session)
    make_media_source(db_session, media.id, uploader_id=test_user.id)
    voter = _make_user(db_session, email="voter@t.test", name="Voter")
    db_session.add(
        MediaVote(media_item_id=media.id, user_id=voter.id, value=1)
    )
    db_session.commit()

    inserted = notif.materialize_for_user(test_user, db_session)
    assert inserted >= 1
    row = (
        db_session.query(Notification)
        .filter_by(source="acclaim", source_ref=f"vote:{media.id}:{voter.id}")
        .one()
    )
    assert "Voter" in row.title


def test_acclaim_ignores_downvotes_and_self_votes(db_session, test_user):
    notif.materialize_for_user(test_user, db_session)

    media = make_media_item(db_session)
    make_media_source(db_session, media.id, uploader_id=test_user.id)
    voter = _make_user(db_session, email="downer@t.test", name="Downer")

    # Downvote from other user.
    db_session.add(
        MediaVote(media_item_id=media.id, user_id=voter.id, value=-1)
    )
    # Self-vote on own upload (+1).
    db_session.add(
        MediaVote(media_item_id=media.id, user_id=test_user.id, value=1)
    )
    db_session.commit()

    notif.materialize_for_user(test_user, db_session)

    assert (
        db_session.query(Notification).filter_by(source="acclaim").count() == 0
    )


def test_acclaim_ignores_votes_on_others_uploads(db_session, test_user):
    notif.materialize_for_user(test_user, db_session)

    other = _make_user(db_session, email="up@t.test", name="Up")
    voter = _make_user(db_session, email="vt@t.test", name="Vt")
    media = make_media_item(db_session)
    make_media_source(db_session, media.id, uploader_id=other.id)
    db_session.add(MediaVote(media_item_id=media.id, user_id=voter.id, value=1))
    db_session.commit()

    notif.materialize_for_user(test_user, db_session)

    assert (
        db_session.query(Notification)
        .filter_by(source="acclaim", user_id=test_user.id)
        .count()
        == 0
    )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_re_materialize_does_not_duplicate(db_session, test_user):
    notif.materialize_for_user(test_user, db_session)

    media = make_media_item(db_session)
    db_session.add(
        ExtractionFailure(
            id="dup-fail",
            media_item_id=media.id,
            extraction_type="ocr",
            error_message="boom",
            attempts=1,
            last_attempt_at=datetime.now(timezone.utc),
            resolved=False,
        )
    )
    db_session.commit()

    notif.materialize_for_user(test_user, db_session)
    notif.materialize_for_user(test_user, db_session)
    notif.materialize_for_user(test_user, db_session)

    assert (
        db_session.query(Notification)
        .filter_by(source_ref="failure:dup-fail")
        .count()
        == 1
    )


# ---------------------------------------------------------------------------
# Dismiss / dismiss-all
# ---------------------------------------------------------------------------


def test_dismiss_marks_single_row(db_session, test_user):
    notif.materialize_for_user(test_user, db_session)
    media = make_media_item(db_session)
    db_session.add(
        ExtractionFailure(
            id="d1",
            media_item_id=media.id,
            extraction_type="ocr",
            error_message="x",
            attempts=1,
            last_attempt_at=datetime.now(timezone.utc),
            resolved=False,
        )
    )
    db_session.commit()
    notif.materialize_for_user(test_user, db_session)

    row = (
        db_session.query(Notification)
        .filter_by(source_ref="failure:d1")
        .one()
    )
    assert notif.dismiss(row.id, test_user, db_session) is True

    db_session.refresh(row)
    assert row.dismissed_at is not None
    assert notif.unread_count(test_user, db_session) == 0


def test_dismiss_all_clears_unread(db_session, test_user):
    notif.materialize_for_user(test_user, db_session)
    media = make_media_item(db_session)
    # All three within a single moment so the materializer window
    # captures the whole batch — staggering into the future would push
    # later rows past window_end and out of the pass.
    now = datetime.now(timezone.utc)
    for i in range(3):
        db_session.add(
            ExtractionFailure(
                id=f"da-{i}",
                media_item_id=media.id,
                extraction_type="ocr",
                error_message="x",
                attempts=1,
                last_attempt_at=now,
                resolved=False,
            )
        )
    db_session.commit()
    notif.materialize_for_user(test_user, db_session)

    assert notif.unread_count(test_user, db_session) == 3
    assert notif.dismiss_all(test_user, db_session) == 3
    assert notif.unread_count(test_user, db_session) == 0


def test_dismiss_other_users_notification_returns_false(db_session, test_user):
    """Cross-user dismiss attempts must 404, not silently succeed."""
    other = _make_user(db_session)
    # Seed BOTH users' watermarks before introducing the event, so the
    # subsequent failure falls into both polling windows.
    notif.materialize_for_user(test_user, db_session)
    notif.materialize_for_user(other, db_session)

    media = make_media_item(db_session)
    db_session.add(
        ExtractionFailure(
            id="cross",
            media_item_id=media.id,
            extraction_type="ocr",
            error_message="x",
            attempts=1,
            last_attempt_at=datetime.now(timezone.utc),
            resolved=False,
        )
    )
    db_session.commit()
    notif.materialize_for_user(other, db_session)

    others_row = (
        db_session.query(Notification)
        .filter_by(user_id=other.id, source_ref="failure:cross")
        .one()
    )

    assert notif.dismiss(others_row.id, test_user, db_session) is False
    db_session.refresh(others_row)
    assert others_row.dismissed_at is None


# ---------------------------------------------------------------------------
# Fold sources no-op when FOLD_DATABASE_URL is unset
# ---------------------------------------------------------------------------


def test_fold_sources_noop_when_unconfigured(db_session, test_user, monkeypatch):
    """Without FOLD_DATABASE_URL the fold materializers must early-return
    cleanly; the orchestrator should still process the internal sources."""
    from server import fold_db

    monkeypatch.setattr(fold_db, "FOLD_DATABASE_URL", "")
    monkeypatch.setattr(fold_db, "is_configured", lambda: False)

    # Should not raise, should return 0 events (no internal data either).
    inserted = notif.materialize_for_user(test_user, db_session)
    assert inserted == 0
