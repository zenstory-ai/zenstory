"""
Tests for Agent Authentication Service.

Unit tests for apps/server/services/agent_auth_service.py covering:
- generate_api_key() - API key generation
- hash_api_key() - SHA256 hashing
- verify_scope() - Scope validation
- verify_project_access() - Project access validation
- get_agent_user() - FastAPI dependency injection
"""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import status
from sqlmodel import Session

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models.agent_api_key import AgentApiKey
from services.agent_auth_service import (
    API_KEY_LENGTH,
    API_KEY_PREFIX,
    generate_api_key,
    get_agent_user,
    hash_api_key,
    verify_project_access,
    verify_scope,
)


@pytest.mark.unit
class TestGenerateApiKey:
    """Tests for generate_api_key() function."""

    def test_generate_api_key_has_correct_prefix(self):
        """Test that generated key has 'eg_' prefix."""
        key = generate_api_key()
        assert key.startswith(API_KEY_PREFIX), f"Key should start with '{API_KEY_PREFIX}'"

    def test_generate_api_key_correct_length(self):
        """Test that generated key has correct total length (prefix + 64 hex chars)."""
        key = generate_api_key()
        # API_KEY_LENGTH = 32 bytes = 64 hex characters + 3 chars for "eg_"
        expected_length = len(API_KEY_PREFIX) + (API_KEY_LENGTH * 2)
        assert len(key) == expected_length, f"Key should be {expected_length} characters long"

    def test_generate_api_key_hex_characters_only(self):
        """Test that key suffix contains only hexadecimal characters."""
        key = generate_api_key()
        suffix = key[len(API_KEY_PREFIX):]
        assert all(c in "0123456789abcdef" for c in suffix), "Key suffix should be lowercase hex"

    def test_generate_api_key_uniqueness(self):
        """Test that multiple calls generate unique keys."""
        keys = [generate_api_key() for _ in range(100)]
        unique_keys = set(keys)
        assert len(unique_keys) == 100, "All generated keys should be unique"

    def test_generate_api_key_format(self):
        """Test that key matches expected format: eg_ followed by 64 hex chars."""
        key = generate_api_key()
        import re
        pattern = r"^eg_[0-9a-f]{64}$"
        assert re.match(pattern, key), f"Key '{key}' does not match expected format"


@pytest.mark.unit
class TestHashApiKey:
    """Tests for hash_api_key() function."""

    def test_hash_api_key_returns_sha256_hash(self):
        """Test that hash_api_key returns a valid SHA256 hash."""
        key = "eg_test123456789"
        hash_result = hash_api_key(key)

        # SHA256 produces 64 character hex string
        assert len(hash_result) == 64, "SHA256 hash should be 64 characters"
        assert all(c in "0123456789abcdef" for c in hash_result), "Hash should be lowercase hex"

    def test_hash_api_key_consistent(self):
        """Test that same input produces same hash."""
        key = "eg_test123456789"
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)
        assert hash1 == hash2, "Same key should produce same hash"

    def test_hash_api_key_different_inputs_different_hashes(self):
        """Test that different inputs produce different hashes."""
        key1 = "eg_test123456789"
        key2 = "eg_test123456790"
        hash1 = hash_api_key(key1)
        hash2 = hash_api_key(key2)
        assert hash1 != hash2, "Different keys should produce different hashes"

    def test_hash_api_key_matches_standard_sha256(self):
        """Test that hash_api_key matches standard SHA256 implementation."""
        key = "eg_test123456789"
        expected_hash = hashlib.sha256(key.encode()).hexdigest()
        actual_hash = hash_api_key(key)
        assert actual_hash == expected_hash, "Hash should match standard SHA256"

    def test_hash_api_key_empty_string(self):
        """Test hashing an empty string."""
        hash_result = hash_api_key("")
        assert len(hash_result) == 64, "Empty string should still produce 64 char hash"

    def test_hash_api_key_unicode(self):
        """Test hashing a string with unicode characters."""
        key = "eg_unicode_\u4e2d\u6587"
        hash_result = hash_api_key(key)
        assert len(hash_result) == 64, "Unicode key should produce valid hash"


@pytest.mark.unit
class TestVerifyScope:
    """Tests for verify_scope() function."""

    def test_verify_scope_has_scope(self):
        """Test verification when key has the required scope."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read", "write", "chat"],
            is_active=True,
        )
        assert verify_scope(api_key, "read") is True
        assert verify_scope(api_key, "write") is True
        assert verify_scope(api_key, "chat") is True

    def test_verify_scope_missing_scope(self):
        """Test verification when key lacks the required scope."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read"],
            is_active=True,
        )
        assert verify_scope(api_key, "write") is False
        assert verify_scope(api_key, "chat") is False
        assert verify_scope(api_key, "admin") is False

    def test_verify_scope_inactive_key(self):
        """Test that inactive keys always fail scope verification."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read", "write"],
            is_active=False,
        )
        assert verify_scope(api_key, "read") is False
        assert verify_scope(api_key, "write") is False

    def test_verify_scope_none_scopes(self):
        """Test verification when scopes is None."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=None,
            is_active=True,
        )
        assert verify_scope(api_key, "read") is False

    def test_verify_scope_empty_scopes(self):
        """Test verification when scopes is empty list."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=[],
            is_active=True,
        )
        assert verify_scope(api_key, "read") is False

    def test_verify_scope_case_sensitive(self):
        """Test that scope verification is case-sensitive."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read"],
            is_active=True,
        )
        assert verify_scope(api_key, "READ") is False
        assert verify_scope(api_key, "Read") is False


@pytest.mark.unit
class TestVerifyProjectAccess:
    """Tests for verify_project_access() function."""

    def test_verify_project_access_none_project_ids_allows_all(self):
        """Test that None project_ids allows access to all projects."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read"],
            project_ids=None,
            is_active=True,
        )
        assert verify_project_access(api_key, "project-1") is True
        assert verify_project_access(api_key, "project-2") is True
        assert verify_project_access(api_key, "any-project") is True

    def test_verify_project_access_specific_projects(self):
        """Test access with specific project restrictions."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read"],
            project_ids=["project-1", "project-2"],
            is_active=True,
        )
        assert verify_project_access(api_key, "project-1") is True
        assert verify_project_access(api_key, "project-2") is True
        assert verify_project_access(api_key, "project-3") is False

    def test_verify_project_access_inactive_key(self):
        """Test that inactive keys always fail project access verification."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read"],
            project_ids=["project-1"],
            is_active=False,
        )
        assert verify_project_access(api_key, "project-1") is False

    def test_verify_project_access_none_project_id_param(self):
        """Test that None project_id parameter returns True."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read"],
            project_ids=["project-1"],
            is_active=True,
        )
        assert verify_project_access(api_key, None) is True

    def test_verify_project_access_empty_project_ids_list(self):
        """Test that empty project_ids list denies all access."""
        api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix="eg_abc",
            key_hash="hash",
            name="Test Key",
            scopes=["read"],
            project_ids=[],
            is_active=True,
        )
        assert verify_project_access(api_key, "project-1") is False


@pytest.mark.unit
class TestGetAgentUser:
    """Tests for get_agent_user() dependency."""

    def test_get_agent_user_missing_header(self):
        """Test that missing API key header raises unauthorized."""
        mock_session = MagicMock(spec=Session)

        with pytest.raises(APIException) as exc_info:
            get_agent_user(x_agent_api_key=None, session=mock_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_UNAUTHORIZED
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Missing" in exc_info.value.detail

    def test_get_agent_user_invalid_format(self):
        """Test that invalid API key format raises error."""
        mock_session = MagicMock(spec=Session)

        with pytest.raises(APIException) as exc_info:
            get_agent_user(x_agent_api_key="invalid_key", session=mock_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_TOKEN_INVALID
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "format" in exc_info.value.detail.lower()

    def test_get_agent_user_not_found_in_db(self):
        """Test that key not found in database raises error."""
        mock_session = MagicMock(spec=Session)
        mock_session.exec.return_value.first.return_value = None

        valid_key = generate_api_key()

        with pytest.raises(APIException) as exc_info:
            get_agent_user(x_agent_api_key=valid_key, session=mock_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_TOKEN_INVALID
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_agent_user_inactive_key(self):
        """Test that inactive key raises forbidden error."""
        mock_session = MagicMock(spec=Session)

        valid_key = generate_api_key()
        key_hash = hash_api_key(valid_key)

        inactive_api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix=valid_key[:8],
            key_hash=key_hash,
            name="Inactive Key",
            scopes=["read"],
            is_active=False,
        )

        mock_session.exec.return_value.first.return_value = inactive_api_key

        with pytest.raises(APIException) as exc_info:
            get_agent_user(x_agent_api_key=valid_key, session=mock_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_INACTIVE_USER
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "inactive" in exc_info.value.detail.lower()

    def test_get_agent_user_expired_key(self):
        """Test that expired key raises unauthorized error."""
        mock_session = MagicMock(spec=Session)

        valid_key = generate_api_key()
        key_hash = hash_api_key(valid_key)

        expired_api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix=valid_key[:8],
            key_hash=key_hash,
            name="Expired Key",
            scopes=["read"],
            is_active=True,
            expires_at=utcnow() - timedelta(days=1),
        )

        mock_session.exec.return_value.first.return_value = expired_api_key

        with pytest.raises(APIException) as exc_info:
            get_agent_user(x_agent_api_key=valid_key, session=mock_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_TOKEN_EXPIRED
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "expired" in exc_info.value.detail.lower()

    def test_get_agent_user_rejects_key_at_exact_expiry_boundary(self, monkeypatch):
        """A key is no longer usable at the instant named by expires_at."""
        from services import agent_auth_service

        mock_session = MagicMock(spec=Session)
        valid_key = generate_api_key()
        boundary = datetime(2026, 10, 6, 12, tzinfo=UTC)
        boundary_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix=valid_key[:8],
            key_hash=hash_api_key(valid_key),
            name="Boundary Key",
            scopes=["read"],
            is_active=True,
            expires_at=boundary,
        )
        mock_session.exec.return_value.first.return_value = boundary_key
        monkeypatch.setattr(agent_auth_service, "utcnow", lambda: boundary)

        with pytest.raises(APIException) as exc_info:
            get_agent_user(x_agent_api_key=valid_key, session=mock_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_TOKEN_EXPIRED
        mock_session.commit.assert_not_called()

    def test_get_agent_user_success(self):
        """Test successful API key validation."""
        mock_session = MagicMock(spec=Session)

        valid_key = generate_api_key()
        key_hash = hash_api_key(valid_key)

        active_api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix=valid_key[:8],
            key_hash=key_hash,
            name="Active Key",
            scopes=["read", "write"],
            is_active=True,
            request_count=0,
        )

        mock_session.exec.return_value.first.return_value = active_api_key

        result_session, result_user_id, result_key = get_agent_user(
            x_agent_api_key=valid_key, session=mock_session
        )

        assert result_session == mock_session
        assert result_user_id == "user-1"
        assert result_key == active_api_key

        # Verify that the atomic usage update was persisted and reloaded.
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once_with(active_api_key)

    def test_get_agent_user_updates_last_used_at(self):
        """Test that successful validation updates last_used_at timestamp."""
        mock_session = MagicMock(spec=Session)

        valid_key = generate_api_key()
        key_hash = hash_api_key(valid_key)

        active_api_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix=valid_key[:8],
            key_hash=key_hash,
            name="Active Key",
            scopes=["read"],
            is_active=True,
            last_used_at=None,
            request_count=0,
        )

        mock_session.exec.return_value.first.return_value = active_api_key

        get_agent_user(x_agent_api_key=valid_key, session=mock_session)

        statement = mock_session.exec.call_args_list[-1].args[0]
        compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
        assert "request_count=(agent_api_key.request_count + 1)" in compiled
        assert "last_used_at=" in compiled

    def test_get_agent_user_key_not_expiring_soon(self):
        """Test key that expires in the future works correctly."""
        mock_session = MagicMock(spec=Session)

        valid_key = generate_api_key()
        key_hash = hash_api_key(valid_key)

        future_expiry_key = AgentApiKey(
            id="test-id",
            user_id="user-1",
            key_prefix=valid_key[:8],
            key_hash=key_hash,
            name="Future Expiry Key",
            scopes=["read"],
            is_active=True,
            expires_at=utcnow() + timedelta(days=30),
            request_count=0,
        )

        mock_session.exec.return_value.first.return_value = future_expiry_key

        result_session, result_user_id, result_key = get_agent_user(
            x_agent_api_key=valid_key, session=mock_session
        )

        assert result_key == future_expiry_key


@pytest.mark.unit
class TestApiKeyConstants:
    """Tests for API key constants."""

    def test_api_key_prefix_value(self):
        """Test that API_KEY_PREFIX is 'eg_'."""
        assert API_KEY_PREFIX == "eg_"

    def test_api_key_length_value(self):
        """Test that API_KEY_LENGTH is 32 (producing 64 hex chars)."""
        assert API_KEY_LENGTH == 32
