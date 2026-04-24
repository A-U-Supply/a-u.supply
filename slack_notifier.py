"""Slack activity logger — posts site events to #supply-side.

Two tiers:
- Immediate: release/job/app events post to Slack right away.
- Batched: tag, index, and midden events queue in ``activity_log`` and get
  rolled up every 30 minutes. Rollup is skipped entirely when the queue is
  empty, so the channel never gets an empty heartbeat.

Every Slack call is fire-and-forget — a Slack outage can't block a release
edit or job submit. When ``SLACK_BOT_TOKEN`` is unset (local dev), messages
go to stdout as a ``[slack-dry-run]`` log line so forgetting the env var
isn't a crash.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from models import ActivityLog, SessionLocal, User

logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_LOG_CHANNEL = os.environ.get("SLACK_LOG_CHANNEL", "C0AUNJ6BMJT")  # #supply-side
SLACK_LOG_ENABLED = os.environ.get("SLACK_LOG_ENABLED", "true").lower() in ("1", "true", "yes")
SITE_URL = os.environ.get("SITE_URL", "https://a-u.supply")
ROLLUP_INTERVAL_SECONDS = int(os.environ.get("SLACK_ROLLUP_INTERVAL", "1800"))  # 30 min

_API_URL = "https://slack.com/api/chat.postMessage"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def notify_immediate(event_type: str, user: User | None, **payload: Any) -> None:
    """Fire an immediate Slack post for a high-signal event.

    Call from a route handler *after* ``db.commit()``. Never raises; a Slack
    error or missing event loop is swallowed with a log line.
    """
    try:
        user_name = user.name if user is not None else "someone"
        formatted = _format_immediate(event_type, user_name, payload)
        if formatted is None:
            logger.warning("No formatter for immediate event %r", event_type)
            return
        _schedule_post(formatted)
        _persist(event_type, user.id if user else None, payload, tier="immediate", posted=True)
    except Exception:
        logger.exception("notify_immediate failed for %s", event_type)


def queue_batched(event_type: str, user: User | None, **payload: Any) -> None:
    """Queue a low-signal event for the next 30-minute rollup."""
    try:
        _persist(event_type, user.id if user else None, payload, tier="batched", posted=False)
    except Exception:
        logger.exception("queue_batched failed for %s", event_type)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _schedule_post(payload: dict) -> None:
    """Schedule an async Slack post on the running event loop.

    If no loop is running (e.g. from a sync thread), fall back to a one-shot
    run — slower, but the caller still doesn't block long.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(_post_slack(payload))
    else:
        # Rare: called from a worker thread with no loop. Run sync.
        try:
            asyncio.run(_post_slack(payload))
        except Exception:
            logger.exception("Slack sync post failed")


async def _post_slack(payload: dict) -> None:
    if not SLACK_LOG_ENABLED:
        return
    if not SLACK_BOT_TOKEN:
        logger.info("[slack-dry-run] %s", payload.get("text") or payload)
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                _API_URL,
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                json={"channel": SLACK_LOG_CHANNEL, **payload},
            )
            data = r.json()
            if not data.get("ok"):
                logger.warning("Slack post not ok: %s", data.get("error"))
    except Exception:
        logger.exception("Slack post errored")


def _persist(
    event_type: str,
    user_id: int | None,
    payload: dict,
    *,
    tier: str,
    posted: bool,
) -> None:
    session: Session = SessionLocal()
    try:
        row = ActivityLog(
            event_type=event_type,
            tier=tier,
            user_id=user_id,
            payload=json.dumps(payload, default=str),
            posted_at=datetime.now(timezone.utc) if posted else None,
        )
        session.add(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Link + formatting helpers
# ---------------------------------------------------------------------------


def _release_link(code: str) -> str:
    return f"{SITE_URL}/catalog/{quote(code, safe='')}"


def _job_link(job_id: str) -> str:
    return f"{SITE_URL}/dashboard/jobs/{job_id}"


def _batch_link(batch_id: str) -> str:
    return f"{SITE_URL}/dashboard/jobs?batch_id={batch_id}"


def _midden_link() -> str:
    return f"{SITE_URL}/dashboard/the-midden"


def _cover_url_if_public(code: str, published: bool) -> str | None:
    """Slack can only fetch covers for published releases (drafts 404)."""
    if not published:
        return None
    return f"{SITE_URL}/api/releases/{quote(code, safe='')}/cover?size=thumb"


def _text_and_image_blocks(text: str, image_url: str, alt: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "image", "image_url": image_url, "alt_text": alt},
    ]


def _fmt_duration(seconds: float | None) -> str | None:
    if not seconds:
        return None
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


# ---------------------------------------------------------------------------
# Immediate formatters
# ---------------------------------------------------------------------------


def _format_immediate(event_type: str, user_name: str, d: dict) -> dict | None:
    fn = _IMMEDIATE_FORMATTERS.get(event_type)
    return fn(user_name, d) if fn else None


def _format_release_created(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    status = d.get("status", "draft")
    tracks = d.get("track_count") or 0
    bits = [f"📀 *{u}* filed a new release: *{title}* `{code}`"]
    if tracks:
        bits.append(f"{tracks} track{'s' if tracks != 1 else ''}")
    if status == "draft":
        bits.append("_(draft)_")
    text = " · ".join(bits)
    text += f"\n<{_release_link(code)}|open release>"
    return _maybe_with_cover(text, code, status == "published", title)


def _format_release_updated(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    changed = d.get("changed_fields") or []
    changed_txt = ", ".join(changed) if changed else "details"
    text = (
        f"✏️ *{u}* edited *{title}* `{code}` — changed: {changed_txt}"
        f"\n<{_release_link(code)}|see changes>"
    )
    return {"text": text, "unfurl_links": False}


def _format_release_published(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    tracks = d.get("track_count") or 0
    duration = _fmt_duration(d.get("total_duration_seconds"))
    extras = [f"{tracks} track{'s' if tracks != 1 else ''}" if tracks else None, duration]
    extras_txt = " · ".join(x for x in extras if x)
    tail = f" · {extras_txt}" if extras_txt else ""
    text = (
        f"🚀 *{u}* published *{title}* `{code}`{tail}"
        f"\n<{_release_link(code)}|listen>"
    )
    return _maybe_with_cover(text, code, True, title)


def _format_release_unpublished(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    text = f"🙈 *{u}* pulled *{title}* `{code}` back to draft"
    return {"text": text, "unfurl_links": False}


def _format_release_deleted(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    text = f"🗑️ *{u}* deleted release *{title}* `{code}` — gone for good"
    return {"text": text, "unfurl_links": False}


def _format_job_submitted(u: str, d: dict) -> dict:
    app_label = d.get("app_display_name") or d.get("app_name") or "an app"
    job_id = d.get("job_id", "")
    inputs = d.get("input_count") or 0
    params = d.get("params") or {}
    param_bits = []
    for k in ("recipe", "model", "processing_mode"):
        if params.get(k):
            param_bits.append(f"{k}=`{params[k]}`")
    text = f"⚙️ *{u}* fired *{app_label}* on {inputs} input{'s' if inputs != 1 else ''}"
    if param_bits:
        text += "\n" + " · ".join(param_bits)
    if job_id:
        text += f"\n<{_job_link(job_id)}|watch job>"
    return {"text": text, "unfurl_links": False}


def _format_job_batch_submitted(u: str, d: dict) -> dict:
    app_label = d.get("app_display_name") or d.get("app_name") or "an app"
    batch_id = d.get("batch_id", "")
    count = d.get("job_count") or 0
    random_recipe = d.get("random_recipe")
    text = f"🎰 *{u}* queued a batch of {count} *{app_label}* job{'s' if count != 1 else ''}"
    if random_recipe:
        text += " _(random recipes)_"
    if batch_id:
        text += f"\n<{_batch_link(batch_id)}|watch batch>"
    return {"text": text, "unfurl_links": False}


def _format_app_registered(u: str, d: dict) -> dict:
    name = d.get("display_name") or d.get("name", "(app)")
    image = d.get("image", "")
    text = f"🤖 *{u}* registered a new app: *{name}*"
    if image:
        text += f"\n`{image}`"
    return {"text": text, "unfurl_links": False}


def _format_app_updated(u: str, d: dict) -> dict:
    name = d.get("display_name") or d.get("name", "(app)")
    text = f"🔧 *{u}* updated the *{name}* manifest"
    return {"text": text, "unfurl_links": False}


def _maybe_with_cover(text: str, code: str, published: bool, alt: str) -> dict:
    payload: dict = {"text": text, "unfurl_links": False}
    cover = _cover_url_if_public(code, published)
    if cover:
        payload["blocks"] = _text_and_image_blocks(text, cover, alt)
    return payload


_IMMEDIATE_FORMATTERS = {
    "release.created": _format_release_created,
    "release.updated": _format_release_updated,
    "release.published": _format_release_published,
    "release.unpublished": _format_release_unpublished,
    "release.deleted": _format_release_deleted,
    "job.submitted": _format_job_submitted,
    "job.batch_submitted": _format_job_batch_submitted,
    "app.registered": _format_app_registered,
    "app.updated": _format_app_updated,
}


# ---------------------------------------------------------------------------
# Batched rollup
# ---------------------------------------------------------------------------


async def rollup_loop() -> None:
    """Background task: drain the batched queue on an interval.

    Skips posting entirely when there are no unposted rows.
    """
    await asyncio.sleep(60)  # let the app settle before the first sweep
    while True:
        try:
            await _run_rollup()
        except Exception:
            logger.exception("Slack rollup failed")
        await asyncio.sleep(ROLLUP_INTERVAL_SECONDS)


async def _run_rollup() -> None:
    session = SessionLocal()
    try:
        rows = (
            session.query(ActivityLog)
            .filter(ActivityLog.tier == "batched", ActivityLog.posted_at.is_(None))
            .order_by(ActivityLog.created_at.asc())
            .all()
        )
        if not rows:
            return

        user_ids = {r.user_id for r in rows if r.user_id is not None}
        users_by_id: dict[int, str] = {}
        if user_ids:
            user_rows = session.query(User.id, User.name).filter(User.id.in_(user_ids)).all()
            users_by_id = {u[0]: u[1] for u in user_rows}

        lines = _format_rollup_lines(rows, users_by_id)
        if not lines:
            # Rows exist but all had unknown event types — mark posted so they
            # don't pile up forever, but don't spam the channel.
            now = datetime.now(timezone.utc)
            for r in rows:
                r.posted_at = now
            session.commit()
            return

        oldest = min(r.created_at for r in rows)
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        since_txt = oldest.astimezone(timezone.utc).strftime("%H:%M UTC")

        text = f"🧹 *since {since_txt}*\n" + "\n".join(lines)
        await _post_slack({"text": text, "unfurl_links": False})

        now = datetime.now(timezone.utc)
        for r in rows:
            r.posted_at = now
        session.commit()
    finally:
        session.close()


def _format_rollup_lines(rows: list[ActivityLog], users_by_id: dict[int, str]) -> list[str]:
    groups: dict[tuple[int | None, str], list[dict]] = defaultdict(list)
    for r in rows:
        try:
            payload = json.loads(r.payload) if r.payload else {}
        except (TypeError, ValueError):
            payload = {}
        groups[(r.user_id, r.event_type)].append(payload)

    lines: list[str] = []
    for (uid, event_type), payloads in groups.items():
        user_name = users_by_id.get(uid, "someone") if uid is not None else "someone"
        count = sum(int(p.get("count", 1)) for p in payloads)
        plural = "s" if count != 1 else ""

        if event_type == "tag.added":
            tag_counter: Counter[str] = Counter()
            for p in payloads:
                for t in p.get("tags") or []:
                    tag_counter[t] += 1
            top = [t for t, _ in tag_counter.most_common(5)]
            extra = f" — top: {', '.join(f'`{t}`' for t in top)}" if top else ""
            lines.append(f"• *{user_name}* tagged {count} item{plural}{extra}")

        elif event_type == "tag.removed":
            lines.append(f"• *{user_name}* removed {count} tag{plural}")

        elif event_type == "midden.discarded":
            apps = Counter(p.get("app_name") for p in payloads if p.get("app_name"))
            app_bits = ", ".join(f"*{a}*" for a, _ in apps.most_common(3))
            suffix = f" from {app_bits}" if app_bits else ""
            lines.append(
                f"• *{user_name}* sent {count} output{plural}{suffix} to the midden"
                f" — <{_midden_link()}|review>"
            )

        elif event_type == "output.indexed":
            apps = Counter(p.get("app_name") for p in payloads if p.get("app_name"))
            app_bits = ", ".join(f"*{a}*" for a, _ in apps.most_common(3))
            suffix = f" from {app_bits}" if app_bits else ""
            lines.append(f"• *{user_name}* indexed {count} output{plural}{suffix} into search")

    return lines
