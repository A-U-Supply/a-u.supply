import asyncio
import hashlib
import hmac
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from auth import (
    COOKIE_NAME,
    create_access_token,
    generate_csrf_token,
    get_current_user,
    get_db,
    hash_password,
    require_admin,
    verify_password,
)
from bookmarks_api import router as bookmarks_router
from catalog import router as catalog_router
from jobs_api import router as jobs_router
from search_api import router as search_router
from models import Base, User, engine

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Ensure data directory exists
Path("data").mkdir(exist_ok=True)

Base.metadata.create_all(bind=engine)

# Migrate existing DB: add output_index column if missing
from sqlalchemy import inspect as _sa_inspect, text as _sa_text
_cols = [c["name"] for c in _sa_inspect(engine).get_columns("media_items")]
if "output_index" not in _cols:
    with engine.begin() as _conn:
        _conn.execute(_sa_text("ALTER TABLE media_items ADD COLUMN output_index TEXT"))


# ---------------------------------------------------------------------------
# Background auto-sync scheduler
# ---------------------------------------------------------------------------

SYNC_ENABLED = os.environ.get("SLACK_AUTO_SYNC", "").lower() in ("1", "true", "yes")
SYNC_INTERVAL_SCRAPE = int(os.environ.get("SYNC_INTERVAL_SCRAPE", "120"))   # seconds
SYNC_INTERVAL_REACTIONS = int(os.environ.get("SYNC_INTERVAL_REACTIONS", "300"))  # seconds


# Dedicated thread pool for Slack sync so it can never starve the default
# executor that uvicorn uses for serving requests.
_sync_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="slack-sync")


async def _auto_scrape_loop():
    """Periodically run incremental Slack scrapes."""
    await asyncio.sleep(30)  # let the app finish starting up
    loop = asyncio.get_running_loop()
    while True:
        try:
            from slack_scraper import trigger_incremental_scrape
            result = await loop.run_in_executor(_sync_executor, trigger_incremental_scrape)
            logger.info("Auto-sync scrape: %s", result.get("status"))
        except Exception:
            logger.exception("Auto-sync scrape failed")
        await asyncio.sleep(SYNC_INTERVAL_SCRAPE)


async def _auto_reactions_loop():
    """Periodically refresh reaction counts."""
    await asyncio.sleep(60)  # offset from scrape loop
    loop = asyncio.get_running_loop()
    while True:
        try:
            from slack_scraper import trigger_reaction_refresh
            result = await loop.run_in_executor(_sync_executor, lambda: trigger_reaction_refresh(days_back=7))
            logger.info("Auto-sync reactions: updated=%s errors=%s skipped=%s",
                        result.get("updated"), result.get("errors"), result.get("skipped", 0))
        except Exception:
            logger.exception("Auto-sync reactions failed")
        await asyncio.sleep(SYNC_INTERVAL_REACTIONS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background tasks for the app lifecycle."""
    tasks = []
    if SYNC_ENABLED:
        logger.info(
            "Slack auto-sync enabled (scrape every %ds, reactions every %ds)",
            SYNC_INTERVAL_SCRAPE, SYNC_INTERVAL_REACTIONS,
        )
        tasks.append(asyncio.create_task(_auto_scrape_loop()))
        tasks.append(asyncio.create_task(_auto_reactions_loop()))
    else:
        logger.info("Slack auto-sync disabled (set SLACK_AUTO_SYNC=true to enable)")

    yield

    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


limiter = Limiter(key_func=get_remote_address)

API_DESCRIPTION = """
## A-U.Supply — Audio Units Division API

Internal API for the A-U.Supply platform. Manages the release catalog, media search engine,
user accounts, and Slack-based media ingestion. All access requires authentication.

---

### Authentication

There are two ways to authenticate:

**1. Session cookie (browser)**

Log in via `POST /api/login` with email and password. The server sets an httpOnly cookie
(`au_session`) containing a JWT. All subsequent requests from that browser session are
automatically authenticated. Sessions last 1 year.

**2. API key (programmatic)**

Generate a key at `POST /api/keys` (or in the admin UI at `/admin/search`). Send it as
a Bearer token:

```
Authorization: Bearer au_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

API keys have a **scope** that controls what they can do (see below). The raw key is shown
**only once** at creation — store it somewhere safe.

### Scopes & Permissions

Both session cookies and API keys resolve to a **scope** that determines access:

| Scope | Can do | Who gets it |
|-------|--------|-------------|
| `read` | Search, view metadata, stream/download files | API keys with read scope |
| `write` | Everything in read + upload, tag, edit | Members (session), API keys with write scope |
| `admin` | Everything in write + delete, manage users, trigger scrapes | Admins (session), API keys with admin scope |

Session scope is derived from your user role: `admin` role → admin scope, `member` role → write scope.

### Rate Limiting

`POST /api/login` is limited to **5 requests per minute** per IP address. All other endpoints
are unlimited.

### CSRF Protection

State-changing requests made via session cookie (login, password change, user creation) require
a CSRF token. Fetch one from `GET /api/csrf` and include it in your request body as `csrf_token`.
API key requests do not need CSRF tokens.

### Product Codes & Special Characters

Release product codes can contain special characters (`#`, spaces, dots). When using product
codes in URL paths, you **must** URL-encode them:

- JavaScript: `encodeURIComponent(code)`
- Python: `urllib.parse.quote(code, safe='')`
- Example: `A-U# M5497.H37` → `A-U%23%20M5497.H37`

### Response Format

All endpoints return JSON. Successful mutations return `{"ok": true}` or the created/updated
resource. Errors return `{"detail": "error message"}` with an appropriate HTTP status code.

### Interactive Documentation

You're looking at it. Use the **"Try it out"** button on any endpoint to test it live.
Authenticate first via `POST /api/login` in your browser — the session cookie will carry
over to requests made from this page.
"""

TAGS_METADATA = [
    {
        "name": "Authentication",
        "description": "Login, logout, CSRF tokens, and session management. No API key needed — these "
                       "endpoints bootstrap your session.",
    },
    {
        "name": "User Profile",
        "description": "View and manage your own account (password changes, profile info).",
    },
    {
        "name": "User Administration",
        "description": "Create, list, and delete user accounts. **Admin only.** "
                       "There is no public signup — users are invited by an admin.",
    },
    {
        "name": "Entities",
        "description": "Manage artist/manufacturer entities. An entity is a project or alias name "
                       "(e.g. a band name, a solo alias). Entities are linked to releases via a "
                       "many-to-many relationship — a release can have multiple entities and an "
                       "entity can appear on many releases.",
    },
    {
        "name": "Releases",
        "description": "Create, read, update, and delete music releases in the catalog. Releases "
                       "have a lifecycle: they start as **drafts** (only visible to authenticated users) "
                       "and are **published** to make them publicly visible.\n\n"
                       "Product codes are auto-generated in the format `AU-{YYYY}-{CAT}-{SEQ}` "
                       "(e.g. `AU-2026-LP-004`) but can be replaced with any unique string.",
    },
    {
        "name": "Tracks",
        "description": "Upload, delete, reorder, and stream audio tracks within a release. "
                       "Supported formats: FLAC, WAV, MP3, OGG, AAC, M4A, AIFF.\n\n"
                       "**Quirks:**\n"
                       "- Track numbers are auto-assigned on upload and auto-renumbered on delete\n"
                       "- Duration is extracted server-side via ffprobe\n"
                       "- Streaming supports HTTP Range requests for seeking\n"
                       "- Draft release tracks require authentication to stream",
    },
    {
        "name": "Cover Art",
        "description": "Upload and serve release cover art. Supported formats: JPG, PNG, WEBP, GIF.\n\n"
                       "On upload, a 400x400 WebP thumbnail is auto-generated alongside the original. "
                       "Use `?size=thumb` to fetch the thumbnail instead of the full image.",
    },
    {
        "name": "Media Search",
        "description": "Full-text search across all indexed media (images, audio, video). Powered by "
                       "Meilisearch with typo tolerance, faceted filtering, and multi-index search.\n\n"
                       "Search hits include items ingested from Slack channels and manual uploads. "
                       "Results can be filtered by tags, source channel, date range, reaction count, "
                       "dominant color, and more.",
    },
    {
        "name": "Media Items",
        "description": "CRUD operations for individual media items in the search engine. Each media "
                       "item has a unique SHA-256 content hash used for deduplication — uploading the "
                       "same file twice creates a new **source** record but doesn't duplicate the file.\n\n"
                       "Media items can have type-specific metadata:\n"
                       "- **Images**: dimensions, format, dominant colors\n"
                       "- **Audio**: duration, sample rate, channels, bit depth, speech transcript\n"
                       "- **Video**: duration, dimensions, FPS, speech transcript",
    },
    {
        "name": "Tagging",
        "description": "Add and remove tags on media items. Tags are normalized (lowercased, trimmed) "
                       "and deduplicated. A shared vocabulary tracks all known tags with usage counts "
                       "for autocomplete suggestions.\n\n"
                       "Any member can create new tags — there's no approval workflow. "
                       "Tag autocomplete searches by substring, not just prefix.",
    },
    {
        "name": "Batch Operations",
        "description": "Perform operations on multiple media items at once — bulk tagging, deletion, "
                       "re-extraction, or ZIP export. All batch endpoints accept an array of media IDs.",
    },
    {
        "name": "Slack Ingestion",
        "description": "Trigger and monitor Slack channel scraping. The scraper pulls file attachments "
                       "and linked media (YouTube, TikTok, SoundCloud via yt-dlp) from configured "
                       "Slack channels.\n\n"
                       "**Workflows:**\n"
                       "- **Full scrape** (`POST /api/ingest/slack`): Pulls all messages since last scrape. "
                       "First run fetches entire channel history.\n"
                       "- **Incremental sync** (`POST /api/ingest/slack/sync`): Refreshes reactions + "
                       "pulls new messages. This is what the 'Sync Now' button does.\n"
                       "- **Dry run** (`POST /api/ingest/slack/dry-run`): Calculates download sizes "
                       "without actually fetching anything. Use this before a large backfill.\n"
                       "- **Reaction refresh** (`POST /api/ingest/slack/reactions`): Re-fetches reaction "
                       "counts from Slack for recently posted items.\n\n"
                       "Auto-sync can be enabled server-side to run scrapes and reaction refreshes on "
                       "a configurable interval.",
    },
    {
        "name": "API Keys",
        "description": "Create, list, and revoke API keys for programmatic access. Keys use the "
                       "`Authorization: Bearer au_xxxxx` header.\n\n"
                       "**Important details:**\n"
                       "- The raw key is shown **only once** at creation — copy it immediately\n"
                       "- Keys are stored as bcrypt hashes (we can't recover a lost key)\n"
                       "- Revocation is instant — the key stops working on the next request\n"
                       "- `last_used_at` is updated at most once per minute (debounced)\n"
                       "- You can only see and revoke your own keys",
    },
    {
        "name": "Workspaces",
        "description": "Persistent selection carts for collecting media items before processing. "
                       "Add items from the search engine across multiple sessions, then submit "
                       "the workspace to an app for processing.",
    },
    {
        "name": "Apps",
        "description": "Registry of available processing apps. Each app is a Docker container "
                       "with a TOML manifest defining what inputs it accepts, what parameters "
                       "it takes, and how to run it.\n\n"
                       "Apps are registered by admins. Use `GET /api/apps` to list available apps "
                       "and their manifests, which describe the parameter schema for building UI forms.\n\n"
                       "See `POST /api/apps` for the full manifest format documentation.",
    },
    {
        "name": "Jobs",
        "description": "Job queue for processing media through apps. Submit a job with input items "
                       "and parameters, and the worker will run it in a Docker container.\n\n"
                       "**Job lifecycle:** `pending` → `running` → `completed` or `failed`\n\n"
                       "- Pending jobs are picked up by the worker in priority order (lower = first)\n"
                       "- Running jobs can be cancelled (the container is stopped)\n"
                       "- Failed jobs can be retried up to `max_retries` times\n"
                       "- Completed jobs have output files that can be previewed, indexed, or discarded",
    },
    {
        "name": "Job Outputs",
        "description": "Files produced by completed jobs. Outputs can be:\n\n"
                       "- **Downloaded** directly\n"
                       "- **Indexed** into the search engine (creates a media item, runs extraction, "
                       "syncs to Meilisearch, tagged with `job:<app_name>`)\n"
                       "- **Discarded** (deleted from disk)\n\n"
                       "Indexing is non-destructive — the original output file stays in the job directory "
                       "and a copy is placed in the search media directory.",
    },
    {
        "name": "Extraction Failures",
        "description": "View and manage failures from the async metadata extraction pipeline. When a "
                       "media item is ingested, background tasks extract metadata (image dimensions, "
                       "audio transcripts, video thumbnails, etc.). If extraction fails, it's logged "
                       "here for manual review.\n\n"
                       "Failure types: `whisper` (speech transcription), `ffprobe` (media probing), "
                       "`dominant_colors` (image analysis), `thumbnail` (video frame grab), "
                       "`yt-dlp` (link download).",
    },
    {
        "name": "Webhooks",
        "description": "GitHub webhook receivers for automated deployments. Verified via "
                       "HMAC-SHA256 signature (`X-Hub-Signature-256` header). These are not meant "
                       "to be called manually.",
    },
]

app = FastAPI(
    title="A-U.Supply API",
    description=API_DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=TAGS_METADATA,
    docs_url="/docs",
    redoc_url="/redoc",
)
app.state.limiter = limiter

# Rate limit error handler
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests"})

# CORS — allow Astro dev server in development
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:4321"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bookmarks_router)
app.include_router(catalog_router)
app.include_router(jobs_router)
app.include_router(search_router)

IS_PRODUCTION = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")

LEGACY_DIR = Path(os.environ.get("LEGACY_SITE_DIR", "/srv/legacy-site"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


@app.middleware("http")
async def legacy_fallback(request: Request, call_next):
    """Serve legacy site files as fallback when Astro returns 404."""
    response = await call_next(request)
    if response.status_code == 404 and LEGACY_DIR.is_dir():
        path = request.url.path.lstrip("/")
        if path:
            legacy_file = (LEGACY_DIR / path).resolve()
            if legacy_file.is_file() and legacy_file.is_relative_to(LEGACY_DIR.resolve()):
                return FileResponse(legacy_file)
    return response


# --- Schemas ---


class LoginRequest(BaseModel):
    """Credentials for logging in. Requires a CSRF token from `GET /api/csrf`."""
    email: str = Field(..., description="User's email address.")
    password: str = Field(..., description="User's password.")
    csrf_token: str = Field(..., description="CSRF token from `GET /api/csrf`. Required for all session-based mutations.")


class UserResponse(BaseModel):
    """Public user profile information."""
    id: int = Field(..., description="Unique user ID.")
    email: str = Field(..., description="User's email address.")
    name: str = Field(..., description="Display name.")
    role: str = Field(..., description="User role: `admin` or `member`. Admins can manage users and perform destructive operations.")


class ChangePasswordRequest(BaseModel):
    """Password change request. Must include the current password for verification."""
    current_password: str = Field(..., description="Your current password for verification.")
    new_password: str = Field(..., min_length=8, description="New password. Must be at least 8 characters.")
    csrf_token: str = Field(..., description="CSRF token from `GET /api/csrf`.")


class InviteRequest(BaseModel):
    """Create a new user account. Only admins can do this — there is no public signup."""
    email: str = Field(..., description="Email address for the new user. Must be unique.")
    name: str = Field(..., description="Display name for the new user.")
    password: str = Field(..., description="Initial password. The user can change it later.")
    role: str = Field("member", description="Role: `admin` or `member`. Members can read/write but not manage users or delete content.")
    csrf_token: str = Field(..., description="CSRF token from `GET /api/csrf`.")


# --- Auth routes ---


@app.get("/api/csrf", tags=["Authentication"], summary="Get a CSRF token")
def get_csrf():
    """Generate a fresh CSRF token for use in state-changing requests.

    CSRF tokens are required for `POST /api/login`, `POST /api/me/password`,
    and `POST /api/admin/users` when authenticating via session cookie. Include
    the token in the request body as `csrf_token`.

    Tokens are single-use, cryptographically random strings. Generate a new one
    before each form submission.
    """
    return {"csrf_token": generate_csrf_token()}


@app.post("/api/login", tags=["Authentication"], summary="Log in with email and password")
@limiter.limit("5/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate with email and password. On success, sets an httpOnly session cookie
    (`au_session`) containing a JWT that lasts 1 year.

    **Rate limited to 5 attempts per minute per IP address.**

    The response body includes the authenticated user's profile. The cookie is set
    automatically — you don't need to manage it manually in a browser.

    There is no public signup. User accounts are created by admins via
    `POST /api/admin/users` or the CLI.
    """
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token({"sub": user.email})
    response = JSONResponse(content={
        "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role}
    })
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=IS_PRODUCTION,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,  # 1 year
        path="/",
    )
    return response


@app.post("/api/logout", tags=["Authentication"], summary="Log out (clear session cookie)")
def logout():
    """Clear the session cookie and end the current session.

    This does not invalidate the JWT itself — it just removes the cookie from the
    browser. If someone has a copy of the token, it remains valid until it expires.
    """
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response


@app.get("/api/me", response_model=UserResponse, tags=["User Profile"], summary="Get current user profile")
def me(current_user: User = Depends(get_current_user)):
    """Return the profile of the currently authenticated user.

    Use this to check if a session is still valid and to get the user's role.
    Returns 401 if the session cookie is missing, expired, or invalid.
    """
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        role=current_user.role,
    )


@app.post("/api/me/password", tags=["User Profile"], summary="Change your password")
def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the password for the currently authenticated user.

    Requires the current password for verification. The new password must be
    at least 8 characters. Your existing session remains valid after the change.
    """
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


# --- Admin routes ---


@app.get("/api/admin/users", tags=["User Administration"], summary="List all users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Return a list of all user accounts, sorted by creation date (newest first).

    **Admin only.** Includes each user's ID, email, name, role, and creation timestamp.
    """
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [
        {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "created_at": u.created_at.isoformat()}
        for u in users
    ]


@app.post("/api/admin/users", tags=["User Administration"], summary="Create a new user")
def create_user(body: InviteRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Create a new user account. There is no public signup — only admins can create users.

    **Admin only.**

    The new user can log in immediately with the provided email and password. Their role
    determines what they can do:

    - `member`: Can read, upload, tag, and edit media. Cannot delete content or manage users.
    - `admin`: Full access including user management and destructive operations.

    Returns 400 if the email is already registered or the role is invalid.
    """
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    if body.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Role must be admin or member")

    user = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@app.delete("/api/admin/users/{user_id}", tags=["User Administration"], summary="Delete a user")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Permanently delete a user account.

    **Admin only.** You cannot delete your own account (returns 400). This is a safety
    measure to prevent accidental lockout.

    Deleting a user does not delete content they created (releases, uploads, etc.) —
    those remain in the system attributed to the deleted user's ID.
    """
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}


# --- Legacy site ---


@app.get("/the-expenditure", include_in_schema=False)
def legacy_home():
    """Serve the old site's homepage at /the-expenditure."""
    index = LEGACY_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Legacy site not found")
    return FileResponse(index)


# --- Webhooks ---


def _verify_webhook(body: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


@app.post("/hooks/legacy", tags=["Webhooks"], summary="Legacy site auto-deploy webhook")
async def webhook_legacy(request: Request):
    """GitHub webhook receiver that pulls the latest legacy site on push.

    Called automatically by GitHub when changes are pushed to the legacy site
    repository. Verifies the request signature via HMAC-SHA256 before acting.

    **Do not call manually** — this is triggered by GitHub's webhook system.
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_webhook(body, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")
    if not LEGACY_DIR.is_dir():
        raise HTTPException(status_code=503, detail="Legacy site directory not found")
    subprocess.Popen(
        ["git", "-C", str(LEGACY_DIR), "pull", "--ff-only"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "action": "pulling legacy site"}


@app.post("/hooks/deploy", tags=["Webhooks"], summary="Main app redeploy webhook")
async def webhook_deploy(request: Request):
    """GitHub webhook receiver that triggers a redeploy of the main application.

    Runs the configured deploy script if set, otherwise acknowledges the webhook
    without action (the deployment platform rebuilds automatically on git push).

    **Do not call manually** — this is triggered by GitHub's webhook system.
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_webhook(body, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")
    deploy_script = os.environ.get("DEPLOY_SCRIPT", "")
    if deploy_script:
        subprocess.Popen(
            deploy_script.split(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "action": "deploy script triggered"}
    return {"ok": True, "action": "webhook received (no DEPLOY_SCRIPT configured)"}


# --- Static files ---
# Serve Astro build output as middleware rather than a mount, so it
# doesn't swallow routes like /catalog that are handled by FastAPI.

DIST_DIR = Path("dist")


@app.middleware("http")
async def static_files(request: Request, call_next):
    """Serve static files from dist/ only when the file actually exists."""
    response = await call_next(request)
    if response.status_code == 404 and DIST_DIR.is_dir():
        url_path = request.url.path.lstrip("/")
        # Try exact file
        candidate = (DIST_DIR / url_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(DIST_DIR.resolve()):
            return FileResponse(candidate)
        # Try as directory with index.html (html=True behavior)
        index = (DIST_DIR / url_path / "index.html").resolve()
        if index.is_file() and index.is_relative_to(DIST_DIR.resolve()):
            return FileResponse(index)
    return response


@app.get("/", include_in_schema=False)
def index():
    return FileResponse("dist/index.html")
