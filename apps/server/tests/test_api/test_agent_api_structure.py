"""
Agent API: project structure, file order, move and version history.

- POST /api/v1/agent/projects creates the same default folders as the web app.
- `order` on file create/update; parent must be a folder in the same project.
- POST /api/v1/agent/files/{id}/move (folder / root, no cycles).
- GET  /api/v1/agent/files/{id}/versions, GET .../versions/{n}, POST .../versions/{n}/rollback.
All with the usual scope, allowlist and ownership isolation.
"""

import asyncio
import inspect
import time
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

import api.agent_api as agent_api
from config.datetime_utils import utcnow
from config.project_templates import get_folders_for_type
from core.error_handler import APIException
from models import (
    ACTIVATION_EVENT_PROJECT_CREATED,
    ActivationEvent,
    File,
    FileVersion,
    Project,
    SubscriptionPlan,
    UserSubscription,
)
from models.file_version import CHANGE_TYPE_CREATE, CHANGE_TYPE_RESTORE
from services.file_tree_rules import MAX_FILE_ORDER
from services.project_service import create_project_with_default_folders
from services.quota_service import quota_service
from services.skill_md_service import skill_md_service
from tests.test_api.test_agent_api import (
    create_test_api_key,
    create_test_file,
    create_test_project,
    create_test_user,
)


def _h(key: str) -> dict[str, str]:
    return {"X-Agent-API-Key": key}


def _folder(session: Session, project_id: str, title: str = "Folder", parent_id: str | None = None) -> File:
    folder = File(project_id=project_id, title=title, file_type="folder", parent_id=parent_id, order=0)
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return folder


def _limit_versions(session: Session, user_id: str, max_versions: int) -> None:
    """Put the user on a plan with `max_versions` user versions per file."""
    plan = SubscriptionPlan(
        name=f"agent-version-limit-{user_id[:8]}",
        display_name="Version Limited",
        display_name_en="Version Limited",
        price_monthly_cents=999,
        price_yearly_cents=9999,
        features={"file_versions_per_file": max_versions},
        is_active=True,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    now = utcnow()
    session.add(
        UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
        )
    )
    session.commit()


def _setup(session: Session, name: str, scopes: list[str] | None = None):
    user = create_test_user(session, name)
    project = create_test_project(session, user.id)
    _, key = create_test_api_key(session, user.id, scopes=scopes or ["read", "write"])
    return user, project, key


# ==================== Default folders ====================


@pytest.mark.integration
@pytest.mark.parametrize("project_type", ["novel", "short", "screenplay"])
async def test_create_project_creates_default_folders(client: AsyncClient, db_session: Session, project_type: str):
    user = create_test_user(db_session, f"agent_folders_{project_type}")
    _, key = create_test_api_key(db_session, user.id, scopes=["write"])

    response = await client.post(
        "/api/v1/agent/projects",
        headers=_h(key),
        json={"name": "新书", "project_type": project_type},
    )

    assert response.status_code == 200
    data = response.json()
    expected = get_folders_for_type(project_type, "zh")
    assert [f["title"] for f in data["folders"]] == [c["title"] for c in expected]
    assert [f["id"] for f in data["folders"]] == [f"{data['id']}-{c['id']}" for c in expected]
    assert all(f["file_type"] == "folder" for f in data["folders"])

    db_session.expire_all()
    stored = db_session.exec(
        select(File).where(File.project_id == data["id"]).order_by(File.order)
    ).all()
    assert [(f.id, f.title, f.parent_id, f.order) for f in stored] == [
        (f"{data['id']}-{c['id']}", c["title"], None, c["order"]) for c in expected
    ]


@pytest.mark.integration
async def test_create_project_folder_titles_follow_accept_language(client: AsyncClient, db_session: Session):
    user = create_test_user(db_session, "agent_folders_en")
    _, key = create_test_api_key(db_session, user.id, scopes=["write"])

    response = await client.post(
        "/api/v1/agent/projects",
        headers={**_h(key), "Accept-Language": "en-US,en;q=0.9"},
        json={"name": "Book"},
    )

    assert response.status_code == 200
    assert [f["title"] for f in response.json()["folders"]] == [
        c["title"] for c in get_folders_for_type("novel", "en")
    ]


# ==================== Order and parent validation on create/update ====================


@pytest.mark.integration
async def test_create_file_with_order_and_default_append(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_order_create")
    folder = _folder(db_session, project.id)
    url = f"/api/v1/agent/projects/{project.id}/files"

    explicit = await client.post(url, headers=_h(key), json={"title": "Alpha", "parent_id": folder.id, "order": 7})
    appended = await client.post(url, headers=_h(key), json={"title": "Beta", "parent_id": folder.id})
    chapter = await client.post(url, headers=_h(key), json={"title": "第3章 相遇", "parent_id": folder.id, "order": 30})
    assert chapter.status_code == 200
    chapter_updated = await client.put(
        f"/api/v1/agent/files/{chapter.json()['id']}",
        headers=_h(key),
        json={"title": "第4章 重逢", "order": 70},
    )

    assert explicit.status_code == 200 and explicit.json()["order"] == 7
    assert appended.status_code == 200 and appended.json()["order"] == 8
    # Same rule as the web API: nonzero structured order wins over title labels.
    assert chapter.json()["order"] == 30
    assert chapter_updated.status_code == 200 and chapter_updated.json()["order"] == 70
    assert explicit.json()["parent_id"] == folder.id


@pytest.mark.integration
async def test_create_file_rejects_negative_order(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_order_negative")

    response = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "Alpha", "order": -1}
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_update_file_order(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_order_update")
    file = create_test_file(db_session, project.id, "Alpha", content="正文")

    response = await client.put(f"/api/v1/agent/files/{file.id}", headers=_h(key), json={"order": 5})
    negative = await client.put(f"/api/v1/agent/files/{file.id}", headers=_h(key), json={"order": -2})

    assert response.status_code == 200
    assert response.json()["order"] == 5
    assert response.json()["content"] == "正文"
    assert negative.status_code == 422


@pytest.mark.integration
async def test_create_file_parent_must_be_folder_in_same_project(client: AsyncClient, db_session: Session):
    user, project, key = _setup(db_session, "agent_parent_rules")
    not_a_folder = create_test_file(db_session, project.id, "Chapter")
    other_project = create_test_project(db_session, user.id, "Other")
    other_folder = _folder(db_session, other_project.id)
    url = f"/api/v1/agent/projects/{project.id}/files"

    non_folder = await client.post(url, headers=_h(key), json={"title": "X", "parent_id": not_a_folder.id})
    cross_project = await client.post(url, headers=_h(key), json={"title": "X", "parent_id": other_folder.id})
    missing = await client.post(url, headers=_h(key), json={"title": "X", "parent_id": "nope"})

    assert non_folder.status_code == 400
    assert non_folder.json()["error_code"] == "ERR_VALIDATION_ERROR"
    assert cross_project.status_code == 400
    assert cross_project.json()["error_code"] == "ERR_FILE_NOT_FOUND"
    assert missing.status_code == 400


# ==================== Move ====================


@pytest.mark.integration
async def test_move_file_into_folder_and_back_to_root(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_move_ok")
    folder = _folder(db_session, project.id)
    file = create_test_file(db_session, project.id, "Alpha")

    moved = await client.post(
        f"/api/v1/agent/files/{file.id}/move", headers=_h(key), json={"parent_id": folder.id, "order": 4}
    )
    assert moved.status_code == 200
    assert moved.json()["parent_id"] == folder.id
    assert moved.json()["order"] == 4

    to_root = await client.post(f"/api/v1/agent/files/{file.id}/move", headers=_h(key), json={"parent_id": None})
    assert to_root.status_code == 200
    assert to_root.json()["parent_id"] is None
    assert to_root.json()["order"] == 4  # order untouched when omitted


@pytest.mark.integration
async def test_move_file_validation(client: AsyncClient, db_session: Session):
    user, project, key = _setup(db_session, "agent_move_invalid")
    outer = _folder(db_session, project.id, "Outer")
    inner = _folder(db_session, project.id, "Inner", parent_id=outer.id)
    chapter = create_test_file(db_session, project.id, "Chapter")
    other_folder = _folder(db_session, create_test_project(db_session, user.id, "Other").id)

    async def move(file_id: str, body: dict):
        return await client.post(f"/api/v1/agent/files/{file_id}/move", headers=_h(key), json=body)

    non_folder = await move(outer.id, {"parent_id": chapter.id})
    cross_project = await move(chapter.id, {"parent_id": other_folder.id})
    cycle = await move(outer.id, {"parent_id": inner.id})
    into_self = await move(outer.id, {"parent_id": outer.id})
    missing_parent_field = await move(chapter.id, {})
    negative_order = await move(chapter.id, {"parent_id": None, "order": -1})

    assert non_folder.status_code == 400
    assert cross_project.status_code == 400
    assert cycle.status_code == 400
    assert cycle.json()["error_code"] == "ERR_VALIDATION_ERROR"
    assert into_self.status_code == 400
    assert missing_parent_field.status_code == 422
    assert negative_order.status_code == 422

    db_session.expire_all()
    assert db_session.get(File, outer.id).parent_id is None
    assert db_session.get(File, chapter.id).parent_id is None


@pytest.mark.integration
async def test_move_file_scope_and_ownership(client: AsyncClient, db_session: Session):
    user, project, _ = _setup(db_session, "agent_move_scope")
    folder = _folder(db_session, project.id)
    file = create_test_file(db_session, project.id, "Alpha")
    _, read_only = create_test_api_key(db_session, user.id, scopes=["read"])
    _, other_project_only = create_test_api_key(
        db_session, user.id, project_ids=[create_test_project(db_session, user.id, "Else").id]
    )
    intruder = create_test_user(db_session, "agent_move_intruder")
    _, intruder_key = create_test_api_key(db_session, intruder.id)
    body = {"parent_id": folder.id}

    assert (await client.post(f"/api/v1/agent/files/{file.id}/move", headers=_h(read_only), json=body)).status_code == 403
    assert (
        await client.post(f"/api/v1/agent/files/{file.id}/move", headers=_h(other_project_only), json=body)
    ).status_code == 403
    assert (await client.post(f"/api/v1/agent/files/{file.id}/move", headers=_h(intruder_key), json=body)).status_code == 404
    db_session.expire_all()
    assert db_session.get(File, file.id).parent_id is None


# ==================== Versions ====================


@pytest.mark.integration
async def test_versions_list_detail_and_rollback(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_versions_flow")

    created = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "Alpha", "content": "初稿。"}
    )
    file_id = created.json()["id"]
    await client.put(f"/api/v1/agent/files/{file_id}", headers=_h(key), json={"content": "二稿。"})
    await client.put(f"/api/v1/agent/files/{file_id}", headers=_h(key), json={"content": "三稿，改坏了。"})

    listed = await client.get(f"/api/v1/agent/files/{file_id}/versions", headers=_h(key))
    assert listed.status_code == 200
    data = listed.json()
    assert data["total"] == 3
    assert data["file_title"] == "Alpha"
    assert [v["version_number"] for v in data["versions"]] == [3, 2, 1]
    assert data["versions"][-1]["change_type"] == CHANGE_TYPE_CREATE
    assert all("content" not in v for v in data["versions"])

    page = await client.get(f"/api/v1/agent/files/{file_id}/versions?limit=1&offset=1", headers=_h(key))
    assert [v["version_number"] for v in page.json()["versions"]] == [2]
    assert page.json()["total"] == 3

    detail = await client.get(f"/api/v1/agent/files/{file_id}/versions/1", headers=_h(key))
    assert detail.status_code == 200
    assert detail.json()["content"] == "初稿。"
    assert detail.json()["version_number"] == 1

    rollback = await client.post(f"/api/v1/agent/files/{file_id}/versions/1/rollback", headers=_h(key))
    assert rollback.status_code == 200
    body = rollback.json()
    assert body["restored_version"] == 1
    assert body["new_version_number"] == 4
    assert body["snapshot_created"] is True
    assert body["version_quota_exceeded"] is False

    current = await client.get(f"/api/v1/agent/files/{file_id}", headers=_h(key))
    assert current.json()["content"] == "初稿。"
    db_session.expire_all()
    newest = db_session.exec(
        select(FileVersion).where(FileVersion.file_id == file_id, FileVersion.version_number == 4)
    ).one()
    assert newest.change_type == CHANGE_TYPE_RESTORE


@pytest.mark.integration
async def test_create_without_content_records_no_version(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_versions_empty")

    created = await client.post(f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "Empty"})
    listed = await client.get(f"/api/v1/agent/files/{created.json()['id']}/versions", headers=_h(key))

    assert listed.status_code == 200
    assert listed.json()["total"] == 0
    assert listed.json()["versions"] == []


@pytest.mark.integration
async def test_version_not_found(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_versions_missing")
    file = create_test_file(db_session, project.id, "Alpha", content="正文")

    detail = await client.get(f"/api/v1/agent/files/{file.id}/versions/9", headers=_h(key))
    rollback = await client.post(f"/api/v1/agent/files/{file.id}/versions/9/rollback", headers=_h(key))
    not_a_number = await client.get(f"/api/v1/agent/files/{file.id}/versions/latest", headers=_h(key))

    assert detail.status_code == 404
    assert detail.json()["error_code"] == "ERR_VERSION_NOT_FOUND"
    assert rollback.status_code == 404
    assert not_a_number.status_code == 422
    db_session.expire_all()
    assert db_session.get(File, file.id).content == "正文"


@pytest.mark.integration
async def test_versions_scope_and_ownership(client: AsyncClient, db_session: Session):
    user, project, key = _setup(db_session, "agent_versions_scope")
    created = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "Alpha", "content": "v1"}
    )
    file_id = created.json()["id"]
    await client.put(f"/api/v1/agent/files/{file_id}", headers=_h(key), json={"content": "v2"})

    _, read_only = create_test_api_key(db_session, user.id, scopes=["read"])
    _, write_only = create_test_api_key(db_session, user.id, scopes=["write"])
    _, other_project_only = create_test_api_key(
        db_session, user.id, project_ids=[create_test_project(db_session, user.id, "Else").id]
    )
    intruder = create_test_user(db_session, "agent_versions_intruder")
    _, intruder_key = create_test_api_key(db_session, intruder.id)
    versions = f"/api/v1/agent/files/{file_id}/versions"

    # Reads need "read"; rollback needs "write".
    assert (await client.get(versions, headers=_h(read_only))).status_code == 200
    assert (await client.get(f"{versions}/1", headers=_h(read_only))).status_code == 200
    assert (await client.get(versions, headers=_h(write_only))).status_code == 403
    assert (await client.get(f"{versions}/1", headers=_h(write_only))).status_code == 403
    assert (await client.post(f"{versions}/1/rollback", headers=_h(read_only))).status_code == 403

    # Project allowlist → 403; another user's file → 404 (no existence leak).
    assert (await client.get(versions, headers=_h(other_project_only))).status_code == 403
    assert (await client.post(f"{versions}/1/rollback", headers=_h(other_project_only))).status_code == 403
    assert (await client.get(versions, headers=_h(intruder_key))).status_code == 404
    assert (await client.get(f"{versions}/1", headers=_h(intruder_key))).status_code == 404
    assert (await client.post(f"{versions}/1/rollback", headers=_h(intruder_key))).status_code == 404

    current = await client.get(f"/api/v1/agent/files/{file_id}", headers=_h(key))
    assert current.json()["content"] == "v2"


# ==================== /skill.md ====================


@pytest.mark.unit
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_skill_md_lists_structure_and_version_routes(lang: str):
    content = skill_md_service.generate_skill_md(lang=lang)

    assert "| `/api/v1/agent/files/{file_id}/move` | POST | move_file | write |" in content
    assert "| `/api/v1/agent/files/{file_id}/versions` | GET | list_file_versions | read |" in content
    assert "| `/api/v1/agent/files/{file_id}/versions/{version_number}` | GET | get_file_version | read |" in content
    assert (
        "| `/api/v1/agent/files/{file_id}/versions/{version_number}/rollback` | POST | rollback_file_version | write |"
        in content
    )


# ==================== Project limit, allowlist (create project) ====================


def _fill_to_project_limit(session: Session, user_id: str) -> int:
    """Create active projects until the user's plan project limit is reached; return the limit."""
    _, used, limit = quota_service.check_project_limit(session, user_id)
    assert limit > 0
    for i in range(limit - used):
        create_test_project(session, user_id, f"Existing {i}")
    return limit


@pytest.mark.integration
async def test_create_project_enforces_plan_project_limit(client: AsyncClient, db_session: Session):
    user = create_test_user(db_session, "agent_project_limit")
    _, key = create_test_api_key(db_session, user.id, scopes=["write"])
    limit = _fill_to_project_limit(db_session, user.id)

    response = await client.post("/api/v1/agent/projects", headers=_h(key), json={"name": "One too many"})

    assert response.status_code == 402
    assert response.json()["error_code"] == "ERR_QUOTA_PROJECTS_EXCEEDED"
    assert f"({limit}/{limit})" in response.json()["error_detail"]
    db_session.expire_all()
    names = db_session.exec(select(Project.name).where(Project.owner_id == user.id)).all()
    assert "One too many" not in names
    assert db_session.exec(select(ActivationEvent).where(ActivationEvent.user_id == user.id)).first() is None


@pytest.mark.integration
async def test_create_project_under_limit_creates_folders_and_records_activation(
    client: AsyncClient, db_session: Session
):
    user = create_test_user(db_session, "agent_project_under_limit")
    _, key = create_test_api_key(db_session, user.id, scopes=["write"])
    _fill_to_project_limit(db_session, user.id)
    # Free one slot: soft-deleted projects do not count.
    victim = db_session.exec(select(Project).where(Project.owner_id == user.id)).first()
    victim.is_deleted = True
    db_session.commit()

    response = await client.post("/api/v1/agent/projects", headers=_h(key), json={"name": "Fits"})

    assert response.status_code == 200
    assert len(response.json()["folders"]) == len(get_folders_for_type("novel", "zh"))
    db_session.expire_all()
    events = db_session.exec(
        select(ActivationEvent).where(
            ActivationEvent.user_id == user.id,
            ActivationEvent.event_name == ACTIVATION_EVENT_PROJECT_CREATED,
        )
    ).all()
    assert [e.project_id for e in events] == [response.json()["id"]]


@pytest.mark.integration
def test_project_service_itself_enforces_the_limit(db_session: Session):
    """The limit lives in the shared service, so no entry point can forget it."""
    user = create_test_user(db_session, "service_project_limit")
    _fill_to_project_limit(db_session, user.id)

    with pytest.raises(APIException) as exc:
        create_project_with_default_folders(db_session, Project(name="Nope", owner_id=user.id))

    assert exc.value.status_code == 402
    assert exc.value.error_code == "ERR_QUOTA_PROJECTS_EXCEEDED"
    assert db_session.exec(select(Project).where(Project.name == "Nope")).first() is None


@pytest.mark.integration
async def test_web_create_project_still_enforces_limit_once(client: AsyncClient, db_session: Session):
    user = create_test_user(db_session, "web_project_limit")
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    _, used, limit = quota_service.check_project_limit(db_session, user.id)
    for i in range(limit - used - 1):
        create_test_project(db_session, user.id, f"Existing {i}")

    last_slot = await client.post("/api/v1/projects", headers=headers, json={"name": "Last slot"})
    over = await client.post("/api/v1/projects", headers=headers, json={"name": "Over"})

    assert last_slot.status_code == 200
    assert over.status_code == 402
    assert over.json()["error_code"] == "ERR_QUOTA_PROJECTS_EXCEEDED"


@pytest.mark.integration
@pytest.mark.parametrize("allowlist", ["other_project", "empty"])
async def test_create_project_rejected_for_key_with_project_allowlist(
    client: AsyncClient, db_session: Session, allowlist: str
):
    user = create_test_user(db_session, f"agent_create_allowlist_{allowlist}")
    project_ids = [create_test_project(db_session, user.id).id] if allowlist == "other_project" else []
    _, key = create_test_api_key(db_session, user.id, scopes=["read", "write"], project_ids=project_ids)

    response = await client.post("/api/v1/agent/projects", headers=_h(key), json={"name": "Unreachable"})

    assert response.status_code == 403
    assert "limited to specific projects" in response.json()["error_detail"]
    db_session.expire_all()
    assert db_session.exec(select(Project).where(Project.name == "Unreachable")).first() is None


# ==================== Version quota on content writes ====================


@pytest.mark.integration
async def test_put_reports_version_quota_exceeded(client: AsyncClient, db_session: Session):
    user, project, key = _setup(db_session, "agent_put_version_quota")
    _limit_versions(db_session, user.id, 1)
    created = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "Alpha", "content": "初稿。"}
    )
    assert created.json()["version_quota_exceeded"] is False
    file_id = created.json()["id"]

    updated = await client.put(f"/api/v1/agent/files/{file_id}", headers=_h(key), json={"content": "二稿。"})
    title_only = await client.put(f"/api/v1/agent/files/{file_id}", headers=_h(key), json={"title": "Beta"})

    assert updated.status_code == 200
    assert updated.json()["content"] == "二稿。"
    assert updated.json()["version_quota_exceeded"] is True
    assert title_only.json()["version_quota_exceeded"] is False  # no content change, no snapshot attempted
    versions = await client.get(f"/api/v1/agent/files/{file_id}/versions", headers=_h(key))
    assert versions.json()["total"] == 1


@pytest.mark.integration
async def test_create_with_content_when_version_quota_exhausted(client: AsyncClient, db_session: Session):
    user, project, key = _setup(db_session, "agent_create_version_quota")
    _limit_versions(db_session, user.id, 0)

    created = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "Alpha", "content": "初稿。"}
    )

    assert created.status_code == 200
    assert created.json()["content"] == "初稿。"
    assert created.json()["version_quota_exceeded"] is True
    versions = await client.get(f"/api/v1/agent/files/{created.json()['id']}/versions", headers=_h(key))
    assert versions.json()["total"] == 0


@pytest.mark.integration
async def test_rollback_with_version_quota_full(client: AsyncClient, db_session: Session):
    user, project, key = _setup(db_session, "agent_rollback_version_quota")
    _limit_versions(db_session, user.id, 1)
    created = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "Alpha", "content": "初稿。"}
    )
    file_id = created.json()["id"]
    await client.put(f"/api/v1/agent/files/{file_id}", headers=_h(key), json={"content": "改坏了。"})

    rollback = await client.post(f"/api/v1/agent/files/{file_id}/versions/1/rollback", headers=_h(key))

    assert rollback.status_code == 200
    body = rollback.json()
    assert body["version_quota_exceeded"] is True
    assert body["new_version_number"] is None
    assert body["snapshot_created"] is False
    current = await client.get(f"/api/v1/agent/files/{file_id}", headers=_h(key))
    assert current.json()["content"] == "初稿。"


# ==================== Soft-deleted files / projects ====================


@pytest.mark.integration
@pytest.mark.parametrize("deleted", ["file", "project"])
async def test_move_and_versions_404_for_soft_deleted(client: AsyncClient, db_session: Session, deleted: str):
    _, project, key = _setup(db_session, f"agent_soft_deleted_{deleted}")
    folder = _folder(db_session, project.id)
    created = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "Alpha", "content": "v1"}
    )
    file_id = created.json()["id"]
    target = db_session.get(File, file_id) if deleted == "file" else db_session.get(Project, project.id)
    target.is_deleted = True
    target.deleted_at = utcnow()
    db_session.commit()
    versions = f"/api/v1/agent/files/{file_id}/versions"

    responses = [
        await client.post(f"/api/v1/agent/files/{file_id}/move", headers=_h(key), json={"parent_id": folder.id}),
        await client.get(versions, headers=_h(key)),
        await client.get(f"{versions}/1", headers=_h(key)),
        await client.post(f"{versions}/1/rollback", headers=_h(key)),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404, 404]
    assert all(r.json()["error_code"] == "ERR_FILE_NOT_FOUND" for r in responses)


@pytest.mark.integration
async def test_move_into_deleted_folder_is_rejected(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_move_deleted_folder")
    folder = _folder(db_session, project.id)
    folder.is_deleted = True
    db_session.commit()
    file = create_test_file(db_session, project.id, "Alpha")

    response = await client.post(f"/api/v1/agent/files/{file.id}/move", headers=_h(key), json={"parent_id": folder.id})

    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_FILE_NOT_FOUND"
    db_session.expire_all()
    assert db_session.get(File, file.id).parent_id is None


@pytest.mark.integration
async def test_create_file_empty_parent_id_is_rejected(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_create_empty_parent")

    response = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "X", "parent_id": ""}
    )

    assert response.status_code == 400


# ==================== Order bound ====================


@pytest.mark.integration
async def test_agent_order_is_bounded_to_int32(client: AsyncClient, db_session: Session):
    _, project, key = _setup(db_session, "agent_order_bound")
    file = create_test_file(db_session, project.id, "Alpha")
    too_big = MAX_FILE_ORDER + 1

    create = await client.post(
        f"/api/v1/agent/projects/{project.id}/files", headers=_h(key), json={"title": "X", "order": too_big}
    )
    update = await client.put(f"/api/v1/agent/files/{file.id}", headers=_h(key), json={"order": too_big})
    move = await client.post(
        f"/api/v1/agent/files/{file.id}/move", headers=_h(key), json={"parent_id": None, "order": too_big}
    )
    at_max = await client.put(f"/api/v1/agent/files/{file.id}", headers=_h(key), json={"order": MAX_FILE_ORDER})

    assert [create.status_code, update.status_code, move.status_code] == [422, 422, 422]
    assert at_max.status_code == 200
    assert at_max.json()["order"] == MAX_FILE_ORDER


@pytest.mark.integration
async def test_web_order_is_bounded_to_int32(client: AsyncClient, db_session: Session):
    user = create_test_user(db_session, "web_order_bound")
    project = create_test_project(db_session, user.id)
    file = create_test_file(db_session, project.id, "Alpha")
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    create = await client.post(
        f"/api/v1/projects/{project.id}/files", headers=headers, json={"title": "X", "order": MAX_FILE_ORDER + 1}
    )
    update = await client.put(f"/api/v1/files/{file.id}", headers=headers, json={"order": MAX_FILE_ORDER + 1})

    assert create.status_code == 422
    assert update.status_code == 422


# ==================== Keyword search after move ====================


@pytest.mark.integration
@pytest.mark.parametrize("surface", ["agent", "web"])
async def test_move_keeps_content_immediately_searchable(client: AsyncClient, db_session: Session, monkeypatch, surface: str):
    user, project, key = _setup(db_session, f"move_search_{surface}")
    folder = _folder(db_session, project.id)
    file = create_test_file(db_session, project.id, "Alpha", content="正文")

    if surface == "agent":
        response = await client.post(
            f"/api/v1/agent/files/{file.id}/move", headers=_h(key), json={"parent_id": folder.id}
        )
    else:
        login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
        response = await client.post(
            f"/api/v1/files/{file.id}/move",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            json={"target_parent_id": folder.id},
        )

    assert response.status_code == 200
    db_session.expire_all()
    persisted = db_session.get(File, file.id)
    assert persisted.parent_id == folder.id
    from services.infra.file_search_service import FileSearchService
    results = FileSearchService().search(project.id, "正文", session=db_session)
    assert [(result.entity_id, result.content) for result in results] == [(file.id, "正文")]



# ==================== Blocking work off the event loop ====================


@pytest.mark.unit
@pytest.mark.parametrize(
    "endpoint", ["move_file", "list_file_versions", "get_file_version", "rollback_file_version"]
)
def test_lock_and_db_heavy_endpoints_are_sync(endpoint: str):
    """rollback takes a threading.Lock / SELECT FOR UPDATE; sync handlers run in FastAPI's threadpool."""
    assert not inspect.iscoroutinefunction(getattr(agent_api, endpoint))


# ==================== Writing context timeout ====================


@pytest.mark.integration
async def test_writing_context_timeout_returns_504(client: AsyncClient, db_session: Session, monkeypatch):
    _, project, key = _setup(db_session, "agent_context_timeout")
    monkeypatch.setattr(agent_api, "WRITING_CONTEXT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(agent_api.ContextAssembler, "assemble", lambda self, **kw: time.sleep(0.5))

    response = await client.get(f"/api/v1/agent/projects/{project.id}/writing-context", headers=_h(key))

    assert response.status_code == 504
    assert response.json()["error_code"] == "ERR_SERVICE_UNAVAILABLE"
    await asyncio.sleep(0.5)  # let the abandoned worker thread finish before the DB fixture tears down
