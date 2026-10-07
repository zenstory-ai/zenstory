"""Concurrency regressions for the process-memory dashboard cache."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError

import pytest

from services.infra import dashboard_cache


@pytest.fixture(autouse=True)
def _memory_backend(monkeypatch):
    monkeypatch.setenv("DASHBOARD_CACHE_BACKEND", "memory")
    dashboard_cache._memory_cache.clear()
    dashboard_cache._memory_versions.clear()
    yield
    dashboard_cache._memory_cache.clear()
    dashboard_cache._memory_versions.clear()


def test_memory_project_version_increments_are_atomic(monkeypatch):
    barrier = Barrier(2)

    class _InterleavingVersions(dict):
        def get(self, key, default=None):
            value = super().get(key, default)
            try:
                barrier.wait(timeout=0.2)
            except BrokenBarrierError:
                pass
            return value

    versions = _InterleavingVersions()
    monkeypatch.setattr(dashboard_cache, "_memory_versions", versions)

    with ThreadPoolExecutor(max_workers=2) as executor:
        returned = list(
            executor.map(
                lambda _index: dashboard_cache.bump_project_version("user-1", "project-1"),
                range(2),
            )
        )

    version_key = dashboard_cache._project_version_key("user-1", "project-1")
    assert sorted(returned) == [2, 3]
    assert versions[version_key] == 3
