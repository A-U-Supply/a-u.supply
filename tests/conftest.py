"""Shared test fixtures for the media search engine test suite."""

import os
import tempfile

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient


@pytest.fixture
def db_engine():
    """Create an in-memory SQLite engine shared across all connections."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    from models import Base

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Create a scoped SQLAlchemy session for a single test."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def test_user(db_session):
    """Create a test admin user."""
    from models import User
    from auth import hash_password

    user = User(
        email="test@test.com",
        name="Test User",
        password_hash=hash_password("testpass"),
        role="admin",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_member(db_session):
    """Create a test member (non-admin) user."""
    from models import User
    from auth import hash_password

    user = User(
        email="member@test.com",
        name="Member User",
        password_hash=hash_password("memberpass"),
        role="member",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def tmp_media_dir():
    """Create a temporary directory for media files during tests."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def app(db_engine, tmp_media_dir):
    """Create a test FastAPI app with overridden dependencies."""
    os.environ["SEARCH_MEDIA_DIR"] = tmp_media_dir
    from main import app
    from auth import get_db

    Session = sessionmaker(bind=db_engine)

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    """Create a FastAPI TestClient."""
    return TestClient(app)


@pytest.fixture
def auth_headers(test_user):
    """Return Authorization headers with a valid JWT cookie for the test admin user."""
    from auth import create_access_token, COOKIE_NAME

    token = create_access_token({"sub": test_user.email})
    return {"Cookie": f"{COOKIE_NAME}={token}"}


@pytest.fixture
def member_auth_headers(test_member):
    """Return Authorization headers with a valid JWT cookie for a member user."""
    from auth import create_access_token, COOKIE_NAME

    token = create_access_token({"sub": test_member.email})
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def make_media_item(db_session, **kwargs):
    """Helper to create a MediaItem with sensible defaults."""
    import uuid
    import hashlib

    from models import MediaItem

    defaults = {
        "id": str(uuid.uuid4()),
        "sha256": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        "filename": "test.png",
        "file_path": "image/2026-04/abcdef12_test.png",
        "media_type": "image",
        "file_size_bytes": 1024,
        "mime_type": "image/png",
    }
    defaults.update(kwargs)
    item = MediaItem(**defaults)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def make_media_source(db_session, media_item_id, **kwargs):
    """Helper to create a MediaSource with sensible defaults."""
    from models import MediaSource

    defaults = {
        "media_item_id": media_item_id,
        "source_type": "manual_upload",
    }
    defaults.update(kwargs)
    source = MediaSource(**defaults)
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)
    return source
