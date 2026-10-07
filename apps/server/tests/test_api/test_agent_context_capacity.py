"""Capacity and cancellation contracts for Agent writing-context assembly."""

import asyncio
import contextlib
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlmodel import Session

import api.agent_api as agent_api
from tests.test_api.test_agent_api import create_test_api_key, create_test_project, create_test_user


class _RecordingExecutor:
    def __init__(self, max_workers: int):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="test-writing-context")
        self.futures = []
        self.second_submitted = threading.Event()

    def submit(self, *args, **kwargs):
        future = self._pool.submit(*args, **kwargs)
        self.futures.append(future)
        if len(self.futures) >= 2:
            self.second_submitted.set()
        return future

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


@pytest.fixture
def install_owned_capacity(monkeypatch):
    executors: list[_RecordingExecutor] = []

    def install(*, workers: int, capacity: int):
        executor = _RecordingExecutor(workers)
        gate = threading.BoundedSemaphore(capacity)
        executors.append(executor)
        monkeypatch.setattr(agent_api, "_writing_context_executor", executor, raising=False)
        monkeypatch.setattr(agent_api, "_writing_context_capacity", gate, raising=False)
        return executor, gate

    yield install

    for executor in executors:
        executor.shutdown()


def _setup(session: Session, username: str) -> tuple[str, str]:
    user = create_test_user(session, username)
    project = create_test_project(session, user.id)
    _, key = create_test_api_key(session, user.id, scopes=["read"])
    return project.id, key


def _headers(key: str) -> dict[str, str]:
    return {"X-Agent-API-Key": key}


def _context_result(*, content: str = "body") -> SimpleNamespace:
    return SimpleNamespace(
        items=[{"type": "draft", "title": "Chapter", "content": content, "id": "file-1", "relevance_score": 0.9}],
        refs=["file-1"],
        token_estimate=12,
    )


def _available_permits(gate: threading.BoundedSemaphore) -> int:
    available = 0
    while gate.acquire(blocking=False):
        available += 1
    for _ in range(available):
        gate.release()
    return available


@pytest.mark.integration
async def test_writing_context_uses_dedicated_pool_and_propagates_contextvars(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
    install_owned_capacity,
):
    project_id, key = _setup(db_session, "agent_context_success")
    executor, gate = install_owned_capacity(workers=1, capacity=1)
    marker = contextvars.ContextVar("writing_context_marker", default="missing")
    seen: list[str] = []

    def assemble(_self, **_kwargs):
        seen.append(marker.get())
        return _context_result(content="context body")

    monkeypatch.setattr(agent_api.ContextAssembler, "assemble", assemble)
    token = marker.set("request-context")
    try:
        response = await client.get(
            f"/api/v1/agent/projects/{project_id}/writing-context",
            headers=_headers(key),
        )
    finally:
        marker.reset(token)

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "type": "draft",
                "title": "Chapter",
                "content_snippet": "context body",
                "source_file_id": "file-1",
                "relevance": 0.9,
            }
        ],
        "refs": ["file-1"],
        "total_available": 1,
        "returned": 1,
        "token_estimate": 12,
    }
    assert seen == ["request-context"]
    assert len(executor.futures) == 1
    assert _available_permits(gate) == 1
    assert agent_api.WRITING_CONTEXT_WORKERS == 4


@pytest.mark.integration
async def test_writing_context_timeout_retains_running_worker_permit(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
    install_owned_capacity,
):
    project_id, key = _setup(db_session, "agent_context_timeout_capacity")
    executor, gate = install_owned_capacity(workers=1, capacity=1)
    started = threading.Event()
    release = threading.Event()
    callback_finished = threading.Event()

    def assemble(_self, **_kwargs):
        started.set()
        release.wait()
        return _context_result()

    monkeypatch.setattr(agent_api.ContextAssembler, "assemble", assemble)
    monkeypatch.setattr(agent_api, "WRITING_CONTEXT_TIMEOUT_SECONDS", 0.03)
    try:
        response = await client.get(
            f"/api/v1/agent/projects/{project_id}/writing-context",
            headers=_headers(key),
        )
        assert started.is_set()
        assert response.status_code == 504
        assert response.json()["error_code"] == "ERR_SERVICE_UNAVAILABLE"
        assert response.json()["error_detail"] == "Writing context assembly timed out"
        assert _available_permits(gate) == 0
    finally:
        executor.futures[0].add_done_callback(lambda _future: callback_finished.set())
        release.set()
        assert await asyncio.to_thread(callback_finished.wait, 1)

    await asyncio.sleep(0)
    assert _available_permits(gate) == 1


@pytest.mark.integration
async def test_writing_context_saturation_rejects_immediately_without_using_default_pool(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
    install_owned_capacity,
):
    project_id, key = _setup(db_session, "agent_context_saturation")
    executor, _gate = install_owned_capacity(workers=1, capacity=1)
    started = threading.Event()
    release = threading.Event()

    def assemble(_self, **_kwargs):
        started.set()
        release.wait()
        return _context_result()

    monkeypatch.setattr(agent_api.ContextAssembler, "assemble", assemble)
    monkeypatch.setattr(agent_api, "WRITING_CONTEXT_TIMEOUT_SECONDS", 5.0)
    first = asyncio.create_task(
        client.get(f"/api/v1/agent/projects/{project_id}/writing-context", headers=_headers(key))
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        rejected = await client.get(
            f"/api/v1/agent/projects/{project_id}/writing-context",
            headers=_headers(key),
        )
        assert rejected.status_code == 503
        assert rejected.json()["error_code"] == "ERR_SERVICE_UNAVAILABLE"
        assert len(executor.futures) == 1
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "default-pool-free"), timeout=1.0) == "default-pool-free"
    finally:
        release.set()

    assert (await first).status_code == 200


@pytest.mark.integration
async def test_writing_context_cancelled_queued_future_releases_permit(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
    install_owned_capacity,
):
    project_id, key = _setup(db_session, "agent_context_cancel_queue")
    executor, gate = install_owned_capacity(workers=1, capacity=2)
    started = threading.Event()
    release = threading.Event()

    def assemble(_self, **_kwargs):
        started.set()
        release.wait()
        return _context_result()

    monkeypatch.setattr(agent_api.ContextAssembler, "assemble", assemble)
    monkeypatch.setattr(agent_api, "WRITING_CONTEXT_TIMEOUT_SECONDS", 1.0)
    first = asyncio.create_task(
        client.get(f"/api/v1/agent/projects/{project_id}/writing-context", headers=_headers(key))
    )
    second = None
    try:
        assert await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(
            client.get(f"/api/v1/agent/projects/{project_id}/writing-context", headers=_headers(key))
        )
        assert await asyncio.to_thread(executor.second_submitted.wait, 0.5)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        await asyncio.sleep(0)

        assert executor.futures[1].cancelled()
        assert _available_permits(gate) == 1
    finally:
        if second is not None and not second.done():
            second.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await second
        release.set()

    assert (await first).status_code == 200
    await asyncio.sleep(0)
    assert _available_permits(gate) == 2


@pytest.mark.integration
async def test_writing_context_submit_failure_releases_permit(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    project_id, key = _setup(db_session, "agent_context_submit_failure")
    gate = threading.BoundedSemaphore(1)

    class FailingExecutor:
        @staticmethod
        def submit(*_args, **_kwargs):
            raise RuntimeError("executor unavailable")

    monkeypatch.setattr(agent_api, "_writing_context_executor", FailingExecutor(), raising=False)
    monkeypatch.setattr(agent_api, "_writing_context_capacity", gate, raising=False)
    monkeypatch.setattr(agent_api.ContextAssembler, "assemble", lambda _self, **_kwargs: _context_result())

    try:
        response = await client.get(
            f"/api/v1/agent/projects/{project_id}/writing-context",
            headers=_headers(key),
        )
    except RuntimeError as exc:
        assert str(exc) == "executor unavailable"
    else:
        assert response.status_code == 500

    assert _available_permits(gate) == 1
