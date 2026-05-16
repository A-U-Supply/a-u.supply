"""Lemmy integration for Latents discussion threading.

All requests go through this module — Latents UI never talks to fold directly.

Auto-provisioning:
    - Each au-supply admin user maps 1:1 to a Lemmy user. Created via the
      Lemmy admin register-user endpoint on first access.
    - JWTs are stored encrypted in `users.lemmy_token_encrypted` using
      Fernet (symmetric, authenticated). The encryption key comes from
      `LEMMY_TOKEN_KEY` env var.

Graceful degradation:
    - If `LEMMY_URL` is unset, the module operates in dry-run mode: all calls
      return None and log a notice. This allows the Latents feature to ship
      before the fold instance is live.
    - Any request error logs and raises `LemmyUnavailable`, which proxy
      endpoints translate into a 503 with a user-friendly message.
"""

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


LEMMY_URL = os.environ.get("LEMMY_URL", "").rstrip("/")
LEMMY_ADMIN_TOKEN = os.environ.get("LEMMY_ADMIN_TOKEN", "")
LEMMY_TOKEN_KEY = os.environ.get("LEMMY_TOKEN_KEY", "")
STACKS_COMMUNITY_NAME = "stacks"

# Singleton cache for the resolved stacks community id, set on first access.
_stacks_community_id: int | None = None


class LemmyUnavailable(Exception):
    """Lemmy is unreachable, misconfigured, or returned an error."""


def is_configured() -> bool:
    return bool(LEMMY_URL) and bool(LEMMY_TOKEN_KEY)


# ---------------------------------------------------------------------------
# Token encryption (Fernet via cryptography)
# ---------------------------------------------------------------------------


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise LemmyUnavailable("cryptography package not installed")
    if not LEMMY_TOKEN_KEY:
        raise LemmyUnavailable("LEMMY_TOKEN_KEY env var is not set")
    key = LEMMY_TOKEN_KEY.encode("utf-8")
    # Fernet wants a urlsafe base64-encoded 32-byte key. If the env var is plain
    # text, derive a key from it deterministically (suitable for our scale; not
    # a substitute for a proper KMS).
    if len(key) != 44 or not key.endswith(b"="):
        import hashlib
        digest = hashlib.sha256(key).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _client() -> httpx.Client:
    if not LEMMY_URL:
        raise LemmyUnavailable("LEMMY_URL is not set")
    return httpx.Client(base_url=LEMMY_URL, timeout=10.0)


def _request(method: str, path: str, *, token: str | None = None, json_body: dict | None = None, params: dict | None = None) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with _client() as c:
            r = c.request(method, path, json=json_body, params=params, headers=headers)
    except httpx.RequestError as e:
        logger.warning("Lemmy %s %s failed: %s", method, path, e)
        raise LemmyUnavailable(f"Lemmy unreachable: {e}") from e
    if r.status_code >= 400:
        logger.warning("Lemmy %s %s returned %s: %s", method, path, r.status_code, r.text[:200])
        raise LemmyUnavailable(f"Lemmy {r.status_code}: {r.text[:120]}")
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return r.text


# ---------------------------------------------------------------------------
# User provisioning
# ---------------------------------------------------------------------------


def _slug_login(email: str) -> str:
    """Lemmy logins must be unique per instance and ASCII-safe. Derive from email."""
    local = (email.split("@", 1)[0] or "user").lower()
    slug = re.sub(r"[^a-z0-9_]+", "_", local)[:20] or "user"
    return slug


def _gen_password() -> str:
    import secrets
    return secrets.token_urlsafe(24)


def ensure_user_and_token(db, user) -> str | None:
    """Auto-provision a Lemmy account for `user` if missing; return their JWT.

    Returns None if Lemmy is not configured (dry-run mode).
    """
    if not is_configured():
        logger.info("Lemmy not configured — skipping provisioning for user %s", user.id)
        return None

    if user.lemmy_user_id and user.lemmy_token_encrypted:
        try:
            return decrypt_token(user.lemmy_token_encrypted)
        except Exception:
            logger.exception("Failed to decrypt Lemmy token for user %s; reprovisioning", user.id)

    login = _slug_login(user.email)
    password = _gen_password()
    display_name = user.name

    # Register via admin endpoint
    try:
        resp = _request(
            "POST",
            "/api/v3/user/register",
            token=LEMMY_ADMIN_TOKEN or None,
            json_body={
                "username": login,
                "password": password,
                "password_verify": password,
                "show_nsfw": False,
                "display_name": display_name,
            },
        )
        jwt = resp.get("jwt") if isinstance(resp, dict) else None
    except LemmyUnavailable:
        raise

    if not jwt:
        # Possibly already registered — try login instead.
        try:
            resp = _request(
                "POST",
                "/api/v3/user/login",
                json_body={"username_or_email": login, "password": password},
            )
            jwt = resp.get("jwt") if isinstance(resp, dict) else None
        except LemmyUnavailable:
            raise

    if not jwt:
        raise LemmyUnavailable("Lemmy did not return a JWT")

    # Resolve the user id
    me = _request("GET", "/api/v3/user", token=jwt, params={"username": login})
    lemmy_user_id = None
    if isinstance(me, dict):
        # /api/v3/user returns { person_view: { person: { id, ... } } }
        pv = me.get("person_view") or {}
        person = pv.get("person") or {}
        lemmy_user_id = person.get("id")

    user.lemmy_user_id = lemmy_user_id
    user.lemmy_token_encrypted = encrypt_token(jwt)
    db.commit()
    return jwt


def get_user_token(db, user) -> str | None:
    """Return the user's Lemmy JWT, auto-provisioning if needed."""
    if not is_configured():
        return None
    if user.lemmy_token_encrypted:
        try:
            return decrypt_token(user.lemmy_token_encrypted)
        except Exception:
            logger.exception("Decrypt Lemmy token failed for user %s; reprovisioning", user.id)
    return ensure_user_and_token(db, user)


# ---------------------------------------------------------------------------
# Communities
# ---------------------------------------------------------------------------


def ensure_project_community(db, project) -> int | None:
    """Create (or fetch) a private Lemmy community for `project`. Returns its id."""
    if not is_configured():
        return None
    if project.lemmy_community_id:
        return project.lemmy_community_id
    if not LEMMY_ADMIN_TOKEN:
        raise LemmyUnavailable("LEMMY_ADMIN_TOKEN required to create communities")

    payload = {
        "name": project.slug,
        "title": project.name,
        "nsfw": False,
        "visibility": "LocalOnly",
    }
    try:
        resp = _request("POST", "/api/v3/community", token=LEMMY_ADMIN_TOKEN, json_body=payload)
    except LemmyUnavailable:
        # Maybe it already exists — try to resolve by name
        resp = _request("GET", "/api/v3/community", token=LEMMY_ADMIN_TOKEN, params={"name": project.slug})

    cid = None
    if isinstance(resp, dict):
        cv = resp.get("community_view") or {}
        community = cv.get("community") or {}
        cid = community.get("id")
    if cid:
        project.lemmy_community_id = cid
        db.commit()
    return cid


def ensure_stacks_community() -> int | None:
    """Resolve (or create) the global `stacks` community for media-item threads."""
    global _stacks_community_id
    if _stacks_community_id is not None:
        return _stacks_community_id
    if not is_configured():
        return None
    if not LEMMY_ADMIN_TOKEN:
        raise LemmyUnavailable("LEMMY_ADMIN_TOKEN required to bootstrap stacks community")

    try:
        resp = _request(
            "POST",
            "/api/v3/community",
            token=LEMMY_ADMIN_TOKEN,
            json_body={
                "name": STACKS_COMMUNITY_NAME,
                "title": "Stacks",
                "description": "Discussion threads anchored to individual media items.",
                "nsfw": False,
                "visibility": "LocalOnly",
            },
        )
    except LemmyUnavailable:
        resp = _request("GET", "/api/v3/community", token=LEMMY_ADMIN_TOKEN, params={"name": STACKS_COMMUNITY_NAME})

    if isinstance(resp, dict):
        cv = resp.get("community_view") or {}
        community = cv.get("community") or {}
        cid = community.get("id")
        if cid:
            _stacks_community_id = cid
            return cid
    return None


# ---------------------------------------------------------------------------
# Posts (threads) and comments
# ---------------------------------------------------------------------------


@dataclass
class LemmyPost:
    id: int
    name: str
    body: str | None
    url: str | None
    creator_id: int
    published: str | None
    community_id: int


@dataclass
class LemmyComment:
    id: int
    content: str
    creator_id: int
    parent_id: int | None
    path: str
    published: str | None
    deleted: bool


def list_posts(token: str, community_id: int, limit: int = 50) -> list[LemmyPost]:
    if not is_configured():
        return []
    resp = _request("GET", "/api/v3/post/list", token=token, params={"community_id": community_id, "limit": limit, "sort": "New"})
    out: list[LemmyPost] = []
    if isinstance(resp, dict):
        for pv in resp.get("posts", []):
            p = pv.get("post") or {}
            out.append(LemmyPost(
                id=p.get("id"),
                name=p.get("name"),
                body=p.get("body"),
                url=p.get("url"),
                creator_id=p.get("creator_id"),
                published=p.get("published"),
                community_id=p.get("community_id"),
            ))
    return out


def get_post(token: str, post_id: int) -> tuple[LemmyPost | None, list[LemmyComment]]:
    if not is_configured():
        return None, []
    resp = _request("GET", "/api/v3/post", token=token, params={"id": post_id})
    post = None
    comments: list[LemmyComment] = []
    if isinstance(resp, dict):
        pv = resp.get("post_view") or {}
        p = pv.get("post") or {}
        if p:
            post = LemmyPost(
                id=p.get("id"),
                name=p.get("name"),
                body=p.get("body"),
                url=p.get("url"),
                creator_id=p.get("creator_id"),
                published=p.get("published"),
                community_id=p.get("community_id"),
            )
    # Pull comments separately for nested threading
    cresp = _request("GET", "/api/v3/comment/list", token=token, params={"post_id": post_id, "max_depth": 8, "limit": 200, "sort": "Old"})
    if isinstance(cresp, dict):
        for cv in cresp.get("comments", []):
            c = cv.get("comment") or {}
            path = c.get("path") or ""
            # parent id from path: "0.<root>.<...>.<self>"
            parts = path.split(".")
            parent = int(parts[-2]) if len(parts) >= 2 and parts[-2] != "0" else None
            comments.append(LemmyComment(
                id=c.get("id"),
                content=c.get("content") or "",
                creator_id=c.get("creator_id"),
                parent_id=parent,
                path=path,
                published=c.get("published"),
                deleted=bool(c.get("deleted")),
            ))
    return post, comments


def create_post(token: str, community_id: int, title: str, body: str | None = None, url: str | None = None) -> LemmyPost:
    if not is_configured():
        raise LemmyUnavailable("Lemmy not configured")
    payload = {"community_id": community_id, "name": title}
    if body:
        payload["body"] = body
    if url:
        payload["url"] = url
    resp = _request("POST", "/api/v3/post", token=token, json_body=payload)
    pv = resp.get("post_view", {}) if isinstance(resp, dict) else {}
    p = pv.get("post") or {}
    return LemmyPost(
        id=p.get("id"),
        name=p.get("name"),
        body=p.get("body"),
        url=p.get("url"),
        creator_id=p.get("creator_id"),
        published=p.get("published"),
        community_id=p.get("community_id"),
    )


def edit_post(token: str, post_id: int, title: str | None = None, body: str | None = None) -> None:
    payload: dict[str, Any] = {"post_id": post_id}
    if title is not None:
        payload["name"] = title
    if body is not None:
        payload["body"] = body
    _request("PUT", "/api/v3/post", token=token, json_body=payload)


def delete_post(token: str, post_id: int) -> None:
    _request("POST", "/api/v3/post/delete", token=token, json_body={"post_id": post_id, "deleted": True})


def create_comment(token: str, post_id: int, content: str, parent_id: int | None = None) -> LemmyComment:
    if not is_configured():
        raise LemmyUnavailable("Lemmy not configured")
    payload: dict[str, Any] = {"post_id": post_id, "content": content}
    if parent_id:
        payload["parent_id"] = parent_id
    resp = _request("POST", "/api/v3/comment", token=token, json_body=payload)
    cv = resp.get("comment_view", {}) if isinstance(resp, dict) else {}
    c = cv.get("comment") or {}
    return LemmyComment(
        id=c.get("id"),
        content=c.get("content") or "",
        creator_id=c.get("creator_id"),
        parent_id=parent_id,
        path=c.get("path") or "",
        published=c.get("published"),
        deleted=False,
    )


def edit_comment(token: str, comment_id: int, content: str) -> None:
    _request("PUT", "/api/v3/comment", token=token, json_body={"comment_id": comment_id, "content": content})


def delete_comment(token: str, comment_id: int) -> None:
    _request("POST", "/api/v3/comment/delete", token=token, json_body={"comment_id": comment_id, "deleted": True})
