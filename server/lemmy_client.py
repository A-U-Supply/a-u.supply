"""Lemmy integration for Latents discussion threading.

All requests go through this module — Latents UI never talks to fold directly.

Identity model
--------------
Lemmy 0.19.x has no admin-create-user API, and fold runs with `private_instance`
+ closed registration. So we don't auto-provision. Instead, each band member
links their existing fold account in admin settings: they enter their fold
username + password once, we log in server-side, encrypt and store the JWT.
After that all proxy calls happen under their real fold identity.

JWTs are stored encrypted in `users.lemmy_token_encrypted` using Fernet, with
the encryption key derived from the `LEMMY_TOKEN_KEY` env var.

Graceful degradation
--------------------
- `LEMMY_URL` unset → `is_configured()` returns False; calls return None/[].
- Network failures or non-2xx responses raise `LemmyUnavailable` which proxy
  endpoints turn into a 503 with a clean UI message.
- User hasn't linked yet → `LemmyNotLinked`, distinct from `LemmyUnavailable`
  so the UI can show a "Link your fold account" CTA instead of a generic error.
"""

import base64
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


LEMMY_URL = os.environ.get("LEMMY_URL", "").rstrip("/")
LEMMY_TOKEN_KEY = os.environ.get("LEMMY_TOKEN_KEY", "")
STACKS_COMMUNITY_NAME = "stacks"

# Cached resolution of the global `stacks` community id.
_stacks_community_id: int | None = None


class LemmyUnavailable(Exception):
    """Lemmy is unreachable, misconfigured, or returned an error."""


class LemmyNotLinked(Exception):
    """The calling au-supply user hasn't linked a fold account yet."""


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
    # text, derive one deterministically.
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
    return httpx.Client(base_url=LEMMY_URL, timeout=15.0)


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
# Account linkage (replaces auto-provisioning)
# ---------------------------------------------------------------------------


def link_account(db, user, fold_username: str, fold_password: str) -> dict:
    """Log into fold with the supplied credentials, store the JWT against the
    calling au-supply user. The plaintext password is never persisted.

    Returns a small status dict ({lemmy_user_id, lemmy_display, lemmy_username}).
    """
    if not is_configured():
        raise LemmyUnavailable("Lemmy not configured on this server")
    resp = _request(
        "POST",
        "/api/v3/user/login",
        json_body={"username_or_email": fold_username, "password": fold_password},
    )
    jwt = resp.get("jwt") if isinstance(resp, dict) else None
    if not jwt:
        raise LemmyUnavailable("Login did not return a JWT")

    me = _request("GET", "/api/v3/user", token=jwt, params={"username": fold_username})
    lemmy_user_id = None
    display = None
    real_username = fold_username
    if isinstance(me, dict):
        pv = me.get("person_view") or {}
        person = pv.get("person") or {}
        lemmy_user_id = person.get("id")
        display = person.get("display_name") or person.get("name")
        real_username = person.get("name") or fold_username

    user.lemmy_user_id = lemmy_user_id
    user.lemmy_token_encrypted = encrypt_token(jwt)
    db.commit()

    return {
        "lemmy_user_id": lemmy_user_id,
        "lemmy_display": display,
        "lemmy_username": real_username,
    }


def unlink_account(db, user) -> None:
    user.lemmy_user_id = None
    user.lemmy_token_encrypted = None
    db.commit()


def get_user_token(db, user) -> str:
    """Return the user's Lemmy JWT. Raises if unconfigured / not linked."""
    if not is_configured():
        raise LemmyUnavailable("Lemmy not configured")
    if not user.lemmy_token_encrypted:
        raise LemmyNotLinked("Link your fold account in Settings")
    try:
        return decrypt_token(user.lemmy_token_encrypted)
    except Exception as e:
        logger.exception("Decrypt Lemmy token failed for user %s", user.id)
        raise LemmyNotLinked("Stored fold token is invalid — re-link in Settings") from e


def status_for_user(user) -> dict:
    """Lightweight status: configured + linked + display name."""
    return {
        "configured": is_configured(),
        "linked": bool(user.lemmy_token_encrypted),
        "lemmy_user_id": user.lemmy_user_id,
        "lemmy_url": LEMMY_URL if is_configured() else None,
    }


# ---------------------------------------------------------------------------
# Communities
# ---------------------------------------------------------------------------


def _community_payload(name: str, title: str, description: str | None = None) -> dict:
    # 0.19.17 only accepts Public / Private. Use Private — the instance is
    # already private and federation-off, so this is belt-and-suspenders.
    out: dict[str, Any] = {
        "name": name,
        "title": title,
        "nsfw": False,
        "visibility": "Private",
    }
    if description:
        out["description"] = description
    return out


def _extract_community_id(resp: Any) -> int | None:
    if not isinstance(resp, dict):
        return None
    cv = resp.get("community_view") or {}
    community = cv.get("community") or {}
    cid = community.get("id")
    if cid:
        return cid
    # /api/v3/community returns either community_view (single) or communities (list)
    communities = resp.get("communities") or []
    for c in communities:
        cc = (c.get("community") or {}) if isinstance(c, dict) else {}
        if cc.get("id"):
            return cc["id"]
    return None


def ensure_project_community(db, project, token: str) -> int | None:
    """Create (or fetch) a Lemmy community for `project`. Caller supplies the
    JWT under which the community will be created; the calling user must be a
    fold admin (band members on fold all are).
    """
    if not is_configured():
        return None
    if project.lemmy_community_id:
        return project.lemmy_community_id

    payload = _community_payload(project.slug, project.name)
    try:
        resp = _request("POST", "/api/v3/community", token=token, json_body=payload)
        cid = _extract_community_id(resp)
    except LemmyUnavailable as e:
        # Likely "community_already_exists" — try to resolve by name.
        try:
            resp = _request(
                "GET",
                "/api/v3/community",
                token=token,
                params={"name": project.slug},
            )
            cid = _extract_community_id(resp)
        except LemmyUnavailable:
            raise e
    if cid:
        project.lemmy_community_id = cid
        db.commit()
    return cid


def ensure_stacks_community(token: str) -> int | None:
    """Resolve (or create) the global `stacks` community for media-item threads."""
    global _stacks_community_id
    if _stacks_community_id is not None:
        return _stacks_community_id
    if not is_configured():
        return None

    payload = _community_payload(
        STACKS_COMMUNITY_NAME,
        "Stacks",
        description="Discussion threads anchored to individual media items.",
    )
    try:
        resp = _request("POST", "/api/v3/community", token=token, json_body=payload)
        cid = _extract_community_id(resp)
    except LemmyUnavailable as e:
        try:
            resp = _request(
                "GET",
                "/api/v3/community",
                token=token,
                params={"name": STACKS_COMMUNITY_NAME},
            )
            cid = _extract_community_id(resp)
        except LemmyUnavailable:
            raise e
    if cid:
        _stacks_community_id = cid
    return cid


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
    resp = _request(
        "GET",
        "/api/v3/post/list",
        token=token,
        params={"community_id": community_id, "limit": limit, "sort": "New"},
    )
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
    cresp = _request(
        "GET",
        "/api/v3/comment/list",
        token=token,
        params={"post_id": post_id, "max_depth": 8, "limit": 200, "sort": "Old"},
    )
    if isinstance(cresp, dict):
        for cv in cresp.get("comments", []):
            c = cv.get("comment") or {}
            path = c.get("path") or ""
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
    payload: dict[str, Any] = {"community_id": community_id, "name": title}
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
