import hashlib
import hmac
import os
import subprocess
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
from catalog import router as catalog_router
from catalog_views import router as catalog_views_router
from models import Base, User, engine

# Ensure data directory exists
Path("data").mkdir(exist_ok=True)

Base.metadata.create_all(bind=engine)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="a-u.supply")
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

app.include_router(catalog_router)

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
    email: str
    password: str
    csrf_token: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    csrf_token: str


class InviteRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "member"
    csrf_token: str


# --- Auth routes ---


@app.get("/api/csrf")
def get_csrf():
    return {"csrf_token": generate_csrf_token()}


@app.post("/api/login")
@limiter.limit("5/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
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
        max_age=60 * 60 * 24,  # 24 hours
        path="/",
    )
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response


@app.get("/api/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        role=current_user.role,
    )


@app.post("/api/me/password")
def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


# --- Admin routes ---


@app.get("/api/admin/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [
        {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "created_at": u.created_at.isoformat()}
        for u in users
    ]


@app.post("/api/admin/users")
def create_user(body: InviteRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}


# --- Legacy site ---


@app.get("/the-expenditure")
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


@app.post("/hooks/legacy")
async def webhook_legacy(request: Request):
    """GitHub webhook: pull latest legacy site on push."""
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


@app.post("/hooks/deploy")
async def webhook_deploy(request: Request):
    """GitHub webhook: trigger a redeploy of the main app.

    Runs DEPLOY_SCRIPT if configured, otherwise acknowledges the webhook
    without action (Dokku rebuilds automatically on git push).
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


# --- SSR Catalog pages (must come before static mount) ---
app.include_router(catalog_views_router)


# --- Static / homepage ---


@app.get("/")
def index():
    return FileResponse("dist/index.html")


# Serve Astro build output — must come after all API routes
app.mount("/", StaticFiles(directory="dist", html=True), name="static")
