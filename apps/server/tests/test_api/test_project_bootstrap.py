"""
Integration tests for GET /api/v1/projects/{project_id}/bootstrap.

Covers:
- Authentication & ownership isolation (401/403/404, superuser access)
- Aggregated response shape parity (project, file tree without bodies, initial_file, chat_session, recent_messages)
- Explicit vs default initial_file selection (most recently updated editable file, empty project -> null)
- Invalid/deleted/folder/cross-project file_id fallback
- chat_limit pagination, chronological ordering, and has_more_messages accuracy
- client_tool_calls and client_message_metadata slimming parity with /chat/session/{project_id}/recent
- Read-only chat_session behavior (does not create a ChatSession when none exists)
"""

import json
import sys
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password


async def _create_and_login(
    client: AsyncClient,
    db_session: Session,
    *,
    username: str,
    is_superuser: bool = False,
) -> tuple[User, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    login_response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert login_response.status_code == 200
    return user, login_response.json()["access_token"]


@pytest.mark.integration
async def test_bootstrap_requires_authentication(client: AsyncClient):
    response = await client.get("/api/v1/projects/non-existent/bootstrap")
    assert response.status_code == 401


@pytest.mark.integration
async def test_bootstrap_enforces_ownership_and_not_found(
    client: AsyncClient,
    db_session: Session,
):
    owner, owner_token = await _create_and_login(client, db_session, username="boot_owner")
    _, other_token = await _create_and_login(client, db_session, username="boot_other")
    _, admin_token = await _create_and_login(
        client, db_session, username="boot_admin", is_superuser=True
    )

    project = Project(name="Owner Novel", owner_id=owner.id, project_type="novel")
    deleted_project = Project(
        name="Deleted Novel",
        owner_id=owner.id,
        project_type="novel",
        is_deleted=True,
    )
    db_session.add(project)
    db_session.add(deleted_project)
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(deleted_project)

    # Another user gets 403
    forbidden = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert forbidden.status_code == 403

    # Missing project gets 404
    missing = await client.get(
        "/api/v1/projects/does-not-exist/bootstrap",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert missing.status_code == 404

    # Soft-deleted project gets 404
    deleted = await client.get(
        f"/api/v1/projects/{deleted_project.id}/bootstrap",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert deleted.status_code == 404

    # Superuser can still inspect owner's project
    admin_res = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert admin_res.status_code == 200
    assert admin_res.json()["project"]["id"] == project.id


@pytest.mark.integration
async def test_bootstrap_returns_aggregated_payload_without_file_bodies_in_tree(
    client: AsyncClient,
    db_session: Session,
):
    owner, token = await _create_and_login(client, db_session, username="boot_full")
    project = Project(name="Aggregate Novel", owner_id=owner.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    now = utcnow()
    folder = File(
        project_id=project.id,
        title="正文",
        file_type="folder",
        content="",
        order=1,
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
    )
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    older_draft = File(
        project_id=project.id,
        parent_id=folder.id,
        title="第一章",
        file_type="draft",
        content="第一章秘密正文内容，不应出现在文件树里。",
        order=1,
        created_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=5),
    )
    newer_draft = File(
        project_id=project.id,
        parent_id=folder.id,
        title="第二章",
        file_type="draft",
        content="第二章最新正文内容。",
        order=2,
        created_at=now - timedelta(minutes=2),
        updated_at=now - timedelta(minutes=1),
    )
    deleted_draft = File(
        project_id=project.id,
        parent_id=folder.id,
        title="已删章节",
        file_type="draft",
        content="已删除内容",
        is_deleted=True,
        order=3,
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([older_draft, newer_draft, deleted_draft])
    db_session.commit()
    db_session.refresh(older_draft)
    db_session.refresh(newer_draft)

    # Explicit file_id chooses older_draft over newer_draft
    res = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        params={"file_id": older_draft.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()

    # Project matches GET /projects/{id}
    project_res = await client.get(
        f"/api/v1/projects/{project.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert body["project"] == project_res.json()

    # File tree matches GET /projects/{id}/file-tree and omits bodies + deleted files
    tree_res = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert body["files"] == tree_res.json()["tree"]
    assert len(body["files"]) == 1
    children = body["files"][0]["children"]
    assert [node["id"] for node in children] == [older_draft.id, newer_draft.id]
    assert all(node["content"] == "" for node in children)

    # Initial file has full content and updated_at token
    assert body["initial_file"]["id"] == older_draft.id
    assert body["initial_file"]["content"] == older_draft.content
    assert body["initial_file"]["updated_at"]

    # No chat session exists yet: read-only, does not auto-create one
    assert body["chat_session"] is None
    assert body["recent_messages"] == []
    assert body["has_more_messages"] is False
    sessions_in_db = db_session.exec(
        select(ChatSession).where(ChatSession.project_id == project.id)
    ).all()
    assert sessions_in_db == []


@pytest.mark.integration
async def test_bootstrap_initial_file_fallbacks_and_empty_project(
    client: AsyncClient,
    db_session: Session,
):
    owner, token = await _create_and_login(client, db_session, username="boot_fallback")
    other_user, _ = await _create_and_login(client, db_session, username="boot_foreign")

    project = Project(name="Main Project", owner_id=owner.id, project_type="novel")
    empty_project = Project(name="Folders Only", owner_id=owner.id, project_type="novel")
    foreign_project = Project(name="Foreign Project", owner_id=other_user.id, project_type="novel")
    db_session.add_all([project, empty_project, foreign_project])
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(empty_project)
    db_session.refresh(foreign_project)

    now = utcnow()
    folder = File(
        project_id=project.id,
        title="正文",
        file_type="folder",
        content="",
        order=1,
        updated_at=now,
    )
    old_file = File(
        project_id=project.id,
        title="旧章",
        file_type="draft",
        content="旧正文",
        order=1,
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )
    latest_file = File(
        project_id=project.id,
        title="最新章",
        file_type="draft",
        content="最新正文",
        order=2,
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(minutes=5),
    )
    deleted_file = File(
        project_id=project.id,
        title="已删章",
        file_type="draft",
        content="已删",
        is_deleted=True,
        updated_at=now,
    )
    foreign_file = File(
        project_id=foreign_project.id,
        title="别人项目的文件",
        file_type="draft",
        content="绝密跨项目正文",
        updated_at=now,
    )
    empty_folder = File(
        project_id=empty_project.id,
        title="空目录",
        file_type="folder",
        content="",
        updated_at=now,
    )
    db_session.add_all([folder, old_file, latest_file, deleted_file, foreign_file, empty_folder])
    db_session.commit()
    for item in (folder, latest_file, deleted_file, foreign_file):
        db_session.refresh(item)

    # 1) Omitted file_id -> most recently updated editable file
    res_default = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_default.status_code == 200
    assert res_default.json()["initial_file"]["id"] == latest_file.id

    # 2) Folder file_id -> falls back to latest editable file
    res_folder = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        params={"file_id": folder.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_folder.status_code == 200
    assert res_folder.json()["initial_file"]["id"] == latest_file.id

    # 3) Deleted file_id -> falls back to latest editable file
    res_deleted = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        params={"file_id": deleted_file.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_deleted.status_code == 200
    assert res_deleted.json()["initial_file"]["id"] == latest_file.id

    # 4) Foreign project's file_id -> never leaks foreign file, falls back to project's latest file
    res_foreign = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        params={"file_id": foreign_file.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_foreign.status_code == 200
    assert res_foreign.json()["initial_file"]["id"] == latest_file.id
    assert "绝密跨项目正文" not in json.dumps(res_foreign.json(), ensure_ascii=False)

    # 5) Project with only folders -> initial_file is null
    res_empty = await client.get(
        f"/api/v1/projects/{empty_project.id}/bootstrap",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_empty.status_code == 200
    assert res_empty.json()["initial_file"] is None


@pytest.mark.integration
async def test_bootstrap_chat_limit_has_more_and_client_slimming(
    client: AsyncClient,
    db_session: Session,
):
    owner, token = await _create_and_login(client, db_session, username="boot_chat")
    project = Project(name="Chat Project", owner_id=owner.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chat_session = ChatSession(
        user_id=owner.id,
        project_id=project.id,
        title="AI 助手对话",
        is_active=True,
        message_count=18,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    base_time = utcnow() - timedelta(minutes=30)
    heavy_tool_calls = json.dumps(
        [
            {
                "id": "call-1",
                "name": "query_files",
                "arguments": {"file_type": "draft"},
                "status": "success",
                "result": {
                    "files": [
                        {
                            "id": "f-1",
                            "title": "第一章",
                            "file_type": "draft",
                            "content": "超长正文载荷" * 500,
                        }
                    ],
                    "total": 1,
                },
            }
        ],
        ensure_ascii=False,
    )
    heavy_metadata = json.dumps(
        {
            "compaction": {
                "summary": "已汇总前文要点",
                "kept_author_messages": ["作者原话1" * 200, "作者原话2" * 200],
            }
        },
        ensure_ascii=False,
    )

    for index in range(18):
        msg = ChatMessage(
            session_id=chat_session.id,
            role="assistant" if index % 2 == 1 else "user",
            content=f"message-{index}",
            tool_calls=heavy_tool_calls if index == 17 else None,
            message_metadata=heavy_metadata if index == 17 else None,
            created_at=base_time + timedelta(seconds=index),
        )
        db_session.add(msg)
    db_session.commit()

    # Default chat_limit=15 returns newest 15 in chronological order and has_more_messages=True
    res_default = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_default.status_code == 200
    body = res_default.json()
    assert body["chat_session"]["id"] == chat_session.id
    assert body["chat_session"]["message_count"] == 18
    assert body["has_more_messages"] is True
    assert [m["content"] for m in body["recent_messages"]] == [f"message-{i}" for i in range(3, 18)]

    # Exact slimming parity with GET /api/v1/chat/session/{project_id}/recent
    recent_res = await client.get(
        f"/api/v1/chat/session/{project.id}/recent",
        params={"limit": 15},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert recent_res.status_code == 200
    assert body["recent_messages"] == recent_res.json()

    last_msg = body["recent_messages"][-1]
    assert "超长正文载荷" not in (last_msg["tool_calls"] or "")
    assert "kept_author_messages" not in (last_msg["metadata"] or "")
    assert "已汇总前文要点" in (last_msg["metadata"] or "")

    # chat_limit=20 covers all 18 messages -> has_more_messages=False
    res_all = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        params={"chat_limit": 20},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_all.status_code == 200
    assert len(res_all.json()["recent_messages"]) == 18
    assert res_all.json()["has_more_messages"] is False

    # Out-of-range chat_limit rejected with 422
    res_zero = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        params={"chat_limit": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_zero.status_code == 422
    res_over = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        params={"chat_limit": 51},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_over.status_code == 422


@pytest.mark.integration
async def test_bootstrap_serializes_deeply_nested_file_tree_without_recursion_error(
    client: AsyncClient,
    db_session: Session,
):
    owner, token = await _create_and_login(client, db_session, username="boot_deep_tree")
    project = Project(name="Deep Tree Project", owner_id=owner.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    depth = 1200
    now = utcnow()
    ids = [f"deep-node-{index:04d}" for index in range(depth)]
    rows = [
        {
            "id": file_id,
            "project_id": project.id,
            "title": f"Folder {index}",
            "content": f"body-{index}",
            "file_type": "draft" if index == depth - 1 else "folder",
            "parent_id": ids[index - 1] if index else None,
            "order": 0,
            "file_metadata": None,
            "created_at": now,
            "updated_at": now,
            "is_deleted": False,
            "deleted_at": None,
        }
        for index, file_id in enumerate(ids)
    ]
    db_session.execute(insert(File), rows)
    db_session.commit()

    original_limit = sys.getrecursionlimit()
    response = await client.get(
        f"/api/v1/projects/{project.id}/bootstrap",
        params={"file_id": ids[-1]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sys.getrecursionlimit() == original_limit
    assert response.status_code == 200, response.text

    try:
        sys.setrecursionlimit(max(original_limit, depth * 4 + 1000))
        body = response.json()
    finally:
        sys.setrecursionlimit(original_limit)

    assert body["project"]["id"] == project.id
    assert body["initial_file"]["id"] == ids[-1]
    assert body["initial_file"]["content"] == f"body-{depth - 1}"

    # Iterative traversal verifies every node in the 1200-deep chain survived with empty tree bodies.
    visited_ids: list[str] = []
    stack = list(reversed(body["files"]))
    while stack:
        node = stack.pop()
        visited_ids.append(node["id"])
        assert node["content"] == ""
        stack.extend(reversed(node["children"]))
    assert visited_ids == ids
