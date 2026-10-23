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


SECRET = "test-secret"


def _s3_error(status: int, code: str | None) -> httpx.Response:
    body = b"" if code is None else (
        b"<?xml version=\"1.0\"?><Error><Code>" + code.encode() + b"</Code>"
        b"<Message>provider detail</Message><StringToSign>signed text</StringToSign></Error>"
    )
    return httpx.Response(status, content=body)


def _assert_no_secret_or_body(caplog) -> None:
    for record in caplog.records:
        message = record.getMessage()
        assert SECRET not in message
        assert "provider detail" not in message
        assert "signed text" not in message


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (403, "SignatureDoesNotMatch", "signature"),
        (403, "InvalidAccessKeyId", "auth"),
        (403, "AccessDenied", "auth"),
        (403, None, "auth"),
        (403, "SomethingNew", "auth"),
        (403, "RequestTimeTooSkewed", "clock"),
        (404, "NoSuchBucket", "config"),
        (400, "AuthorizationHeaderMalformed", "config"),
        (503, "SlowDown", "provider"),
        (500, None, "provider"),
    ],
)
def test_s3_failures_get_fixed_redacted_reasons(caplog, status, code, reason):
    storage = S3UploadStorage(
        _s3_settings(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: _s3_error(status, code))),
    )
    with pytest.raises(UploadStorageError) as raised:
        storage.read_material_reference(f"s3://zenstory-stage/material/user-1/{'a' * 32}.txt", owner_id="user-1")

    assert raised.value.reason == reason
    assert raised.value.status == status
    assert f"reason={reason}" in caplog.text
    assert reason in str(raised.value)
    _assert_no_secret_or_body(caplog)


def test_s3_missing_object_stays_not_found_and_is_not_logged(caplog):
    from services.infra.upload_storage import UploadNotFoundError

    storage = S3UploadStorage(
        _s3_settings(),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: _s3_error(404, "NoSuchKey"))),
    )
    with pytest.raises(UploadNotFoundError):
        storage.read_feedback(f"s3://zenstory-stage/feedback/{'a' * 32}.png")
    assert "Private object storage" not in caplog.text


def test_s3_network_failure_midway_through_download_is_a_storage_error(caplog):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("connection reset")

    storage = S3UploadStorage(
        _s3_settings(),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=BrokenStream()))
        ),
    )
    with pytest.raises(UploadStorageError) as raised:
        storage.read_feedback(f"s3://zenstory-stage/feedback/{'a' * 32}.png")
    assert raised.value.reason == "network"
    assert "reason=network" in caplog.text


class _FakeBucket:
    """In-memory S3 endpoint whose PUT/GET/DELETE can be overridden per method."""

    def __init__(self, **overrides):
        self.objects: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.overrides = overrides

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        override = self.overrides.get(request.method)
        if override is not None:
            return override(request)
        key = request.url.path
        if request.method == "PUT":
            assert request.headers["if-none-match"] == "*"
            if key in self.objects:
                return httpx.Response(412)
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "GET":
            if key not in self.objects:
                return _s3_error(404, "NoSuchKey")
            return httpx.Response(200, content=self.objects[key])
        self.objects.pop(key, None)
        return httpx.Response(204)

    def storage(self) -> S3UploadStorage:
        return S3UploadStorage(_s3_settings(), client=httpx.Client(transport=httpx.MockTransport(self)))

    @property
    def methods(self) -> list[str]:
        return [request.method for request in self.requests]


def test_probe_round_trip_writes_reads_and_deletes_one_synthetic_object():
    bucket = _FakeBucket()
    result = bucket.storage().probe_round_trip()

    assert result.ok and result.step == "done" and result.cleanup == "deleted"
    assert result.reason is None
    assert bucket.methods == ["PUT", "GET", "DELETE"]
    assert bucket.objects == {}
    put_path = bucket.requests[0].url.path
    assert put_path.startswith("/preflight/") and put_path == f"/{result.key}"
    assert all(request.url.path == put_path for request in bucket.requests)


def test_probe_with_a_stale_secret_fails_as_signature_and_writes_nothing(caplog):
    bucket = _FakeBucket(PUT=lambda _request: _s3_error(403, "SignatureDoesNotMatch"))
    result = bucket.storage().probe_round_trip()

    assert not result.ok
    assert (result.step, result.reason, result.cleanup) == ("put", "signature", "not_needed")
    assert bucket.methods == ["PUT"]
    _assert_no_secret_or_body(caplog)


def test_probe_does_not_call_every_403_a_bad_secret():
    bucket = _FakeBucket(PUT=lambda _request: _s3_error(403, "AccessDenied"))
    result = bucket.storage().probe_round_trip()
    assert (result.step, result.reason, result.cleanup) == ("put", "auth", "not_needed")


def test_probe_never_deletes_an_existing_object_at_its_key():
    bucket = _FakeBucket(PUT=lambda _request: httpx.Response(412))
    result = bucket.storage().probe_round_trip()
    assert (result.step, result.reason, result.cleanup) == ("put", "conflict", "not_needed")
    assert bucket.methods == ["PUT"]


def test_probe_network_failure_cleans_up_in_case_the_put_landed():
    def unreachable(request):
        raise httpx.ConnectError("unreachable", request=request)

    bucket = _FakeBucket(PUT=unreachable)
    result = bucket.storage().probe_round_trip()
    assert (result.step, result.reason, result.cleanup) == ("put", "network", "deleted")
    assert bucket.methods == ["PUT", "DELETE"]


def test_probe_provider_error_on_put_still_attempts_cleanup():
    bucket = _FakeBucket(PUT=lambda _request: _s3_error(503, "SlowDown"))
    result = bucket.storage().probe_round_trip()
    assert (result.step, result.reason, result.cleanup) == ("put", "provider", "deleted")


def test_probe_read_back_mismatch_fails_and_still_deletes():
    bucket = _FakeBucket(GET=lambda _request: httpx.Response(200, content=b"x" * 64))
    result = bucket.storage().probe_round_trip()
    assert (result.step, result.reason, result.cleanup) == ("verify", "integrity", "deleted")
    assert bucket.objects == {}


def test_probe_read_denied_after_write_still_deletes():
    bucket = _FakeBucket(GET=lambda _request: _s3_error(403, "AccessDenied"))
    result = bucket.storage().probe_round_trip()
    assert (result.step, result.reason, result.cleanup) == ("get", "auth", "deleted")
    assert bucket.objects == {}


def test_probe_reports_a_failed_cleanup():
    bucket = _FakeBucket(DELETE=lambda _request: _s3_error(403, "AccessDenied"))
    result = bucket.storage().probe_round_trip()
    assert not result.ok
    assert (result.step, result.reason, result.cleanup) == ("delete", "auth", "failed")
