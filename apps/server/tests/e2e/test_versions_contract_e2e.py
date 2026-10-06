"""
Focused E2E contracts for file versions and project snapshots.

These tests intentionally cover the API contracts that web nightly suites rely on:
- file version list / compare / rollback
- snapshot list / compare with file filtering
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from .test_core_api_e2e import (
    _auth_headers,
    _create_file,
    _create_project,
    _create_user,
    _login,
)

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_file_versions_compare_and_rollback_contract(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="versions_contract")
    login_payload = await _login(client, identifier=user.email)
    access_token = login_payload["access_token"]

    project = await _create_project(client, access_token, name="Versions Contract Project")
    file_record = await _create_file(
        client,
        access_token,
        project_id=project["id"],
        title="Versioned Draft",
        content="seed",
    )
    file_id = file_record["id"]

    baseline = await client.get(f"/api/v1/files/{file_id}/versions/1/content", headers=_auth_headers(access_token))
    assert baseline.status_code == 200
    assert baseline.json()["content"] == "seed"

    version_one = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={
            "content": "Line 1\nLine 2",
            "change_type": "edit",
            "change_source": "user",
            "change_summary": "contract v1",
        },
        headers=_auth_headers(access_token),
    )
    assert version_one.status_code == 200
    assert version_one.json()["version_number"] == 2

    version_two = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={
            "content": "Line 1\nLine 2 updated\nLine 3",
            "change_type": "edit",
            "change_source": "user",
            "change_summary": "contract v2",
        },
        headers=_auth_headers(access_token),
    )
    assert version_two.status_code == 200
    assert version_two.json()["version_number"] == 3

    list_response = await client.get(
        f"/api/v1/files/{file_id}/versions?limit=10",
        headers=_auth_headers(access_token),
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["total"] == 3
    assert [item["version_number"] for item in list_payload["versions"]] == [3, 2, 1]
    assert list_payload["versions"][-1]["change_source"] == "system"

    compare_response = await client.get(
        f"/api/v1/files/{file_id}/versions/compare?v1=2&v2=3",
        headers=_auth_headers(access_token),
    )
    assert compare_response.status_code == 200
    compare_payload = compare_response.json()
    assert compare_payload["version1"]["number"] == 2
    assert compare_payload["version2"]["number"] == 3
    assert "Line 2 updated" in compare_payload["unified_diff"]

    rollback_response = await client.post(
        f"/api/v1/files/{file_id}/versions/2/rollback",
        headers=_auth_headers(access_token),
    )
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()
    assert rollback_payload["success"] is True
    assert rollback_payload["restored_version"] == 2
    assert rollback_payload["new_version_number"] == 4

    restored_content = await client.get(
        f"/api/v1/files/{file_id}/versions/4/content",
        headers=_auth_headers(access_token),
    )
    assert restored_content.status_code == 200
    assert restored_content.json()["content"] == "Line 1\nLine 2"


@pytest.mark.asyncio
async def test_snapshot_list_filter_and_compare_contract(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="snapshot_contract")
    login_payload = await _login(client, identifier=user.username)
    access_token = login_payload["access_token"]

    project = await _create_project(client, access_token, name="Snapshot Contract Project")
    await _create_file(
        client,
        access_token,
        project_id=project["id"],
        title="Snapshot A",
        content="A0",
    )
    file_b = await _create_file(
        client,
        access_token,
        project_id=project["id"],
        title="Snapshot B",
        content="B0",
    )

    snapshot_1 = await client.post(
        f"/api/v1/projects/{project['id']}/snapshots",
        json={"description": "baseline", "snapshot_type": "manual"},
        headers=_auth_headers(access_token),
    )
    assert snapshot_1.status_code == 200
    snapshot_1_payload = snapshot_1.json()

    update_b = await client.put(
        f"/api/v1/files/{file_b['id']}",
        json={"content": "B1"},
        headers=_auth_headers(access_token),
    )
    assert update_b.status_code == 200

    snapshot_2 = await client.post(
        f"/api/v1/projects/{project['id']}/snapshots",
        json={"description": "after-b", "snapshot_type": "manual", "file_id": file_b["id"]},
        headers=_auth_headers(access_token),
    )
    assert snapshot_2.status_code == 200
    snapshot_2_payload = snapshot_2.json()

    list_all = await client.get(
        f"/api/v1/projects/{project['id']}/snapshots",
        headers=_auth_headers(access_token),
    )
    assert list_all.status_code == 200
    assert len(list_all.json()) >= 2

    list_file_scoped = await client.get(
        f"/api/v1/projects/{project['id']}/snapshots?file_id={file_b['id']}&limit=10",
        headers=_auth_headers(access_token),
    )
    assert list_file_scoped.status_code == 200
    filtered_payload = list_file_scoped.json()
    assert len(filtered_payload) >= 1
    assert all(item.get("file_id") == file_b["id"] for item in filtered_payload)

    compare_response = await client.get(
        f"/api/v1/snapshots/{snapshot_1_payload['id']}/compare/{snapshot_2_payload['id']}",
        headers=_auth_headers(access_token),
    )
    assert compare_response.status_code == 200
    compare_payload = compare_response.json()
    assert compare_payload["snapshot1"]["id"] in {snapshot_1_payload["id"], snapshot_2_payload["id"]}
    assert compare_payload["snapshot2"]["id"] in {snapshot_1_payload["id"], snapshot_2_payload["id"]}
    assert "changes" in compare_payload
    assert len(compare_payload["changes"]["modified"]) >= 1
