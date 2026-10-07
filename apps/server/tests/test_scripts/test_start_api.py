"""API startup bootstrap tests."""

import importlib
import json
import os
import stat
import subprocess
from pathlib import Path
from unittest import mock

import pytest

with mock.patch.dict(os.environ):
    startup = importlib.import_module("scripts.start_api")


def test_default_start_execs_uvicorn_with_existing_production_timeouts():
    exec_calls: list[tuple[str, list[str]]] = []

    startup.main(
        environment={"PORT": "9000", "WEB_CONCURRENCY": "3"},
        runner=lambda *_args, **_kwargs: pytest.fail("maintenance must remain disabled"),
        execvp=lambda executable, command: exec_calls.append((executable, list(command))),
    )

    assert exec_calls == [
        (
            "uvicorn",
            [
                "uvicorn",
                "main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--workers",
                "3",
                "--timeout-keep-alive",
                "300",
                "--timeout-worker-healthcheck",
                "600",
                "--timeout-graceful-shutdown",
                "295",
            ],
        )
    ]


def test_default_port_and_worker_count_are_stable():
    assert startup._api_command({})[5:9] == ["8000", "--workers", "1", "--timeout-keep-alive"]


def test_maintenance_uses_private_temporary_receipt_then_execs_api():
    receipt = {
        "schema_version": 1,
        "backup_operation_id": "cleanup-20261007",
        "archive_sha256": "c05fd49a6ebfa5294774c34a42e2edd0771f43729e9a5d21d5334ea7b0f96429",
        "archive_bytes": 165_129_508,
        "verified_sha256": True,
        "sqlite_integrity": "ok",
        "collection_count": 180,
        "offsite_archive_path": (
            "/outputs/zenstory/vector-maintenance-20261007/backup-stream.tar.gz"
        ),
        "generated_at": "2026-10-07T12:00:00+00:00",
    }
    maintenance_calls: list[list[str]] = []
    receipt_paths: list[Path] = []
    exec_calls: list[list[str]] = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is True
        maintenance_calls.append(command)
        receipt_path = Path(command[command.index("--backup-receipt") + 1])
        receipt_paths.append(receipt_path)
        assert receipt_path.parent == Path("/tmp")
        assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
        assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
        return subprocess.CompletedProcess(command, 0)

    startup.main(
        environment={
            startup.MAINTENANCE_OPERATION_ENV: "cleanup-20261007",
            startup.MAINTENANCE_RECEIPT_ENV: json.dumps(receipt),
        },
        runner=run,
        execvp=lambda _executable, command: exec_calls.append(list(command)),
    )

    assert len(maintenance_calls) == 1
    assert maintenance_calls[0][-4:] == [
        "--max-candidates",
        "57",
        "--max-documents",
        "2006",
    ]
    assert exec_calls[0][0:2] == ["uvicorn", "main:app"]
    assert not receipt_paths[0].exists()


def test_maintenance_failure_cleans_receipt_and_does_not_start_api():
    receipt_paths: list[Path] = []
    exec_calls: list[list[str]] = []

    def fail(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is True
        receipt_paths.append(Path(command[command.index("--backup-receipt") + 1]))
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        startup.main(
            environment={
                startup.MAINTENANCE_OPERATION_ENV: "cleanup-20261007",
                startup.MAINTENANCE_RECEIPT_ENV: "{}",
            },
            runner=fail,
            execvp=lambda _executable, command: exec_calls.append(list(command)),
        )

    assert exec_calls == []
    assert receipt_paths and not receipt_paths[0].exists()


@pytest.mark.parametrize(
    "environment",
    [
        {startup.MAINTENANCE_OPERATION_ENV: "cleanup-20261007"},
        {startup.MAINTENANCE_RECEIPT_ENV: "{}"},
    ],
)
def test_partial_maintenance_configuration_fails_closed(environment: dict[str, str]):
    with pytest.raises(RuntimeError, match="must be set together"):
        startup.main(
            environment=environment,
            runner=lambda *_args, **_kwargs: pytest.fail("must fail before maintenance"),
            execvp=lambda *_args: pytest.fail("must fail before API startup"),
        )
