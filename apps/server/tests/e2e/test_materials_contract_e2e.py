"""
Focused E2E contracts for user-facing material library flows.

These tests pin the backend contracts that materials browser suites rely on:
- root material list alias, job status, and chapter tree metadata
- retrying failed jobs with quota consumption
- soft delete behavior removing novels from the visible library
- library summary, entity search, preview, and import flows
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import File
from models.material_models import Chapter, Character, IngestionJob, Novel, Plot, StoryLine, WorldView

from .test_core_api_e2e import (
    _attach_subscription,
    _auth_headers,
    _create_plan,
    _create_project,
    _create_user,
    _login,
)

pytestmark = pytest.mark.e2e


def _attach_materials_access_subscription(db_session: Session, *, user_id: str) -> None:
    plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={
            "materials_library_access": True,
            "material_uploads": 5,
            "material_decompositions": 5,
            "max_projects": 3,
        },
    )
    _attach_subscription(db_session, user_id=user_id, plan_id=plan.id)


@pytest.mark.asyncio
async def test_materials_contract_root_status_tree_and_delete_roundtrip(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="materials_contract_tree")
    _attach_materials_access_subscription(db_session, user_id=user.id)
    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    novel = Novel(
        user_id=user.id,
        title="Tree Contract Novel",
        author="E2E Tester",
        synopsis="Material tree contract coverage",
        source_meta=json.dumps({"original_filename": "tree-contract.txt", "file_path": "/tmp/tree-contract.txt"}),
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    completed_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/tree-contract.txt",
        status="completed",
        total_chapters=2,
        processed_chapters=2,
    )
    completed_job.update_stage_progress("extract", "completed", message="章节解析完成")
    db_session.add(completed_job)
    db_session.commit()
    db_session.refresh(completed_job)

    chapter_one = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章 开场",
        summary="第一章摘要",
        original_content="开场内容",
    )
    chapter_two = Chapter(
        novel_id=novel.id,
        chapter_number=2,
        title="第二章 转折",
        summary="第二章摘要",
        original_content="转折内容",
    )
    db_session.add(chapter_one)
    db_session.add(chapter_two)
    db_session.commit()
    db_session.refresh(chapter_one)
    db_session.refresh(chapter_two)

    db_session.add_all(
        [
            Plot(chapter_id=chapter_one.id, index=0, plot_type="SETUP", description="铺垫1"),
            Plot(chapter_id=chapter_one.id, index=1, plot_type="CONFLICT", description="冲突1"),
            Plot(chapter_id=chapter_two.id, index=0, plot_type="TURNING_POINT", description="转折1"),
        ]
    )
    db_session.commit()

    list_response = await client.get("/api/v1/materials", headers=headers)
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload) == 1
    assert list_payload[0]["id"] == novel.id
    assert list_payload[0]["title"] == "Tree Contract Novel"
    assert list_payload[0]["status"] == "completed"
    assert list_payload[0]["chapters_count"] == 2
    assert list_payload[0]["original_filename"] == "tree-contract.txt"

    status_response = await client.get(f"/api/v1/materials/{novel.id}/status", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["job_id"] == completed_job.id
    assert status_payload["status"] == "completed"
    assert status_payload["progress_percentage"] == 100.0
    assert status_payload["stage_progress"]["extract"]["status"] == "completed"

    tree_response = await client.get(f"/api/v1/materials/{novel.id}/tree", headers=headers)
    assert tree_response.status_code == 200
    tree_payload = tree_response.json()["tree"]
    assert [item["title"] for item in tree_payload] == ["第一章 开场", "第二章 转折"]
    assert tree_payload[0]["metadata"]["chapter_number"] == 1
    assert tree_payload[0]["metadata"]["summary"] == "第一章摘要"
    assert tree_payload[0]["metadata"]["plots_count"] == 2
    assert tree_payload[1]["metadata"]["plots_count"] == 1

    delete_response = await client.delete(f"/api/v1/materials/{novel.id}", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Material library deleted successfully"

    list_after_delete = await client.get("/api/v1/materials", headers=headers)
    assert list_after_delete.status_code == 200
    assert list_after_delete.json() == []

    detail_after_delete = await client.get(f"/api/v1/materials/{novel.id}", headers=headers)
    assert detail_after_delete.status_code == 403

    tree_after_delete = await client.get(f"/api/v1/materials/{novel.id}/tree", headers=headers)
    assert tree_after_delete.status_code == 403


@pytest.mark.asyncio
async def test_materials_contract_retry_creates_pending_job_and_consumes_quota(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    flow_dispatch = AsyncMock(return_value="flow-run-test")
    monkeypatch.setattr("api.materials.upload._start_flow_deployment", flow_dispatch)

    user = await _create_user(db_session, prefix="materials_contract_retry")
    free_plan = _create_plan(
        db_session,
        name="free",
        display_name="Free",
        features={
            "ai_conversations_per_day": 10,
            "max_projects": 1,
            "materials_library_access": True,
            "material_uploads": 5,
            "material_decompositions": 3,
            "custom_skills": 3,
            "inspiration_copies_monthly": 10,
            "export_formats": ["txt"],
        },
    )
    _attach_subscription(db_session, user_id=user.id, plan_id=free_plan.id)

    novel = Novel(
        user_id=user.id,
        title="Retry Contract Novel",
        author="Retry Tester",
        synopsis="Failed material retry contract",
        source_meta=json.dumps({"original_filename": "retry.txt", "file_path": "/tmp/retry.txt"}),
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    failed_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/retry.txt",
        status="failed",
        total_chapters=12,
        processed_chapters=4,
        error_message="Decomposition failed",
    )
    failed_job.update_stage_progress("extract", "failed", message="解析失败")
    db_session.add(failed_job)
    db_session.commit()
    db_session.refresh(failed_job)

    login_payload = await _login(client, identifier=user.username)
    headers = _auth_headers(login_payload["access_token"])

    retry_response = await client.post(f"/api/v1/materials/{novel.id}/retry", headers=headers)
    assert retry_response.status_code == 200
    retry_payload = retry_response.json()
    assert retry_payload["message"] == "Retry started successfully"
    assert retry_payload["status"] == "pending"
    assert retry_payload["job_id"] != failed_job.id
    flow_dispatch.assert_awaited_once()

    status_response = await client.get(f"/api/v1/materials/{novel.id}/status", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["job_id"] == retry_payload["job_id"]
    assert status_payload["status"] == "pending"
    assert status_payload["processed_chapters"] == 0
    assert status_payload["stage_progress"]["queue"]["status"] == "pending"

    quota_response = await client.get("/api/v1/subscription/quota", headers=headers)
    assert quota_response.status_code == 200
    assert quota_response.json()["material_decompositions"]["used"] == 1


@pytest.mark.asyncio
async def test_materials_contract_summary_search_preview_and_import_roundtrip(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="materials_contract_search")
    _attach_materials_access_subscription(db_session, user_id=user.id)
    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    novel = Novel(
        user_id=user.id,
        title="Search Contract Novel",
        author="Search Tester",
        synopsis="Material summary/search/preview/import contract coverage",
        source_meta=json.dumps({"original_filename": "search-contract.txt", "file_path": "/tmp/search-contract.txt"}),
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    completed_job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/search-contract.txt",
        status="completed",
        total_chapters=1,
        processed_chapters=1,
    )
    db_session.add(completed_job)
    db_session.commit()

    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章 角色登场",
        summary="主角登场并建立世界观",
        original_content="英雄林川第一次登场。",
    )
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    character = Character(
        novel_id=novel.id,
        name="林川",
        aliases=json.dumps(["阿川"]),
        description="故事主角",
        archetype="Hero",
        first_appearance_chapter_id=chapter.id,
    )
    storyline = StoryLine(
        novel_id=novel.id,
        title="主线故事",
        description="林川的成长冒险",
        main_characters=json.dumps(["林川"]),
        themes=json.dumps(["成长"]),
    )
    worldview = WorldView(
        novel_id=novel.id,
        power_system="灵力体系",
        world_structure="三域九州",
        key_factions=json.dumps([{"name": "青岚宗"}]),
        special_rules="禁地不可飞行",
    )
    db_session.add(character)
    db_session.add(storyline)
    db_session.add(worldview)
    db_session.commit()
    db_session.refresh(character)

    summary_response = await client.get("/api/v1/materials/library-summary", headers=headers)
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert len(summary_payload) == 1
    assert summary_payload[0]["id"] == novel.id
    assert summary_payload[0]["counts"]["characters"] == 1
    assert summary_payload[0]["counts"]["worldview"] == 1
    assert summary_payload[0]["counts"]["storylines"] == 1

    search_response = await client.get("/api/v1/materials/search", params={"q": "林川"}, headers=headers)
    assert search_response.status_code == 200
    search_payload = search_response.json()
    assert any(
        item["entity_type"] == "characters" and item["entity_id"] == character.id and item["name"] == "林川"
        for item in search_payload
    )

    preview_response = await client.get(
        f"/api/v1/materials/{novel.id}/characters/{character.id}/preview",
        headers={**headers, "Accept-Language": "zh-CN"},
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["title"] == "林川"
    assert preview_payload["suggested_file_type"] == "character"
    assert preview_payload["suggested_folder_name"] == "角色"
    assert "林川" in preview_payload["markdown"]

    project = await _create_project(client, login_payload["access_token"], name="Imported Search Materials Project")
    import_response = await client.post(
        "/api/v1/materials/import",
        json={
            "project_id": project["id"],
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
        },
        headers=headers,
    )
    assert import_response.status_code == 200
    import_payload = import_response.json()
    assert import_payload["file_type"] == "character"
    assert import_payload["folder_name"] == "角色"

    imported_file = db_session.get(File, import_payload["file_id"])
    assert imported_file is not None
    assert imported_file.project_id == project["id"]
    assert imported_file.parent_id is not None
    assert imported_file.title == import_payload["title"]
    assert "林川" in (imported_file.content or "")
