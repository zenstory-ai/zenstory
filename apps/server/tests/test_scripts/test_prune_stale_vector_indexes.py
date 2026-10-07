"""Safety contract for stale Chroma project-index cleanup."""

import importlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from sqlmodel import Session

from models import ChatMessage, ChatSession, File, LLMUsageEvent, Project, User

with mock.patch.dict(os.environ):
    cleanup = importlib.import_module("scripts.prune_stale_vector_indexes")
    maintenance = importlib.import_module("scripts.run_vector_maintenance")


def _make_chroma_catalog(path: Path, project_counts: dict[str, int]) -> None:
    path.mkdir()
    connection = sqlite3.connect(path / "chroma.sqlite3")
    connection.executescript(
        """
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE segments (id TEXT PRIMARY KEY, collection TEXT NOT NULL);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT NOT NULL);
        """
    )
    for index, (project_id, count) in enumerate(project_counts.items()):
        collection_id = f"c{index}"
        segment_id = f"s{index}"
        connection.execute("INSERT INTO collections VALUES (?, ?)", (collection_id, f"zenstory_project_{project_id}"))
        connection.execute("INSERT INTO segments VALUES (?, ?)", (segment_id, collection_id))
        connection.executemany(
            "INSERT INTO embeddings(segment_id) VALUES (?)",
            [(segment_id,)] * count,
        )
    connection.execute("INSERT INTO collections VALUES ('foreign', 'unrelated_collection')")
    connection.commit()
    connection.close()


def _project(db_session: Session, project_id: str, owner: User | None, updated_at: datetime) -> Project:
    project = Project(
        id=project_id,
        name=project_id,
        owner_id=owner.id if owner else None,
        created_at=updated_at,
        updated_at=updated_at,
    )
    db_session.add(project)
    db_session.commit()
    return project


@pytest.mark.integration
def test_dry_run_requires_six_calendar_months_across_all_activity(db_session: Session, tmp_path: Path):
    now = datetime(2026, 10, 31, 12, 0)
    owner = User(username="vector-owner", email="vector-owner@example.com", hashed_password="x")
    db_session.add(owner)
    db_session.commit()

    stale = _project(db_session, "stale", owner, datetime(2026, 4, 30, 12, 0))
    recent_file = _project(db_session, "recent-file", owner, datetime(2025, 1, 1))
    recent_chat = _project(db_session, "recent-chat", owner, datetime(2025, 1, 1))
    recent_usage = _project(db_session, "recent-usage", owner, datetime(2025, 1, 1))
    unknown_owner = _project(db_session, "unknown-owner", None, datetime(2025, 1, 1))

    db_session.add(File(project_id=recent_file.id, title="x", updated_at=now - timedelta(days=1)))
    chat = ChatSession(
        user_id=owner.id,
        project_id=recent_chat.id,
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )
    db_session.add(chat)
    db_session.commit()
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="active", created_at=now - timedelta(days=2)))
    db_session.add(
        LLMUsageEvent(
            user_id=owner.id,
            project_id=recent_usage.id,
            source="agent",
            model="m",
            price_band="peak",
            pricing_version="v",
            occurred_at=now - timedelta(days=3),
        )
    )
    db_session.commit()

    persist_dir = tmp_path / "chroma"
    _make_chroma_catalog(
        persist_dir,
        {stale.id: 3, recent_file.id: 2, recent_chat.id: 2, recent_usage.id: 1, unknown_owner.id: 4, "orphan": 9},
    )
    plan = cleanup.build_plan(db_session, persist_dir, now=now, inactive_months=6)

    assert cleanup._parse_iso(plan["cutoff"]) == datetime(2026, 4, 30, 12, 0)
    assert [item["project_id"] for item in plan["candidates"]] == [stale.id]
    assert plan["candidates"][0]["document_count"] == 3
    assert plan["excluded_unknown_or_unowned_count"] == 2
    assert plan["plan_sha256"] == cleanup.plan_sha256(plan)


@pytest.mark.integration
def test_apply_rechecks_activity_and_refuses_changed_project(db_session: Session, tmp_path: Path, monkeypatch):
    now = datetime(2026, 10, 7, 12, 0)
    owner = User(username="vector-race", email="vector-race@example.com", hashed_password="x")
    db_session.add(owner)
    db_session.commit()
    project = _project(db_session, "race", owner, datetime(2026, 1, 1))
    persist_dir = tmp_path / "chroma"
    _make_chroma_catalog(persist_dir, {project.id: 1})
    plan = cleanup.build_plan(db_session, persist_dir, now=now, inactive_months=6)

    project.updated_at = now
    db_session.add(project)
    db_session.commit()
    monkeypatch.setenv("VECTOR_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("ASYNC_VECTOR_INDEX_ENABLED", "false")

    with pytest.raises(ValueError, match="activity changed"):
        cleanup.apply_plan(
            db_session,
            plan,
            confirmation=plan["plan_sha256"],
            now=now + timedelta(minutes=1),
            application_stopped=True,
            client_factory=lambda _path: pytest.fail("must not open Chroma after failed preflight"),
        )


@pytest.mark.integration
def test_apply_deletes_only_planned_collection_and_never_project_content(
    db_session: Session, tmp_path: Path, monkeypatch
):
    now = datetime(2026, 10, 7, 12, 0)
    owner = User(username="vector-apply", email="vector-apply@example.com", hashed_password="x")
    db_session.add(owner)
    db_session.commit()
    stale = _project(db_session, "apply-stale", owner, datetime(2026, 1, 1))
    active = _project(db_session, "apply-active", owner, now)
    source_file = File(project_id=stale.id, title="manuscript", content="must remain")
    db_session.add(source_file)
    db_session.commit()
    # Keep the file old enough to remain eligible.
    source_file.created_at = datetime(2026, 1, 1)
    source_file.updated_at = datetime(2026, 1, 1)
    db_session.add(source_file)
    db_session.commit()

    persist_dir = tmp_path / "chroma"
    _make_chroma_catalog(persist_dir, {stale.id: 2, active.id: 5})
    plan = cleanup.build_plan(db_session, persist_dir, now=now, inactive_months=6)
    deleted: list[str] = []

    class Client:
        def delete_collection(self, name: str) -> None:
            deleted.append(name)

    monkeypatch.setenv("VECTOR_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("ASYNC_VECTOR_INDEX_ENABLED", "false")
    count = cleanup.apply_plan(
        db_session,
        plan,
        confirmation=plan["plan_sha256"],
        now=now + timedelta(minutes=1),
        application_stopped=True,
        client_factory=lambda _path: Client(),
    )

    assert count == 1
    assert deleted == [f"zenstory_project_{stale.id}"]
    db_session.expire_all()
    assert db_session.get(Project, stale.id) is not None
    assert db_session.get(File, source_file.id).content == "must remain"


def test_apply_requires_paused_vector_writes(monkeypatch):
    monkeypatch.delenv("VECTOR_EMBEDDINGS_ENABLED", raising=False)
    monkeypatch.delenv("ASYNC_VECTOR_INDEX_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="VECTOR_EMBEDDINGS_ENABLED=false"):
        cleanup.apply_plan(
            mock.Mock(),
            {"persist_dir": "/does/not/matter"},
            confirmation="x",
            now=datetime(2026, 10, 7),
            application_stopped=True,
        )


def test_apply_requires_application_stopped():
    with pytest.raises(RuntimeError, match="application must be stopped"):
        cleanup.apply_plan(
            mock.Mock(),
            {"persist_dir": "/does/not/matter"},
            confirmation="x",
            now=datetime(2026, 10, 7),
            application_stopped=False,
        )


def test_plan_file_is_private_and_tampering_changes_checksum(tmp_path: Path):
    payload = {"schema_version": 1, "candidates": [], "plan_sha256": "placeholder"}
    payload["plan_sha256"] = cleanup.plan_sha256(payload)
    path = tmp_path / "plan.json"
    cleanup.write_plan(path, payload)
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = json.loads(path.read_text())
    loaded["candidates"].append({"project_id": "injected"})
    assert loaded["plan_sha256"] != cleanup.plan_sha256(loaded)


@pytest.mark.integration
def test_real_chroma_cleanup_and_vacuum_preserve_colocated_files_and_active_data(
    db_session: Session, tmp_path: Path, monkeypatch
):
    import chromadb

    assert chromadb.__version__ == "1.5.9"

    now = datetime(2026, 10, 7, 12, 0)
    owner = User(username="vector-real", email="vector-real@example.com", hashed_password="x")
    db_session.add(owner)
    db_session.commit()
    stale = _project(db_session, "real-stale", owner, datetime(2026, 1, 1))
    active = _project(db_session, "real-active", owner, now)
    source_file = File(
        project_id=stale.id,
        title="source",
        content="source survives vector eviction",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    db_session.add(source_file)
    db_session.commit()

    persist_dir = tmp_path / "real-chroma"
    feedback_file = persist_dir / "feedback" / "preserve.png"
    material_file = persist_dir / "material_uploads" / "preserve.txt"
    feedback_bytes = b"\x89PNG\r\n\x1a\nfeedback-sentinel"
    material_bytes = b"material-upload-sentinel\x00\xff"
    feedback_file.parent.mkdir(parents=True)
    material_file.parent.mkdir(parents=True)
    feedback_file.write_bytes(feedback_bytes)
    material_file.write_bytes(material_bytes)
    client = chromadb.PersistentClient(path=str(persist_dir))
    stale_collection = client.create_collection(f"zenstory_project_{stale.id}")
    stale_collection.add(ids=["s1"], embeddings=[[1.0, 0.0]], documents=["stale vector copy"])
    active_collection = client.create_collection(f"zenstory_project_{active.id}")
    active_collection.add(ids=["a1"], embeddings=[[0.0, 1.0]], documents=["active vector remains"])
    chromadb.api.client.SharedSystemClient.clear_system_cache()
    stale_segment_ids = maintenance._read_hnsw_segment_ids(
        persist_dir / "chroma.sqlite3", [f"zenstory_project_{stale.id}"]
    )
    active_segment_ids = maintenance._read_hnsw_segment_ids(
        persist_dir / "chroma.sqlite3", [f"zenstory_project_{active.id}"]
    )
    assert len(stale_segment_ids) == len(active_segment_ids) == 1
    stale_segment_dir = persist_dir / stale_segment_ids[0]
    active_segment_dir = persist_dir / active_segment_ids[0]
    assert stale_segment_dir.is_dir()
    assert active_segment_dir.is_dir()

    plan = cleanup.build_plan(db_session, persist_dir, now=now, inactive_months=6)
    monkeypatch.setenv("VECTOR_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("ASYNC_VECTOR_INDEX_ENABLED", "false")
    assert cleanup.apply_plan(
        db_session,
        plan,
        confirmation=plan["plan_sha256"],
        now=now + timedelta(minutes=1),
        application_stopped=True,
    ) == 1

    chromadb.api.client.SharedSystemClient.clear_system_cache()
    released_dirs, released_bytes = maintenance._delete_orphaned_hnsw_segments(
        persist_dir, stale_segment_ids
    )
    assert released_dirs == 1
    assert released_bytes > 0
    assert not stale_segment_dir.exists()
    assert active_segment_dir.is_dir()
    chroma_cli = Path(sys.executable).with_name("chroma")
    subprocess.run(
        [
            str(chroma_cli),
            "vacuum",
            "--path",
            str(persist_dir),
            "--force",
            "--timeout",
            "60",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert feedback_file.read_bytes() == feedback_bytes
    assert material_file.read_bytes() == material_bytes
    chromadb.api.client.SharedSystemClient.clear_system_cache()
    verify = chromadb.PersistentClient(path=str(persist_dir))
    assert [collection.name for collection in verify.list_collections()] == [
        f"zenstory_project_{active.id}"
    ]
    assert verify.get_collection(f"zenstory_project_{active.id}").get()["documents"] == [
        "active vector remains"
    ]
    db_session.expire_all()
    assert db_session.get(File, source_file.id).content == "source survives vector eviction"
    with sqlite3.connect(persist_dir / "chroma.sqlite3") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
