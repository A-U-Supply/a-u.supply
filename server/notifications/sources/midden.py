"""Midden materializer.

Surfaces JobOutput rows newly moved to the Midden (discarded_at set
within the current poll window). Items are reaped after 24h by the
midden reaper, but the notification persists until dismissed.
"""

from server.models import JobOutput, User

from ._base import (
    advance_watermark,
    get_or_seed_watermark,
    upsert_notification,
    utcnow,
)

SOURCE = "midden"


def materialize(user: User, db) -> int:
    watermark = get_or_seed_watermark(db, user.id, SOURCE)
    window_end = utcnow()

    rows = (
        db.query(JobOutput)
        .filter(
            JobOutput.discarded_at.isnot(None),
            JobOutput.discarded_at >= watermark,
            JobOutput.discarded_at <= window_end,
        )
        .order_by(JobOutput.discarded_at.asc())
        .all()
    )

    inserted = 0
    for output in rows:
        ref = f"job_output:{output.id}"
        url = "/admin/search/midden"
        title = "Sent to the midden"
        snippet = output.filename or output.id
        if upsert_notification(
            db,
            user_id=user.id,
            source=SOURCE,
            source_ref=ref,
            title=title,
            snippet=snippet,
            url=url,
            created_at=output.discarded_at,
        ):
            inserted += 1

    advance_watermark(db, user.id, SOURCE, window_end)
    return inserted
