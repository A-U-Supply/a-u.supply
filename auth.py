import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from models import ApiKey, SessionLocal, User

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 365  # 1 year

COOKIE_NAME = "au_session"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


# --- API Key Auth ---

SCOPE_HIERARCHY = {"read": 0, "write": 1, "admin": 2}


def hash_api_key(key: str) -> str:
    """Hash an API key using bcrypt."""
    return pwd_context.hash(key)


def verify_api_key(plain_key: str, hashed: str) -> bool:
    """Verify an API key against its bcrypt hash."""
    return pwd_context.verify(plain_key, hashed)


def generate_api_key() -> str:
    """Generate a random API key."""
    return secrets.token_urlsafe(32)


def get_current_user_or_apikey(
    request: Request, db: Session = Depends(get_db)
) -> tuple[User, str]:
    """Authenticate via JWT cookie or API key Bearer token.

    Returns a tuple of (User, scope_string).
    For JWT users, scope is derived from role: admin -> "admin", member -> "write".
    """
    # First, try JWT cookie
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            email: str = payload.get("sub")
            if email is not None:
                user = db.query(User).filter(User.email == email).first()
                if user is not None:
                    scope = "admin" if user.role == "admin" else "write"
                    return (user, scope)
        except JWTError:
            pass

    # Then, try Authorization: Bearer header
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:]
        active_keys = (
            db.query(ApiKey)
            .filter(ApiKey.revoked_at.is_(None))
            .all()
        )
        for api_key in active_keys:
            if verify_api_key(bearer_token, api_key.key_hash):
                user = db.query(User).filter(User.id == api_key.user_id).first()
                if user is None:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="API key owner not found",
                    )
                # Debounce last_used_at updates to once per minute
                now = datetime.now(timezone.utc)
                if (
                    api_key.last_used_at is None
                    or (now - api_key.last_used_at) > timedelta(minutes=1)
                ):
                    api_key.last_used_at = now
                    db.commit()
                return (user, api_key.scope)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


def require_scope(required: str):
    """FastAPI dependency factory that checks scope level.

    Scope hierarchy: admin > write > read.
    """
    required_level = SCOPE_HIERARCHY.get(required, 0)

    def _check_scope(
        user_and_scope: tuple[User, str] = Depends(get_current_user_or_apikey),
    ) -> tuple[User, str]:
        user, scope = user_and_scope
        user_level = SCOPE_HIERARCHY.get(scope, 0)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Scope '{required}' required, but you have '{scope}'",
            )
        return (user, scope)

    return _check_scope
