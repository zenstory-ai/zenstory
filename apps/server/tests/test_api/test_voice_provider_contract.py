"""SentenceRecognition's actual wire contract, without a paid provider request."""

import base64
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.voice import call_tencent_asr
from tests.test_api.test_voice import create_verified_user_and_get_token

FORMATS = ("wav", "pcm", "ogg-opus", "speex", "silk", "mp3", "m4a", "aac", "amr")
ENCODED_LIMIT = 3_000_000


async def test_status_matches_sentence_recognition_formats(client):
    response = await client.get("/api/v1/voice/status")
    assert response.status_code == 200
    assert set(response.json()["supported_formats"]) == set(FORMATS)


async def test_empty_audio_rejected_before_provider(client, db_session, monkeypatch):
    monkeypatch.setenv("TENCENT_SECRET_ID", "local-test-id")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "local-test-key")
    token = await create_verified_user_and_get_token(client, db_session)
    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as provider:
        provider.return_value = {"Response": {"Result": "should not be called"}}
        response = await client.post(
            "/api/v1/voice/recognize", json={"audio_data": ""},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422
    provider.assert_not_awaited()


@pytest.mark.parametrize("envelope", [{}, {"Response": {}}, {"Response": None}, {"Response": []}, {"Response": {"Result": None}}, {"Response": {"Result": 123}}])
async def test_malformed_provider_success_is_sanitized_failure(client, db_session, monkeypatch, envelope):
    monkeypatch.setenv("TENCENT_SECRET_ID", "local-test-id")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "local-test-key")
    token = await create_verified_user_and_get_token(client, db_session)
    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as provider:
        provider.return_value = envelope
        response = await client.post(
            "/api/v1/voice/recognize", json={"audio_data": "YXVkaW8="},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 502
    assert "ERR_VOICE_API_REQUEST_FAILED" in str(response.json())
    assert "local-test-key" not in response.text


@pytest.mark.parametrize("audio_format", ["webm", "flac"])
async def test_unsupported_container_rejected_before_provider(client, db_session, monkeypatch, audio_format):
    monkeypatch.setenv("TENCENT_SECRET_ID", "local-test-id")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "local-test-key")
    token = await create_verified_user_and_get_token(client, db_session)
    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as provider:
        provider.return_value = {"Response": {"Result": "should not be called"}}
        response = await client.post(
            "/api/v1/voice/recognize",
            json={"audio_data": "YXVkaW8=", "audio_format": audio_format},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422
    provider.assert_not_awaited()


async def test_encoded_data_limit_is_three_mb(client, db_session, monkeypatch):
    monkeypatch.setenv("TENCENT_SECRET_ID", "local-test-id")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "local-test-key")
    token = await create_verified_user_and_get_token(client, db_session)
    data = base64.b64encode(b"x" * (ENCODED_LIMIT // 4 * 3)).decode("ascii")
    assert len(data) == ENCODED_LIMIT
    headers = {"Authorization": f"Bearer {token}"}
    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as provider:
        provider.return_value = {"Response": {"Result": "local result", "AudioDuration": 2430}}
        accepted = await client.post(
            "/api/v1/voice/recognize", json={"audio_data": data}, headers=headers,
        )
        assert accepted.status_code == 200
        provider.assert_awaited_once()
        provider.reset_mock()
        rejected = await client.post(
            "/api/v1/voice/recognize", json={"audio_data": data + "A"}, headers=headers,
        )
        assert rejected.status_code == 413
        provider.assert_not_awaited()


@pytest.mark.parametrize("duration", [0, 2430])
async def test_duration_is_already_milliseconds(client, db_session, monkeypatch, duration):
    monkeypatch.setenv("TENCENT_SECRET_ID", "local-test-id")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "local-test-key")
    token = await create_verified_user_and_get_token(client, db_session)
    with patch("api.voice.call_tencent_asr", new_callable=AsyncMock) as provider:
        provider.return_value = {"Response": {"Result": "local result", "AudioDuration": duration}}
        response = await client.post(
            "/api/v1/voice/recognize", json={"audio_data": "YXVkaW8="},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["duration_ms"] == duration


@pytest.mark.parametrize("audio_format", FORMATS)
async def test_signed_http_payload_preserves_provider_format(audio_format):
    client = AsyncMock()
    client.post.return_value = Mock(status_code=200)
    client.post.return_value.json.return_value = {"Response": {"Result": "local result"}}
    with patch("httpx.AsyncClient") as client_class:
        client_class.return_value.__aenter__.return_value = client
        await call_tencent_asr("YXVkaW8=", audio_format, 16000, "test-id", "test-key")
    client.post.assert_awaited_once()
    request = client.post.call_args
    payload = json.loads(request.kwargs["content"])
    assert payload["VoiceFormat"] == audio_format
    assert payload["DataLen"] == 5
    assert payload["Data"] == "YXVkaW8="
    assert payload["EngSerViceType"] == "16k_zh"
    assert request.kwargs["headers"]["X-TC-Action"] == "SentenceRecognition"


async def test_authorization_matches_independent_openssl_vector():
    """The fixed vector was generated independently with OpenSSL HMAC, not this helper."""
    client = AsyncMock()
    client.post.return_value = Mock(status_code=200)
    client.post.return_value.json.return_value = {"Response": {"Result": "local result"}}
    with patch("httpx.AsyncClient") as client_class, patch("api.voice.time.time", return_value=1551113065):
        client_class.return_value.__aenter__.return_value = client
        await call_tencent_asr("YXVkaW8=", "wav", 16000, "test-id", "test-key")
    args = client.post.call_args
    assert args.args == ("https://asr.tencentcloudapi.com",)
    assert args.kwargs["content"] == (
        b'{"ProjectId":0,"SubServiceType":2,"EngSerViceType":"16k_zh","SourceType":1,'
        b'"VoiceFormat":"wav","Data":"YXVkaW8=","DataLen":5,"FilterDirty":0,'
        b'"FilterModal":0,"ConvertNumMode":1}'
    )
    headers = args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Host"] == "asr.tencentcloudapi.com"
    assert headers["X-TC-Version"] == "2019-06-14"
    assert headers["X-TC-Timestamp"] == "1551113065"
    assert headers["Authorization"] == (
        "TC3-HMAC-SHA256 Credential=test-id/2019-02-25/asr/tc3_request, "
        "SignedHeaders=content-type;host, "
        "Signature=6eae7484e26aee14049dc6a00006402dce4be2bddc4fbd6ebf36c4e066958fc2"
    )
