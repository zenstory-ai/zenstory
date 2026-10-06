"""
Tests for AuthService.

Unit tests for the authentication service, covering:
- Password hashing and verification
- Access token creation
- Refresh token creation
- Token verification
- get_current_user dependency
- get_current_active_user dependency
- get_optional_current_user dependency
- get_current_superuser dependency
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status
from jose import jwt
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from services.core.auth_service import (
    ALGORITHM,
    _resolve_runtime_environment,
    create_access_token,
    create_refresh_token,
    generate_token_jti,
    get_current_active_user,
    get_current_superuser,
    get_current_user,
    get_optional_current_user,
    get_refresh_token_expires_at,
    hash_password,
    validate_auth_runtime_configuration,
    verify_password,
    verify_token,
)


@pytest.mark.unit
class TestHashPassword:
    """Tests for hash_password function."""

    def test_hash_password_returns_string(self):
        """Test that hash_password returns a string."""
        password = "test_password_123"
        hashed = hash_password(password)

        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_password_is_bcrypt(self):
        """Test that hash_password produces a valid bcrypt hash."""
        password = "test_password_123"
        hashed = hash_password(password)

        # bcrypt hashes start with $2b$
        assert hashed.startswith("$2b$")

    def test_hash_password_different_each_time(self):
        """Test that same password produces different hashes (bcrypt salt)."""
        password = "test_password_123"
        hash1 = hash_password(password)
        hash2 = hash_password(password)

        # Different salts should produce different hashes
        assert hash1 != hash2

    def test_hash_password_long_password(self):
        """Test that passwords longer than 72 bytes are truncated."""
        # bcrypt has a 72 byte limit
        long_password = "x" * 100
        hashed = hash_password(long_password)

        assert isinstance(hashed, str)
        assert hashed.startswith("$2b$")

    def test_hash_password_unicode(self):
        """Test hashing password with unicode characters."""
        password = "密码测试123"
        hashed = hash_password(password)

        assert isinstance(hashed, str)
        assert hashed.startswith("$2b$")

    def test_hash_password_empty_string(self):
        """Test hashing empty string."""
        hashed = hash_password("")

        assert isinstance(hashed, str)
        assert hashed.startswith("$2b$")


@pytest.mark.unit
class TestVerifyPassword:
    """Tests for verify_password function."""

    def test_verify_password_correct(self):
        """Test that verify_password returns True for correct password."""
        password = "test_password_123"
        hashed = hash_password(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Test that verify_password returns False for incorrect password."""
        password = "test_password_123"
        hashed = hash_password(password)

        assert verify_password("wrong_password", hashed) is False

    def test_verify_password_empty(self):
        """Test verification with empty password."""
        hashed = hash_password("real_password")

        assert verify_password("", hashed) is False

    def test_verify_password_case_sensitive(self):
        """Test that password verification is case-sensitive."""
        password = "TestPassword123"
        hashed = hash_password(password)

        assert verify_password("testpassword123", hashed) is False
        assert verify_password("TestPassword123", hashed) is True

    def test_verify_password_unicode(self):
        """Test verification with unicode password."""
        password = "密码测试123"
        hashed = hash_password(password)

        assert verify_password(password, hashed) is True
        assert verify_password("密码测试124", hashed) is False


@pytest.mark.unit
class TestCreateAccessToken:
    """Tests for create_access_token function."""

    def test_create_access_token_returns_string(self):
        """Test that create_access_token returns a string."""
        data = {"sub": "user123"}
        token = create_access_token(data)

        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_access_token_is_valid_jwt(self):
        """Test that created token is a valid JWT."""
        data = {"sub": "user123"}
        token = create_access_token(data)

        # JWT has 3 parts separated by dots
        parts = token.split(".")
        assert len(parts) == 3

    def test_create_access_token_contains_data(self):
        """Test that token contains the provided data."""
        data = {"sub": "user123", "role": "admin"}
        token = create_access_token(data)

        # Decode and verify
        from services.core.auth_service import SECRET_KEY
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        assert payload["sub"] == "user123"
        assert payload["role"] == "admin"

    def test_create_access_token_has_expiration(self):
        """Test that token has an expiration claim."""
        data = {"sub": "user123"}
        token = create_access_token(data)

        from services.core.auth_service import SECRET_KEY
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        assert "exp" in payload

    def test_create_access_token_expiration_time(self):
        """Test that token expiration is in the future and approximately correct."""
        data = {"sub": "user123"}
        now = datetime.now(UTC)
        token = create_access_token(data)

        from services.core.auth_service import SECRET_KEY
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        # JWT NumericDate is UTC; decoding it in the host's local timezone makes
        # this assertion fail on every non-UTC developer machine.
        exp_time = datetime.fromtimestamp(payload["exp"], tz=UTC)

        # Check expiration is in the future
        assert exp_time > now

        # Check expiration is within reasonable range (1 minute to 24 hours from now)
        # This test just verifies the expiration is set and reasonable
        min_exp = now + timedelta(minutes=1)
        max_exp = now + timedelta(hours=24)

        assert min_exp < exp_time < max_exp

    def test_create_access_token_converts_sub_to_string(self):
        """Test that 'sub' claim is converted to string."""
        data = {"sub": 12345}  # Integer instead of string
        token = create_access_token(data)

        from services.core.auth_service import SECRET_KEY
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        assert payload["sub"] == "12345"
        assert isinstance(payload["sub"], str)

    def test_create_access_token_preserves_original_data(self):
        """Test that original data dict is not modified."""
        data = {"sub": "user123"}
        original_data = data.copy()

        create_access_token(data)

        assert data == original_data


@pytest.mark.unit
class TestCreateRefreshToken:
    """Tests for create_refresh_token function."""

    def test_create_refresh_token_returns_string(self):
        """Test that create_refresh_token returns a string."""
        data = {"sub": "user123"}
        token = create_refresh_token(data)

        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_refresh_token_is_valid_jwt(self):
        """Test that created token is a valid JWT."""
        data = {"sub": "user123"}
        token = create_refresh_token(data)

        parts = token.split(".")
        assert len(parts) == 3

    def test_create_refresh_token_has_expiration(self):
        """Test that refresh token has an expiration claim."""
        data = {"sub": "user123"}
        token = create_refresh_token(data)

        from services.core.auth_service import SECRET_KEY
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        assert "exp" in payload

    def test_create_refresh_token_longer_expiry(self):
        """Test that refresh token has longer expiry than access token."""
        data = {"sub": "user123"}
        access_token = create_access_token(data)
        refresh_token = create_refresh_token(data)

        from services.core.auth_service import SECRET_KEY
        access_payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        refresh_payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])

        assert refresh_payload["exp"] > access_payload["exp"]

    def test_create_refresh_token_expiration_time(self):
        """Test that refresh token expiration is in the future and longer than access token."""
        data = {"sub": "user123"}
        now = datetime.utcnow()
        access_token = create_access_token(data)
        refresh_token = create_refresh_token(data)

        from services.core.auth_service import SECRET_KEY
        access_payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        refresh_payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])

        access_exp = datetime.fromtimestamp(access_payload["exp"])
        refresh_exp = datetime.fromtimestamp(refresh_payload["exp"])

        # Check expiration is in the future
        assert refresh_exp > now

        # Refresh token should expire later than access token
        assert refresh_exp > access_exp

        # Refresh token should expire within reasonable range (1 day to 30 days)
        min_exp = now + timedelta(days=1)
        max_exp = now + timedelta(days=30)

        assert min_exp < refresh_exp < max_exp

    def test_create_refresh_token_contains_type_and_jti_when_provided(self):
        """Refresh token should preserve jti/family_id claims for rotation."""
        data = {"sub": "user123", "jti": "jti-1", "family_id": "fam-1"}
        token = create_refresh_token(data)

        from services.core.auth_service import SECRET_KEY
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        assert payload["typ"] == "refresh"
        assert payload["jti"] == "jti-1"
        assert payload["family_id"] == "fam-1"


@pytest.mark.unit
class TestTokenHelpers:
    """Tests for token helper utilities."""

    def test_generate_token_jti_returns_unique_ids(self):
        jti1 = generate_token_jti()
        jti2 = generate_token_jti()
        assert jti1 != jti2
        assert isinstance(jti1, str)
        assert len(jti1) >= 16

    def test_get_refresh_token_expires_at_in_future(self):
        expires_at = get_refresh_token_expires_at()
        assert expires_at > datetime.utcnow().astimezone(expires_at.tzinfo)


@pytest.mark.unit
class TestAuthRuntimeConfiguration:
    """Tests for auth runtime configuration checks."""

    def test_validate_auth_runtime_configuration_non_strict_env(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("JWT_SECRET_KEY", "short")
        monkeypatch.setenv("ALLOW_LEGACY_UNTYPED_TOKENS", "true")
        validate_auth_runtime_configuration()

    def test_validate_auth_runtime_configuration_strict_requires_secret(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET_KEY", "short")
        monkeypatch.setenv("ALLOW_LEGACY_UNTYPED_TOKENS", "false")

        with pytest.raises(RuntimeError):
            validate_auth_runtime_configuration()

    def test_validate_auth_runtime_configuration_strict_rejects_legacy_untyped(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
        monkeypatch.setenv("ALLOW_LEGACY_UNTYPED_TOKENS", "true")

        with pytest.raises(RuntimeError):
            validate_auth_runtime_configuration()

    def test_resolve_runtime_environment_prefers_environment(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        assert _resolve_runtime_environment() == "staging"

    def test_resolve_runtime_environment_uses_railway_when_environment_missing(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        assert _resolve_runtime_environment() == "production"

    def test_validate_auth_runtime_configuration_strict_when_railway_production(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET_KEY", "short")
        monkeypatch.setenv("ALLOW_LEGACY_UNTYPED_TOKENS", "false")

        with pytest.raises(RuntimeError):
            validate_auth_runtime_configuration()


@pytest.mark.unit
class TestVerifyToken:
    """Tests for verify_token function."""

    def test_verify_token_valid(self):
        """Test verification of a valid token."""
        data = {"sub": "user123"}
        token = create_access_token(data)

        payload = verify_token(token)

        assert payload is not None
        assert payload["sub"] == "user123"

    def test_verify_token_invalid_format(self):
        """Test verification of an invalid token format."""
        result = verify_token("invalid.token.format")

        assert result is None

    def test_verify_token_expired(self):
        """Test verification of an expired token."""
        # Create an expired token manually
        from services.core.auth_service import SECRET_KEY
        expired_data = {
            "sub": "user123",
            "exp": datetime.utcnow() - timedelta(hours=1)
        }
        expired_token = jwt.encode(expired_data, SECRET_KEY, algorithm=ALGORITHM)

        result = verify_token(expired_token)

        assert result is None

    def test_verify_token_wrong_secret(self):
        """Test verification of token signed with wrong secret."""
        # Create token with different secret
        wrong_token = jwt.encode({"sub": "user123"}, "wrong_secret", algorithm=ALGORITHM)

        result = verify_token(wrong_token)

        assert result is None

    def test_verify_token_empty_string(self):
        """Test verification of empty string."""
        result = verify_token("")

        assert result is None

    def test_verify_token_none(self):
        """Test verification of None input."""
        # verify_token expects a string, so None should raise AttributeError
        # or return None if there's a try/except block
        try:
            result = verify_token(None)
            # If it doesn't raise, it should return None
            assert result is None
        except (AttributeError, TypeError):
            # This is also acceptable - function doesn't handle None
            pass


@pytest.mark.unit
class TestGetCurrentUser:
    """Tests for get_current_user dependency."""

    @pytest.mark.asyncio
    async def test_get_current_user_success(self, db_session: Session):
        """Test successful user retrieval from valid token."""
        # Create user
        user = User(
            id="user-123",
            email="test@example.com",
            username="testuser",
            hashed_password="hashed",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        # Create token
        token = create_access_token({"sub": user.id})

        # Get current user
        result_user = get_current_user(token=token, session=db_session)

        assert result_user.id == user.id
        assert result_user.email == user.email

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self, db_session: Session):
        """Test that invalid token raises APIException."""
        with pytest.raises(APIException) as exc_info:
            get_current_user(token="invalid_token", session=db_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_TOKEN_INVALID
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_get_current_user_no_sub_claim(self, db_session: Session):
        """Test that token without sub claim raises APIException."""
        # Create token without sub claim
        from services.core.auth_service import SECRET_KEY
        token = jwt.encode({"role": "admin"}, SECRET_KEY, algorithm=ALGORITHM)

        with pytest.raises(APIException) as exc_info:
            get_current_user(token=token, session=db_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_TOKEN_INVALID
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_get_current_user_user_not_found(self, db_session: Session):
        """Test that non-existent user raises APIException."""
        # Create token for non-existent user
        token = create_access_token({"sub": "nonexistent-user-id"})

        with pytest.raises(APIException) as exc_info:
            get_current_user(token=token, session=db_session)

        assert exc_info.value.error_code == ErrorCode.AUTH_TOKEN_INVALID
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.unit
class TestGetCurrentActiveUser:
    """Tests for get_current_active_user dependency."""

    @pytest.mark.asyncio
    async def test_get_current_active_user_active(self, db_session: Session):
        """Test that active user is returned."""
        user = User(
            id="user-123",
            email="test@example.com",
            username="testuser",
            hashed_password="hashed",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        result = await get_current_active_user(current_user=user)

        assert result.id == user.id
        assert result.is_active is True

    @pytest.mark.asyncio
    async def test_get_current_active_user_inactive(self, db_session: Session):
        """Test that inactive user raises APIException."""
        user = User(
            id="user-123",
            email="test@example.com",
            username="testuser",
            hashed_password="hashed",
            is_active=False,
        )

        with pytest.raises(APIException) as exc_info:
            await get_current_active_user(current_user=user)

        assert exc_info.value.error_code == ErrorCode.AUTH_INACTIVE_USER
        assert exc_info.value.status_code == 400


@pytest.mark.unit
class TestGetOptionalCurrentUser:
    """Tests for get_optional_current_user dependency."""

    @pytest.mark.asyncio
    async def test_get_optional_current_user_no_token(self, db_session: Session):
        """Test that None token returns None user."""
        result = await get_optional_current_user(token=None, session=db_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_optional_current_user_invalid_token(self, db_session: Session):
        """Test that invalid token returns None user."""
        result = await get_optional_current_user(token="invalid_token", session=db_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_optional_current_user_valid_token(self, db_session: Session):
        """Test that valid token returns the user."""
        user = User(
            id="user-123",
            email="test@example.com",
            username="testuser",
            hashed_password="hashed",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        token = create_access_token({"sub": user.id})

        result = await get_optional_current_user(token=token, session=db_session)

        assert result is not None
        assert result.id == user.id

    @pytest.mark.asyncio
    async def test_get_optional_current_user_user_not_found(self, db_session: Session):
        """Test that non-existent user returns None."""
        token = create_access_token({"sub": "nonexistent-user-id"})

        result = await get_optional_current_user(token=token, session=db_session)

        assert result is None


@pytest.mark.unit
class TestGetCurrentSuperuser:
    """Tests for get_current_superuser dependency."""

    @pytest.mark.asyncio
    async def test_get_current_superuser_success(self, db_session: Session):
        """Test that superuser is returned."""
        user = User(
            id="user-123",
            email="admin@example.com",
            username="adminuser",
            hashed_password="hashed",
            is_active=True,
            is_superuser=True,
        )

        result = await get_current_superuser(current_user=user)

        assert result.is_superuser is True

    @pytest.mark.asyncio
    async def test_get_current_superuser_not_superuser(self, db_session: Session):
        """Test that non-superuser raises APIException."""
        user = User(
            id="user-123",
            email="test@example.com",
            username="testuser",
            hashed_password="hashed",
            is_active=True,
            is_superuser=False,
        )

        with pytest.raises(APIException) as exc_info:
            await get_current_superuser(current_user=user)

        assert exc_info.value.error_code == ErrorCode.NOT_AUTHORIZED
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
