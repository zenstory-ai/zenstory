"""Author-source copy contract for material files."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agent.tools.mcp_tools import create_file
from agent.tools.tool_context import AgentRequestContext

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _context(author_message: str | None) -> AgentRequestContext:
    return AgentRequestContext(
        session=MagicMock(),
        user_id="author-1",
        project_id="project-1",
        session_id="session-1",
        author_message=author_message,
    )


async def test_source_text_is_saved_byte_for_byte_from_current_author_message():
    source = (
        "《旧港残信》三章大纲\n"
        "第一章 退信：邮局分拣员苏砚收到一封旧信。\n"
        "第二章 暗格：苏砚找到钟表行学徒陈默。\n\n"
        "陈默：28岁，话少手稳，对十年前的火灾讳莫如深。"
    )
    req_ctx = _context(f"请把下面资料保存到项目中，保持原样：\n\n{source}\n\n保存后先别续写。")
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "outline-1",
        "title": "旧港残信",
        "file_type": "outline",
        "content": source,
        "mutation_applied": True,
    }

    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "旧港残信", "file_type": "outline", "source_text": source},
                request_context=req_ctx,
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
    )
    assert not req_ctx.has_pending_empty_file()


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
    req_ctx = _context("作者只提供：陈默原文")
    executor = MagicMock()

    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(await create_file(args, request_context=req_ctx))

    assert result["status"] == "error"
    assert result["error_type"] == error_type
    executor.create_file.assert_not_called()


async def test_source_copy_requires_current_raw_author_message():
    req_ctx = _context(None)
    executor = MagicMock()

    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "苏砚", "file_type": "character", "source_text": "苏砚原文"},
                request_context=req_ctx,
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
    req_ctx_1 = _context("苏砚：26岁。")
    with patch.object(req_ctx_1, "executor", return_value=executor):
        first = _payload(
            await create_file(
                {"title": "苏砚", "file_type": "character", "source_text": "苏砚：26岁。"},
                request_context=req_ctx_1,
            )
        )
    assert first["status"] == "success"

    req_ctx_2 = _context("这一轮只讨论陈默，不包含上一轮原文。")
    with patch.object(req_ctx_2, "executor", return_value=executor):
        second = _payload(
            await create_file(
                {"title": "苏砚副本", "file_type": "character", "source_text": "苏砚：26岁。"},
                request_context=req_ctx_2,
            )
        )

    assert second["status"] == "error"
    assert second["error_type"] == "source_text_not_in_author_message"
    assert executor.create_file.call_count == 1


async def test_source_copy_preserves_nonblank_leading_and_trailing_whitespace():
    source = "\n  苏砚：26岁。  \n"
    req_ctx = _context(f"资料如下：{source}结束")
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "character-spaced",
        "title": "苏砚",
        "file_type": "character",
        "content": source,
        "mutation_applied": True,
    }

    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "苏砚", "file_type": "character", "source_text": source},
                request_context=req_ctx,
            )
        )

    assert result["status"] == "success"
    assert result["data"]["content"] == source
    assert executor.create_file.call_args.kwargs["content"] == source


async def test_mismatched_existing_reuse_is_reported_without_a_false_copy_receipt():
    req_ctx = _context("陈默：28岁，话少手稳。")
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "existing-character",
        "title": "陈默",
        "file_type": "character",
        "content": "现有文件里是另一版设定。",
        "reused_existing": True,
        "mutation_applied": False,
    }

    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "陈默", "file_type": "character", "source_text": "陈默：28岁，话少手稳。"},
                request_context=req_ctx,
            )
        )

    assert result["status"] == "error"
    assert result["error_type"] == "source_text_existing_mismatch"
    assert result["mutation_applied"] is False
    assert "source_copied" not in result and "body_saved" not in result


async def test_exact_existing_reuse_is_confirmed_without_mutation():
    source = "陈默：28岁，话少手稳。"
    req_ctx = _context(source)
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "existing-character",
        "title": "陈默",
        "file_type": "character",
        "content": source,
        "reused_existing": True,
        "mutation_applied": False,
    }

    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "陈默", "file_type": "character", "source_text": source},
                request_context=req_ctx,
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
    req_ctx = _context("请帮我设计一个海港城市设定。")
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "lore-1",
        "title": "海港城市",
        "file_type": "lore",
        "content": "",
        "updated_at": "2026-11-17T00:00:00Z",
        "mutation_applied": True,
    }

    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(await create_file({"title": "海港城市", "file_type": "lore"}, request_context=req_ctx))

    assert result["status"] == "success"
    assert result["data"]["awaiting_body"] is True
    assert req_ctx.has_pending_empty_file()
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
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="source-copy",
        author_message=copied,
    )
    result = _payload(
        await create_file(
            {"title": "陈默", "file_type": "character", "source_text": copied},
            request_context=req_ctx,
        )
    )

    db_session.expire_all()
    assert result["status"] == "success"
    assert db_session.get(File, existing.id).content == "作者已经保存的旧设定。"
    assert result["data"]["id"] != existing.id
    assert result["data"]["content"] == copied


async def test_source_copy_matches_crlf_trailing_spaces_and_blank_line_runs_while_keeping_author_slice():
    author_block = "## 世界观总纲  \r\n\r\n第一条：灵气复苏百年。\t\r\n第二条：东海群岛封禁。   "
    req_ctx = _context(f"请帮我把下面这段设定原样建档：\r\n\r\n{author_block}\r\n\r\n先不要写正文。")
    model_normalized = "## 世界观总纲\n第一条：灵气复苏百年。\n第二条：东海群岛封禁。\n"
    expected_saved = "## 世界观总纲  \n\n第一条：灵气复苏百年。\t\n第二条：东海群岛封禁。"

    executor = MagicMock()
    executor.create_file.side_effect = lambda **kwargs: {
        "id": "lore-crlf",
        "title": kwargs["title"],
        "file_type": kwargs["file_type"],
        "content": kwargs["content"],
        "mutation_applied": True,
    }
    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "世界观总纲", "file_type": "lore", "source_text": model_normalized},
                request_context=req_ctx,
            )
        )

    assert result["status"] == "success"
    assert executor.create_file.call_args.kwargs["content"] == expected_saved
    assert result["data"]["source_copied"] is True


async def test_source_copy_preserves_first_line_chinese_indent_across_crlf_and_trailing_spaces():
    author_block = "　　第一段：寒江孤影。  \r\n　　第二段：江湖故人。  "
    req_ctx = _context(f"设定如下：\r\n{author_block}\r\n完。")
    model_source = "　　第一段：寒江孤影。\n　　第二段：江湖故人。\n"
    expected_saved = "　　第一段：寒江孤影。  \n　　第二段：江湖故人。"

    executor = MagicMock()
    executor.create_file.side_effect = lambda **kwargs: {
        "id": "lore-indent",
        "title": kwargs["title"],
        "file_type": kwargs["file_type"],
        "content": kwargs["content"],
        "mutation_applied": True,
    }
    with patch.object(req_ctx, "executor", return_value=executor):
        result = _payload(
            await create_file(
                {"title": "门派小传", "file_type": "lore", "source_text": model_source},
                request_context=req_ctx,
            )
        )

    assert result["status"] == "success"
    assert executor.create_file.call_args.kwargs["content"] == expected_saved


async def test_source_copy_still_rejects_skipped_middle_line_or_ambiguous_raw_slices():
    req_ctx = _context("第一行：沈砚\n第二行：二十八岁\n第三行：六扇门捕快")
    executor = MagicMock()
    with patch.object(req_ctx, "executor", return_value=executor):
        skipped = _payload(
            await create_file(
                {
                    "title": "沈砚",
                    "file_type": "character",
                    "source_text": "第一行：沈砚\n第三行：六扇门捕快",
                },
                request_context=req_ctx,
            )
        )
    assert skipped["status"] == "error"
    assert skipped["error_type"] == "source_text_not_in_author_message"
    executor.create_file.assert_not_called()

    # 两处归一化相同但原始空行不同的重复块：无法唯一确定哪一处原文切片时拒绝。
    req_ctx_2 = _context("版本A：\n甲行\n乙行\n---\n版本B：\n甲行\n\n\n乙行")
    with patch.object(req_ctx_2, "executor", return_value=executor):
        ambiguous = _payload(
            await create_file(
                {"title": "重复块", "file_type": "lore", "source_text": "甲行\n\n乙行"},
                request_context=req_ctx_2,
            )
        )
    assert ambiguous["status"] == "error"
    assert ambiguous["error_type"] == "source_text_not_in_author_message"
    executor.create_file.assert_not_called()

