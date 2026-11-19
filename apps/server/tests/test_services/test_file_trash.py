"""Recoverable deletion batches preserve exact tree generations."""

import pytest
from httpx import AsyncClient

from models import File, Project, User
from services.file_trash import FileTrashConflictError, FileTrashError, FileTrashService

pytestmark = pytest.mark.unit


@pytest.fixture
def trash_tree(db_session):
    user = User(username="trash-owner", email="trash-owner@example.test", hashed_password="unused")
    project = Project(name="Trash test", owner_id=user.id)
    folder = File(project_id=project.id, title="卷一", file_type="folder")
    chapter = File(project_id=project.id, title="第一章", parent_id=folder.id, file_type="draft", content="正文")
    old_deleted = File(
        project_id=project.id,
        title="旧废稿",
        parent_id=folder.id,
        file_type="draft",
        content="旧稿",
        is_deleted=True,
    )
    db_session.add_all([user, project, folder, chapter, old_deleted])
    db_session.commit()
    return user, project, folder, chapter, old_deleted


def _service(db_session, tree):
    user, project, *_ = tree
    return FileTrashService(db_session, user_id=user.id, project_id=project.id)


def test_target_limit_applies_after_deduplication(db_session, trash_tree):
    _, _, _, chapter, _ = trash_tree
    service = _service(db_session, trash_tree)
    assert service.preview([chapter.id] * 101, recursive=False)["count"] == 1
    with pytest.raises(FileTrashError, match="100"):
        service.preview([f"file-{i}" for i in range(101)], recursive=False)


def test_folder_preview_forces_recursive_and_restore_exact_live_set(db_session, trash_tree):
    _, _, folder, chapter, old_deleted = trash_tree
    service = _service(db_session, trash_tree)

    preview = service.preview([folder.id], recursive=False)
    assert preview["recursive"] is True
    assert {row["id"] for row in preview["targets"]} == {folder.id, chapter.id}
    deleted = service.delete(
        [folder.id], recursive=False, source="file_menu", expected_target_hash=preview["target_hash"]
    )
    assert deleted["count"] == 2
    db_session.expire_all()
    assert db_session.get(File, folder.id).is_deleted is True
    assert db_session.get(File, chapter.id).is_deleted is True
    assert db_session.get(File, old_deleted.id).is_deleted is True

    restored = service.restore(deleted["batch_id"])
    assert restored["file_ids"] == sorted([folder.id, chapter.id])
    db_session.expire_all()
    assert db_session.get(File, folder.id).is_deleted is False
    assert db_session.get(File, chapter.id).is_deleted is False
    assert db_session.get(File, old_deleted.id).is_deleted is True


def test_confirmed_hash_rejects_any_target_state_change(db_session, trash_tree):
    _, _, _, chapter, _ = trash_tree
    service = _service(db_session, trash_tree)
    preview = service.preview([chapter.id], recursive=False)
    chapter.title = "作者刚改的标题"
    db_session.add(chapter)
    db_session.commit()

    with pytest.raises(FileTrashConflictError, match="重新确认"):
        service.delete(
            [chapter.id],
            recursive=False,
            source="agent_confirmed",
            expected_target_hash=preview["target_hash"],
        )
    db_session.refresh(chapter)
    assert chapter.is_deleted is False


def test_batch_write_failure_rolls_back_every_file(db_session, trash_tree, monkeypatch):
    _, _, folder, chapter, _ = trash_tree
    service = _service(db_session, trash_tree)

    def fail_commit():
        raise RuntimeError("synthetic batch write failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="batch write failure"):
        service.delete([folder.id], recursive=True, source="file_menu")
    db_session.expire_all()
    assert db_session.get(File, folder.id).is_deleted is False
    assert db_session.get(File, chapter.id).is_deleted is False


def test_restore_is_all_or_nothing_on_name_collision(db_session, trash_tree):
    _, project, folder, chapter, _ = trash_tree
    service = _service(db_session, trash_tree)
    deleted = service.delete([chapter.id], recursive=False, source="agent_direct")
    clash = File(
        project_id=project.id,
        title=chapter.title,
        parent_id=folder.id,
        file_type="draft",
        content="新稿",
    )
    db_session.add(clash)
    db_session.commit()

    with pytest.raises(FileTrashConflictError, match="同名"):
        service.restore(deleted["batch_id"])
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is True
    assert db_session.get(File, clash.id).is_deleted is False


def test_old_batch_cannot_restore_a_later_deletion_generation(db_session, trash_tree):
    _, _, _, chapter, _ = trash_tree
    service = _service(db_session, trash_tree)
    first = service.delete([chapter.id], recursive=False, source="agent_direct")
    assert service.restore(first["batch_id"])["already_restored"] is False
    second = service.delete([chapter.id], recursive=False, source="agent_direct")

    repeated = service.restore(first["batch_id"])
    assert repeated == {
        "batch_id": first["batch_id"],
        "file_ids": [],
        "restored": True,
        "already_restored": True,
    }
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is True
    assert service.restore(second["batch_id"])["already_restored"] is False


def test_list_only_exposes_unrestored_batches(db_session, trash_tree):
    _, _, folder, chapter, _ = trash_tree
    service = _service(db_session, trash_tree)
    first = service.delete([chapter.id], recursive=False, source="agent_direct")
    service.restore(first["batch_id"])
    second = service.delete([folder.id], recursive=True, source="file_menu")

    page = service.list_batches(limit=1, offset=0)
    assert page["total"] == 1
    assert page["next_offset"] is None
    assert page["batches"][0]["batch_id"] == second["batch_id"]


@pytest.mark.integration
async def test_trash_api_preview_confirm_list_and_restore(client: AsyncClient, db_session):
    from services.core.auth_service import hash_password

    user = User(
        username="trash-api",
        email="trash-api@example.test",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    project = Project(name="Trash API", owner_id=user.id)
    first = File(project_id=project.id, title="第一章", file_type="draft", content="一")
    second = File(project_id=project.id, title="第二章", file_type="draft", content="二")
    db_session.add_all([user, project, first, second])
    db_session.commit()
    login = await client.post(
        "/api/auth/login", data={"username": user.username, "password": "password123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    preview = await client.post(
        f"/api/v1/projects/{project.id}/files/deletion-preview",
        json={"ids": [first.id, second.id], "recursive": False},
        headers=headers,
    )
    assert preview.status_code == 200
    preview_data = preview.json()
    assert preview_data["count"] == 2
    deleted = await client.post(
        f"/api/v1/projects/{project.id}/files/delete-batch",
        json={
            "ids": [first.id, second.id],
            "recursive": False,
            "target_hash": preview_data["target_hash"],
        },
        headers=headers,
    )
    assert deleted.status_code == 200
    batch_id = deleted.json()["batch_id"]
    trash = await client.get(f"/api/v1/projects/{project.id}/files/trash", headers=headers)
    assert trash.status_code == 200
    assert trash.json()["batches"][0]["batch_id"] == batch_id
    restored = await client.post(
        f"/api/v1/projects/{project.id}/files/trash/{batch_id}/restore", headers=headers
    )
    assert restored.status_code == 200
    assert set(restored.json()["file_ids"]) == {first.id, second.id}
