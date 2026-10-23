"""Release preflight upload-storage gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release_preflight_check.py"
SECRET = "release-secret-value"
S3_ENV = {
    "UPLOAD_STORAGE_BACKEND": "s3",
    "UPLOAD_S3_ENDPOINT": "https://objects.example.test",
    "UPLOAD_S3_REGION": "auto",
    "UPLOAD_S3_BUCKET": "zenstory-prod",
    "UPLOAD_S3_ACCESS_KEY_ID": "release-access",
    "UPLOAD_S3_SECRET_ACCESS_KEY": SECRET,
    "UPLOAD_S3_URL_STYLE": "path",
}


def _load_script():
    spec = importlib.util.spec_from_file_location("release_preflight_check", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def preflight(monkeypatch):
    for name in ["UPLOAD_STORAGE_BACKEND", *S3_ENV]:
        monkeypatch.delenv(name, raising=False)
    return _load_script()


def _set_s3_env(monkeypatch, **overrides):
    for name, value in {**S3_ENV, **overrides}.items():
        monkeypatch.setenv(name, value)


def _bucket(put_response=None):
    objects: dict[str, bytes] = {}
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "PUT":
            if put_response is not None:
                return put_response
            objects[request.url.path] = request.content
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, content=objects[request.url.path])
        objects.pop(request.url.path, None)
        return httpx.Response(204)

    return httpx.Client(transport=httpx.MockTransport(handle)), objects, methods


def test_local_backend_keeps_its_contract_without_network(preflight):
    ok, detail = preflight.upload_storage_preflight(probe=True, strict=True)
    assert ok
    assert detail.startswith("local backend")


def test_incomplete_s3_configuration_fails_naming_variables_only(preflight, monkeypatch):
    _set_s3_env(monkeypatch, UPLOAD_S3_REGION="")
    ok, detail = preflight.upload_storage_preflight(probe=True, strict=False)
    assert not ok
    assert "UPLOAD_S3_REGION" in detail
    assert SECRET not in detail


def test_strict_release_requires_the_round_trip_for_s3(preflight, monkeypatch):
    _set_s3_env(monkeypatch)
    assert preflight.upload_storage_preflight(probe=False, strict=True)[0] is False
    assert preflight.upload_storage_preflight(probe=False, strict=False)[0] is True


def test_round_trip_passes_and_leaves_no_object(preflight, monkeypatch):
    _set_s3_env(monkeypatch)
    client, objects, methods = _bucket()
    ok, detail = preflight.upload_storage_preflight(probe=True, strict=True, client=client)
    assert ok, detail
    assert methods == ["PUT", "GET", "DELETE"]
    assert objects == {}


def test_stale_secret_fails_the_release_with_a_redacted_signature_reason(preflight, monkeypatch, caplog):
    _set_s3_env(monkeypatch)
    client, _objects, methods = _bucket(
        put_response=httpx.Response(
            403,
            content=b"<Error><Code>SignatureDoesNotMatch</Code><Message>provider detail</Message></Error>",
        )
    )
    ok, detail = preflight.upload_storage_preflight(probe=True, strict=True, client=client)
    assert not ok
    assert detail.startswith("put failed: signature")
    assert "nothing was written" in detail
    assert methods == ["PUT"]
    for text in (detail, caplog.text):
        assert SECRET not in text
        assert "provider detail" not in text


@pytest.mark.parametrize(("probe_ok", "exit_code"), [(True, 0), (False, 1)])
def test_main_exit_code_follows_the_probe(preflight, monkeypatch, capsys, probe_ok, exit_code):
    calls: list[dict] = []
    for name, value in {
        "JWT_SECRET_KEY": "x" * 40,
        "CORS_ORIGINS": "https://app.zenstory.ai",
        "RATE_LIMIT_BACKEND": "redis",
        "ZPAY_ENABLED": "false",
    }.items():
        monkeypatch.setenv(name, value)
    _set_s3_env(monkeypatch)
    detail = "s3 synthetic object PUT/GET/DELETE matched (sha256)" if probe_ok else "put failed: network"
    monkeypatch.setattr(
        preflight,
        "upload_storage_preflight",
        lambda **kwargs: calls.append(kwargs) or (probe_ok, detail),
    )
    monkeypatch.setattr("sys.argv", ["release_preflight_check.py", "--probe-upload-storage"])

    assert preflight.main() == exit_code
    assert calls == [{"probe": True, "strict": False}]
    output = capsys.readouterr().out
    assert f"[{'PASS' if probe_ok else 'FAIL'}] Upload storage: {detail}" in output
    assert SECRET not in output
