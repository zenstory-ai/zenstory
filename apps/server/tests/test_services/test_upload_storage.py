"""Private upload storage contract tests."""

from __future__ import annotations

import hashlib
from datetime import UTC
from datetime import datetime as RealDatetime
from pathlib import Path

import httpx
import pytest

from config.upload_storage import UploadStorageSettings
from services.infra.upload_storage import MAX_FEEDBACK_BYTES, S3UploadStorage, UploadStorageError


def _s3_settings(**overrides: str) -> UploadStorageSettings:
    values = {
        "UPLOAD_STORAGE_BACKEND": "s3",
        "UPLOAD_S3_ENDPOINT": "https://objects.example.test",
        "UPLOAD_S3_REGION": "auto",
        "UPLOAD_S3_BUCKET": "zenstory-stage",
        "UPLOAD_S3_ACCESS_KEY_ID": "test-access",
        "UPLOAD_S3_SECRET_ACCESS_KEY": "test-secret",
        "UPLOAD_S3_URL_STYLE": "virtual",
    }
    values.update(overrides)
    return UploadStorageSettings.from_environment(values)


@pytest.mark.parametrize(
    "missing",
    [
        "UPLOAD_S3_ENDPOINT",
        "UPLOAD_S3_REGION",
        "UPLOAD_S3_BUCKET",
        "UPLOAD_S3_ACCESS_KEY_ID",
        "UPLOAD_S3_SECRET_ACCESS_KEY",
        "UPLOAD_S3_URL_STYLE",
    ],
)
def test_s3_configuration_never_silently_falls_back_when_incomplete(missing: str):
    values = {
        "UPLOAD_STORAGE_BACKEND": "s3",
        "UPLOAD_S3_ENDPOINT": "https://objects.example.test",
        "UPLOAD_S3_REGION": "auto",
        "UPLOAD_S3_BUCKET": "zenstory-stage",
        "UPLOAD_S3_ACCESS_KEY_ID": "test-access",
        "UPLOAD_S3_SECRET_ACCESS_KEY": "test-secret",
        "UPLOAD_S3_URL_STYLE": "virtual",
    }
    del values[missing]
    with pytest.raises(ValueError, match=missing):
        UploadStorageSettings.from_environment(values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"UPLOAD_S3_ENDPOINT": "https://user:secret@objects.example.test"},
        {"UPLOAD_S3_ENDPOINT": "https://objects.example.test/arbitrary/path"},
        {"UPLOAD_S3_ENDPOINT": "http://objects.example.test"},
        {"UPLOAD_S3_BUCKET": "../escape"},
        {"UPLOAD_S3_BUCKET": "127.0.0.1"},
    ],
)
def test_s3_configuration_rejects_unsafe_origins_and_buckets(overrides: dict[str, str]):
    with pytest.raises(ValueError):
        _s3_settings(**overrides)


def test_s3_signed_roundtrip_uses_private_canonical_references(monkeypatch):
    objects: dict[str, bytes] = {}
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=test-access/")
        assert request.headers["x-amz-content-sha256"] == hashlib.sha256(request.content).hexdigest()
        key = request.url.path
        if request.method == "PUT":
            objects[key] = request.content
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, content=objects[key])
        if request.method == "DELETE":
            objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    storage = S3UploadStorage(_s3_settings(), client=client)
    monkeypatch.setattr("services.infra.upload_storage.secrets.token_hex", lambda _size: "a" * 32)

    stored = storage.put_material(
        owner_id="user-1",
        timestamp="ignored",
        original_name="story.txt",
        content=b"chapter bytes",
    )
    assert stored.reference == f"s3://zenstory-stage/material/user-1/{'a' * 32}.txt"
    assert storage.read_material_reference(stored.reference, owner_id="user-1") == b"chapter bytes"
    storage.delete(stored.reference, kind="material", owner_id="user-1")
    assert objects == {}
    assert [request.method for request in requests] == ["PUT", "GET", "DELETE"]


def test_sigv4_signature_fixture_is_stable(monkeypatch):
    class FixedDatetime:
        @classmethod
        def now(cls, _tz):
            return RealDatetime(2013, 5, 24, tzinfo=UTC)

    authorization = ""

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal authorization
        authorization = request.headers["authorization"]
        return httpx.Response(200, content=b"x")

    monkeypatch.setattr("services.infra.upload_storage.datetime", FixedDatetime)
    storage = S3UploadStorage(
        _s3_settings(
            UPLOAD_S3_REGION="us-east-1",
            UPLOAD_S3_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE",
            UPLOAD_S3_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ),
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    storage.read_feedback(f"s3://zenstory-stage/feedback/{'a' * 32}.png")

    assert authorization.endswith(
        "Signature=4f8dec913443c166b6409c62126db2aafa086a00b98f5797eb96b1fd245bbd38"
    )


def test_s3_feedback_download_is_bounded():
    storage = S3UploadStorage(
        _s3_settings(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"x" * (MAX_FEEDBACK_BYTES + 1))
            )
        ),
    )
    with pytest.raises(UploadStorageError, match="download limit"):
        storage.read_feedback(f"s3://zenstory-stage/feedback/{'a' * 32}.png")


@pytest.mark.parametrize(
    "reference",
    [
        "https://objects.example.test/zenstory-stage/feedback/a.png",
        "s3://other-bucket/feedback/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
        "s3://zenstory-stage/untrusted/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
        "s3://zenstory-stage/feedback/../escape.png",
        "s3://zenstory-stage/feedback/not-opaque.png",
    ],
)
def test_s3_reference_parser_rejects_arbitrary_urls_buckets_and_prefixes(reference: str):
    storage = S3UploadStorage(_s3_settings(), client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    with pytest.raises(UploadStorageError):
        storage.read_feedback(reference)


def test_local_storage_remains_default_and_preserves_legacy_roots(tmp_path: Path):
    from services.infra.upload_storage import LocalUploadStorage

    assert UploadStorageSettings.from_environment({}).backend == "local"
    storage = LocalUploadStorage(
        material_root=tmp_path / "materials",
        feedback_root=tmp_path / "feedback",
    )
    material = storage.put_material(
        owner_id="user-1",
        timestamp="20261007_120000",
        original_name="story.txt",
        content=b"legacy local bytes",
    )
    assert Path(material.reference).is_file()
    assert storage.read_material_name(
        owner_id="user-1", object_name=Path(material.reference).name
    ) == b"legacy local bytes"
