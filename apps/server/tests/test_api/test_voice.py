"""
Tests for Voice Recognition API.

Tests voice recognition endpoints using Tencent Cloud ASR:
- POST /api/v1/voice/recognize
- GET /api/v1/voice/status
"""

import base64
import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from api.voice import MAX_VOICE_REQUEST_BYTES
from models import User
from services.core.auth_service import hash_password


# Helper function to create a verified user and get auth token
async def create_verified_user_and_get_token(
    client: AsyncClient, db_session, username: str = "testuser"
) -> str:
    """Create a verified user and return access token."""
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Login to get token
    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": username,
            "password": "testpassword123",
        },
    )
    assert login_response.status_code == 200
    return login_response.json()["access_token"]


# ============================================
# Voice Status Tests
# ============================================


@pytest.mark.integration
async def test_voice_status_not_configured(client: AsyncClient):
    """Test voice status endpoint when credentials are not configured."""
    # Ensure no credentials are set
    with patch.dict(os.environ, {}, clear=True):
        # Remove TENCENT credentials if they exist
        env_copy = dict(os.environ)
        env_copy.pop("TENCENT_SECRET_ID", None)
        env_copy.pop("TENCENT_SECRET_KEY", None)

        with patch.dict(os.environ, env_copy, clear=True):
            response = await client.get("/api/v1/voice/status")

            assert response.status_code == 200
            data = response.json()
            assert data["configured"] is False
            assert data["provider"] == "tencent"
            assert "supported_formats" in data
            assert "wav" in data["supported_formats"]


@pytest.mark.integration
async def test_voice_status_configured(client: AsyncClient):
    """Test voice status endpoint when credentials are configured."""
    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        response = await client.get("/api/v1/voice/status")

        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is True
        assert data["provider"] == "tencent"
        assert data["service"] == "一句话识别"
        assert data["max_duration_seconds"] == 60
        assert "wav" in data["supported_formats"]
        assert "mp3" in data["supported_formats"]
        assert "ogg-opus" in data["supported_formats"]
        assert "webm" not in data["supported_formats"]


# ============================================
# Voice Recognize Tests - Validation
# ============================================


@pytest.mark.integration
async def test_recognize_missing_audio_data(client: AsyncClient, db_session):
    """Test voice recognize with missing audio_data returns 422."""
    token = await create_verified_user_and_get_token(client, db_session)

    response = await client.post(
        "/api/v1/voice/recognize",
        json={
            "audio_format": "wav",
            "sample_rate": 16000,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_recognize_requires_authentication_before_provider(client: AsyncClient):
    """Anonymous callers must not be able to trigger the paid ASR provider."""
    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as mock_asr:
        response = await client.post(
            "/api/v1/voice/recognize",
            json={"audio_data": base64.b64encode(b"audio").decode("ascii")},
        )

    assert response.status_code == 401
    mock_asr.assert_not_awaited()


@pytest.mark.integration
async def test_recognize_rejects_oversized_http_body_before_provider(client: AsyncClient):
    """The route-level size guard runs before parsing a clearly oversized body."""
    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as mock_asr:
        response = await client.post(
            "/api/v1/voice/recognize",
            content=b'{}',
            headers={"content-type": "application/json", "content-length": "8000000"},
        )

    assert response.status_code == 413
    mock_asr.assert_not_awaited()


@pytest.mark.integration
@pytest.mark.parametrize("declared_length", [None, "2"])
async def test_recognize_caps_streamed_body_without_trusting_content_length(
    client: AsyncClient, declared_length
):
    async def oversized_stream():
        yield b'{"audio_data":"'
        chunk = b"A" * (MAX_VOICE_REQUEST_BYTES // 2)
        yield chunk
        yield chunk
        yield b'AA"}'

    headers = {"content-type": "application/json"}
    if declared_length is not None:
        headers["content-length"] = declared_length

    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as mock_asr:
        response = await client.post(
            "/api/v1/voice/recognize",
            content=oversized_stream(),
            headers=headers,
        )

    assert response.status_code == 413
    mock_asr.assert_not_awaited()


@pytest.mark.integration
async def test_recognize_invalid_sample_rate(client: AsyncClient, db_session):
    """Test voice recognize with invalid sample rate."""
    token = await create_verified_user_and_get_token(client, db_session)

    # Create minimal valid base64 audio data
    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    response = await client.post(
        "/api/v1/voice/recognize",
        json={
            "audio_data": audio_data,
            "audio_format": "wav",
            "sample_rate": 44100,  # Invalid - should be 8000 or 16000
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_recognize_invalid_audio_format(client: AsyncClient, db_session):
    """Test voice recognize with unsupported audio format."""
    token = await create_verified_user_and_get_token(client, db_session)

    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    response = await client.post(
        "/api/v1/voice/recognize",
        json={
            "audio_data": audio_data,
            "audio_format": "xyz",  # Invalid format
            "sample_rate": 16000,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


# ============================================
# Voice Recognize Tests - Credentials Error
# ============================================


@pytest.mark.integration
async def test_recognize_credentials_not_configured(client: AsyncClient, db_session):
    """Test voice recognize when credentials are not configured returns 500."""
    token = await create_verified_user_and_get_token(client, db_session)

    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    # Clear credentials
    env_copy = dict(os.environ)
    env_copy.pop("TENCENT_SECRET_ID", None)
    env_copy.pop("TENCENT_SECRET_KEY", None)

    with patch.dict(os.environ, env_copy, clear=True):
        response = await client.post(
            "/api/v1/voice/recognize",
            json={
                "audio_data": audio_data,
                "audio_format": "wav",
                "sample_rate": 16000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 500
        data = response.json()
        assert "ERR_VOICE_CREDENTIALS_NOT_CONFIGURED" in str(data)


# ============================================
# Voice Recognize Tests - Audio Decode Error
# ============================================


@pytest.mark.integration
async def test_recognize_invalid_base64_audio(client: AsyncClient, db_session):
    """Test voice recognize with invalid base64 audio data."""
    token = await create_verified_user_and_get_token(client, db_session)

    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        response = await client.post(
            "/api/v1/voice/recognize",
            json={
                "audio_data": "not-valid-base64!!!",  # Invalid base64
                "audio_format": "wav",
                "sample_rate": 16000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422
        data = response.json()
        assert "ERR_VOICE_AUDIO_DECODE_FAILED" in str(data)


@pytest.mark.integration
async def test_recognize_rejects_audio_over_encoded_limit_before_provider(
    client: AsyncClient, db_session
):
    token = await create_verified_user_and_get_token(client, db_session)
    oversized_audio = base64.b64encode(b"x" * (2_250_000 + 1)).decode("ascii")

    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as mock_asr:
        response = await client.post(
            "/api/v1/voice/recognize",
            json={"audio_data": oversized_audio},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 413
    mock_asr.assert_not_awaited()


# ============================================
# Voice Recognize Tests - Mocked ASR Success
# ============================================


@pytest.mark.integration
async def test_recognize_success_mocked(client: AsyncClient, db_session):
    """Test successful voice recognition with mocked Tencent ASR."""
    token = await create_verified_user_and_get_token(client, db_session)

    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    # Mock the Tencent ASR API call
    mock_response = {
        "Response": {
            "Result": "你好世界",
            "AudioDuration": 2500,
            "RequestId": "test-request-id",
        }
    }

    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        with patch(
            "api.voice.call_tencent_asr", new_callable=AsyncMock
        ) as mock_asr:
            mock_asr.return_value = mock_response

            response = await client.post(
                "/api/v1/voice/recognize",
                json={
                    "audio_data": audio_data,
                    "audio_format": "wav",
                    "sample_rate": 16000,
                    "language": "zh",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["text"] == "你好世界"
            assert data["duration_ms"] == 2500
            assert data["error"] is None


@pytest.mark.integration
async def test_recognize_with_various_languages_mocked(client: AsyncClient, db_session):
    """Test voice recognition with different language settings."""
    token = await create_verified_user_and_get_token(client, db_session)

    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    mock_response = {
        "Response": {
            "Result": "Hello world",
            "AudioDuration": 1500,
            "RequestId": "test-request-id",
        }
    }

    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        with patch(
            "api.voice.call_tencent_asr", new_callable=AsyncMock
        ) as mock_asr:
            mock_asr.return_value = mock_response

            # Test with English
            response = await client.post(
                "/api/v1/voice/recognize",
                json={
                    "audio_data": audio_data,
                    "audio_format": "mp3",
                    "sample_rate": 16000,
                    "language": "en",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["text"] == "Hello world"

            # Verify the call was made with correct language
            mock_asr.assert_called_once()
            call_kwargs = mock_asr.call_args[1]
            assert call_kwargs["language"] == "en"
            assert call_kwargs["audio_format"] == "mp3"


@pytest.mark.integration
async def test_recognize_with_language_variant_mocked(client: AsyncClient, db_session):
    """Test voice recognition with language variants like zh-CN and en-US."""
    token = await create_verified_user_and_get_token(client, db_session)

    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    mock_response = {
        "Response": {
            "Result": "测试文本",
            "AudioDuration": 1000,
            "RequestId": "test-request-id",
        }
    }

    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        with patch(
            "api.voice.call_tencent_asr", new_callable=AsyncMock
        ) as mock_asr:
            mock_asr.return_value = mock_response

            # Test with zh-CN (should be normalized to zh)
            response = await client.post(
                "/api/v1/voice/recognize",
                json={
                    "audio_data": audio_data,
                    "audio_format": "wav",
                    "sample_rate": 8000,  # Test 8k sample rate
                    "language": "zh-CN",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

            # Verify the call was made
            mock_asr.assert_called_once()


# ============================================
# Voice Recognize Tests - ASR Error Responses
# ============================================


@pytest.mark.integration
async def test_recognize_asr_returns_error(client: AsyncClient, db_session):
    """Test voice recognition when ASR service returns an error."""
    token = await create_verified_user_and_get_token(client, db_session)

    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    # Mock ASR returning an error
    mock_response = {
        "Response": {
            "Error": {
                "Code": "InvalidParameter",
                "Message": "Audio format not supported",
            },
            "RequestId": "test-request-id",
        }
    }

    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        with patch(
            "api.voice.call_tencent_asr", new_callable=AsyncMock
        ) as mock_asr:
            mock_asr.return_value = mock_response

            response = await client.post(
                "/api/v1/voice/recognize",
                json={
                    "audio_data": audio_data,
                    "audio_format": "wav",
                    "sample_rate": 16000,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 502
            data = response.json()
            assert "ERR_VOICE_API_REQUEST_FAILED" in str(data)
            assert "Audio format not supported" not in str(data)


@pytest.mark.integration
async def test_recognize_unexpected_provider_error_is_typed_and_sanitized(
    client: AsyncClient, db_session
):
    token = await create_verified_user_and_get_token(client, db_session)
    audio_data = base64.b64encode(b"fake audio data").decode("ascii")

    with patch.dict(
        os.environ,
        {"TENCENT_SECRET_ID": "test-secret-id", "TENCENT_SECRET_KEY": "test-secret-key"},
    ):
        with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as mock_asr:
            mock_asr.side_effect = RuntimeError("provider secret detail")
            response = await client.post(
                "/api/v1/voice/recognize",
                json={"audio_data": audio_data},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 502
    assert "ERR_VOICE_API_REQUEST_FAILED" in response.text
    assert "provider secret detail" not in response.text


@pytest.mark.integration
async def test_recognize_asr_api_request_failed(client: AsyncClient, db_session):
    """Test voice recognition when ASR API request fails."""
    from core.error_codes import ErrorCode
    from core.error_handler import APIException

    token = await create_verified_user_and_get_token(client, db_session)

    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        with patch(
            "api.voice.call_tencent_asr", new_callable=AsyncMock
        ) as mock_asr:
            mock_asr.side_effect = APIException(
                error_code=ErrorCode.VOICE_API_REQUEST_FAILED,
                status_code=502,
                detail="腾讯云 API 请求失败: 500",
            )

            response = await client.post(
                "/api/v1/voice/recognize",
                json={
                    "audio_data": audio_data,
                    "audio_format": "wav",
                    "sample_rate": 16000,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 502
            data = response.json()
            assert "ERR_VOICE_API_REQUEST_FAILED" in str(data)


# ============================================
# Voice Recognize Tests - Supported Formats
# ============================================


@pytest.mark.integration
async def test_recognize_various_formats_mocked(client: AsyncClient, db_session):
    """Test voice recognition with various supported audio formats."""
    token = await create_verified_user_and_get_token(client, db_session)

    supported_formats = ["wav", "pcm", "ogg-opus", "speex", "silk", "mp3", "m4a", "aac", "amr"]

    mock_response = {
        "Response": {
            "Result": "测试",
            "AudioDuration": 1000,
            "RequestId": "test-request-id",
        }
    }

    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        for audio_format in supported_formats:
            with patch(
                "api.voice.call_tencent_asr", new_callable=AsyncMock
            ) as mock_asr:
                mock_asr.return_value = mock_response

                audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

                response = await client.post(
                    "/api/v1/voice/recognize",
                    json={
                        "audio_data": audio_data,
                        "audio_format": audio_format,
                        "sample_rate": 16000,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )

                assert response.status_code == 200, f"Failed for format: {audio_format}"
                data = response.json()
                assert data["success"] is True, f"Failed for format: {audio_format}"


# ============================================
# Voice Recognize Tests - Default Values
# ============================================


@pytest.mark.integration
async def test_recognize_uses_default_values(client: AsyncClient, db_session):
    """Test that voice recognition uses default values for optional fields."""
    token = await create_verified_user_and_get_token(client, db_session)

    audio_data = base64.b64encode(b"fake audio data").decode("utf-8")

    mock_response = {
        "Response": {
            "Result": "默认测试",
            "AudioDuration": 1000,
            "RequestId": "test-request-id",
        }
    }

    with patch.dict(
        os.environ,
        {
            "TENCENT_SECRET_ID": "test-secret-id",
            "TENCENT_SECRET_KEY": "test-secret-key",
        },
    ):
        with patch(
            "api.voice.call_tencent_asr", new_callable=AsyncMock
        ) as mock_asr:
            mock_asr.return_value = mock_response

            # Send only required field
            response = await client.post(
                "/api/v1/voice/recognize",
                json={
                    "audio_data": audio_data,
                    # Not specifying audio_format, sample_rate, language
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

            # Verify defaults were used
            mock_asr.assert_called_once()
            call_kwargs = mock_asr.call_args[1]
            assert call_kwargs["audio_format"] == "wav"  # Default
            assert call_kwargs["sample_rate"] == 16000  # Default
            assert call_kwargs["language"] == "zh"  # Default
