from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import api.materials.helpers as materials_helpers
from flows.pipelines import novel_ingestion_v3_flow as flow_mod


class _FakeResponse:
    def __init__(self, content: bytes):
        self._content = content

    def read(self):
        return self._content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestEnsureFileLocal:
    def test_interrupted_download_does_not_publish_and_retry_downloads_again(self, tmp_path, monkeypatch):
        target = tmp_path / "uploads" / "user-9_小说.txt"
        target.parent.mkdir()
        unrelated = target.parent / "existing.txt"
        unrelated.write_bytes(b"keep")
        content = "第一章 中文\n正文🙂\n".encode()

        class InterruptedResponse(_FakeResponse):
            def read(self):
                raise OSError("interrupted response")

        responses = iter([InterruptedResponse(b""), _FakeResponse(content)])
        requests = []

        def download(request, timeout):
            requests.append((request.full_url, request.get_header("X-internal-token"), timeout))
            assert not target.exists()
            return next(responses)

        monkeypatch.setenv("API_SERVER_INTERNAL_URL", "http://api.internal/")
        monkeypatch.setenv("MATERIAL_INTERNAL_TOKEN", "dummy-local-token")
        monkeypatch.setattr(flow_mod.urllib.request, "urlopen", download)
        with pytest.raises(OSError, match="interrupted response"):
            flow_mod._ensure_file_local(str(target), "user-9", MagicMock())

        assert not target.exists()
        assert list(target.parent.iterdir()) == [unrelated]
        assert flow_mod._ensure_file_local(str(target), "user-9", MagicMock()) == str(target)
        assert target.read_bytes() == content
        assert len(requests) == 2
        assert requests[0] == requests[1]
        assert requests[0] == (
            "http://api.internal/api/v1/materials/internal/system/files/user-9_%E5%B0%8F%E8%AF%B4.txt?user_id=user-9",
            "dummy-local-token",
            30,
        )
        assert sorted(p.name for p in target.parent.iterdir()) == sorted([target.name, unrelated.name])
        assert unrelated.read_bytes() == b"keep"

    def test_publish_failure_cleans_only_owned_temporary_file(self, tmp_path, monkeypatch):
        target = tmp_path / "novel.txt"
        unrelated = tmp_path / "existing.txt"
        unrelated.write_bytes(b"keep")
        monkeypatch.setenv("API_SERVER_INTERNAL_URL", "http://api.internal")
        monkeypatch.setenv("MATERIAL_INTERNAL_TOKEN", "dummy-local-token")
        monkeypatch.setattr(flow_mod.urllib.request, "urlopen", lambda *_args, **_kwargs: _FakeResponse(b"complete"))

        def reject_publish(source, destination):
            assert Path(source).parent == target.parent
            assert Path(source).read_bytes() == b"complete"
            assert destination == str(target)
            assert not target.exists()
            raise OSError("publish failed")

        monkeypatch.setattr(flow_mod.os, "replace", reject_publish)
        with pytest.raises(OSError, match="publish failed"):
            flow_mod._ensure_file_local(str(target), "u1", MagicMock())
        assert list(tmp_path.iterdir()) == [unrelated]
        assert unrelated.read_bytes() == b"keep"

    def test_existing_empty_cache_keeps_current_shortcircuit(self, tmp_path, monkeypatch):
        target = tmp_path / "novel.txt"
        target.write_bytes(b"")
        download = MagicMock(side_effect=AssertionError("must not download existing cache"))
        monkeypatch.setattr(flow_mod.urllib.request, "urlopen", download)
        assert flow_mod._ensure_file_local(str(target), "u1", MagicMock()) == str(target)
        assert target.read_bytes() == b""
        download.assert_not_called()

    def test_returns_existing_path_directly(self, tmp_path: Path):
        file_path = tmp_path / "novel.txt"
        file_path.write_text("hello", encoding="utf-8")

        out = flow_mod._ensure_file_local(str(file_path), user_id="u1", logger=MagicMock())

        assert out == str(file_path)

    def test_raises_when_missing_api_server_url(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "missing.txt"
        monkeypatch.delenv("API_SERVER_INTERNAL_URL", raising=False)
        monkeypatch.delenv("MATERIAL_INTERNAL_TOKEN", raising=False)

        with pytest.raises(FileNotFoundError, match="API_SERVER_INTERNAL_URL"):
            flow_mod._ensure_file_local(str(target), user_id="u1", logger=MagicMock())

    def test_raises_when_missing_internal_token(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "missing.txt"
        monkeypatch.setenv("API_SERVER_INTERNAL_URL", "http://api.internal")
        monkeypatch.delenv("MATERIAL_INTERNAL_TOKEN", raising=False)

        with pytest.raises(FileNotFoundError, match="MATERIAL_INTERNAL_TOKEN"):
            flow_mod._ensure_file_local(str(target), user_id="u1", logger=MagicMock())

    def test_downloads_file_when_not_local(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "uploads" / "novel.txt"
        logger = MagicMock()

        monkeypatch.setenv("API_SERVER_INTERNAL_URL", "http://api.internal")
        monkeypatch.setenv("MATERIAL_INTERNAL_TOKEN", "token-123")
        monkeypatch.setattr(
            flow_mod.urllib.request,
            "urlopen",
            lambda _req, timeout=30: _FakeResponse(b"downloaded-content"),
        )

        out = flow_mod._ensure_file_local(str(target), user_id="user-9", logger=logger)

        assert out == str(target)
        assert target.exists()
        assert target.read_bytes() == b"downloaded-content"
        assert logger.info.call_count >= 1

    def test_s3_reference_downloads_through_token_proxy_to_worker_cache(
        self, tmp_path: Path, monkeypatch
    ):
        object_name = f"{'a' * 32}.txt"
        reference = f"s3://stage-bucket/material/user-9/{object_name}"
        requests = []

        def download(request, timeout):
            requests.append((request.full_url, request.get_header("X-internal-token"), timeout))
            return _FakeResponse(b"private object bytes")

        monkeypatch.setenv("API_SERVER_INTERNAL_URL", "http://api.internal")
        monkeypatch.setenv("MATERIAL_INTERNAL_TOKEN", "worker-token")
        monkeypatch.setattr(flow_mod.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(flow_mod.urllib.request, "urlopen", download)

        local_path = flow_mod._ensure_file_local(reference, "user-9", MagicMock())
        assert Path(local_path).parent.parent == tmp_path
        assert Path(local_path).parent.name.startswith("zenstory-material-source-")
        assert Path(local_path).read_bytes() == b"private object bytes"
        assert requests == [
            (
                f"http://api.internal/api/v1/materials/internal/system/files/{object_name}?user_id=user-9",
                "worker-token",
                30,
            )
        ]
        second_path = flow_mod._ensure_file_local(reference, "user-9", MagicMock())
        assert second_path != local_path
        Path(local_path).unlink()
        assert Path(second_path).read_bytes() == b"private object bytes"
        assert len(requests) == 2

    @pytest.mark.parametrize(
        "reference",
        [
            "https://objects.example.test/material/user-9/file.txt",
            "s3://stage-bucket/material/other-user/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt",
            "s3://stage-bucket/other/user-9/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt",
            "s3://stage-bucket/material/user-9/not-opaque.txt",
        ],
    )
    def test_rejects_untrusted_remote_source_references(self, reference: str):
        with pytest.raises(ValueError, match="不受信任"):
            flow_mod._ensure_file_local(reference, "user-9", MagicMock())


@pytest.mark.asyncio
async def test_start_flow_deployment_persists_flow_run_id(monkeypatch):
    class _FlowRun:
        id = "flow-run-123"

    async def _run_deployment(**kwargs):
        return _FlowRun()

    class _Job:
        def __init__(self):
            self.correlation_id = None
            self.updated_at = None
            self.stage_progress = "{}"

        def update_stage_progress(self, stage: str, status: str, **kwargs):
            self.stage_progress = f"{stage}:{status}:{kwargs.get('flow_run_id')}"

    class _QueryResult:
        def __init__(self, job):
            self._job = job

        def first(self):
            return self._job

    class _Session:
        def __init__(self, job):
            self.job = job
            self.commits = 0

        def exec(self, _stmt):
            return _QueryResult(self.job)

        def add(self, _obj):
            return None

        def commit(self):
            self.commits += 1

        def close(self):
            return None

    job = _Job()
    monkeypatch.setattr(materials_helpers, "create_session", lambda: _Session(job))
    monkeypatch.setitem(
        __import__("sys").modules,
        "prefect.deployments",
        SimpleNamespace(run_deployment=_run_deployment),
    )

    flow_run_id = await materials_helpers._start_flow_deployment(
        file_path="/tmp/test.txt",
        novel_title="Novel",
        author="Author",
        user_id="user-1",
        novel_id=1,
    )

    assert str(flow_run_id) == "flow-run-123"
    assert job.correlation_id == "flow-run-123"
