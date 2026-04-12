"""Tests for API key authentication and scope enforcement."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tests.conftest import make_media_item


class TestApiKeyGeneration:
    """Tests for API key generation and hashing."""

    def test_generate_api_key_returns_string(self):
        from auth import generate_api_key

        key = generate_api_key()
        assert isinstance(key, str)
        assert len(key) > 20

    def test_generate_api_key_unique(self):
        from auth import generate_api_key

        keys = {generate_api_key() for _ in range(50)}
        assert len(keys) == 50

    def test_hash_verify_roundtrip(self):
        from auth import hash_api_key, verify_api_key

        key = "test-secret-key-12345"
        hashed = hash_api_key(key)
        assert isinstance(hashed, str)
        assert hashed != key
        assert verify_api_key(key, hashed) is True

    def test_verify_wrong_key_returns_false(self):
        from auth import hash_api_key, verify_api_key

        hashed = hash_api_key("correct-key")
        assert verify_api_key("wrong-key", hashed) is False

    def test_hash_is_not_deterministic(self):
        """bcrypt hashes include a random salt, so two hashes of the same key differ."""
        from auth import hash_api_key

        h1 = hash_api_key("same-key")
        h2 = hash_api_key("same-key")
        assert h1 != h2


class TestScopeHierarchy:
    """Tests for scope hierarchy: admin > write > read."""

    def test_scope_hierarchy_values(self):
        from auth import SCOPE_HIERARCHY

        assert SCOPE_HIERARCHY["read"] < SCOPE_HIERARCHY["write"]
        assert SCOPE_HIERARCHY["write"] < SCOPE_HIERARCHY["admin"]

    def test_require_scope_read_allows_all(self, db_session, test_user):
        """read scope should be accessible by read, write, and admin."""
        from auth import require_scope, SCOPE_HIERARCHY

        for scope in ("read", "write", "admin"):
            dep = require_scope("read")
            # Simulate calling the dependency with a user+scope tuple
            result = dep(user_and_scope=(test_user, scope))
            assert result == (test_user, scope)

    def test_require_scope_write_rejects_read(self, db_session, test_user):
        from auth import require_scope
        from fastapi import HTTPException

        dep = require_scope("write")
        with pytest.raises(HTTPException) as exc_info:
            dep(user_and_scope=(test_user, "read"))
        assert exc_info.value.status_code == 403

    def test_require_scope_admin_rejects_write(self, db_session, test_user):
        from auth import require_scope
        from fastapi import HTTPException

        dep = require_scope("admin")
        with pytest.raises(HTTPException) as exc_info:
            dep(user_and_scope=(test_user, "write"))
        assert exc_info.value.status_code == 403

    def test_require_scope_admin_allows_admin(self, db_session, test_user):
        from auth import require_scope

        dep = require_scope("admin")
        result = dep(user_and_scope=(test_user, "admin"))
        assert result == (test_user, "admin")


class TestApiKeyAuth:
    """Tests for API key authentication via Bearer header."""

    def test_bearer_auth_with_valid_key(self, db_session, test_user):
        from models import ApiKey
        from auth import hash_api_key, get_current_user_or_apikey

        raw_key = "test-api-key-for-auth"
        api_key = ApiKey(
            user_id=test_user.id,
            key_hash=hash_api_key(raw_key),
            key_prefix=raw_key[:8],
            label="test",
            scope="read",
        )
        db_session.add(api_key)
        db_session.commit()

        # Build a mock request with Bearer header
        request = MagicMock()
        request.cookies = {}
        request.headers = {"authorization": f"Bearer {raw_key}"}

        user, scope = get_current_user_or_apikey(request, db_session)
        assert user.id == test_user.id
        assert scope == "read"

    def test_revoked_key_is_rejected(self, db_session, test_user):
        from models import ApiKey
        from auth import hash_api_key, get_current_user_or_apikey
        from fastapi import HTTPException

        raw_key = "revoked-key-12345"
        api_key = ApiKey(
            user_id=test_user.id,
            key_hash=hash_api_key(raw_key),
            key_prefix=raw_key[:8],
            label="revoked",
            scope="write",
            revoked_at=datetime.now(timezone.utc),
        )
        db_session.add(api_key)
        db_session.commit()

        request = MagicMock()
        request.cookies = {}
        request.headers = {"authorization": f"Bearer {raw_key}"}

        with pytest.raises(HTTPException) as exc_info:
            get_current_user_or_apikey(request, db_session)
        assert exc_info.value.status_code == 401

    def test_jwt_cookie_auth_still_works(self, db_session, test_user):
        from auth import create_access_token, get_current_user_or_apikey, COOKIE_NAME

        token = create_access_token({"sub": test_user.email})

        request = MagicMock()
        request.cookies = {COOKIE_NAME: token}
        request.headers = {}

        user, scope = get_current_user_or_apikey(request, db_session)
        assert user.id == test_user.id
        # Admin user gets "admin" scope from JWT
        assert scope == "admin"

    def test_jwt_member_gets_write_scope(self, db_session, test_member):
        from auth import create_access_token, get_current_user_or_apikey, COOKIE_NAME

        token = create_access_token({"sub": test_member.email})

        request = MagicMock()
        request.cookies = {COOKIE_NAME: token}
        request.headers = {}

        user, scope = get_current_user_or_apikey(request, db_session)
        assert user.id == test_member.id
        assert scope == "write"

    def test_no_auth_raises_401(self, db_session):
        from auth import get_current_user_or_apikey
        from fastapi import HTTPException

        request = MagicMock()
        request.cookies = {}
        request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            get_current_user_or_apikey(request, db_session)
        assert exc_info.value.status_code == 401

    def test_api_key_scope_returned(self, db_session, test_user):
        """Verify that the scope stored on the API key is what's returned."""
        from models import ApiKey
        from auth import hash_api_key, get_current_user_or_apikey

        for scope in ("read", "write", "admin"):
            raw_key = f"key-for-{scope}-scope"
            api_key = ApiKey(
                user_id=test_user.id,
                key_hash=hash_api_key(raw_key),
                key_prefix=raw_key[:8],
                label=f"{scope} key",
                scope=scope,
            )
            db_session.add(api_key)
            db_session.commit()

            request = MagicMock()
            request.cookies = {}
            request.headers = {"authorization": f"Bearer {raw_key}"}

            user, returned_scope = get_current_user_or_apikey(request, db_session)
            assert returned_scope == scope
