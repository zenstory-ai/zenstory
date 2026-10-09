"""
Tests for verification service.

Tests verification code generation, storage, and validation logic.
Uses mock for Redis and email client to avoid external dependencies.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.features.verification_service import (
    generate_verification_code,
    get_code_ttl,
    get_remaining_cooldown,
    send_verification_code,
    verify_code,
)


class TestGenerateVerificationCode:
    """Tests for generate_verification_code function."""

    def test_generate_code_default_length(self):
        """Test generating code with default length (6 digits)."""
        code = generate_verification_code()
        assert len(code) == 6
        assert code.isdigit()
        # Each character should be a digit 0-9
        for char in code:
            assert char in "0123456789"

    def test_generate_code_custom_length(self):
        """Test generating code with custom length."""
        for length in [4, 6, 8, 10]:
            code = generate_verification_code(length)
            assert len(code) == length
            assert code.isdigit()

    def test_generate_code_uses_random_choice_for_each_digit(self):
        """Random draws may repeat; every digit must come from secrets.choice."""
        with patch(
            "services.features.verification_service.secrets.choice",
            side_effect=list("001234"),
        ) as choose:
            assert generate_verification_code(6) == "001234"
        assert choose.call_count == 6
        assert all(call.args == ("0123456789",) for call in choose.call_args_list)



class TestSendVerificationCode:
    """Tests for send_verification_code function."""

    @pytest.mark.asyncio
    async def test_send_code_success(self):
        """Test successfully sending verification code."""
        email = "test@example.com"

        with patch(
            "services.features.verification_service.check_resend_cooldown"
        ) as mock_check_cooldown, \
             patch(
            "services.features.verification_service.store_verification_code"
        ) as mock_store, \
             patch(
            "services.features.verification_service.set_resend_cooldown"
        ) as mock_set_cooldown, \
             patch(
            "services.features.verification_service.reset_verification_attempts"
        ) as mock_reset_attempts, \
             patch(
            "services.features.verification_service.send_verification_email",
            new_callable=AsyncMock
        ) as mock_send_email:

            # Setup mocks
            mock_check_cooldown.return_value = False
            mock_store.return_value = True
            mock_set_cooldown.return_value = True
            mock_reset_attempts.return_value = True
            mock_send_email.return_value = True

            # Execute
            success, error = await send_verification_code(email)

            # Verify
            assert success is True
            assert error is None
            mock_check_cooldown.assert_called_once_with(email, 60)
            mock_store.assert_called_once()
            mock_set_cooldown.assert_called_once()
            mock_reset_attempts.assert_called_once()
            mock_send_email.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_code_cooldown_active(self):
        """Test sending code when cooldown is active."""
        email = "test@example.com"

        with patch(
            "services.features.verification_service.check_resend_cooldown"
        ) as mock_check_cooldown, \
             patch(
            "services.features.verification_service.get_remaining_cooldown"
        ) as mock_get_cooldown:

            # Setup mocks
            mock_check_cooldown.return_value = True
            mock_get_cooldown.return_value = 45

            # Execute
            success, error = await send_verification_code(email, language="zh")

            # Verify
            assert success is False
            assert error == "验证码发送过于频繁,请在 45 秒后重试"
            mock_check_cooldown.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_code_storage_failure(self):
        """Test handling of Redis storage failure."""
        email = "test@example.com"

        with patch(
            "services.features.verification_service.check_resend_cooldown"
        ) as mock_check_cooldown, \
             patch(
            "services.features.verification_service.store_verification_code"
        ) as mock_store:

            # Setup mocks
            mock_check_cooldown.return_value = False
            mock_store.return_value = False

            # Execute
            success, error = await send_verification_code(email, language="zh")

            # Verify
            assert success is False
            assert error is not None
            assert "发送失败" in error or "failed" in error

    @pytest.mark.asyncio
    async def test_send_code_email_failure(self):
        """Test handling of email sending failure."""
        email = "test@example.com"

        with patch(
            "services.features.verification_service.check_resend_cooldown"
        ) as mock_check_cooldown, \
             patch(
            "services.features.verification_service.store_verification_code"
        ) as mock_store, \
             patch(
            "services.features.verification_service.set_resend_cooldown"
        ) as mock_set_cooldown, \
             patch(
            "services.features.verification_service.reset_verification_attempts"
        ) as mock_reset_attempts, \
             patch(
            "services.features.verification_service.delete_verification_code"
        ) as mock_delete, \
             patch(
            "services.features.verification_service.send_verification_email",
            new_callable=AsyncMock
        ) as mock_send_email:

            # Setup mocks
            mock_check_cooldown.return_value = False
            mock_store.return_value = True
            mock_set_cooldown.return_value = True
            mock_reset_attempts.return_value = True
            mock_send_email.return_value = False
            mock_delete.return_value = True

            # Execute
            success, error = await send_verification_code(email, language="zh")

            # Verify
            assert success is False
            assert error is not None
            assert "邮件" in error or "email" in error.lower()
            # Verify that code was deleted after email failure
            mock_delete.assert_called_once_with(email)

    @pytest.mark.asyncio
    async def test_send_code_email_failure_clears_resend_cooldown(self):
        """If email delivery fails, users should not be locked out from retrying."""
        email = "delivery-failed@example.com"
        redis_store: dict[str, str] = {}

        class _FakeRedisClient:
            def setex(self, key: str, _ttl: int, value: str):
                redis_store[key] = value

            def delete(self, key: str):
                redis_store.pop(key, None)

            def ttl(self, key: str):
                return 60 if key in redis_store else -2

        with patch("services.infra.redis_client.get_redis_client", return_value=_FakeRedisClient()), \
             patch(
                "services.features.verification_service.send_verification_email",
                new_callable=AsyncMock,
            ) as mock_send_email:
            mock_send_email.return_value = False

            success, error = await send_verification_code(email, language="zh")

            assert success is False
            assert error is not None
            assert f"verification:{email}" not in redis_store
            assert f"resend_cooldown:{email}" not in redis_store

    @pytest.mark.asyncio
    async def test_send_code_english_language(self):
        """Test sending code with English language setting."""
        email = "test@example.com"

        with patch(
            "services.features.verification_service.check_resend_cooldown"
        ) as mock_check_cooldown, \
             patch(
            "services.features.verification_service.store_verification_code"
        ) as mock_store, \
             patch(
            "services.features.verification_service.set_resend_cooldown"
        ) as mock_set_cooldown, \
             patch(
            "services.features.verification_service.reset_verification_attempts"
        ) as mock_reset_attempts, \
             patch(
            "services.features.verification_service.send_verification_email",
            new_callable=AsyncMock
        ) as mock_send_email:

            # Setup mocks
            mock_check_cooldown.return_value = False
            mock_store.return_value = True
            mock_set_cooldown.return_value = True
            mock_reset_attempts.return_value = True
            mock_send_email.return_value = True

            # Execute
            success, error = await send_verification_code(email, language="en")

            # Verify
            assert success is True
            assert error is None
            # Verify email was sent with English language
            mock_send_email.assert_called_once()
            call_args = mock_send_email.call_args
            assert call_args[1]["language"] == "en"


class TestVerifyCode:
    """Tests for verify_code function."""

    @pytest.mark.asyncio
    async def test_verify_code_success(self):
        """Test successful code verification."""
        email = "test@example.com"
        correct_code = "123456"

        with patch(
            "services.features.verification_service.get_verification_attempts"
        ) as mock_get_attempts, \
             patch(
            "services.features.verification_service.get_verification_code"
        ) as mock_get_code, \
             patch(
            "services.features.verification_service.consume_verification_code"
        ) as mock_delete, \
             patch(
            "services.features.verification_service.reset_verification_attempts"
        ) as mock_reset_attempts:

            # Setup mocks
            mock_get_attempts.return_value = 0
            mock_get_code.return_value = correct_code
            mock_delete.return_value = True
            mock_reset_attempts.return_value = True

            # Execute
            success, error = await verify_code(email, correct_code)

            # Verify
            assert success is True
            assert error is None
            mock_delete.assert_called_once_with(email, correct_code)
            mock_reset_attempts.assert_called_once_with(email)

    @pytest.mark.asyncio
    async def test_verify_code_incorrect(self):
        """Test verification with incorrect code."""
        email = "test@example.com"
        stored_code = "123456"
        wrong_code = "654321"

        with patch(
            "services.features.verification_service.get_verification_attempts"
        ) as mock_get_attempts, \
             patch(
            "services.features.verification_service.get_verification_code"
        ) as mock_get_code, \
             patch(
            "services.features.verification_service.increment_verification_attempts"
        ) as mock_increment:

            # Setup mocks
            mock_get_attempts.return_value = 0
            mock_get_code.return_value = stored_code
            mock_increment.return_value = True

            # Execute
            success, error = await verify_code(email, wrong_code, language="zh")

            # Verify
            assert success is False
            assert error is not None
            assert "{" not in error
            assert error.startswith("验证码错误,还剩 ")
            mock_increment.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_code_too_many_attempts(self):
        """Test verification when max attempts reached."""
        email = "test@example.com"

        with patch(
            "services.features.verification_service.get_verification_attempts"
        ) as mock_get_attempts:

            # Setup mocks - already at max attempts
            mock_get_attempts.return_value = 5

            # Execute
            success, error = await verify_code(email, "123456", language="zh")

            # Verify
            assert success is False
            assert error is not None
            assert "次数过多" in error or "too many" in error.lower()

    @pytest.mark.asyncio
    async def test_verify_code_not_exist(self):
        """Test verification when code doesn't exist."""
        email = "test@example.com"

        with patch(
            "services.features.verification_service.get_verification_attempts"
        ) as mock_get_attempts, \
             patch(
            "services.features.verification_service.get_verification_code"
        ) as mock_get_code:

            # Setup mocks
            mock_get_attempts.return_value = 0
            mock_get_code.return_value = None

            # Execute
            success, error = await verify_code(email, "123456", language="zh")

            # Verify
            assert success is False
            assert error is not None
            assert "过期" in error or "不存在" in error

    @pytest.mark.asyncio
    async def test_verify_code_attempts_increment(self):
        """Test that failed verification increments attempts correctly."""
        email = "test@example.com"
        stored_code = "123456"

        with patch(
            "services.features.verification_service.get_verification_attempts"
        ) as mock_get_attempts, \
             patch(
            "services.features.verification_service.get_verification_code"
        ) as mock_get_code, \
             patch(
            "services.features.verification_service.increment_verification_attempts"
        ) as mock_increment:

            # Test multiple failed attempts
            for attempt in range(5):
                mock_get_attempts.return_value = attempt
                mock_get_code.return_value = stored_code
                mock_increment.return_value = True

                success, error = await verify_code(email, "wrong", language="zh")

                assert success is False
                # Verify the error message is returned
                assert error is not None
                assert "验证码错误" in error or "verification code" in error.lower()

    @pytest.mark.asyncio
    async def test_verify_code_english_language(self):
        """Test verification with English error messages."""
        email = "test@example.com"

        with patch(
            "services.features.verification_service.get_verification_attempts"
        ) as mock_get_attempts, \
             patch(
            "services.features.verification_service.get_verification_code"
        ) as mock_get_code:

            # Setup mocks - code doesn't exist
            mock_get_attempts.return_value = 0
            mock_get_code.return_value = None

            # Execute
            success, error = await verify_code(email, "123456", language="en")

            # Verify
            assert success is False
            assert error is not None
            # Should use English error message


class TestGetRemainingCooldown:
    """Tests for get_remaining_cooldown function."""

    def test_get_remaining_cooldown_active(self):
        """Test getting remaining cooldown when active."""
        email = "test@example.com"

        with patch(
            "services.infra.redis_client.get_redis_client"
        ) as mock_get_client:
            # Setup mock
            mock_client = MagicMock()
            mock_client.ttl.return_value = 45
            mock_get_client.return_value = mock_client

            # Execute
            cooldown = get_remaining_cooldown(email)

            # Verify
            assert cooldown == 45
            mock_client.ttl.assert_called_once()

    def test_get_remaining_cooldown_expired(self):
        """Test getting cooldown when expired."""
        email = "test@example.com"

        with patch(
            "services.infra.redis_client.get_redis_client"
        ) as mock_get_client:
            # Setup mock - TTL of -2 means key doesn't exist
            mock_client = MagicMock()
            mock_client.ttl.return_value = -2
            mock_get_client.return_value = mock_client

            # Execute
            cooldown = get_remaining_cooldown(email)

            # Verify
            assert cooldown == 0

    def test_get_remaining_cooldown_no_cooldown(self):
        """Test getting cooldown when none set."""
        email = "test@example.com"

        with patch(
            "services.infra.redis_client.get_redis_client"
        ) as mock_get_client:
            # Setup mock - TTL of -1 means key exists but has no expiry
            mock_client = MagicMock()
            mock_client.ttl.return_value = -1
            mock_get_client.return_value = mock_client

            # Execute
            cooldown = get_remaining_cooldown(email)

            # Verify
            assert cooldown == 0

    def test_get_remaining_cooldown_error(self):
        """Test handling of Redis error."""
        email = "test@example.com"

        with patch(
            "services.infra.redis_client.get_redis_client"
        ) as mock_get_client:
            # Setup mock - raise exception
            mock_client = MagicMock()
            mock_client.ttl.side_effect = Exception("Redis connection error")
            mock_get_client.return_value = mock_client

            # Execute - should handle error gracefully
            cooldown = get_remaining_cooldown(email)

            # Verify
            assert cooldown == 0  # Should return 0 on error


class TestGetCodeTTL:
    """Tests for get_code_ttl function."""

    def test_get_code_ttl_active(self):
        """Test getting code TTL when code exists."""
        email = "test@example.com"

        with patch(
            "services.infra.redis_client.get_redis_client"
        ) as mock_get_client:
            # Setup mock
            mock_client = MagicMock()
            mock_client.ttl.return_value = 180
            mock_get_client.return_value = mock_client

            # Execute
            ttl = get_code_ttl(email)

            # Verify
            assert ttl == 180
            mock_client.ttl.assert_called_once()

    def test_get_code_ttl_expired(self):
        """Test getting TTL when code expired."""
        email = "test@example.com"

        with patch(
            "services.infra.redis_client.get_redis_client"
        ) as mock_get_client:
            # Setup mock - TTL of -2 means key doesn't exist
            mock_client = MagicMock()
            mock_client.ttl.return_value = -2
            mock_get_client.return_value = mock_client

            # Execute
            ttl = get_code_ttl(email)

            # Verify
            assert ttl == 0

    def test_get_code_ttl_no_code(self):
        """Test getting TTL when no code exists."""
        email = "test@example.com"

        with patch(
            "services.infra.redis_client.get_redis_client"
        ) as mock_get_client:
            # Setup mock
            mock_client = MagicMock()
            mock_client.ttl.return_value = -2
            mock_get_client.return_value = mock_client

            # Execute
            ttl = get_code_ttl(email)

            # Verify
            assert ttl == 0

    def test_get_code_ttl_error(self):
        """Test handling of Redis error."""
        email = "test@example.com"

        with patch(
            "services.infra.redis_client.get_redis_client"
        ) as mock_get_client:
            # Setup mock - raise exception
            mock_client = MagicMock()
            mock_client.ttl.side_effect = Exception("Redis connection error")
            mock_get_client.return_value = mock_client

            # Execute - should handle error gracefully
            ttl = get_code_ttl(email)

            # Verify
            assert ttl == 0  # Should return 0 on error
