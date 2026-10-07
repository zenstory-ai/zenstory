"""Creation content must be recoverable before an Editor ever opens the file."""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models import File, FileVersion, Project, User
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.features.file_version_service import get_file_version_service


@pytest.fixture
async def creation_context(client, db_session, monkeypatch):
    # Indexing is outside the creation transaction and must not call providers.
    import services.llama_index as indexing

    monkeypatch.setattr(indexing, "schedule_index_upsert", lambda **kwargs: None)
    user = User(
        username="creation-baseline",
        email="creation-baseline@example.test",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    project = Project(name="Creation baseline", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    response = await client.post(
        "/api/auth/login",
        data={"username": user.username, "password": "password123"},
    )
    assert response.status_code == 200
    return user, project, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _versions(session, file_id):
    return session.exec(
        select(FileVersion)
        .where(FileVersion.file_id == file_id)
        .order_by(FileVersion.version_number)
    ).all()


def _assert_initial_baseline(session, file):
    versions = _versions(session, file.id)
    assert len(versions) == 1
    version = versions[0]
    assert version.version_number == 1
    assert version.is_base_version is True
    assert version.content == file.content
    assert version.project_id == file.project_id
    assert version.change_type == "create"
    assert version.change_source == "system"
    assert get_file_version_service().get_version_count(
        session, file.id, change_source="user"
    ) == 0


async def _create(client, project, headers, kind, content="Original content"):
    url = f"/api/v1/projects/{project.id}/files"
    if kind == "web":
        return await client.post(
            url,
            json={"title": "Original", "file_type": "draft", "content": content},
            headers=headers,
        )
    field = "file" if kind == "material" else "files"
    suffix = "upload" if kind == "material" else "upload-drafts"
    return await client.post(
        f"{url}/{suffix}",
        files={field: ("original.txt", content.encode("utf-8"), "text/plain")},
        headers=headers,
    )


@pytest.mark.parametrize("file_type", ["draft", "outline", "snippet"])
async def test_web_creation_preserves_original_before_non_editor_edit(
    client, db_session, creation_context, file_type
):
    _, project, headers = creation_context
    original = "Original creation\n原始内容。"
    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={"title": "Original", "file_type": file_type, "content": original},
        headers=headers,
    )
    assert response.status_code == 200
    file = db_session.get(File, response.json()["id"])
    _assert_initial_baseline(db_session, file)

    # A non-Editor writer can intentionally omit a subsequent version, but must
    # not destroy the creation baseline by becoming the first captured content.
    updated = await client.put(
        f"/api/v1/files/{file.id}",
        json={"content": "Later edit", "skip_version": True},
        headers=headers,
    )
    assert updated.status_code == 200
    assert _versions(db_session, file.id)[0].content == original
    assert get_file_version_service().get_content_at_version(db_session, file.id, 1) == original


@pytest.mark.parametrize(
    ("file_type", "content"), [("draft", ""), ("folder", ""), ("folder", "Ignored folder content")]
)
async def test_empty_and_folder_creation_have_no_initial_version(
    client, db_session, creation_context, file_type, content
):
    _, project, headers = creation_context
    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={"title": "Empty or folder", "file_type": file_type, "content": content},
        headers=headers,
    )
    assert response.status_code == 200
    file_id = response.json()["id"]
    assert _versions(db_session, file_id) == []
    if file_type == "draft":
        updated = await client.put(
            f"/api/v1/files/{file_id}", json={"content": "First streamed content"}, headers=headers
        )
        assert updated.status_code == 200
        versions = _versions(db_session, file_id)
        assert len(versions) == 1
        assert versions[0].version_number == 1
        assert versions[0].content == "First streamed content"


@pytest.mark.parametrize("kind", ["web", "material", "draft"])
async def test_structural_creation_baseline_is_not_blocked_by_zero_user_quota(
    client, db_session, creation_context, kind
):
    user, project, headers = creation_context
    now = utcnow()
    plan = SubscriptionPlan(
        name="zero-user-versions", display_name="Zero user versions",
        features={"file_versions_per_file": 0},
    )
    db_session.add(plan)
    db_session.flush()
    db_session.add(UserSubscription(
        user_id=user.id, plan_id=plan.id, status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
    ))
    db_session.commit()
    response = await _create(client, project, headers, kind)
    assert response.status_code == 200
    files = db_session.exec(
        select(File).where(File.project_id == project.id, File.file_type != "folder")
    ).all()
    assert len(files) == 1
    _assert_initial_baseline(db_session, files[0])
    assert get_file_version_service().check_user_version_quota(
        db_session, files[0].id, user.id
    ) == (False, 0, 0)


@pytest.mark.parametrize("kind", ["material", "draft"])
async def test_every_split_upload_file_gets_its_own_creation_baseline(
    client, db_session, creation_context, kind
):
    _, project, headers = creation_context
    content = "第一章 初始\n" + "原始正文。" * 2200 + "\n第二章 后续\n" + "后续内容。" * 2200
    response = await _create(client, project, headers, kind, content)
    assert response.status_code == 200
    files = db_session.exec(
        select(File).where(File.project_id == project.id, File.file_type != "folder")
    ).all()
    assert len(files) == 2
    for file in files:
        _assert_initial_baseline(db_session, file)
    folders = db_session.exec(
        select(File).where(File.project_id == project.id, File.file_type == "folder")
    ).all()
    assert all(_versions(db_session, folder.id) == [] for folder in folders)


@pytest.mark.parametrize("kind", ["web", "material", "draft"])
async def test_unexpected_baseline_failure_cannot_commit_creation(
    client, db_session, creation_context, monkeypatch, kind
):
    _, project, headers = creation_context
    service = get_file_version_service()
    failure = Mock(side_effect=RuntimeError("initial baseline unavailable"))
    monkeypatch.setattr(type(service), "create_version", failure)
    response = await _create(client, project, headers, kind)
    assert response.status_code == 500
    db_session.rollback()  # Match request session cleanup in production.
    failure.assert_called_once()
    with Session(db_session.get_bind()) as reader:
        assert reader.exec(select(File).where(
            File.project_id == project.id, File.file_type != "folder"
        )).all() == []
        assert reader.exec(select(FileVersion).where(FileVersion.project_id == project.id)).all() == []


@pytest.mark.parametrize("kind", ["material", "draft"])
async def test_second_split_baseline_failure_rolls_back_entire_content_batch(
    client, db_session, creation_context, monkeypatch, kind
):
    _, project, headers = creation_context
    service = get_file_version_service()
    original_create = type(service).create_version
    calls = []

    def fail_second(self, *args, **kwargs):
        calls.append(kwargs["file_id"])
        if len(calls) == 2:
            raise RuntimeError("second initial baseline unavailable")
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(type(service), "create_version", fail_second)
    content = "第一章 初始\n" + "原始正文。" * 2200 + "\n第二章 后续\n" + "后续内容。" * 2200
    response = await _create(client, project, headers, kind, content)
    assert response.status_code == 500
    db_session.rollback()
    assert len(calls) == 2
    with Session(db_session.get_bind()) as reader:
        assert reader.exec(select(File).where(
            File.project_id == project.id, File.file_type != "folder"
        )).all() == []
        assert reader.exec(select(FileVersion).where(FileVersion.project_id == project.id)).all() == []


@pytest.mark.parametrize("kind", ["material", "draft"])
async def test_new_upload_folder_participates_in_failed_content_transaction(
    client, db_session, creation_context, monkeypatch, kind
):
    _, project, headers = creation_context
    monkeypatch.setattr(type(get_file_version_service()), "create_version", Mock(side_effect=RuntimeError("initial history failed")))
    response = await _create(client, project, headers, kind)
    assert response.status_code == 500
    db_session.rollback()
    with Session(db_session.get_bind()) as reader:
        assert reader.exec(select(File).where(File.project_id == project.id)).all() == []


async def test_new_tool_root_folder_rolls_back_with_failed_creation(
    db_session, creation_context, monkeypatch
):
    from agent.tools.file_ops.crud import FileCRUD

    user, project, _ = creation_context
    def fail_normalization(*args, **kwargs):
        raise RuntimeError("injected normalization failure")

    monkeypatch.setattr(FileCRUD, "_normalize_parent_to_folder", fail_normalization)
    with pytest.raises(RuntimeError, match="injected normalization failure"):
        FileCRUD(db_session, user.id).create_file(
            project.id, "Draft", file_type="draft", parent_id=f"{project.id}-draft-folder"
        )
    db_session.rollback()
    with Session(db_session.get_bind()) as reader:
        assert reader.exec(select(File).where(File.project_id == project.id)).all() == []
