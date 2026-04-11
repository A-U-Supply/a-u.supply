import os
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

IS_PRODUCTION = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")


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


# --- Static / homepage ---


@app.get("/")
def index():
    return FileResponse("dist/index.html")


# Serve Astro build output — must come after all API routes
app.mount("/", StaticFiles(directory="dist", html=True), name="static")
