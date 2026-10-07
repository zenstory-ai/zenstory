"""
Tests for Snapshot API endpoints.

Focuses on rollback behavior for project-level and file-level snapshots.
"""

import pytest
from httpx import AsyncClient

from models import File, User


async def _create_user_and_headers(client: AsyncClient, db_session, username: str) -> tuple[User, dict[str, str]]:
    from services.core.auth_service import hash_password

    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    login_response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return user, {"Authorization": f"Bearer {token}"}


async def _create_project(client: AsyncClient, headers: dict[str, str], name: str = "Snapshot Project") -> dict:
    response = await client.post(
        "/api/v1/projects",
        json={"name": name},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


async def _create_file(
    client: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    title: str,
    content: str,
    file_type: str = "draft",
    parent_id: str | None = None,
) -> dict:
    payload = {
        "title": title,
        "content": content,
        "file_type": file_type,
    }
    if parent_id is not None:
        payload["parent_id"] = parent_id

    response = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


async def _create_snapshot(
    client: AsyncClient,
    headers: dict[str, str],
    project_id: str,
    description: str,
    file_id: str | None = None,
    snapshot_type: str = "manual",
) -> dict:
    payload: dict[str, str] = {
        "description": description,
        "snapshot_type": snapshot_type,
    }
    if file_id:
        payload["file_id"] = file_id

    response = await client.post(
        f"/api/v1/projects/{project_id}/snapshots",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.integration
async def test_create_snapshot_rejects_file_from_other_project(client: AsyncClient, db_session):
    """Creating a snapshot should reject file_id that belongs to another project."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_1")

    project_1 = await _create_project(client, headers, "P1")
    project_2 = await _create_project(client, headers, "P2")
    file_in_project_2 = await _create_file(
        client,
        headers,
        project_2["id"],
        "Other project file",
        "content",
    )

    response = await client.post(
        f"/api/v1/projects/{project_1['id']}/snapshots",
        json={
            "description": "invalid snapshot",
            "file_id": file_in_project_2["id"],
            "snapshot_type": "manual",
        },
        headers=headers,
    )

    assert response.status_code == 400


@pytest.mark.integration
async def test_project_snapshot_rollback_soft_deletes_extra_files(client: AsyncClient, db_session):
    """Project-level rollback should soft-delete files created after snapshot."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_2")
    project = await _create_project(client, headers, "Rollback Project")

    base_file = await _create_file(
        client,
        headers,
        project["id"],
        "Base Draft",
        "Base content",
    )

    snapshot_response = await client.post(
        f"/api/v1/projects/{project['id']}/snapshots",
        json={"description": "baseline", "snapshot_type": "manual"},
        headers=headers,
    )
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()

    extra_file = await _create_file(
        client,
        headers,
        project["id"],
        "Added Later",
        "Later content",
    )

    rollback_response = await client.post(
        f"/api/v1/snapshots/{snapshot['id']}/rollback",
        headers=headers,
    )
    assert rollback_response.status_code == 200
    rollback_data = rollback_response.json()
    assert rollback_data["restored"]["deleted_extra_files"] == 1
    assert rollback_data["restored"]["restore_versions"] == 0

    # Soft-deleted file should no longer be visible through read endpoint.
    deleted_get = await client.get(
        f"/api/v1/files/{extra_file['id']}",
        headers=headers,
    )
    assert deleted_get.status_code == 404

    # Verify baseline file still exists.
    base_get = await client.get(
        f"/api/v1/files/{base_file['id']}",
        headers=headers,
    )
    assert base_get.status_code == 200
    assert base_get.json()["title"] == "Base Draft"


@pytest.mark.integration
async def test_file_scoped_snapshot_rollback_keeps_unrelated_files(client: AsyncClient, db_session):
    """File-level rollback should not delete or modify unrelated project files."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_3")
    project = await _create_project(client, headers, "Scoped Rollback Project")

    target_file = await _create_file(
        client,
        headers,
        project["id"],
        "Target File",
        "Original target content",
    )
    unrelated_file = await _create_file(
        client,
        headers,
        project["id"],
        "Unrelated File",
        "Unrelated content",
    )

    snapshot_response = await client.post(
        f"/api/v1/projects/{project['id']}/snapshots",
        json={
            "description": "target-only snapshot",
            "file_id": target_file["id"],
            "snapshot_type": "manual",
        },
        headers=headers,
    )
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()

    # Change target file after snapshot.
    update_response = await client.put(
        f"/api/v1/files/{target_file['id']}",
        json={"content": "Changed target content"},
        headers=headers,
    )
    assert update_response.status_code == 200

    # Add another file after snapshot; file-scoped rollback should not delete it.
    extra_file = await _create_file(
        client,
        headers,
        project["id"],
        "Extra File",
        "Extra content",
    )

    rollback_response = await client.post(
        f"/api/v1/snapshots/{snapshot['id']}/rollback",
        headers=headers,
    )
    assert rollback_response.status_code == 200
    rollback_data = rollback_response.json()
    assert rollback_data["restored"]["files"] == 1
    assert rollback_data["restored"]["deleted_extra_files"] == 0
    assert rollback_data["restored"]["restore_versions"] == 1

    target_get = await client.get(
        f"/api/v1/files/{target_file['id']}",
        headers=headers,
    )
    assert target_get.status_code == 200
    assert target_get.json()["content"] == "Original target content"

    unrelated_get = await client.get(
        f"/api/v1/files/{unrelated_file['id']}",
        headers=headers,
    )
    assert unrelated_get.status_code == 200
    assert unrelated_get.json()["content"] == "Unrelated content"

    extra_get = await client.get(
        f"/api/v1/files/{extra_file['id']}",
        headers=headers,
    )
    assert extra_get.status_code == 200


@pytest.mark.integration
async def test_project_snapshot_rollback_restores_parent_and_undeletes_file(client: AsyncClient, db_session):
    """Project rollback should restore parent_id and undelete files from snapshot."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_4")
    project = await _create_project(client, headers, "Hierarchy Rollback Project")

    folder = await _create_file(
        client,
        headers,
        project["id"],
        "Chapter Folder",
        "",
        file_type="folder",
    )
    draft = await _create_file(
        client,
        headers,
        project["id"],
        "Draft in Folder",
        "Draft content",
        parent_id=folder["id"],
    )

    snapshot_response = await client.post(
        f"/api/v1/projects/{project['id']}/snapshots",
        json={"description": "hierarchy baseline", "snapshot_type": "manual"},
        headers=headers,
    )
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()

    # Break hierarchy and soft-delete file.
    move_response = await client.put(
        f"/api/v1/files/{draft['id']}",
        json={"parent_id": None},
        headers=headers,
    )
    assert move_response.status_code == 200

    delete_response = await client.delete(
        f"/api/v1/files/{draft['id']}",
        headers=headers,
    )
    assert delete_response.status_code == 200

    rollback_response = await client.post(
        f"/api/v1/snapshots/{snapshot['id']}/rollback",
        headers=headers,
    )
    assert rollback_response.status_code == 200
    rollback_data = rollback_response.json()
    assert rollback_data["restored"]["undeleted_files"] >= 1

    restored_get = await client.get(
        f"/api/v1/files/{draft['id']}",
        headers=headers,
    )
    assert restored_get.status_code == 200
    restored_file = restored_get.json()
    assert restored_file["parent_id"] == folder["id"]
    assert restored_file["content"] == "Draft content"

    # Database-level assertion to ensure it was undeleted instead of recreated as deleted.
    db_file = db_session.get(File, draft["id"])
    assert db_file is not None
    assert db_file.is_deleted is False


@pytest.mark.integration
async def test_get_snapshots_list_filter_and_pagination(client: AsyncClient, db_session):
    """List endpoint should support project list, file filter, and pagination."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_5")
    project = await _create_project(client, headers, "List Snapshot Project")

    file_a = await _create_file(client, headers, project["id"], "A", "A content")
    file_b = await _create_file(client, headers, project["id"], "B", "B content")

    await _create_snapshot(client, headers, project["id"], "Project snapshot")
    await _create_snapshot(client, headers, project["id"], "A snapshot", file_id=file_a["id"])
    await _create_snapshot(client, headers, project["id"], "B snapshot", file_id=file_b["id"])

    all_resp = await client.get(
        f"/api/v1/projects/{project['id']}/snapshots",
        headers=headers,
    )
    assert all_resp.status_code == 200
    all_items = all_resp.json()
    assert len(all_items) == 3

    file_filtered_resp = await client.get(
        f"/api/v1/projects/{project['id']}/snapshots",
        params={"file_id": file_a["id"]},
        headers=headers,
    )
    assert file_filtered_resp.status_code == 200
    file_filtered_items = file_filtered_resp.json()
    assert len(file_filtered_items) == 1
    assert file_filtered_items[0]["file_id"] == file_a["id"]

    paged_resp = await client.get(
        f"/api/v1/projects/{project['id']}/snapshots",
        params={"limit": 1, "offset": 1},
        headers=headers,
    )
    assert paged_resp.status_code == 200
    assert len(paged_resp.json()) == 1


@pytest.mark.integration
async def test_get_and_update_snapshot_description(client: AsyncClient, db_session):
    """Snapshot detail and description update endpoints should work end-to-end."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_6")
    project = await _create_project(client, headers, "Update Snapshot Project")
    file_data = await _create_file(client, headers, project["id"], "Draft", "Initial content")

    snapshot = await _create_snapshot(
        client,
        headers,
        project["id"],
        "Original description",
        file_id=file_data["id"],
    )

    get_resp = await client.get(
        f"/api/v1/snapshots/{snapshot['id']}",
        headers=headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["description"] == "Original description"

    update_resp = await client.put(
        f"/api/v1/snapshots/{snapshot['id']}",
        json={"description": "Updated description"},
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["description"] == "Updated description"

    get_again = await client.get(
        f"/api/v1/snapshots/{snapshot['id']}",
        headers=headers,
    )
    assert get_again.status_code == 200
    assert get_again.json()["description"] == "Updated description"


@pytest.mark.integration
async def test_snapshot_endpoints_not_found_cases(client: AsyncClient, db_session):
    """Snapshot detail/update/rollback should return 404 for nonexistent snapshot IDs."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_7")
    missing_id = "00000000-0000-0000-0000-000000000000"

    get_resp = await client.get(f"/api/v1/snapshots/{missing_id}", headers=headers)
    assert get_resp.status_code == 404

    update_resp = await client.put(
        f"/api/v1/snapshots/{missing_id}",
        json={"description": "no-op"},
        headers=headers,
    )
    assert update_resp.status_code == 404

    rollback_resp = await client.post(
        f"/api/v1/snapshots/{missing_id}/rollback",
        headers=headers,
    )
    assert rollback_resp.status_code == 404


@pytest.mark.integration
async def test_snapshot_access_forbidden_for_other_user(client: AsyncClient, db_session):
    """A user should not access another user's snapshot or snapshot list."""
    _owner, owner_headers = await _create_user_and_headers(client, db_session, "snap_owner_1")
    project = await _create_project(client, owner_headers, "Owner Project")
    await _create_file(client, owner_headers, project["id"], "Owner Draft", "owner content")
    snapshot = await _create_snapshot(
        client,
        owner_headers,
        project["id"],
        "Owner snapshot",
    )

    _other, other_headers = await _create_user_and_headers(client, db_session, "snap_guest_1")

    list_resp = await client.get(
        f"/api/v1/projects/{project['id']}/snapshots",
        headers=other_headers,
    )
    assert list_resp.status_code == 403

    detail_resp = await client.get(
        f"/api/v1/snapshots/{snapshot['id']}",
        headers=other_headers,
    )
    assert detail_resp.status_code == 403


@pytest.mark.integration
async def test_compare_snapshots_detects_modified_file(client: AsyncClient, db_session):
    """Compare endpoint should report modified files between snapshots."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_8")
    project = await _create_project(client, headers, "Compare Project")
    file_data = await _create_file(client, headers, project["id"], "Draft", "Version one")

    snapshot_1 = await _create_snapshot(client, headers, project["id"], "S1")

    update_resp = await client.put(
        f"/api/v1/files/{file_data['id']}",
        json={"content": "Version two updated"},
        headers=headers,
    )
    assert update_resp.status_code == 200

    snapshot_2 = await _create_snapshot(client, headers, project["id"], "S2")

    compare_resp = await client.get(
        f"/api/v1/snapshots/{snapshot_1['id']}/compare/{snapshot_2['id']}",
        headers=headers,
    )
    assert compare_resp.status_code == 200
    compare_data = compare_resp.json()
    assert compare_data["snapshot1"]["id"] in {snapshot_1["id"], snapshot_2["id"]}
    assert compare_data["snapshot2"]["id"] in {snapshot_1["id"], snapshot_2["id"]}
    assert len(compare_data["changes"]["modified"]) == 1
    assert compare_data["changes"]["modified"][0]["file_id"] == file_data["id"]


@pytest.mark.integration
async def test_compare_snapshots_detects_added_file(client: AsyncClient, db_session):
    """Compare endpoint should report newly added files as 'added'."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_10")
    project = await _create_project(client, headers, "Compare Added Project")
    await _create_file(client, headers, project["id"], "Base", "Base content")

    snapshot_1 = await _create_snapshot(client, headers, project["id"], "S1")

    added_file = await _create_file(
        client,
        headers,
        project["id"],
        "Added Draft",
        "Added content",
    )

    snapshot_2 = await _create_snapshot(client, headers, project["id"], "S2")

    compare_resp = await client.get(
        f"/api/v1/snapshots/{snapshot_1['id']}/compare/{snapshot_2['id']}",
        headers=headers,
    )
    assert compare_resp.status_code == 200
    compare_data = compare_resp.json()
    assert len(compare_data["changes"]["added"]) == 1
    assert compare_data["changes"]["added"][0]["file_id"] == added_file["id"]
    assert compare_data["changes"]["removed"] == []


@pytest.mark.integration
async def test_compare_snapshots_detects_removed_file(client: AsyncClient, db_session):
    """Compare endpoint should report soft-deleted files as 'removed'."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_11")
    project = await _create_project(client, headers, "Compare Removed Project")
    await _create_file(client, headers, project["id"], "Keep", "Keep content")
    removed_file = await _create_file(
        client,
        headers,
        project["id"],
        "To Remove",
        "Will be deleted",
    )

    snapshot_1 = await _create_snapshot(client, headers, project["id"], "S1")

    delete_resp = await client.delete(
        f"/api/v1/files/{removed_file['id']}",
        headers=headers,
    )
    assert delete_resp.status_code == 200

    snapshot_2 = await _create_snapshot(client, headers, project["id"], "S2")

    compare_resp = await client.get(
        f"/api/v1/snapshots/{snapshot_1['id']}/compare/{snapshot_2['id']}",
        headers=headers,
    )
    assert compare_resp.status_code == 200
    compare_data = compare_resp.json()
    assert len(compare_data["changes"]["removed"]) == 1
    assert compare_data["changes"]["removed"][0]["file_id"] == removed_file["id"]
    assert compare_data["changes"]["added"] == []


@pytest.mark.integration
async def test_compare_snapshots_not_found_returns_404(client: AsyncClient, db_session):
    """Compare endpoint should return 404 when one or both snapshots are missing."""
    _user, headers = await _create_user_and_headers(client, db_session, "snap_user_9")
    missing_1 = "00000000-0000-0000-0000-000000000000"
    missing_2 = "11111111-1111-1111-1111-111111111111"

    compare_resp = await client.get(
        f"/api/v1/snapshots/{missing_1}/compare/{missing_2}",
        headers=headers,
    )
    assert compare_resp.status_code == 404


@pytest.mark.integration
async def test_compare_snapshots_forbidden_for_other_user(client: AsyncClient, db_session):
    """Compare endpoint should reject access when snapshots belong to another user."""
    _owner, owner_headers = await _create_user_and_headers(client, db_session, "snap_owner_2")
    project = await _create_project(client, owner_headers, "Owner Compare Project")
    file_data = await _create_file(client, owner_headers, project["id"], "Draft", "Owner v1")
    snapshot_1 = await _create_snapshot(client, owner_headers, project["id"], "S1")

    update_resp = await client.put(
        f"/api/v1/files/{file_data['id']}",
        json={"content": "Owner v2"},
        headers=owner_headers,
    )
    assert update_resp.status_code == 200
    snapshot_2 = await _create_snapshot(client, owner_headers, project["id"], "S2")

    _other, other_headers = await _create_user_and_headers(client, db_session, "snap_guest_2")

    compare_resp = await client.get(
        f"/api/v1/snapshots/{snapshot_1['id']}/compare/{snapshot_2['id']}",
        headers=other_headers,
    )
    assert compare_resp.status_code == 403


@pytest.mark.integration
async def test_snapshot_rollback_reconciles_vector_index_and_stats_cache(
    client: AsyncClient, db_session, monkeypatch
):
    user, headers = await _create_user_and_headers(client, db_session, "snap_reconcile")
    project = await _create_project(client, headers, "Reconcile Project")
    file = await _create_file(client, headers, project["id"], "Draft", "Before")
    snapshot = await _create_snapshot(
        client, headers, project["id"], "before edit", file_id=file["id"]
    )
    update = await client.put(
        f"/api/v1/files/{file['id']}",
        json={"content": "After"},
        headers=headers,
    )
    assert update.status_code == 200

    indexed: list[dict] = []
    deleted: list[dict] = []
    cache_bumps: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "services.llama_index.schedule_index_upsert",
        lambda **kwargs: indexed.append(kwargs),
    )
    monkeypatch.setattr(
        "services.llama_index.schedule_index_delete",
        lambda **kwargs: deleted.append(kwargs),
    )
    monkeypatch.setattr(
        "services.infra.dashboard_cache.dashboard_cache.bump_project_version",
        lambda user_id, project_id: cache_bumps.append((user_id, project_id)),
    )

    response = await client.post(
        f"/api/v1/snapshots/{snapshot['id']}/rollback", headers=headers
    )

    assert response.status_code == 200, response.text
    assert len(indexed) == 1
    assert indexed[0]["entity_id"] == file["id"]
    assert indexed[0]["content"] == "Before"
    assert deleted == []
    assert cache_bumps == [(user.id, project["id"])]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": -1}, {"limit": 101}, {"offset": -1}])
async def test_snapshot_list_rejects_unbounded_or_invalid_paging(client, db_session, params):
    _, headers = await _create_user_and_headers(client, db_session, "snapshot_page_bounds")
    project = await _create_project(client, headers, "Snapshot page bounds")
    response = await client.get(f"/api/v1/projects/{project['id']}/snapshots", params=params, headers=headers)
    assert response.status_code == 422
