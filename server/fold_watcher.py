"""Background poller: announce new fold (Lemmy) activity to #supply-side.

Latents-originated communities and threads already get announced via
``latent.created`` / ``latent.thread_created``. This watcher closes the gap
for content created natively on fold — someone logging into the Lemmy UI
directly and creating a community or post.

Approach: every ``FOLD_WATCHER_INTERVAL`` seconds, list the newest local
communities and posts from fold's API, dedup against locally-known Latents
state, and fire ``fold.community_created`` / ``fold.post_created`` events
through the existing slack_notifier transport.

High-water marks live in the ``fold_watcher_state`` table; the first tick
records the current max IDs and announces nothing, so a fresh deploy doesn't
backflood the channel with historical content.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

from server.lemmy_client import LEMMY_URL, decrypt_token, is_configured
from server.models import FoldWatcherState, Project, SessionLocal, Thread, User
from server.slack_notifier import notify_immediate

logger = logging.getLogger(__name__)

FOLD_WATCHER_ENABLED = os.environ.get("FOLD_WATCHER_ENABLED", "true").lower() in ("1", "true", "yes")
FOLD_WATCHER_INTERVAL = int(os.environ.get("FOLD_WATCHER_INTERVAL", "300"))  # 5 min

# Lemmy's `stacks` sentinel — created once by ensure_stacks_community() for
# media-item threading. Never announce it; it's plumbing, not activity.
_STACKS_SENTINEL = "stacks"

_COMMUNITY_LIMIT = 20
_POST_LIMIT = 50

_LAST_COMMUNITY_KEY = "last_community_id"
_LAST_POST_KEY = "last_post_id"


def is_enabled() -> bool:
    return FOLD_WATCHER_ENABLED and is_configured()


async def watcher_loop() -> None:
    """Drive the fold poller. Survives every individual failure mode."""
    if not is_enabled():
        logger.info(
            "fold_watcher disabled (FOLD_WATCHER_ENABLED=%s, lemmy configured=%s)",
            FOLD_WATCHER_ENABLED, is_configured(),
        )
        return
    logger.info("fold_watcher enabled (every %ds)", FOLD_WATCHER_INTERVAL)
    await asyncio.sleep(90)  # settle behind the rollup loop's first sweep
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("fold_watcher tick failed")
        await asyncio.sleep(FOLD_WATCHER_INTERVAL)


async def _tick() -> None:
    session = SessionLocal()
    try:
        token = _borrow_admin_token(session)
        if not token:
            logger.info("fold_watcher: no admin user has linked fold yet — skipping tick")
            return

        async with httpx.AsyncClient(base_url=LEMMY_URL, timeout=10.0) as client:
            communities = await _list_local_communities(client, token)
            posts = await _list_local_posts(client, token)

        last_c = _get_state(session, _LAST_COMMUNITY_KEY)
        last_p = _get_state(session, _LAST_POST_KEY)
        bootstrap = last_c is None and last_p is None

        max_c = max((c["community"].get("id") or 0 for c in communities), default=0)
        max_p = max((p["post"].get("id") or 0 for p in posts), default=0)

        if bootstrap:
            # First run: record current high-water marks, don't backflood.
            _set_state(session, _LAST_COMMUNITY_KEY, max_c)
            _set_state(session, _LAST_POST_KEY, max_p)
            session.commit()
            logger.info(
                "fold_watcher bootstrapped (max_community=%d, max_post=%d)",
                max_c, max_p,
            )
            return

        last_c = last_c or 0
        last_p = last_p or 0

        new_communities = [
            cv for cv in communities if (cv["community"].get("id") or 0) > last_c
        ]
        new_posts = [pv for pv in posts if (pv["post"].get("id") or 0) > last_p]

        for cv in sorted(new_communities, key=lambda c: c["community"].get("id") or 0):
            _maybe_announce_community(session, cv)
        for pv in sorted(new_posts, key=lambda p: p["post"].get("id") or 0):
            _maybe_announce_post(session, pv)

        if max_c > last_c:
            _set_state(session, _LAST_COMMUNITY_KEY, max_c)
        if max_p > last_p:
            _set_state(session, _LAST_POST_KEY, max_p)
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Lemmy API
# ---------------------------------------------------------------------------


async def _list_local_communities(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    r = await client.get(
        "/api/v3/community/list",
        params={"type_": "Local", "sort": "New", "limit": _COMMUNITY_LIMIT},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    data = r.json() or {}
    return data.get("communities") or []


async def _list_local_posts(client: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
    r = await client.get(
        "/api/v3/post/list",
        params={"type_": "Local", "sort": "New", "limit": _POST_LIMIT},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    data = r.json() or {}
    return data.get("posts") or []


def _borrow_admin_token(session: Session) -> str | None:
    """Use any linked admin user's stored JWT for read calls. Band members on
    fold are all admins, so any linked one is sufficient.
    """
    user = (
        session.query(User)
        .filter(User.role == "admin", User.lemmy_token_encrypted.isnot(None))
        .order_by(User.id.asc())
        .first()
    )
    if user is None:
        return None
    try:
        return decrypt_token(user.lemmy_token_encrypted)
    except Exception:
        logger.exception("fold_watcher: could not decrypt token for user %s", user.id)
        return None


# ---------------------------------------------------------------------------
# Announcement
# ---------------------------------------------------------------------------


def _maybe_announce_community(session: Session, cv: dict[str, Any]) -> None:
    community = cv.get("community") or {}
    cid = community.get("id")
    name = community.get("name") or ""
    if not cid:
        return
    if name == _STACKS_SENTINEL:
        return
    # Skip if it's already wired up as a Latent — latent.created announced it.
    if session.query(Project.id).filter(Project.lemmy_community_id == cid).first():
        return
    notify_immediate(
        "fold.community_created",
        None,  # community list endpoint doesn't carry creator info
        community_id=cid,
        name=name,
        title=community.get("title") or "",
        description=community.get("description") or "",
    )


def _maybe_announce_post(session: Session, pv: dict[str, Any]) -> None:
    post = pv.get("post") or {}
    pid = post.get("id")
    if not pid:
        return
    # Skip if it's already wired up as a Latent thread — latent.thread_created
    # announced it.
    if session.query(Thread.id).filter(Thread.lemmy_post_id == pid).first():
        return

    creator = pv.get("creator") or {}
    community = pv.get("community") or {}
    user = _resolve_user(session, creator.get("id"))
    notify_immediate(
        "fold.post_created",
        user,
        post_id=pid,
        title=post.get("name") or "",
        body=post.get("body") or "",
        community_name=community.get("name") or "",
        community_title=community.get("title") or "",
        lemmy_username=creator.get("name") or "",
        lemmy_display_name=creator.get("display_name") or "",
    )


def _resolve_user(session: Session, lemmy_user_id: int | None) -> User | None:
    if not lemmy_user_id:
        return None
    return session.query(User).filter(User.lemmy_user_id == lemmy_user_id).first()


# ---------------------------------------------------------------------------
# State KV
# ---------------------------------------------------------------------------


def _get_state(session: Session, key: str) -> int | None:
    row = session.query(FoldWatcherState).filter(FoldWatcherState.key == key).first()
    return row.value if row is not None else None


def _set_state(session: Session, key: str, value: int) -> None:
    row = session.query(FoldWatcherState).filter(FoldWatcherState.key == key).first()
    if row is None:
        session.add(FoldWatcherState(key=key, value=value))
    else:
        row.value = value
