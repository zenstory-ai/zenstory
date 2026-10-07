"""Startup contract for Prefect workers using environment-specific pools."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SERVER_ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = SERVER_ROOT / "docker" / "start-prefect-worker.sh"
DEPLOYMENTS = tuple(
    deployment["name"]
    for deployment in yaml.safe_load((SERVER_ROOT / "prefect.yaml").read_text(encoding="utf-8"))[
        "deployments"
    ]
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_startup(tmp_path: Path, *, pool: str | None, fail_deployment: str | None = None):
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    command_log = tmp_path / "prefect.log"
    _write_executable(
        binary_dir / "python3",
        """#!/bin/sh
if [ "$1" = "-c" ]; then
  exit 0
fi
exec "$REAL_PYTHON" "$@"
""",
    )
    _write_executable(
        binary_dir / "prefect",
        """#!/bin/sh
printf '%s\\n' "$*" >> "$PREFECT_STUB_LOG"
if [ "$1" = "deploy" ] && [ "$3" = "$FAIL_DEPLOYMENT" ]; then
  exit 17
fi
exit 0
""",
    )
    environment = {
        **os.environ,
        "PATH": f"{binary_dir}:{os.environ['PATH']}",
        "PREFECT_API_URL": "http://prefect.invalid/api",
        "PREFECT_STUB_LOG": str(command_log),
        "FAIL_DEPLOYMENT": fail_deployment or "",
        "REAL_PYTHON": sys.executable,
    }
    if pool is None:
        environment.pop("PREFECT_WORK_POOL", None)
    else:
        environment["PREFECT_WORK_POOL"] = pool
    result = subprocess.run(
        ["bash", str(START_SCRIPT)],
        cwd=SERVER_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    calls = command_log.read_text(encoding="utf-8").splitlines()
    return result, calls


@pytest.mark.parametrize("pool", ["zenstory-staging-pool", None])
def test_startup_creates_deploys_and_starts_worker_on_one_pool(tmp_path: Path, pool: str | None):
    expected_pool = pool or "zenstory-pool"
    result, calls = _run_startup(tmp_path, pool=pool)

    assert result.returncode == 0, result.stderr
    assert calls == [
        f"work-pool create {expected_pool} --type process",
        *(f"deploy --name {name} --pool {expected_pool}" for name in DEPLOYMENTS),
        f"worker start --pool {expected_pool}",
    ]


def test_deployment_failure_exits_before_worker_start(tmp_path: Path):
    result, calls = _run_startup(
        tmp_path,
        pool="zenstory-staging-pool",
        fail_deployment="chapter_extraction",
    )

    assert result.returncode == 17
    assert calls == [
        "work-pool create zenstory-staging-pool --type process",
        "deploy --name novel_ingestion_v3 --pool zenstory-staging-pool",
        "deploy --name chapter_extraction --pool zenstory-staging-pool",
    ]
    assert not any(call.startswith("worker start") for call in calls)
