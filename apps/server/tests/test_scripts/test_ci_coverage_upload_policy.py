"""Optional reporting must not overturn mandatory tests/coverage gates."""
from pathlib import Path

import yaml


def _codecov_uploads(job):
    return [step for step in job["steps"] if str(step.get("uses", "")).startswith("codecov/codecov-action@")]


def _assert_single_nonblocking_upload(job):
    uploads = _codecov_uploads(job)
    assert len(uploads) == 1
    assert uploads[0]["with"]["fail_ci_if_error"] is False
    assert uploads[0].get("continue-on-error") is True


def test_codecov_uploads_are_explicitly_nonblocking_but_tests_are_not():
    root = Path(__file__).resolve().parents[4]
    jobs = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]

    # 前端：测试与上传在同一个 job。
    frontend = jobs["frontend-test"]
    assert not frontend.get("continue-on-error", False)
    _assert_single_nonblocking_upload(frontend)
    frontend_tests = [step for step in frontend["steps"] if "test:coverage" in step.get("run", "")]
    assert frontend_tests
    assert all(not step.get("continue-on-error", False) for step in frontend_tests)

    # 后端：pytest 分片在 backend-test，合并覆盖率、80% 门槛与唯一一次上传在 backend-coverage。
    shards = jobs["backend-test"]
    coverage = jobs["backend-coverage"]
    for job in (shards, coverage):
        assert not job.get("continue-on-error", False)
    assert not _codecov_uploads(shards)
    _assert_single_nonblocking_upload(coverage)
    shard_tests = [step for step in shards["steps"] if "pytest" in step.get("run", "")]
    assert shard_tests
    assert all(not step.get("continue-on-error", False) for step in shard_tests)
    gates = [step for step in coverage["steps"] if "--fail-under=80" in step.get("run", "")]
    assert len(gates) == 1
    assert not gates[0].get("continue-on-error", False)
