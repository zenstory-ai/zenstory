import hashlib
import json
from pathlib import Path

from models.material_models import IngestionJob, Novel
from scripts.migrate_upload_storage import MigrationJournal, migrate_references, restore_references
from services.infra.upload_storage import StoredObject


class MemoryStorage:
    def __init__(self, *, corrupt_reads: bool = False):
        self.objects: dict[str, bytes] = {}
        self.corrupt_reads = corrupt_reads
        self.deleted: list[str] = []

    def put_material(self, *, owner_id, object_name, content, **_kwargs):
        reference = f"s3://test-bucket/material/{owner_id}/{object_name}"
        created = reference not in self.objects
        self.objects.setdefault(reference, content)
        return StoredObject(reference, len(content), hashlib.sha256(content).hexdigest(), created)

    def read_material_reference(self, reference, *, owner_id):
        assert f"/material/{owner_id}/" in reference
        value = self.objects[reference]
        return b"corrupt" if self.corrupt_reads else value

    def delete(self, reference, **_kwargs):
        self.deleted.append(reference)
        self.objects.pop(reference, None)


def _novel_with_job(db_session, source: Path, *, status: str = "completed"):
    novel = Novel(
        user_id="migration-user",
        title="Migration",
        source_meta=json.dumps({"file_path": str(source), "encoding": "utf-8"}),
    )
    db_session.add(novel)
    db_session.flush()
    job = IngestionJob(novel_id=novel.id, source_path=str(source), status=status)
    db_session.add(job)
    db_session.commit()
    return novel, job


def test_migration_dry_run_and_apply_resume_and_restore(db_session, tmp_path):
    material_root = tmp_path / "materials"
    feedback_root = tmp_path / "feedback"
    material_root.mkdir()
    feedback_root.mkdir()
    source = material_root / "legacy.txt"
    source.write_bytes("正文".encode())
    novel, job = _novel_with_job(db_session, source)
    storage = MemoryStorage()

    counts, entries = migrate_references(
        db_session,
        storage,
        material_root=material_root,
        feedback_root=feedback_root,
        apply=False,
    )
    assert counts.material_candidates == 1
    assert counts.migrated == 0
    assert entries == []
    assert storage.objects == {}

    counts, entries = migrate_references(
        db_session,
        storage,
        material_root=material_root,
        feedback_root=feedback_root,
        apply=True,
    )
    assert counts.migrated == 1
    assert source.exists()
    db_session.refresh(novel)
    db_session.refresh(job)
    assert json.loads(novel.source_meta)["file_path"] == entries[0]["new_reference"]
    assert job.source_path == entries[0]["new_reference"]

    resumed, resumed_entries = migrate_references(
        db_session,
        storage,
        material_root=material_root,
        feedback_root=feedback_root,
        apply=True,
    )
    assert resumed.migrated == 0
    assert resumed.skipped_remote == 1
    assert resumed_entries == []

    restored = restore_references(
        db_session,
        entries,
        material_root=material_root,
        feedback_root=feedback_root,
    )
    assert restored.migrated == 1
    db_session.refresh(novel)
    db_session.refresh(job)
    assert json.loads(novel.source_meta)["file_path"] == str(source)
    assert job.source_path == str(source)


def test_migration_skips_running_symlink_and_hash_failure(db_session, tmp_path):
    material_root = tmp_path / "materials"
    feedback_root = tmp_path / "feedback"
    material_root.mkdir()
    feedback_root.mkdir()
    running = material_root / "running.txt"
    running.write_text("running")
    _novel_with_job(db_session, running, status="processing")
    real = material_root / "real.txt"
    real.write_text("real")
    symlink = material_root / "link.txt"
    symlink.symlink_to(real)
    _novel_with_job(db_session, symlink)

    counts, entries = migrate_references(
        db_session,
        MemoryStorage(corrupt_reads=True),
        material_root=material_root,
        feedback_root=feedback_root,
        apply=True,
    )
    assert counts.skipped_running == 1
    assert counts.skipped_unsafe == 1
    assert counts.migrated == 0
    assert entries == []


def test_migration_hash_failure_keeps_old_reference(db_session, tmp_path):
    material_root = tmp_path / "materials"
    feedback_root = tmp_path / "feedback"
    material_root.mkdir()
    feedback_root.mkdir()
    source = material_root / "legacy.txt"
    source.write_text("original")
    novel, job = _novel_with_job(db_session, source)

    counts, entries = migrate_references(
        db_session,
        MemoryStorage(corrupt_reads=True),
        material_root=material_root,
        feedback_root=feedback_root,
        apply=True,
    )
    assert counts.failed == 1
    assert entries == []
    db_session.refresh(novel)
    db_session.refresh(job)
    assert json.loads(novel.source_meta)["file_path"] == str(source)
    assert job.source_path == str(source)


def test_write_ahead_manifest_survives_crash_after_db_commit(db_session, tmp_path):
    material_root = tmp_path / "materials"
    feedback_root = tmp_path / "feedback"
    material_root.mkdir()
    feedback_root.mkdir()
    source = material_root / "legacy.txt"
    source.write_text("recoverable")
    novel, _job = _novel_with_job(db_session, source)
    manifest_path = tmp_path / "migration.json"

    class _CrashAfterCommitJournal(MigrationJournal):
        def committed(self, entry):
            raise RuntimeError("simulated crash")

    counts, _entries = migrate_references(
        db_session,
        MemoryStorage(),
        material_root=material_root,
        feedback_root=feedback_root,
        apply=True,
        journal=_CrashAfterCommitJournal(manifest_path),
    )
    assert counts.failed == 1
    payload = json.loads(manifest_path.read_text())
    assert payload["entries"][0]["status"] == "prepared"
    assert manifest_path.stat().st_mode & 0o777 == 0o600
    db_session.refresh(novel)
    assert json.loads(novel.source_meta)["file_path"].startswith("s3://")

    resumed_journal = MigrationJournal(manifest_path)
    restored = restore_references(
        db_session,
        resumed_journal.entries,
        material_root=material_root,
        feedback_root=feedback_root,
    )
    assert restored.migrated == 1
    db_session.refresh(novel)
    assert json.loads(novel.source_meta)["file_path"] == str(source)


def test_documented_script_entry_point_works_without_pythonpath(tmp_path):
    import os
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[2] / "scripts" / "migrate_upload_storage.py"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--manifest" in result.stdout and "--restore" in result.stdout
