#!/usr/bin/env python3
"""Start the API, optionally completing one bounded vector-maintenance operation first."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

MAINTENANCE_OPERATION_ENV = "ZENSTORY_VECTOR_MAINTENANCE_OPERATION_ID"
MAINTENANCE_RECEIPT_ENV = "ZENSTORY_VECTOR_MAINTENANCE_RECEIPT_JSON"
MAX_MAINTENANCE_CANDIDATES = 57
MAX_MAINTENANCE_DOCUMENTS = 2006


def _api_command(environment: Mapping[str, str]) -> list[str]:
    return [
        "uvicorn",
        "main:app",
        "--host",
        "0.0.0.0",
        "--port",
        environment.get("PORT", "8000"),
        "--workers",
        environment.get("WEB_CONCURRENCY", "1"),
        "--timeout-keep-alive",
        "300",
        "--timeout-worker-healthcheck",
        "600",
        "--timeout-graceful-shutdown",
        "295",
    ]


def _write_temporary_receipt(receipt_json: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix="zenstory-vector-backup-receipt-",
        suffix=".json",
        dir="/tmp",
        text=True,
    )
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            handle.write(receipt_json)
            if not receipt_json.endswith("\n"):
                handle.write("\n")
        os.chmod(path, 0o600)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    return path


def _run_startup_maintenance(
    environment: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    operation_id = environment.get(MAINTENANCE_OPERATION_ENV)
    receipt_json = environment.get(MAINTENANCE_RECEIPT_ENV)
    if operation_id is None and receipt_json is None:
        return
    if operation_id is None or receipt_json is None:
        raise RuntimeError(
            f"{MAINTENANCE_OPERATION_ENV} and {MAINTENANCE_RECEIPT_ENV} must be set together"
        )

    receipt_path = _write_temporary_receipt(receipt_json)
    try:
        runner(
            [
                sys.executable,
                str(Path(__file__).with_name("run_vector_maintenance.py")),
                "--operation-id",
                operation_id,
                "--backup-receipt",
                str(receipt_path),
                "--max-candidates",
                str(MAX_MAINTENANCE_CANDIDATES),
                "--max-documents",
                str(MAX_MAINTENANCE_DOCUMENTS),
            ],
            check=True,
        )
    finally:
        receipt_path.unlink(missing_ok=True)


def main(
    _argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    execvp: Callable[[str, Sequence[str]], object] = os.execvp,
) -> int:
    active_environment = os.environ if environment is None else environment
    _run_startup_maintenance(active_environment, runner=runner)
    command = _api_command(active_environment)
    execvp(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
