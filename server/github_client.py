"""GitHub REST API client (read + token validation + webhook signature).

Tokens for private repos are stored encrypted via the same Fernet key the
Lemmy client uses (LEMMY_TOKEN_KEY) for simplicity. If a separate
GITHUB_TOKEN_KEY is set, that takes precedence.

Failure mode: any non-2xx raises GithubError; callers translate to HTTP.
"""

import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


GITHUB_API = "https://api.github.com"
USER_AGENT = "a-u.supply-latents/1.0"


class GithubError(Exception):
    """Anything that goes wrong talking to GitHub."""


# ---------------------------------------------------------------------------
# Token encryption — share Lemmy Fernet key by default
# ---------------------------------------------------------------------------


def _key_material() -> bytes:
    raw = os.environ.get("GITHUB_TOKEN_KEY") or os.environ.get("LEMMY_TOKEN_KEY")
    if not raw:
        raise GithubError("Neither GITHUB_TOKEN_KEY nor LEMMY_TOKEN_KEY is set")
    return raw.encode("utf-8")


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise GithubError("cryptography package not installed") from e
    key = _key_material()
    if len(key) != 44 or not key.endswith(b"="):
        digest = hashlib.sha256(key).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


_REPO_URL_RE = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
    re.IGNORECASE,
)


def parse_repo_url(url: str) -> tuple[str, str]:
    """Return (owner, repo). Raises GithubError if the URL is not a github repo URL."""
    m = _REPO_URL_RE.match((url or "").strip())
    if not m:
        raise GithubError(f"Not a GitHub repo URL: {url!r}")
    return m.group("owner"), m.group("repo")


def canonical_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def blob_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"


def commit_url(owner: str, repo: str, sha: str) -> str:
    return f"https://github.com/{owner}/{repo}/commit/{sha}"


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _client(token: str | None = None) -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=GITHUB_API, timeout=15.0, headers=headers)


def _get(path: str, token: str | None = None, **params) -> Any:
    try:
        with _client(token) as c:
            r = c.get(path, params=params or None)
    except httpx.RequestError as e:
        raise GithubError(f"GitHub unreachable: {e}") from e
    if r.status_code == 404:
        raise GithubError(f"GitHub 404 for {path}")
    if r.status_code == 401:
        raise GithubError("GitHub auth rejected — token invalid or expired")
    if r.status_code == 403 and "rate limit" in (r.text or "").lower():
        raise GithubError("GitHub rate limit hit — add a PAT")
    if r.status_code >= 400:
        raise GithubError(f"GitHub {r.status_code}: {(r.text or '')[:160]}")
    ct = r.headers.get("content-type", "")
    if ct.startswith("application/json"):
        return r.json()
    return r.text


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------


@dataclass
class GithubUser:
    login: str
    id: int
    scopes: list[str]


def validate_token(token: str) -> GithubUser:
    """Hit /user with the token; returns login + id + scopes header."""
    try:
        with _client(token) as c:
            r = c.get("/user")
    except httpx.RequestError as e:
        raise GithubError(f"GitHub unreachable: {e}") from e
    if r.status_code != 200:
        raise GithubError(f"GitHub auth rejected ({r.status_code})")
    body = r.json()
    scopes_hdr = r.headers.get("X-OAuth-Scopes") or r.headers.get("x-oauth-scopes") or ""
    scopes = [s.strip() for s in scopes_hdr.split(",") if s.strip()]
    return GithubUser(login=body.get("login", ""), id=body.get("id", 0), scopes=scopes)


# ---------------------------------------------------------------------------
# Repo metadata + content
# ---------------------------------------------------------------------------


def repo_meta(owner: str, repo: str, token: str | None = None) -> dict:
    return _get(f"/repos/{owner}/{repo}", token=token)


def head_commit(owner: str, repo: str, ref: str, token: str | None = None) -> str:
    data = _get(f"/repos/{owner}/{repo}/commits/{ref}", token=token)
    if not isinstance(data, dict):
        raise GithubError("Unexpected commit response shape")
    sha = data.get("sha")
    if not sha:
        raise GithubError("Commit response missing sha")
    return sha


def file_content(owner: str, repo: str, path: str, ref: str | None = None, token: str | None = None) -> dict:
    params = {"ref": ref} if ref else {}
    data = _get(f"/repos/{owner}/{repo}/contents/{path}", token=token, **params)
    if isinstance(data, list):
        # path is a directory
        return {"type": "dir", "entries": data}
    if isinstance(data, dict) and data.get("type") == "file":
        if data.get("encoding") == "base64" and data.get("content"):
            try:
                raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                data["text"] = raw
            except binascii.Error:
                data["text"] = None
        return data
    return data


# ---------------------------------------------------------------------------
# Webhook HMAC
# ---------------------------------------------------------------------------


def verify_webhook(signature_header: str | None, secret: str, body: bytes) -> bool:
    """Verify a `X-Hub-Signature-256: sha256=...` header against `body`."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header, expected)


def gen_webhook_secret() -> str:
    import secrets
    return secrets.token_urlsafe(32)
