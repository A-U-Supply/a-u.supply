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

from server.models import ActivityLog, SessionLocal, User

logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_LOG_CHANNEL = os.environ.get("SLACK_LOG_CHANNEL", "C0AUNJ6BMJT")  # #supply-side
SLACK_LOG_ENABLED = os.environ.get("SLACK_LOG_ENABLED", "true").lower() in ("1", "true", "yes")
SITE_URL = os.environ.get("SITE_URL", "https://a-u.supply")
FOLD_BASE_URL = os.environ.get("FOLD_BASE_URL", "https://fold.a-u.supply.com").rstrip("/")
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


def _release_link(code: str, *, published: bool) -> str:
    """Public catalog page if published, admin edit page if still draft.

    Published releases: /catalog/release?code=X (user-facing).
    Drafts: /admin/catalog/edit?code=X (public page 404s for unauthenticated).
    """
    encoded = quote(code, safe="")
    if published:
        return f"{SITE_URL}/catalog/release?code={encoded}"
    return f"{SITE_URL}/admin/catalog/edit?code={encoded}"


def _release_edit_link(code: str) -> str:
    return f"{SITE_URL}/admin/catalog/edit?code={quote(code, safe='')}"


def _job_link(job_id: str) -> str:
    return f"{SITE_URL}/admin/jobs/detail?id={quote(job_id, safe='')}"


def _jobs_queue_link() -> str:
    return f"{SITE_URL}/admin/jobs"


def _jobs_by_app_link(app_name: str) -> str:
    return f"{SITE_URL}/admin/jobs?app={quote(app_name, safe='')}"


def _latent_link(project_id: str) -> str:
    return f"{SITE_URL}/admin/latents/detail?id={quote(project_id, safe='')}"


def _latents_index_link() -> str:
    return f"{SITE_URL}/admin/latents"


def _search_detail_link(media_item_id: str) -> str:
    return f"{SITE_URL}/admin/search/detail?id={quote(media_item_id, safe='')}"


def _jobs_for_batch_link(batch_id: str) -> str:
    return f"{SITE_URL}/admin/jobs?batch_id={quote(batch_id, safe='')}"


def _midden_link() -> str:
    return f"{SITE_URL}/admin/search/midden"


def _search_by_tag_link(tag: str) -> str:
    return f"{SITE_URL}/admin/search?tags={quote(tag, safe='')}"


def _search_by_index_link(output_index: str) -> str:
    return f"{SITE_URL}/admin/search?output_index={quote(output_index, safe='')}"


def _search_by_app_link(app_name: str) -> str:
    # Pair ?app=X with ?output_index=outputs because app filtering only
    # applies to outputs — the search UI auto-clears the app dropdown when
    # the index is Inputs. Explicit beats relying on auto-switch.
    return (
        f"{SITE_URL}/admin/search?app={quote(app_name, safe='')}"
        f"&output_index=outputs"
    )


def _media_detail_link(media_item_id: str) -> str:
    """Detail page with OG tags — Slack unfurls this with a thumbnail."""
    return f"{SITE_URL}/admin/search/detail?id={quote(media_item_id, safe='')}"


def _app_linked(app_name: str, app_label: str) -> str:
    """Bold app label, linked to its search results when we have a name."""
    if app_name:
        return f"<{_search_by_app_link(app_name)}|*{app_label}*>"
    return f"*{app_label}*"


def _linked_index(output_index: str) -> str:
    if not output_index:
        return "the archive"
    return f"<{_search_by_index_link(output_index)}|*{output_index}*>"


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


def _fmt_by(entities: list[str] | None) -> str:
    """Return ' by _Name_' / ' by _A_ & _B_' / '' depending on entity list."""
    if not entities:
        return ""
    if len(entities) == 1:
        return f" by _{entities[0]}_"
    if len(entities) == 2:
        return f" by _{entities[0]}_ & _{entities[1]}_"
    return f" by _{entities[0]}_, _{entities[1]}_ & {len(entities) - 2} more"


def _pick(key: str, options: list[str]) -> str:
    """Deterministic variety: same seed key → same pick. Keeps messages varied
    across events without ever re-rolling the same message's phrasing."""
    if not options:
        return ""
    h = sum(ord(c) * (i + 1) for i, c in enumerate(key)) if key else 0
    return options[h % len(options)]


def _format_release_created(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    status = d.get("status", "draft")
    published = status == "published"
    tracks = d.get("track_count") or 0
    entities = d.get("entities") or []
    release_date = d.get("release_date")
    verb = _pick(code, ["filed", "committed", "pressed", "cut", "struck"])
    bits = [f"📀 *{u}* {verb} a new release: *{title}* `{code}`{_fmt_by(entities)}"]
    side: list[str] = []
    if tracks:
        side.append(f"{tracks} track{'s' if tracks != 1 else ''}")
    if release_date:
        side.append(f"dated {release_date}")
    if not published:
        side.append("_(still in draft)_")
    if side:
        bits.append(" · ".join(side))
    text = "\n".join(bits)
    link_label = "open release" if published else "keep editing"
    text += f"\n<{_release_link(code, published=published)}|{link_label}>"
    return _maybe_with_cover(text, code, published, title)


def _format_release_updated(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    status = d.get("status", "draft")
    published = status == "published"
    changed = d.get("changed_fields") or []
    entities = d.get("entities") or []
    changed_txt = ", ".join(changed) if changed else "details"
    status_badge = "" if published else " _(draft)_"
    link_label = "see changes" if published else "keep editing"
    verb = _pick(code + changed_txt, ["revised", "reworked", "tweaked", "polished"])
    text = (
        f"✏️ *{u}* {verb} *{title}* `{code}`{_fmt_by(entities)}{status_badge}"
        f"\nchanged: {changed_txt}"
        f"\n<{_release_link(code, published=published)}|{link_label}>"
    )
    return {"text": text, "unfurl_links": False}


def _format_release_published(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    tracks = d.get("track_count") or 0
    duration = _fmt_duration(d.get("total_duration_seconds"))
    entities = d.get("entities") or []
    release_date = d.get("release_date")
    extras = [
        f"{tracks} track{'s' if tracks != 1 else ''}" if tracks else None,
        duration,
        f"dated {release_date}" if release_date else None,
    ]
    extras_txt = " · ".join(x for x in extras if x)
    tail = f"\n{extras_txt}" if extras_txt else ""
    verb = _pick(code, ["loosed", "released", "unleashed", "shipped", "published"])
    text = (
        f"🚀 *{u}* {verb} *{title}* `{code}`{_fmt_by(entities)} upon the world{tail}"
        f"\n<{_release_link(code, published=True)}|listen>"
    )
    return _maybe_with_cover(text, code, True, title)


def _format_release_unpublished(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    entities = d.get("entities") or []
    text = (
        f"🙈 *{u}* yanked *{title}* `{code}`{_fmt_by(entities)} back to the vault"
        f"\n<{_release_edit_link(code)}|edit>"
    )
    return {"text": text, "unfurl_links": False}


def _format_release_deleted(u: str, d: dict) -> dict:
    code = d.get("product_code", "")
    title = d.get("title", "(untitled)")
    entities = d.get("entities") or []
    verb = _pick(code, ["interred", "buried", "erased", "obliterated"])
    text = f"🗑️ *{u}* {verb} *{title}* `{code}`{_fmt_by(entities)} — gone for good"
    return {"text": text, "unfurl_links": False}


def _format_job_submitted(u: str, d: dict) -> dict:
    app_name = d.get("app_name") or ""
    app_label = d.get("app_display_name") or app_name or "an app"
    job_id = d.get("job_id", "")
    inputs = d.get("input_count") or 0
    params = d.get("params") or {}
    param_bits = []
    for k in ("recipe", "model", "processing_mode"):
        if params.get(k):
            param_bits.append(f"{k}=`{params[k]}`")
    verb = _pick(job_id + app_name, ["loosed", "unleashed", "set loose", "fired"])
    input_word = "input" if inputs == 1 else "inputs"
    text = f"⚙️ *{u}* {verb} {_app_linked(app_name, app_label)} on {inputs} {input_word}"
    if param_bits:
        text += "\n" + " · ".join(param_bits)
    if job_id:
        text += f"\n<{_job_link(job_id)}|watch job>"
    return {"text": text, "unfurl_links": False}


def _format_job_batch_submitted(u: str, d: dict) -> dict:
    # /api/jobs/batch is the endpoint Hecatomb fires — frame accordingly.
    app_name = d.get("app_name") or ""
    app_label = d.get("app_display_name") or app_name or "an app"
    batch_id = d.get("batch_id", "")
    count = d.get("job_count") or 0
    random_recipe = d.get("random_recipe")
    recipe_note = " _(random recipes)_" if random_recipe else ""
    job_word = "job" if count == 1 else "jobs"
    text = (
        f"🎰 *{u}* ran Hecatomb on {_app_linked(app_name, app_label)} — "
        f"{count} {job_word}{recipe_note}"
    )
    links = [f"<{_jobs_queue_link()}|watch queue>"]
    if batch_id:
        links.append(f"<{_jobs_for_batch_link(batch_id)}|watch this batch>")
    text += "\n" + " · ".join(links)
    return {"text": text, "unfurl_links": False}


def _format_upload_failed(u: str, d: dict) -> dict:
    """Uploads that died in the browser.

    One message per drained queue, not per file — the client batches, because
    a dropped uplink fails everything behind it at once. Names are capped at
    five so a 40-file batch doesn't paste a directory listing into the channel.
    """
    count = int(d.get("count") or 0)
    names = [n for n in (d.get("names") or []) if n]
    reason = (d.get("first_message") or "").strip()
    head = f"⚠️ *{u}*'s upload failed" if count == 1 else f"⚠️ {count} of *{u}*'s uploads failed"
    listed = ", ".join(f"`{n}`" for n in names)
    if count > len(names):
        listed += f" +{count - len(names)} more"
    text = head
    if listed:
        text += f" — {listed}"
    if reason:
        text += f"\n_{reason}_"
    return {"text": text, "unfurl_links": False}


def _format_output_indexed(u: str, d: dict) -> dict:
    """Single-output index (or rescue from midden). Includes media preview link
    so Slack unfurls a thumbnail from the detail page's OG tags."""
    app_name = d.get("app_name") or ""
    app_label = d.get("app_display_name") or app_name or "an app"
    media_item_id = d.get("media_item_id") or ""
    output_index = d.get("output_index") or ""
    filename = d.get("filename") or ""
    from_midden = bool(d.get("from_midden"))

    if from_midden:
        verb = _pick(media_item_id or filename, ["rescued", "saved", "pulled"])
        text = (
            f"🫴 *{u}* {verb} an output from the midden — "
            f"filed into {_linked_index(output_index)} "
            f"(from {_app_linked(app_name, app_label)})"
        )
    else:
        verb = _pick(media_item_id or filename, ["enshrined", "filed", "catalogued"])
        text = (
            f"🗂️ *{u}* {verb} an output from {_app_linked(app_name, app_label)} "
            f"into {_linked_index(output_index)}"
        )
    if filename:
        text += f"\n`{filename}`"
    if media_item_id:
        text += f"\n<{_media_detail_link(media_item_id)}|preview>"
        # unfurl_links defaults to True — let Slack render the OG preview inline.
        return {"text": text}
    return {"text": text, "unfurl_links": False}


def _format_outputs_indexed_bulk(u: str, d: dict) -> dict:
    """Bulk-index summary. No per-item preview since it could be many."""
    app_name = d.get("app_name") or ""
    app_label = d.get("app_display_name") or app_name or "an app"
    count = int(d.get("count") or 0)
    output_index = d.get("output_index") or ""
    from_midden_count = int(d.get("from_midden_count") or 0)

    plural = "s" if count != 1 else ""
    verb = _pick(str(count) + app_name, ["enshrined", "filed", "catalogued"])
    text = (
        f"🗂️ *{u}* {verb} {count} output{plural} "
        f"from {_app_linked(app_name, app_label)} "
        f"into {_linked_index(output_index)}"
    )
    if from_midden_count:
        text += f"\n_({from_midden_count} rescued from the midden)_"
    return {"text": text, "unfurl_links": False}


def _format_app_registered(u: str, d: dict) -> dict:
    name = d.get("name", "")
    display_name = d.get("display_name") or name or "(app)"
    image = d.get("image", "")
    description = d.get("description", "")
    verb = _pick(name, ["wired up", "enlisted", "commissioned", "brought online"])
    text = f"🤖 *{u}* {verb} a new apparatus: *{display_name}*"
    if description:
        text += f"\n_{description}_"
    if image:
        text += f"\n`{image}`"
    if name:
        text += f"\n<{_jobs_by_app_link(name)}|jobs for this app>"
    return {"text": text, "unfurl_links": False}


def _format_app_updated(u: str, d: dict) -> dict:
    name = d.get("name", "")
    display_name = d.get("display_name") or name or "(app)"
    verb = _pick(name, ["tuned up", "rejiggered", "tweaked", "retooled"])
    text = f"🔧 *{u}* {verb} the *{display_name}* manifest"
    if name:
        text += f"\n<{_jobs_by_app_link(name)}|jobs for this app>"
    return {"text": text, "unfurl_links": False}


def _maybe_with_cover(text: str, code: str, published: bool, alt: str) -> dict:
    payload: dict = {"text": text, "unfurl_links": False}
    cover = _cover_url_if_public(code, published)
    if cover:
        payload["blocks"] = _text_and_image_blocks(text, cover, alt)
    return payload


def _format_latent_created(u: str, d: dict) -> dict:
    pid = d.get("project_id") or ""
    name = d.get("name") or "(untitled)"
    kind = d.get("kind") or "other"
    verb = _pick(pid, ["opened", "started", "began", "set up"])
    text = f"🎞 *{u}* {verb} a new latent: *{name}* ({kind})\n<{_latent_link(pid)}|open latent>"
    return {"text": text, "unfurl_links": False}


def _format_latent_status_changed(u: str, d: dict) -> dict:
    pid = d.get("project_id") or ""
    name = d.get("name") or "(untitled)"
    status = d.get("status") or "?"
    prior = d.get("prior_status") or "?"
    text = f"🎞 *{u}* moved *{name}* `{prior} → {status}`\n<{_latent_link(pid)}|open latent>"
    return {"text": text, "unfurl_links": False}


def _format_latent_shipped(u: str, d: dict) -> dict:
    """The one status change that is an announcement. `abandoned`'s counterpart:
    the work came out."""
    pid = d.get("project_id") or ""
    name = d.get("name") or "(untitled)"
    text = f"🎞 *{u}* shipped *{name}* — it's out\n<{_latent_link(pid)}|open latent>"
    return {"text": text, "unfurl_links": False}


def _format_latent_abandoned(u: str, d: dict) -> dict:
    pid = d.get("project_id") or ""
    name = d.get("name") or "(untitled)"
    text = f"🎞 *{u}* abandoned the latent *{name}*\n<{_latent_link(pid)}|open latent>"
    return {"text": text, "unfurl_links": False}


def _format_latent_deleted(u: str, d: dict) -> dict:
    """Deletion is the one latent event with no project link — that URL is a
    404 now. Links to the index instead, and leads with what SURVIVED, because
    on a shared workspace that's the part everyone else needs to hear."""
    name = d.get("name") or "(untitled)"
    n = int(d.get("item_count") or 0)
    kept = f" — {n} file{'' if n == 1 else 's'} stayed in Emulsion" if n else ""
    text = (
        f"🎞 *{u}* deleted the latent *{name}*{kept}\n"
        f"<{_latents_index_link()}|all latents>"
    )
    return {"text": text, "unfurl_links": False}


# All fold/Lemmy events share this emoji so they're scannable in #supply-side.
_FOLD_EMOJI = "🗯"


def _fold_user(u: str, d: dict) -> str:
    """Use the linked a-u.supply User name if we have one; otherwise fall back
    to the Lemmy display_name / username from the payload (for fold-native users
    who don't have an a-u.supply account)."""
    if u and u != "someone":
        return u
    return d.get("lemmy_display_name") or d.get("lemmy_username") or "someone"


def _fold_community_url(name: str) -> str:
    return f"{FOLD_BASE_URL}/c/{quote(name, safe='')}" if name else FOLD_BASE_URL


def _fold_post_url(post_id: int | str) -> str:
    return f"{FOLD_BASE_URL}/post/{post_id}" if post_id else FOLD_BASE_URL


def _format_fold_community_created(u: str, d: dict) -> dict:
    name = d.get("name") or ""
    title = d.get("title") or name or "(untitled)"
    description = d.get("description") or ""
    actor = _fold_user(u, d)
    verb = _pick(name, ["opened", "founded", "spun up", "started"])
    text = f"{_FOLD_EMOJI} *{actor}* {verb} a new fold community: *{title}* `{name}`"
    if description:
        snippet = description.split("\n", 1)[0][:160]
        text += f"\n_{snippet}_"
    text += f"\n<{_fold_community_url(name)}|open community>"
    return {"text": text, "unfurl_links": False}


def _format_fold_post_created(u: str, d: dict) -> dict:
    post_id = d.get("post_id") or ""
    title = d.get("title") or "(untitled)"
    community_name = d.get("community_name") or ""
    community_title = d.get("community_title") or community_name or "fold"
    body = d.get("body") or ""
    actor = _fold_user(u, d)
    verb = _pick(str(post_id), ["posted", "dropped", "filed", "shared"])
    text = f"{_FOLD_EMOJI} *{actor}* {verb} in *{community_title}* on fold: *{title}*"
    if body:
        snippet = body.split("\n", 1)[0][:160]
        text += f"\n_{snippet}_"
    text += f"\n<{_fold_post_url(post_id)}|read>"
    return {"text": text, "unfurl_links": False}


def _format_latent_annotation_created(u: str, d: dict) -> dict:
    filename = d.get("filename") or "a file"
    project_name = d.get("project_name")
    timestamp = d.get("timestamp") or ""
    excerpt = (d.get("excerpt") or "").strip()
    media_item_id = d.get("media_item_id") or ""
    where = f" in *{project_name}*" if project_name else ""
    at = f" at `{timestamp}`" if timestamp else ""
    snip = f' — _"{excerpt}"_' if excerpt else ""
    text = (
        f"💬 *{u}* commented{where} on *{filename}*{at}{snip}\n"
        f"<{_search_detail_link(media_item_id)}|open file>"
    )
    return {"text": text, "unfurl_links": False}


def _format_latent_thread_created(u: str, d: dict) -> dict:
    anchor_type = d.get("anchor_type") or "project"
    anchor_id = d.get("anchor_id") or ""
    title = d.get("title") or "(untitled)"
    lemmy_url = d.get("lemmy_url")
    if anchor_type == "media_item":
        link = lemmy_url or _search_detail_link(anchor_id)
        where = "in *the Stacks*"
    elif anchor_type in ("project", "slot"):
        link = lemmy_url or _latents_index_link()
        where = "in a *Latent*"
    else:
        link = lemmy_url or _latents_index_link()
        where = ""
    verb = _pick(title, ["opened", "started", "posted"])
    text = f"{_FOLD_EMOJI} *{u}* {verb} a new fold thread {where}: *{title}*\n<{link}|view on fold>"
    return {"text": text, "unfurl_links": False}


def _format_latent_thread_reply(u: str, d: dict) -> dict:
    anchor_type = d.get("anchor_type") or "media_item"
    anchor_id = d.get("anchor_id") or ""
    thread_title = d.get("thread_title") or "(untitled)"
    snippet = d.get("snippet") or ""
    lemmy_url = d.get("lemmy_url")
    if anchor_type == "media_item":
        fallback_link = _search_detail_link(anchor_id)
        where = "in *the Stacks*"
    else:
        fallback_link = _latents_index_link()
        where = "in a *Latent*"
    link = lemmy_url or fallback_link
    snip = f" — _{snippet}_" if snippet else ""
    text = f"{_FOLD_EMOJI} *{u}* replied on fold {where} to *{thread_title}*{snip}\n<{link}|view on fold>"
    return {"text": text, "unfurl_links": False}


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
    "upload.failed": _format_upload_failed,
    "output.indexed": _format_output_indexed,
    "outputs.indexed_bulk": _format_outputs_indexed_bulk,
    "latent.created": _format_latent_created,
    "latent.status_changed": _format_latent_status_changed,
    "latent.shipped": _format_latent_shipped,
    "latent.abandoned": _format_latent_abandoned,
    "latent.deleted": _format_latent_deleted,
    "latent.thread_created": _format_latent_thread_created,
    "latent.thread_reply": _format_latent_thread_reply,
    "latent.annotation_created": _format_latent_annotation_created,
    "fold.community_created": _format_fold_community_created,
    "fold.post_created": _format_fold_post_created,
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

        header = _pick(
            since_txt,
            [
                f"🧹 *from the workshop, since {since_txt}:*",
                f"🧹 *last sweep of the floor, since {since_txt}:*",
                f"🧹 *the tally since {since_txt}:*",
                f"🧹 *since {since_txt}, on the shop floor:*",
            ],
        )
        text = header + "\n" + "\n".join(lines)
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
            extra = ""
            if top:
                tag_links = ", ".join(f"<{_search_by_tag_link(t)}|`{t}`>" for t in top)
                extra = f" — top: {tag_links}"
            verb = _pick(user_name + "tag", ["labeled", "tagged", "annotated"])
            lines.append(f"• *{user_name}* {verb} {count} item{plural}{extra}")

        elif event_type == "tag.removed":
            lines.append(f"• *{user_name}* stripped {count} tag{plural}")

        elif event_type == "midden.discarded":
            apps = Counter(p.get("app_name") for p in payloads if p.get("app_name"))
            app_bits = ", ".join(
                f"<{_search_by_app_link(a)}|*{a}*>" for a, _ in apps.most_common(3)
            )
            suffix = f" of {app_bits}" if app_bits else ""
            verb = _pick(user_name + "midden", ["consigned", "tossed", "dropped"])
            lines.append(
                f"• *{user_name}* {verb} {count} output{plural}{suffix} to the midden"
                f" — <{_midden_link()}|review>"
            )

        elif event_type == "latent.items_added":
            project_ids = Counter(p.get("project_id") for p in payloads if p.get("project_id"))
            top_proj = project_ids.most_common(3)
            proj_bits = ", ".join(
                f"<{_latent_link(pid)}|*latent*>" for pid, _ in top_proj
            )
            suffix = f" to {proj_bits}" if proj_bits else " to a latent"
            verb = _pick(user_name + "latent_attach", ["attached", "added", "filed"])
            lines.append(f"• *{user_name}* {verb} {count} file{plural}{suffix}")

        elif event_type == "latent.document_edited":
            doc_names = {p.get("document_name") for p in payloads if p.get("document_name")}
            edited_names = ", ".join(f"*{n}*" for n in list(doc_names)[:3])
            project_ids = {p.get("project_id") for p in payloads if p.get("project_id")}
            link = ""
            if project_ids:
                pid = next(iter(project_ids))
                link = f" — <{_latent_link(pid)}|open>"
            lines.append(f"• *{user_name}* edited {edited_names or 'a latent document'}{link}")

        elif event_type == "media.voted":
            # Per-user acclaim/disavow on Stacks items (issue #318). Each
            # payload carries prior_value / new_value so we can describe
            # the net effect of a triage session (5 acclaims, 2 disavows,
            # 3 retractions) in one rollup line.
            up = sum(1 for p in payloads if int(p.get("new_value") or 0) > 0)
            down = sum(1 for p in payloads if int(p.get("new_value") or 0) < 0)
            retracted = sum(
                1
                for p in payloads
                if int(p.get("new_value") or 0) == 0 and int(p.get("prior_value") or 0) != 0
            )
            bits = []
            if up:
                bits.append(f"acclaimed {up}")
            if down:
                bits.append(f"disavowed {down}")
            if retracted:
                bits.append(f"retracted {retracted}")
            summary = ", ".join(bits) or "voted on 0 items"
            # If just one event in the rollup, link straight to that item.
            link_suffix = ""
            if count == 1:
                mid = payloads[0].get("media_item_id")
                if mid:
                    link_suffix = f" — <{_search_detail_link(mid)}|view>"
            lines.append(f"• *{user_name}* {summary} in the Stacks{link_suffix}")

        elif event_type == "output.indexed":
            apps = Counter(p.get("app_name") for p in payloads if p.get("app_name"))
            app_bits = ", ".join(
                f"<{_search_by_app_link(a)}|*{a}*>" for a, _ in apps.most_common(3)
            )
            from_phrase = f" from {app_bits}" if app_bits else ""
            indices = Counter(p.get("output_index") for p in payloads if p.get("output_index"))
            if indices:
                idx_links = ", ".join(
                    f"<{_search_by_index_link(i)}|{i}>" for i, _ in indices.most_common(3)
                )
                into_phrase = f" into {idx_links}"
            else:
                # Old events (pre-#224) don't carry output_index. Keep the line
                # pointing somewhere useful via the app link we already emit.
                into_phrase = " into the archive"
            verb = _pick(user_name + "index", ["enshrined", "filed", "catalogued"])
            lines.append(
                f"• *{user_name}* {verb} {count} output{plural}{from_phrase}{into_phrase}"
            )

    return lines
