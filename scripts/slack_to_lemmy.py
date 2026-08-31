#!/usr/bin/env python3
"""Mirror a Slack thread to a Lemmy post (+ top-level comments) on fold.

One-off / iterative tool — invoke per-thread from the laptop. Designed so the
core flow can later be lifted into an admin UI action.

Usage:
    uv run python scripts/slack_to_lemmy.py \\
        --slack-url https://au-supply.slack.com/archives/C03UA479T5W/p1775095798693579 \\
        --community project_root_of_ars

Reads credentials from ~/.config/a-u.supply/env (override with --env-file):
    SLACK_BOT_TOKEN  (channels|groups:history + files:read; bot must be in the channel)
    FOLD_INSTANCE    (default: https://fold.a-u.supply)
    FOLD_USERNAME    fold account used to author EVERY post + comment via the API
    FOLD_PASSWORD

Authoring as each original Slack user
-------------------------------------
Lemmy has no admin "post as user X" endpoint. So:
  1. We post everything through one fold account (FOLD_USERNAME).
  2. The script reads scripts/slack_fold_user_map.json (Slack display name ->
     fold username) and emits a `.sql` file with UPDATE statements that
     re-author each post/comment to the right person.id, looked up by username.
  3. You apply that SQL against fold's Postgres via `ssh dokku`.

Flow:
    1. Fetch parent + every reply via conversations.replies (paginated).
    2. Resolve poster display names via users.info (cached, with --rename).
    3. Download every file attached to any message (image/video/audio/other).
    4. Login to fold; upload media to pictrs; create post + top-level comments.
    5. Emit slack-to-lemmy-reassign-<post_id>.sql with creator_id updates.

Use --dry-run to fetch + format but skip every Lemmy write.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Config / env loading
# ---------------------------------------------------------------------------

DEFAULT_ENV_PATH = Path.home() / ".config" / "a-u.supply" / "env"
DEFAULT_FOLD_INSTANCE = "https://fold.a-u.supply"

SLACK_API = "https://slack.com/api"
HTTP_TIMEOUT = 60.0


def load_env(path: Path) -> None:
    """Merge KEY=VAL lines from `path` into os.environ (existing env wins)."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# ---------------------------------------------------------------------------
# Slack: URL parsing + API
# ---------------------------------------------------------------------------

_SLACK_URL_RE = re.compile(
    r"slack\.com/archives/(?P<channel>[A-Z0-9]+)/p(?P<ts>\d{10})(?P<us>\d{6})"
)


def parse_slack_url(url: str) -> tuple[str, str]:
    m = _SLACK_URL_RE.search(url)
    if not m:
        raise SystemExit(f"Could not parse Slack thread URL: {url}")
    return m.group("channel"), f"{m.group('ts')}.{m.group('us')}"


def slack_call(method: str, params: dict, token: str) -> dict:
    """GET https://slack.com/api/{method}?... with bot token. Raises on !ok."""
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=HTTP_TIMEOUT) as c:
        r = c.get(f"{SLACK_API}/{method}", params=params, headers=headers)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise SystemExit(f"Slack {method} failed: {data.get('error')!r} {data}")
    return data


def fetch_thread(channel: str, ts: str, token: str) -> list[dict]:
    """Return every message in the thread (parent first), following next_cursor."""
    messages: list[dict] = []
    cursor: str | None = None
    while True:
        params = {"channel": channel, "ts": ts, "limit": "200"}
        if cursor:
            params["cursor"] = cursor
        data = slack_call("conversations.replies", params, token)
        messages.extend(data.get("messages") or [])
        meta = data.get("response_metadata") or {}
        cursor = meta.get("next_cursor") or None
        if not data.get("has_more") or not cursor:
            break
    return messages


def fetch_channel_name(channel_id: str, token: str) -> str:
    try:
        data = slack_call("conversations.info", {"channel": channel_id}, token)
        return (data.get("channel") or {}).get("name") or channel_id
    except SystemExit:
        return channel_id


class UserCache:
    def __init__(
        self,
        token: str,
        renames: dict[str, str] | None = None,
        user_map: dict[str, str] | None = None,
        fold_host: str = "",
    ):
        self.token = token
        self.renames = renames or {}
        # Slack display name (post-rename) -> fold username.
        self.user_map = user_map or {}
        # Bare hostname (e.g. "fold.a-u.supply"), used to render Lemmy
        # mentions as @handle@host so the markdown renderer hyperlinks them.
        self.fold_host = fold_host
        self._cache: dict[str, str] = {}

    def name(self, user_id: str) -> str:
        if not user_id:
            return ""
        if user_id in self._cache:
            return self._cache[user_id]
        try:
            data = slack_call("users.info", {"user": user_id}, self.token)
        except SystemExit:
            display = user_id
        else:
            u = data.get("user") or {}
            profile = u.get("profile") or {}
            display = (
                profile.get("display_name")
                or profile.get("real_name")
                or u.get("real_name")
                or u.get("name")
                or user_id
            )
        display = self.renames.get(display, display)
        self._cache[user_id] = display
        return display

    def mention(self, user_id: str) -> str:
        """Render a Slack <@U…> as a Lemmy @-mention.

        Lemmy hyperlinks fediverse-style `@user@host` mentions. For mapped
        fold users we emit that full form so notification + link both fire.
        Unmapped users fall back to a plain `@displayname` (visible but not
        linked).
        """
        display = self.name(user_id)
        fold = self.user_map.get(display)
        if fold and self.fold_host:
            return f"@{fold}@{self.fold_host}"
        if fold:
            return f"@{fold}"
        return f"@{display}"

    def apply_renames(self, text: str) -> str:
        if not text:
            return text
        for src, dst in self.renames.items():
            if src and src in text:
                text = text.replace(src, dst)
        return text


def download_slack_file(file: dict, token: str, dest_dir: Path) -> Path | None:
    """Download a Slack file dict to dest_dir. Returns the downloaded path."""
    url = file.get("url_private_download") or file.get("url_private")
    if not url:
        return None
    fname = file.get("name") or f"{file.get('id', 'attachment')}.bin"
    # Sanitize filename
    fname = re.sub(r"[^\w.\-]+", "_", fname)[:120] or "attachment.bin"
    dest = dest_dir / f"{file.get('id', 'f')}_{fname}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=300.0, follow_redirects=True) as c:
        with c.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes(65536):
                    fh.write(chunk)
    return dest


# ---------------------------------------------------------------------------
# Slack mrkdwn → markdown
# ---------------------------------------------------------------------------


def slack_text_to_markdown(text: str, users: UserCache) -> str:
    if not text:
        return ""
    # <@U123> or <@U123|name> -> prefer mapped fold @handle so Lemmy notifies them.
    text = re.sub(
        r"<@([UW][A-Z0-9]+)(?:\|[^>]+)?>",
        lambda m: users.mention(m.group(1)),
        text,
    )
    # <#C123|channel-name> or <#C123>
    text = re.sub(
        r"<#([CG][A-Z0-9]+)(?:\|([^>]+))?>",
        lambda m: f"#{m.group(2) or m.group(1)}",
        text,
    )
    # <!here>, <!channel>, <!everyone>
    text = re.sub(r"<!(here|channel|everyone)(?:\|[^>]+)?>", r"@\1", text)
    # <url|label>
    text = re.sub(
        r"<(https?://[^|>\s]+)\|([^>]+)>",
        lambda m: f"[{m.group(2)}]({m.group(1)})",
        text,
    )
    # <url>
    text = re.sub(r"<(https?://[^>\s]+)>", r"\1", text)
    # Slack HTML-escapes &, <, > before sending
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    # Apply any display-name renames inside free text (in case folks were
    # @-mentioned by typed-out name rather than by Slack `<@U…>` reference).
    text = users.apply_renames(text)
    return text


# ---------------------------------------------------------------------------
# Media classification
# ---------------------------------------------------------------------------


def media_kind(mime: str | None, filename: str) -> str:
    """Return one of: image, video, audio, other."""
    if not mime:
        mime, _ = mimetypes.guess_type(filename)
    if not mime:
        return "other"
    prefix = mime.split("/", 1)[0]
    if prefix in {"image", "video", "audio"}:
        return prefix
    return "other"


# ---------------------------------------------------------------------------
# Lemmy (fold)
# ---------------------------------------------------------------------------


@dataclass
class LemmyClient:
    instance: str
    jwt: str
    username: str = ""
    _http: httpx.Client = field(init=False)

    def __post_init__(self):
        self._http = httpx.Client(
            base_url=self.instance.rstrip("/"),
            timeout=HTTP_TIMEOUT,
            headers={"Authorization": f"Bearer {self.jwt}"},
        )

    def close(self):
        self._http.close()

    @classmethod
    def login(cls, instance: str, username: str, password: str) -> "LemmyClient":
        with httpx.Client(base_url=instance.rstrip("/"), timeout=HTTP_TIMEOUT) as c:
            r = c.post(
                "/api/v3/user/login",
                json={"username_or_email": username, "password": password},
            )
        if r.status_code >= 400:
            raise SystemExit(f"fold login as {username!r} failed: {r.status_code} {r.text[:200]}")
        jwt = (r.json() or {}).get("jwt")
        if not jwt:
            raise SystemExit(f"fold login as {username!r} returned no JWT")
        return cls(instance=instance, jwt=jwt, username=username)



    def resolve_community(self, name: str) -> int:
        r = self._http.get("/api/v3/community", params={"name": name})
        if r.status_code >= 400:
            raise SystemExit(
                f"Could not resolve community {name!r}: {r.status_code} {r.text[:200]}"
            )
        body = r.json() or {}
        cv = body.get("community_view") or {}
        cid = ((cv.get("community") or {}).get("id"))
        if not cid:
            raise SystemExit(f"Community {name!r} not found on {self.instance}")
        return int(cid)

    def upload_media(self, path: Path) -> str | None:
        """Upload to fold's pictrs. Returns the hosted absolute URL or None.

        pictrs handles images & most video; audio / pdf / arbitrary files
        usually 415 — caller falls back to a text note.
        """
        mime, _ = mimetypes.guess_type(path.name)
        files = {"images[]": (path.name, path.open("rb"), mime or "application/octet-stream")}
        try:
            r = self._http.post("/pictrs/image", files=files)
        finally:
            for _, (_, fh, _) in files.items():
                try:
                    fh.close()
                except Exception:
                    pass
        if r.status_code >= 400:
            sys.stderr.write(
                f"  pictrs rejected {path.name}: {r.status_code} {r.text[:160]}\n"
            )
            return None
        body = r.json() or {}
        flist = body.get("files") or []
        if not flist:
            sys.stderr.write(f"  pictrs returned no files for {path.name}\n")
            return None
        fname = flist[0].get("file")
        if not fname:
            return None
        return f"{self.instance.rstrip('/')}/pictrs/image/{fname}"

    def create_post(self, community_id: int, name: str, body: str, url: str | None) -> dict:
        payload: dict[str, Any] = {"community_id": community_id, "name": name}
        if body:
            payload["body"] = body
        if url:
            payload["url"] = url
        r = self._http.post("/api/v3/post", json=payload)
        if r.status_code >= 400:
            raise SystemExit(f"Create post failed: {r.status_code} {r.text[:400]}")
        return ((r.json() or {}).get("post_view") or {}).get("post") or {}

    def edit_post(self, post_id: int, *, name: str | None = None, body: str | None = None, url: str | None = None) -> dict:
        payload: dict[str, Any] = {"post_id": post_id}
        if name is not None:
            payload["name"] = name
        if body is not None:
            payload["body"] = body
        if url is not None:
            payload["url"] = url
        r = self._http.put("/api/v3/post", json=payload)
        if r.status_code >= 400:
            raise SystemExit(f"Edit post failed: {r.status_code} {r.text[:400]}")
        return ((r.json() or {}).get("post_view") or {}).get("post") or {}

    def get_post(self, post_id: int) -> dict | None:
        r = self._http.get("/api/v3/post", params={"id": post_id})
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise SystemExit(f"Get post failed: {r.status_code} {r.text[:400]}")
        return ((r.json() or {}).get("post_view") or {}).get("post") or None

    def create_comment(self, post_id: int, content: str) -> dict:
        r = self._http.post(
            "/api/v3/comment", json={"post_id": post_id, "content": content}
        )
        if r.status_code >= 400:
            raise SystemExit(f"Create comment failed: {r.status_code} {r.text[:400]}")
        return ((r.json() or {}).get("comment_view") or {}).get("comment") or {}

    def edit_comment(self, comment_id: int, content: str) -> dict:
        r = self._http.put(
            "/api/v3/comment", json={"comment_id": comment_id, "content": content}
        )
        if r.status_code >= 400:
            raise SystemExit(f"Edit comment failed: {r.status_code} {r.text[:400]}")
        return ((r.json() or {}).get("comment_view") or {}).get("comment") or {}


# ---------------------------------------------------------------------------
# Rendering Slack messages into Lemmy markdown
# ---------------------------------------------------------------------------

# Subtypes that aren't real "content" messages we want to mirror.
_SKIP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "channel_archive", "channel_unarchive",
    "pinned_item", "unpinned_item", "tombstone",
}

# Lemmy 0.19 post title limit (per server.lemmy_client).
POST_TITLE_MAX = 200
# Lemmy body/comment soft cap we'll truncate at — server allows ~10k.
BODY_MAX = 9500


def ts_to_human(ts: str) -> str:
    try:
        secs = float(ts)
    except (TypeError, ValueError):
        return ts or ""
    dt = datetime.fromtimestamp(secs, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def derive_title(text_md: str, fallback: str) -> str:
    """First non-empty line, stripped of markdown noise, clamped to 200 chars."""
    for line in (text_md or "").splitlines():
        line = line.strip().lstrip("> ").strip()
        if line:
            line = re.sub(r"^[#*_>\-`\s]+", "", line)
            return line[:POST_TITLE_MAX] or fallback[:POST_TITLE_MAX]
    return fallback[:POST_TITLE_MAX]


@dataclass
class RenderedAttachment:
    name: str
    kind: str          # image | video | audio | other
    url: str | None    # pictrs hosted URL if upload succeeded


def render_attachments(atts: list[RenderedAttachment]) -> str:
    """Render a list of attachments as markdown lines."""
    lines: list[str] = []
    for a in atts:
        if a.url and a.kind in ("image", "video"):
            # Lemmy renders inline images and embeds videos for the same syntax.
            lines.append(f"![{a.name}]({a.url})")
        elif a.url:
            lines.append(f"[{a.name}]({a.url})")
        else:
            lines.append(f"_(attachment `{a.name}` — could not be uploaded to fold)_")
    return "\n\n".join(lines)


def render_message_body(
    msg: dict,
    *,
    text_md: str,
    poster: str,
    posted_as_self: bool,
    attachments: list[RenderedAttachment],
    role: str,  # "post" or "comment"
    slack_thread_url: str | None = None,
) -> str:
    when = ts_to_human(msg.get("ts", ""))
    if posted_as_self:
        # Author will (after SQL re-author) be the same person on fold.
        header = f"_(mirrored from Slack — {when})_"
    else:
        header = f"_(mirrored from Slack — **{poster}**, {when})_"
    parts = [header]
    if role == "post" and slack_thread_url:
        parts.append(f"_Original Slack thread: <{slack_thread_url}>_")
    if text_md.strip():
        parts.append(text_md.strip())
    if attachments:
        parts.append(render_attachments(attachments))
    body = "\n\n".join(parts)
    if len(body) > BODY_MAX:
        body = body[: BODY_MAX - 30] + "\n\n_…(truncated)_"
    return body


def first_url_in(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"https?://[^\s<>)\]]+", text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def process_message(
    msg: dict,
    *,
    slack_token: str,
    users: UserCache,
    uploader: LemmyClient | None,
    media_dir: Path,
    dry_run: bool,
) -> tuple[str, list[RenderedAttachment]]:
    """Return (markdown text, [attachments]) for a single Slack message.

    `uploader` is the LemmyClient whose pictrs we upload through. Uploaded URLs
    are absolute, so they're usable regardless of which fold account posts the
    parent / comment that embeds them.
    """
    text_md = slack_text_to_markdown(msg.get("text") or "", users)
    rendered: list[RenderedAttachment] = []
    for f in msg.get("files") or []:
        name = f.get("name") or f.get("id") or "attachment"
        mime = f.get("mimetype")
        kind = media_kind(mime, name)
        local = download_slack_file(f, slack_token, media_dir)
        hosted: str | None = None
        if local and uploader and not dry_run:
            hosted = uploader.upload_media(local)
        elif local and dry_run:
            hosted = f"<would-upload:{local.name}>"
        rendered.append(RenderedAttachment(name=name, kind=kind, url=hosted))
    return text_md, rendered


DEFAULT_STATE_PATH = Path.home() / ".config" / "a-u.supply" / "slack_lemmy_state.json"


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()) or {}
    except json.JSONDecodeError:
        sys.stderr.write(f"warning: state file {path} is invalid JSON; ignoring\n")
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def state_key(instance: str, community: str, slack_url: str) -> str:
    return f"{instance.rstrip('/')}|{community}|{slack_url}"


def load_user_map(path: Path) -> dict[str, str]:
    """Parse the Slack display name -> fold username JSON map.

    Format: `{ "<slack display name>": "<fold username>", ... }`.

    The fold username is looked up against `person.name` (Lemmy local user
    table) when emitting the creator_id reassignment SQL.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    out: dict[str, str] = {}
    for slack_name, fold in (data or {}).items():
        if slack_name.startswith("_"):
            continue  # comment keys
        if not isinstance(fold, str) or not fold.strip():
            continue
        out[slack_name] = fold.strip()
    return out


def sql_quote(value: str) -> str:
    """Quote a value for a Postgres SQL literal (very small surface area here)."""
    return "'" + value.replace("'", "''") + "'"


@dataclass
class ParentSync:
    """One UPDATE row for the parent post."""
    post_id: int
    new_title: str | None
    new_body: str | None
    new_fold: str | None  # creator handle; None = leave alone


@dataclass
class CommentSync:
    """One UPDATE row per existing comment we need to rewrite (content + creator)."""
    comment_id: int
    new_content: str | None  # None = don't touch content
    new_fold: str | None     # None = leave creator alone


def emit_sql(
    *,
    out_path: Path,
    parent: ParentSync,
    comments: list[CommentSync],
    community_slug: str,
) -> None:
    """Write UPDATE statements for post + every comment we touched.

    `person.name` is the local Lemmy username. `local = true` keeps us off
    federated mirror accounts (we run private_instance anyway, but belt+braces).
    Idempotent: re-running the SQL is a no-op other than bumping updated.
    """
    lines: list[str] = [
        "-- Re-author + content-sync the imported Slack thread on fold.",
        f"-- Generated by scripts/slack_to_lemmy.py for /c/{community_slug}, post id={parent.post_id}.",
        "-- Apply via:  ssh dokku postgres:connect <fold-db-service> < this-file.sql",
        "BEGIN;",
        "",
    ]
    # Parent post
    post_sets: list[str] = []
    if parent.new_title is not None:
        post_sets.append(f"name = {sql_quote(parent.new_title)}")
    if parent.new_body is not None:
        post_sets.append(f"body = {sql_quote(parent.new_body)}")
    if parent.new_fold:
        post_sets.append(
            "creator_id = (SELECT id FROM person WHERE name = "
            f"{sql_quote(parent.new_fold)} AND local = true)"
        )
    if post_sets:
        lines.append(f"-- Parent post {parent.post_id}" + (
            f" -> {parent.new_fold}" if parent.new_fold else ""
        ))
        lines.append(f"UPDATE post SET {', '.join(post_sets)} WHERE id = {parent.post_id};")
        if parent.new_fold:
            # Lemmy 0.19 denormalizes creator_id into post_aggregates and only
            # populates it via the INSERT trigger on `post`. A bare UPDATE to
            # `post.creator_id` won't propagate — PostView joins through
            # post_aggregates, so it'll keep showing the old creator until we
            # sync this row too.
            lines.append(
                "UPDATE post_aggregates SET creator_id = (SELECT id FROM person WHERE name = "
                f"{sql_quote(parent.new_fold)} AND local = true) WHERE post_id = {parent.post_id};"
            )
        lines.append("")

    # Comments
    for c in comments:
        sets: list[str] = []
        if c.new_content is not None:
            sets.append(f"content = {sql_quote(c.new_content)}")
        if c.new_fold:
            sets.append(
                "creator_id = (SELECT id FROM person WHERE name = "
                f"{sql_quote(c.new_fold)} AND local = true)"
            )
        if not sets:
            lines.append(f"-- comment id={c.comment_id}: nothing to update")
            continue
        suffix = f"  -- -> {c.new_fold}" if c.new_fold else ""
        lines.append(f"UPDATE comment SET {', '.join(sets)} WHERE id = {c.comment_id};{suffix}")

    lines.append("")
    lines.append("COMMIT;")
    out_path.write_text("\n".join(lines) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--slack-url", required=True, help="Slack thread URL (the parent message permalink)")
    p.add_argument("--community", required=True, help="Lemmy community name on fold (e.g. project_root_of_ars)")
    p.add_argument("--instance", default=None, help="Lemmy instance URL (default: $FOLD_INSTANCE or fold.a-u.supply)")
    p.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Path to KEY=VAL env file (default: ~/.config/a-u.supply/env)")
    p.add_argument("--dry-run", action="store_true", help="Fetch + render but skip every Lemmy write")
    p.add_argument("--keep-media", action="store_true", help="Don't delete the temp media dir on exit (debug)")
    p.add_argument(
        "--rename",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="Replace a Slack display name (and any literal occurrences in text). Repeatable. e.g. --rename 'Full Name=Handle'",
    )
    p.add_argument(
        "--user-map",
        default="scripts/slack_fold_user_map.json",
        help="JSON map of Slack display name -> fold username. Used to emit creator_id reassignment SQL.",
    )
    p.add_argument(
        "--sql-out",
        default=None,
        help="Path to write the reassignment SQL (default: slack-to-lemmy-reassign-<post_id>.sql in cwd).",
    )
    p.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE_PATH),
        help="Where to remember post/comment IDs per (instance, community, slack thread) so re-runs sync instead of duplicating.",
    )
    p.add_argument(
        "--force-new",
        action="store_true",
        help="Ignore any existing state for this thread and create a fresh post.",
    )
    args = p.parse_args()

    renames: dict[str, str] = {}
    for spec in args.rename:
        if "=" not in spec:
            raise SystemExit(f"--rename expects OLD=NEW, got: {spec!r}")
        src, _, dst = spec.partition("=")
        renames[src.strip()] = dst.strip()

    load_env(Path(args.env_file).expanduser())

    slack_token = os.environ.get("SLACK_BOT_TOKEN") or ""
    if not slack_token:
        raise SystemExit("SLACK_BOT_TOKEN not set (check ~/.config/a-u.supply/env)")
    instance = args.instance or os.environ.get("FOLD_INSTANCE") or DEFAULT_FOLD_INSTANCE
    fold_user = os.environ.get("FOLD_USERNAME") or ""
    fold_pass = os.environ.get("FOLD_PASSWORD") or ""
    if not args.dry_run and not (fold_user and fold_pass):
        raise SystemExit("FOLD_USERNAME / FOLD_PASSWORD not set (or use --dry-run)")

    user_map = load_user_map(Path(args.user_map).expanduser())

    state_path = Path(args.state_file).expanduser()
    state = load_state(state_path)
    skey = state_key(instance, args.community, args.slack_url)
    thread_state = (state.get(skey) or {}) if not args.force_new else {}

    channel_id, thread_ts = parse_slack_url(args.slack_url)
    print(f"Fetching Slack thread channel={channel_id} ts={thread_ts}", file=sys.stderr)
    messages = fetch_thread(channel_id, thread_ts, slack_token)
    if not messages:
        raise SystemExit("Thread fetch returned no messages")
    messages = [m for m in messages if (m.get("subtype") not in _SKIP_SUBTYPES)]
    messages.sort(key=lambda m: float(m.get("ts", "0")))
    parent, replies = messages[0], messages[1:]
    channel_name = fetch_channel_name(channel_id, slack_token)
    print(f"  parent + {len(replies)} reply(ies) in #{channel_name}", file=sys.stderr)

    # Derive bare host from FOLD_INSTANCE for fediverse-style @user@host mentions.
    fold_host = instance.split("://", 1)[-1].rstrip("/")
    users = UserCache(
        slack_token, renames=renames, user_map=user_map, fold_host=fold_host,
    )

    all_posters = sorted({
        users.name(m.get("user") or m.get("bot_id") or "")
        for m in messages
    })
    print("Slack participants -> fold mapping:", file=sys.stderr)
    unmapped: list[str] = []
    for p_name in all_posters:
        if not p_name:
            continue
        if p_name in user_map:
            print(f"  • {p_name}  -> {user_map[p_name]}", file=sys.stderr)
        else:
            unmapped.append(p_name)
            print(f"  • {p_name}  -> (no mapping; will stay as {fold_user!r})", file=sys.stderr)

    lemmy: LemmyClient | None = None
    community_id = -1
    existing_post_id: int | None = thread_state.get("post_id") if not args.force_new else None
    if not args.dry_run:
        print(f"Logging into {instance} as {fold_user!r}", file=sys.stderr)
        lemmy = LemmyClient.login(instance, fold_user, fold_pass)
        community_id = lemmy.resolve_community(args.community)
        print(f"  community {args.community!r} -> id={community_id}", file=sys.stderr)
        # Validate state: if we think we have a post but Lemmy doesn't, fall back to create.
        if existing_post_id and not lemmy.get_post(existing_post_id):
            print(
                f"  state references post id={existing_post_id} but it no longer exists on fold; "
                f"creating fresh.",
                file=sys.stderr,
            )
            existing_post_id = None
            thread_state = {}
        if existing_post_id:
            print(
                f"  sync mode — updating existing post id={existing_post_id}",
                file=sys.stderr,
            )
    else:
        print(f"DRY RUN — would post to {instance}/c/{args.community}", file=sys.stderr)
        if existing_post_id:
            print(f"  (dry-run sync against post id={existing_post_id})", file=sys.stderr)

    tmp = Path(tempfile.mkdtemp(prefix="slack-to-lemmy-"))
    print(f"Temp media dir: {tmp}", file=sys.stderr)

    # ts (str) -> comment_id, carried over across runs.
    ts_to_cid: dict[str, int] = dict(thread_state.get("comments") or {})

    try:
        # Parent → Lemmy post (create or edit)
        print("Processing parent message…", file=sys.stderr)
        parent_text_md, parent_atts = process_message(
            parent, slack_token=slack_token, users=users,
            uploader=lemmy, media_dir=tmp, dry_run=args.dry_run,
        )
        parent_poster = users.name(parent.get("user") or parent.get("bot_id") or "")
        parent_fold = user_map.get(parent_poster)
        parent_body = render_message_body(
            parent, text_md=parent_text_md, poster=parent_poster,
            posted_as_self=bool(parent_fold),
            attachments=parent_atts, role="post",
            slack_thread_url=args.slack_url,
        )
        title_fallback = f"Slack thread from #{channel_name} on {ts_to_human(parent.get('ts',''))}"
        title = derive_title(parent_text_md, title_fallback)
        post_url: str | None = None
        if parent_atts and not parent.get("text"):
            first_media = next((a for a in parent_atts if a.url and a.kind == "image"), None)
            if first_media:
                post_url = first_media.url
        elif not parent.get("files"):
            url_candidate = first_url_in(parent.get("text") or "")
            if url_candidate and len((parent_text_md or "").strip()) <= len(url_candidate) + 20:
                post_url = url_candidate

        post_id = existing_post_id or 0
        # In sync mode we don't touch the post via API (admins can't edit
        # others' posts on 0.19) — the SQL update at the end handles content.
        if args.dry_run:
            mode = "SQL-UPDATE" if existing_post_id else "CREATE"
            print(f"\n--- POST ({mode}) ---")
            print(f"title: {title}")
            if post_url:
                print(f"url:   {post_url}")
            print(f"author intended: {parent_poster} -> {parent_fold or '(no mapping)'}")
            print(f"body:\n{parent_body}\n")
        else:
            assert lemmy is not None
            if not existing_post_id:
                post = lemmy.create_post(community_id, title, parent_body, post_url)
                post_id = int(post.get("id"))
                print(
                    f"  -> created post id={post_id} {instance.rstrip('/')}/post/{post_id}",
                    file=sys.stderr,
                )
            else:
                print(f"  -> queued content/creator UPDATE for post id={post_id}", file=sys.stderr)

        # Replies → top-level comments. Sync mode: SQL-update existing,
        # API-create new ones. Fresh mode: API-create all.
        comment_syncs: list[CommentSync] = []
        for idx, reply in enumerate(replies, 1):
            r_poster = users.name(reply.get("user") or reply.get("bot_id") or "")
            r_fold = user_map.get(r_poster)
            r_ts = reply.get("ts") or ""
            existing_cid = ts_to_cid.get(r_ts)
            mode = "sql-update" if existing_cid else "create"
            print(
                f"Processing reply {idx}/{len(replies)} by {r_poster!r} "
                f"(-> {r_fold or 'no mapping'}, {mode})…",
                file=sys.stderr,
            )
            r_text_md, r_atts = process_message(
                reply, slack_token=slack_token, users=users,
                uploader=lemmy, media_dir=tmp, dry_run=args.dry_run,
            )
            r_body = render_message_body(
                reply, text_md=r_text_md, poster=r_poster,
                posted_as_self=bool(r_fold),
                attachments=r_atts, role="comment",
            )
            if args.dry_run:
                print(f"\n--- COMMENT {idx} ({mode.upper()})  author intended: {r_poster} -> {r_fold or '(no mapping)'} ---")
                print(r_body)
                if existing_cid:
                    comment_syncs.append(CommentSync(existing_cid, r_body, r_fold))
            else:
                assert lemmy is not None
                if existing_cid:
                    cid = existing_cid
                    print(f"  -> queued content/creator UPDATE for comment id={cid}", file=sys.stderr)
                    comment_syncs.append(CommentSync(cid, r_body, r_fold))
                else:
                    c = lemmy.create_comment(post_id, r_body)
                    cid = int(c.get("id"))
                    ts_to_cid[r_ts] = cid
                    print(f"  -> created comment id={cid}", file=sys.stderr)
                    # New comment: tube IS the creator on disk, so only need
                    # creator_id reassign in SQL — body's already what we want.
                    comment_syncs.append(CommentSync(cid, None, r_fold))

        # Persist state for future re-runs.
        if not args.dry_run and post_id:
            state[skey] = {
                "post_id": post_id,
                "slack_url": args.slack_url,
                "community": args.community,
                "comments": ts_to_cid,
            }
            save_state(state_path, state)
            print(f"State updated: {state_path}", file=sys.stderr)

            sql_path = Path(args.sql_out) if args.sql_out else Path.cwd() / f"slack-to-lemmy-reassign-{post_id}.sql"
            parent_sync = ParentSync(
                post_id=post_id,
                new_title=title if existing_post_id else None,
                new_body=parent_body if existing_post_id else None,
                new_fold=parent_fold,
            )
            emit_sql(
                out_path=sql_path,
                parent=parent_sync,
                comments=comment_syncs,
                community_slug=args.community,
            )
            print(f"\nWrote SQL to: {sql_path}", file=sys.stderr)
            print(
                "Apply on fold:  ssh dokku postgres:connect <fold-db-service> < "
                f"{sql_path.name}",
                file=sys.stderr,
            )

        if unmapped:
            print(
                f"\nNote: no fold mapping for: {', '.join(repr(u) for u in unmapped)}. "
                f"Their posts will stay as {fold_user!r}.",
                file=sys.stderr,
            )

        print("Done.", file=sys.stderr)
        return 0
    finally:
        if lemmy is not None:
            lemmy.close()
        if not args.keep_media:
            for child in tmp.rglob("*"):
                if child.is_file():
                    try:
                        child.unlink()
                    except OSError:
                        pass
            try:
                tmp.rmdir()
            except OSError:
                pass
        else:
            print(f"(kept media dir: {tmp})", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
