"""Author-source copy contract for material files."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agent.tools.mcp_tools import ToolContext, create_file

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _context(author_message: str | None) -> None:
    ToolContext.set_context(
        session=None,
        user_id="author-1",
        project_id="project-1",
        session_id="session-1",
        author_message=author_message,
    )


@pytest.fixture(autouse=True)
def clear_tool_context():
    yield
    ToolContext.clear_context()


async def test_source_text_is_saved_byte_for_byte_from_current_author_message():
    source = (
        "《旧港残信》三章大纲\n"
        "第一章 退信：邮局分拣员苏砚收到一封旧信。\n"
        "第二章 暗格：苏砚找到钟表行学徒陈默。\n\n"
        "陈默：28岁，话少手稳，对十年前的火灾讳莫如深。"
    )
    _context(f"请把下面资料保存到项目中，保持原样：\n\n{source}\n\n保存后先别续写。")
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "outline-1",
        "title": "旧港残信",
        "file_type": "outline",
        "content": source,
        "mutation_applied": True,
    }

    with patch.object(ToolContext, "get_executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "旧港残信", "file_type": "outline", "source_text": source}
            )
        )

    assert result["status"] == "success"
    assert result["data"]["content"] == source
    executor.create_file.assert_called_once_with(
        project_id="project-1",
        title="旧港残信",
        file_type="outline",
        content=source,
        parent_id=None,
        order=None,
        metadata=None,
        normalize_quotes=True,
        protect_author_content=True,
    )
    assert not ToolContext.has_pending_empty_file()


@pytest.mark.parametrize(
    ("args", "error_type"),
    [
        ({"title": "陈默", "file_type": "character", "source_text": "性别：男"}, "source_text_not_in_author_message"),
        ({"title": "陈默", "file_type": "character", "source_text": " \n\t"}, "invalid_source_text"),
        (
            {"title": "陈默", "file_type": "character", "source_text": "陈默原文", "content": "改写内容"},
            "source_text_conflict",
        ),
        (
            {"title": "陈默", "file_type": "character", "source_text": "陈默原文", "empty": True},
            "source_text_conflict",
        ),
        ({"title": "资料", "file_type": "folder", "source_text": "陈默原文"}, "invalid_source_text_file_type"),
        ({"title": "第一章", "file_type": "draft", "source_text": "陈默原文"}, "invalid_source_text_file_type"),
    ],
)
async def test_invalid_source_copy_is_rejected_before_file_mutation(args, error_type):
    _context("作者只提供：陈默原文")
    executor = MagicMock()

    with patch.object(ToolContext, "get_executor", return_value=executor):
        result = _payload(await create_file(args))

    assert result["status"] == "error"
    assert result["error_type"] == error_type
    executor.create_file.assert_not_called()


async def test_source_copy_requires_current_raw_author_message():
    _context(None)
    executor = MagicMock()

    with patch.object(ToolContext, "get_executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "苏砚", "file_type": "character", "source_text": "苏砚原文"}
            )
        )

    assert result["status"] == "error"
    assert result["error_type"] == "source_text_context_missing"
    executor.create_file.assert_not_called()


async def test_source_copy_is_isolated_to_the_current_request():
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "character-1",
        "title": "苏砚",
        "file_type": "character",
        "content": "苏砚：26岁。",
        "mutation_applied": True,
    }
    _context("苏砚：26岁。")
    with patch.object(ToolContext, "get_executor", return_value=executor):
        first = _payload(
            await create_file(
                {"title": "苏砚", "file_type": "character", "source_text": "苏砚：26岁。"}
            )
        )
    assert first["status"] == "success"

    _context("这一轮只讨论陈默，不包含上一轮原文。")
    with patch.object(ToolContext, "get_executor", return_value=executor):
        second = _payload(
            await create_file(
                {"title": "苏砚副本", "file_type": "character", "source_text": "苏砚：26岁。"}
            )
        )

    assert second["status"] == "error"
    assert second["error_type"] == "source_text_not_in_author_message"
    assert executor.create_file.call_count == 1


async def test_source_copy_preserves_nonblank_leading_and_trailing_whitespace():
    source = "\n  苏砚：26岁。  \n"
    _context(f"资料如下：{source}结束")
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "character-spaced",
        "title": "苏砚",
        "file_type": "character",
        "content": source,
        "mutation_applied": True,
    }

    with patch.object(ToolContext, "get_executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "苏砚", "file_type": "character", "source_text": source}
            )
        )

    assert result["status"] == "success"
    assert result["data"]["content"] == source
    assert executor.create_file.call_args.kwargs["content"] == source


async def test_mismatched_existing_reuse_is_reported_without_a_false_copy_receipt():
    _context("陈默：28岁，话少手稳。")
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "existing-character",
        "title": "陈默",
        "file_type": "character",
        "content": "现有文件里是另一版设定。",
        "reused_existing": True,
        "mutation_applied": False,
    }

    with patch.object(ToolContext, "get_executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "陈默", "file_type": "character", "source_text": "陈默：28岁，话少手稳。"}
            )
        )

    assert result["status"] == "error"
    assert result["error_type"] == "source_text_existing_mismatch"
    assert result["mutation_applied"] is False
    assert "source_copied" not in result and "body_saved" not in result


async def test_exact_existing_reuse_is_confirmed_without_mutation():
    source = "陈默：28岁，话少手稳。"
    _context(source)
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "existing-character",
        "title": "陈默",
        "file_type": "character",
        "content": source,
        "reused_existing": True,
        "mutation_applied": False,
    }

    with patch.object(ToolContext, "get_executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "陈默", "file_type": "character", "source_text": source}
            )
        )

    assert result["status"] == "success"
    assert result["data"]["source_copied"] is True
    assert result["data"]["body_saved"] is True
    assert result["data"]["mutation_applied"] is False
    assert result["data"]["reused_existing"] is True
    assert result["data"]["content"] == ""
    assert result["data"]["content_elided"] is True


async def test_ordinary_generated_material_keeps_streamed_body_contract():
    _context("请帮我设计一个海港城市设定。")
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "lore-1",
        "title": "海港城市",
        "file_type": "lore",
        "content": "",
        "updated_at": "2026-11-17T00:00:00Z",
        "mutation_applied": True,
    }

    with patch.object(ToolContext, "get_executor", return_value=executor):
        result = _payload(await create_file({"title": "海港城市", "file_type": "lore"}))

    assert result["status"] == "success"
    assert result["data"]["awaiting_body"] is True
    assert ToolContext.has_pending_empty_file()
    executor.create_file.assert_called_once()
    assert executor.create_file.call_args.kwargs["content"] == ""


async def test_source_copy_does_not_overwrite_an_existing_material_file(db_session):
    from models import File, Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"source-copy-{suffix}@example.com",
        username=f"source_copy_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="Source copy", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    existing = File(
        project_id=project.id,
        title="陈默",
        file_type="character",
        content="作者已经保存的旧设定。",
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    copied = "陈默：28岁，话少手稳。"
    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="source-copy",
        author_message=copied,
    )
    result = _payload(
        await create_file({"title": "陈默", "file_type": "character", "source_text": copied})
    )

    db_session.expire_all()
    assert result["status"] == "success"
    assert db_session.get(File, existing.id).content == "作者已经保存的旧设定。"
    assert result["data"]["id"] != existing.id
    assert result["data"]["content"] == copied
