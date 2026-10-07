"""Offline startup orchestration tests for bounded vector maintenance."""

import importlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

with mock.patch.dict(os.environ):
    maintenance = importlib.import_module("scripts.run_vector_maintenance")


def _seed_paths(tmp_path: Path) -> tuple[Path, Path]:
    persist_dir = tmp_path / "chroma"
    persist_dir.mkdir()
    (persist_dir / "chroma.sqlite3").write_bytes(b"catalog")
    backup_path = tmp_path / "backup.tar"
    backup_path.write_bytes(b"backup")
    return persist_dir, backup_path


def _write_receipt(
    path: Path,
    *,
    operation_id: str = "receipt-op",
    generated_at: datetime | None = None,
    overrides: dict | None = None,
) -> Path:
    payload = {
        "schema_version": 1,
        "backup_operation_id": operation_id,
        "archive_sha256": "a" * 64,
        "archive_bytes": 165_129_508,
        "verified_sha256": True,
        "sqlite_integrity": "ok",
        "collection_count": 180,
        "offsite_archive_path": "/private/offsite/zenstory-chroma-backup.tar.gz",
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
    }
    payload.update(overrides or {})
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def _runner_with_plan(plan: dict, calls: list[list[str]], *, fail_vacuum: bool = False):
    def run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "--output-plan" in command:
            plan_path = Path(command[command.index("--output-plan") + 1])
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
        if fail_vacuum and "vacuum" in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_success_runs_dry_apply_then_vacuum_and_writes_private_marker(tmp_path: Path):
    persist_dir, backup_path = _seed_paths(tmp_path)
    calls: list[list[str]] = []
    plan = {
        "candidate_count": 2,
        "candidates": [{"document_count": 3}, {"document_count": 4}],
        "plan_sha256": "abc",
    }
    result = maintenance.run_maintenance(
        operation_id="stale-20261007",
        persist_dir=persist_dir,
        backup_path=backup_path,
        max_candidates=2,
        max_documents=7,
        runner=_runner_with_plan(plan, calls),
        chroma_executable=Path("/runtime/bin/chroma"),
    )

    assert result == {"status": "success", "candidate_count": 2, "document_count": 7}
    assert "--output-plan" in calls[0]
    assert calls[0][calls[0].index("--inactive-months") + 1] == "6"
    assert "--apply-plan" in calls[1]
    assert "--confirm-application-stopped" in calls[1]
    assert calls[2][1:] == [
        "vacuum", "--path", str(persist_dir), "--force", "--timeout", "60",
    ]
    marker = persist_dir / ".maintenance" / "stale-20261007.json"
    assert json.loads(marker.read_text())["vacuum_completed"] is True
    assert (marker.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize(
    ("max_candidates", "max_documents", "message"),
    [(1, 10, "candidate safety ceiling"), (10, 6, "document safety ceiling")],
)
def test_scope_expansion_fails_before_apply_or_vacuum(
    tmp_path: Path, max_candidates: int, max_documents: int, message: str
):
    persist_dir, backup_path = _seed_paths(tmp_path)
    calls: list[list[str]] = []
    plan = {
        "candidate_count": 2,
        "candidates": [{"document_count": 3}, {"document_count": 4}],
        "plan_sha256": "abc",
    }
    with pytest.raises(RuntimeError, match=message):
        maintenance.run_maintenance(
            operation_id="bounded",
            persist_dir=persist_dir,
            backup_path=backup_path,
            max_candidates=max_candidates,
            max_documents=max_documents,
            runner=_runner_with_plan(plan, calls),
            chroma_executable=Path("/runtime/bin/chroma"),
        )
    assert len(calls) == 1
    assert not (persist_dir / ".maintenance" / "bounded.json").exists()


def test_vacuum_failure_leaves_no_marker_and_preserves_backup(tmp_path: Path):
    persist_dir, backup_path = _seed_paths(tmp_path)
    calls: list[list[str]] = []
    plan = {
        "candidate_count": 1,
        "candidates": [{"document_count": 2}],
        "plan_sha256": "abc",
    }
    with pytest.raises(subprocess.CalledProcessError):
        maintenance.run_maintenance(
            operation_id="vacuum-fails",
            persist_dir=persist_dir,
            backup_path=backup_path,
            max_candidates=1,
            max_documents=2,
            runner=_runner_with_plan(plan, calls, fail_vacuum=True),
            chroma_executable=Path("/runtime/bin/chroma"),
        )
    assert backup_path.read_bytes() == b"backup"
    assert not (persist_dir / ".maintenance" / "vacuum-fails.json").exists()


def test_success_marker_makes_retry_a_noop(tmp_path: Path):
    persist_dir, backup_path = _seed_paths(tmp_path)
    marker = persist_dir / ".maintenance" / "done.json"
    marker.parent.mkdir()
    marker.write_text(
        json.dumps(
            {
                "operation_id": "done",
                "status": "success",
                "candidate_count": 57,
                "document_count": 2006,
            }
        )
    )

    result = maintenance.run_maintenance(
        operation_id="done",
        persist_dir=persist_dir,
        backup_path=backup_path,
        max_candidates=57,
        max_documents=2006,
        runner=lambda *_args, **_kwargs: pytest.fail("completed operation must not run commands"),
    )
    assert result == {"status": "noop", "candidate_count": 57, "document_count": 2006}


def test_invalid_marker_or_missing_backup_fails_closed(tmp_path: Path):
    persist_dir, backup_path = _seed_paths(tmp_path)
    marker = persist_dir / ".maintenance" / "bad.json"
    marker.parent.mkdir()
    marker.write_text('{"operation_id":"bad","status":"running"}')
    with pytest.raises(RuntimeError, match="not a valid success marker"):
        maintenance.run_maintenance(
            operation_id="bad",
            persist_dir=persist_dir,
            backup_path=backup_path,
            max_candidates=57,
            max_documents=2006,
        )

    marker.unlink()
    backup_path.unlink()
    with pytest.raises(FileNotFoundError, match="backup"):
        maintenance.run_maintenance(
            operation_id="bad",
            persist_dir=persist_dir,
            backup_path=backup_path,
            max_candidates=57,
            max_documents=2006,
        )


def test_valid_offsite_backup_receipt_allows_maintenance_without_local_archive(tmp_path: Path):
    persist_dir, _backup_path = _seed_paths(tmp_path)
    receipt = _write_receipt(tmp_path / "receipt.json")
    calls: list[list[str]] = []
    plan = {
        "candidate_count": 0,
        "indexed_project_count": 154,
        "candidates": [],
        "plan_sha256": "abc",
    }
    result = maintenance.run_maintenance(
        operation_id="receipt-op",
        persist_dir=persist_dir,
        backup_receipt_path=receipt,
        max_candidates=57,
        max_documents=2006,
        runner=_runner_with_plan(plan, calls),
        chroma_executable=Path("/runtime/bin/chroma"),
    )
    assert result["status"] == "success"
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"remove": "archive_sha256"}, "fields mismatch"),
        ({"archive_sha256": "bad"}, "archive_sha256"),
        ({"archive_bytes": 0}, "archive_bytes"),
        ({"verified_sha256": False}, "verified_sha256"),
        ({"sqlite_integrity": "corrupt"}, "sqlite_integrity"),
        ({"collection_count": 0}, "collection_count"),
        ({"collection_count": -1}, "collection_count"),
        ({"collection_count": False}, "collection_count"),
        ({"offsite_archive_path": "relative.tar"}, "offsite_archive_path"),
    ],
)
def test_backup_receipt_missing_or_invalid_fields_fail_closed(
    tmp_path: Path, mutation: dict, message: str
):
    persist_dir, _backup_path = _seed_paths(tmp_path)
    remove = mutation.get("remove")
    overrides = {key: value for key, value in mutation.items() if key != "remove"}
    receipt = _write_receipt(tmp_path / "receipt.json", overrides=overrides)
    if remove:
        payload = json.loads(receipt.read_text())
        del payload[remove]
        receipt.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        maintenance.run_maintenance(
            operation_id="receipt-op",
            persist_dir=persist_dir,
            backup_receipt_path=receipt,
            max_candidates=57,
            max_documents=2006,
        )


def test_backup_receipt_expired_or_operation_mismatch_fails_closed(tmp_path: Path):
    persist_dir, _backup_path = _seed_paths(tmp_path)
    expired = _write_receipt(
        tmp_path / "expired.json",
        generated_at=datetime.now(UTC) - timedelta(hours=25),
    )
    with pytest.raises(ValueError, match="expired"):
        maintenance.run_maintenance(
            operation_id="receipt-op",
            persist_dir=persist_dir,
            backup_receipt_path=expired,
            max_candidates=57,
            max_documents=2006,
        )

    mismatch = _write_receipt(tmp_path / "mismatch.json", operation_id="different-op")
    with pytest.raises(ValueError, match="operation id mismatch"):
        maintenance.run_maintenance(
            operation_id="receipt-op",
            persist_dir=persist_dir,
            backup_receipt_path=mismatch,
            max_candidates=57,
            max_documents=2006,
        )


def test_fresh_collection_count_above_receipt_snapshot_requires_new_backup(tmp_path: Path):
    persist_dir, _backup_path = _seed_paths(tmp_path)
    receipt = _write_receipt(
        tmp_path / "receipt.json",
        overrides={"collection_count": 180},
    )
    calls: list[list[str]] = []
    plan = {
        "candidate_count": 0,
        "indexed_project_count": 181,
        "candidates": [],
        "plan_sha256": "abc",
    }
    with pytest.raises(RuntimeError, match="exceeds the offsite backup snapshot"):
        maintenance.run_maintenance(
            operation_id="receipt-op",
            persist_dir=persist_dir,
            backup_receipt_path=receipt,
            max_candidates=57,
            max_documents=2006,
            runner=_runner_with_plan(plan, calls),
            chroma_executable=Path("/runtime/bin/chroma"),
        )
    assert len(calls) == 1


def test_backup_proof_modes_are_mutually_exclusive(tmp_path: Path):
    persist_dir, backup_path = _seed_paths(tmp_path)
    receipt = _write_receipt(tmp_path / "receipt.json")
    with pytest.raises(ValueError, match="exactly one"):
        maintenance.run_maintenance(
            operation_id="receipt-op",
            persist_dir=persist_dir,
            backup_path=backup_path,
            backup_receipt_path=receipt,
            max_candidates=57,
            max_documents=2006,
        )
