"""第三轮深度 review 回归测试：流式适配层（stream_adapter + stream_processor）。

覆盖缺陷：
- #3  剧本分集复用路径下的截断补全整体覆盖已完成正文
- #4  正文含奇数个 ``` 围栏时真实 </file> 被误判为代码块字面量
- #20 edit_file 的 failed_edits / partial_success / warnings 被整体丢弃
- #33 多 agent 协作时 usage 被覆盖而非累加（适配器侧）
- 技能用量改由 load_skill 工具结果驱动 skill_matched，适配器不再解析/剥离任何文本标记
"""

import json
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session

from agent.core.events import EventType
from agent.core.stream_processor import (
    AMBIGUOUS_MARKER_MAX_PENDING,
    StreamProcessor,
    StreamState,
)
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig

# ---------------------------------------------------------------- 通用脚手架 --


def _make_adapter() -> StreamAdapter:
    return StreamAdapter(
        StreamAdapterConfig(
            project_id="p-round3",
            user_id="u-round3",
            process_file_markers=True,
        )
    )


async def _drive(adapter: StreamAdapter, events: list[WorkflowStreamEvent]):
    async def gen():
        for event in events:
            yield event

    collected = []
    async for sse_event in adapter.process_workflow_events(gen()):
        collected.append(sse_event)
    return collected


def _texts(events, event_type: EventType, key: str) -> list[str]:
    return [e.data.get(key, "") for e in events if e.type == event_type]


def _chat(events) -> str:
    return "".join(_texts(events, EventType.CONTENT, "text"))


def _create_file_result(
    file_id: str,
    *,
    content: str = "",
    reused_existing: bool | None = None,
    original_content_length: int | None = None,
    file_type: str = "script",
    title: str = "第3集 迷雾",
) -> WorkflowStreamEvent:
    """构造一条与生产同形的 create_file tool_result 事件（MCP text 包裹）。"""
    data: dict = {
        "id": file_id,
        "title": title,
        "file_type": file_type,
        "content": content,
    }
    if reused_existing is not None:
        data["reused_existing"] = reused_existing
    if original_content_length is not None:
        data["original_content_length"] = original_content_length
    payload = json.dumps({"status": "success", "data": data}, ensure_ascii=False)
    return WorkflowStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "tool_use_id": "call-1",
            "name": "create_file",
            "result": {"content": [{"type": "text", "text": payload}]},
        },
    )


def _text_event(text: str) -> WorkflowStreamEvent:
    return WorkflowStreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _message_end(**data) -> WorkflowStreamEvent:
    return WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data=data)


# ------------------------------------------------------------------- 缺陷 #3 --


@pytest.mark.asyncio
async def test_reused_episode_is_not_overwritten_by_truncated_prose():
    """复用已写满的分集 + 模型漏写 </file>：原文必须保持不变。"""
    adapter = _make_adapter()
    adapter._save_file_content = AsyncMock(return_value=True)

    events = await _drive(
        adapter,
        [
            _create_file_result(
                "f-ep3",
                content="旧正文。" * 2000,
                reused_existing=True,
                original_content_length=8000,
            ),
            _text_event("<file>第3集 迷雾\n（新版开场 300 字）"),
            _message_end(stop_reason="end_turn"),
        ],
    )

    # 关键断言：绝不整体覆盖
    adapter._save_file_content.assert_not_awaited()
    # 收尾 UI 并如实说明未保存，不把模型协议与诊断数字当作作者正文。
    assert EventType.FILE_CONTENT_END in [e.type for e in events]
    assert "未保存" in _chat(events)
    assert "原文保持不变" in _chat(events)
    for internal in ("8000", "</file>", "edit_file", "f-ep3", "系统提醒"):
        assert internal not in _chat(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("language,expected", [
    ("zh", "本次改写未保存，原文保持不变。"),
    ("en-US", "This rewrite was not saved. The original text is unchanged."),
])
async def test_truncated_overwrite_notice_is_localized_without_protocol_details(language, expected):
    adapter = StreamAdapter(StreamAdapterConfig(language=language))
    adapter._save_file_content = AsyncMock(return_value=True)
    events = await _drive(adapter, [
        _create_file_result("private-file-id", content="original" * 1000,
                            title="", reused_existing=True, original_content_length=8000),
        _text_event("<file>A partial replacement"),
        _message_end(stop_reason="end_turn"),
    ])
    adapter._save_file_content.assert_not_awaited()
    assert _chat(events).strip() == expected
    assert EventType.FILE_CONTENT_END in [event.type for event in events]


@pytest.mark.asyncio
async def test_reused_episode_blanked_content_still_protected():
    """即便 create_file 仍按旧写法把 content 抹成空串（只给出 reused_existing /
    original_content_length），截断补全同样不得覆盖已有正文——这是缺陷 #3 的
    原始形态：适配器进入捕获、模型漏写 </file>、update_file 整体替换。"""
    adapter = _make_adapter()
    adapter._save_file_content = AsyncMock(return_value=True)

    await _drive(
        adapter,
        [
            _create_file_result(
                "f-ep3",
                content="",
                reused_existing=True,
                original_content_length=8000,
            ),
            _text_event("<file>第3集 迷雾\n（新版开场 300 字）"),
            _message_end(stop_reason="end_turn"),
        ],
    )

    adapter._save_file_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_reused_episode_with_real_end_marker_is_saved():
    """同一条复用路径，模型正常写出 </file> 时必须照常落库（不能误伤）。"""
    adapter = _make_adapter()
    adapter._save_file_content = AsyncMock(return_value=True)

    await _drive(
        adapter,
        [
            _create_file_result(
                "f-ep3",
                content="旧正文。" * 2000,
                reused_existing=True,
                original_content_length=8000,
            ),
            _text_event("<file>第3集 迷雾（完整新版）</file>写好了。"),
            _message_end(stop_reason="end_turn"),
        ],
    )

    adapter._save_file_content.assert_awaited_once_with(
        "f-ep3", "第3集 迷雾（完整新版）"
    )


@pytest.mark.asyncio
async def test_brand_new_empty_file_truncation_still_saves():
    """新建空文件的截断补全无害，必须保持原有行为（不能被覆盖保护误伤）。"""
    adapter = _make_adapter()
    adapter._save_file_content = AsyncMock(return_value=True)

    await _drive(
        adapter,
        [
            _create_file_result("f-new", content="", title="第9集 新集"),
            _text_event("<file>第9集 新集\n（开场）"),
            _message_end(stop_reason="end_turn"),
        ],
    )

    adapter._save_file_content.assert_awaited_once_with("f-new", "第9集 新集\n（开场）")


@pytest.mark.asyncio
async def test_reused_existing_drives_capture_even_with_non_empty_content():
    """进入捕获的判据是 reused_existing，而不是 content 是否为空。"""
    adapter = _make_adapter()
    adapter._save_file_content = AsyncMock(return_value=True)

    await _drive(
        adapter,
        [
            _create_file_result(
                "f-ep7",
                content="旧正文",
                reused_existing=True,
                original_content_length=3,
            ),
            _text_event("<file>新正文</file>"),
            _message_end(),
        ],
    )

    assert adapter._save_file_content.await_count == 1


@pytest.mark.asyncio
async def test_create_file_with_content_and_no_reuse_does_not_capture():
    """一次性带 content 的创建不进入捕获（既有行为，作为对照）。"""
    adapter = _make_adapter()
    adapter._save_file_content = AsyncMock(return_value=True)

    events = await _drive(
        adapter,
        [
            _create_file_result("f-full", content="完整正文", file_type="draft"),
            _text_event("已经写好了。"),
            _message_end(),
        ],
    )

    assert adapter._pending_file_write is None
    adapter._save_file_content.assert_not_awaited()
    assert _chat(events) == "已经写好了。"


def test_auto_completed_flag_only_set_without_end_marker():
    """StreamResult.auto_completed 只在"没等到 </file>"的补全路径置位。"""
    proc = StreamProcessor()
    proc.start_file_write("f-1")
    proc.process_content("<file>正文")
    truncated = proc.finalize_on_stream_end()
    assert truncated.file_complete is True
    assert truncated.auto_completed is True

    proc.start_file_write("f-2")
    completed = proc.process_content("<file>正文</file>")
    assert completed.file_complete is True
    assert completed.auto_completed is False


# ------------------------------------------------------------------- 缺陷 #4 --


def _drive_processor(chunks: list[str]):
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    results = [proc.process_content(c) for c in chunks]
    final = proc.finalize_on_stream_end()
    return results, final, proc


def _all_conversation(results) -> str:
    return "".join(
        r.conversation_content + r.conversation_content_after_file for r in results
    )


def test_odd_fence_in_body_does_not_swallow_real_end_marker():
    """正文里有未闭合的 ```、</file> 之后叙述里又出现 ```：结束标记必须生效。"""
    stream = (
        "<file>第一章\n```text\n示例未闭合\n夜色渐深。</file>"
        "第一章已写好。参考格式：\n```\n第X章\n```\n请查收。"
    )
    for chunk in (1, 3, 7, len(stream)):
        results, final, proc = _drive_processor(
            [stream[i : i + chunk] for i in range(0, len(stream), chunk)]
        )
        done = next(
            (r for r in results if r.file_complete),
            final if final.file_complete else None,
        )
        assert done is not None, f"chunk={chunk} 文件未落库"
        assert done.final_content == "第一章\n```text\n示例未闭合\n夜色渐深。"
        convo = _all_conversation([*results, final])
        assert "第一章已写好" in convo
        assert "</file>" not in done.final_content
        assert proc.state == StreamState.IDLE


def test_closing_fence_after_end_marker_is_not_treated_as_body():
    """模型把闭合围栏写到 </file> 之后（正文内围栏数为奇数）的生产化错格式。"""
    stream = (
        "<file>```markdown\n第一章\n\n夜色渐深，风穿过长街。\n</file>\n```\n"
        "**第一章** 已写入，接下来我会写第二章。"
    )
    results, final, _ = _drive_processor(
        [stream[i : i + 7] for i in range(0, len(stream), 7)]
    )
    done = next(
        (r for r in results if r.file_complete),
        final if final.file_complete else None,
    )
    assert done is not None
    assert "</file>" not in done.final_content
    assert "接下来我会写第二章" not in done.final_content
    assert "接下来我会写第二章" in _all_conversation([*results, final])


def test_task_complete_tail_no_longer_eats_the_whole_chapter():
    """变体 B：尾部带 [TASK_COMPLETE] 时，正文仍应落库而不是整段倒进聊天。"""
    stream = (
        "<file>第一章\n```text\n示例未闭合\n夜色渐深。</file>"
        "第一章已写好。参考格式：\n```\n第X章\n```\n请查收。\n[TASK_COMPLETE]"
    )
    results, final, _ = _drive_processor([stream])
    done = next(
        (r for r in results if r.file_complete),
        final if final.file_complete else None,
    )
    assert done is not None
    assert done.final_content == "第一章\n```text\n示例未闭合\n夜色渐深。"
    assert "[TASK_COMPLETE]" in _all_conversation([*results, final])


def test_genuine_fenced_end_marker_literal_is_still_protected():
    """对照：围栏内的 </file> 字面量 + 之后存在真实结束标记 -> 仍按字面量处理。"""
    body = "第一段\n```\n</file>\n```\n第二段"
    results, final, proc = _drive_processor(["<file>" + body, "</file>"])
    done = next(
        (r for r in results if r.file_complete),
        final if final.file_complete else None,
    )
    assert done is not None
    assert done.final_content == body
    assert proc.state == StreamState.IDLE


def test_suspended_marker_candidate_is_forced_real_after_cap():
    """悬置的 </file> 候选累计超过上限后按真实结束标记处理，不再无限期挂起。"""
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    # 未闭合围栏 -> 后面的 </file> 起初无法判定
    proc.process_content("<file>正文\n```text\n未闭合\n")
    proc.process_content("结束。</file>")
    assert proc.state == StreamState.WRITING  # 仍在等闭合围栏
    tail = "尾巴" * (AMBIGUOUS_MARKER_MAX_PENDING // 2 + 10)
    completed = proc.process_content(tail)
    assert completed.file_complete is True
    assert completed.final_content == "正文\n```text\n未闭合\n结束。"
    assert completed.auto_completed is False
    assert proc.state == StreamState.IDLE


def test_finalize_refuses_body_carrying_end_marker_literal():
    """自动补全路径拒绝含 </file> 字面量的缓冲：那是错位吞进来的聊天叙述。"""
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    # 先让一段含 </file> 字面量的内容进入 content_buffer（围栏内受保护），
    # 随后流结束却始终没有真实结束标记。
    proc.process_content("<file>第一段\n```\n</file>\n```\n第二段</file>")
    proc.reset()
    proc.start_file_write("file-1")
    proc.state = StreamState.WRITING
    proc.content_buffer = "第一段\n```\n</file>\n```\n第二段"
    final = proc.finalize_on_stream_end()
    assert final.file_complete is False
    assert "</file>" in final.conversation_content
    assert proc.state == StreamState.IDLE


# ------------------------------------------------------------------ 缺陷 #20 --


def _edit_result_event(payload: dict) -> WorkflowStreamEvent:
    body = json.dumps({"status": "success", "data": payload}, ensure_ascii=False)
    return WorkflowStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "tool_use_id": "call-edit",
            "name": "edit_file",
            "result": {"content": [{"type": "text", "text": body}]},
        },
    )


@pytest.mark.asyncio
async def test_partial_edit_failures_are_reported():
    """部分失败：进度总数、逐条失败事件、失败计数都必须出现。"""
    adapter = _make_adapter()
    events = await _drive(
        adapter,
        [
            _edit_result_event(
                {
                    "id": "f-1",
                    "title": "第一章",
                    "file_type": "draft",
                    "edits_applied": 1,
                    "new_length": 100,
                    "details": [{"op": "replace", "old_preview": "A", "new_preview": "B"}],
                    "failed_edits": [
                        {"index": 1, "op": "replace", "error": "old text not found"},
                        {"index": 2, "op": "delete", "error": "多处匹配已中止"},
                    ],
                    "partial_success": True,
                    "all_failed": False,
                    "warnings": ["Edit 1: failed and skipped"],
                }
            ),
            _message_end(),
        ],
    )

    start = next(e for e in events if e.type == EventType.FILE_EDIT_START)
    assert start.data["total_edits"] == 3

    applied = [e for e in events if e.type == EventType.FILE_EDIT_APPLIED]
    assert len(applied) == 3
    assert [e.data["success"] for e in applied] == [True, False, False]
    assert [e.data["edit_index"] for e in applied] == [0, 1, 2]
    assert applied[1].data["error"] == "old text not found"

    end = next(e for e in events if e.type == EventType.FILE_EDIT_END)
    assert end.data["edits_applied"] == 1
    assert end.data["failed_count"] == 2
    assert end.data["partial_success"] is True
    assert end.data["warnings"] == ["Edit 1: failed and skipped"]


@pytest.mark.asyncio
async def test_all_failed_edit_is_reported_as_error():
    """全部失败时工具结果必须是 error，不能渲染成绿色成功卡片。"""
    adapter = _make_adapter()
    events = await _drive(
        adapter,
        [
            _edit_result_event(
                {
                    "id": "f-1",
                    "title": "第一章",
                    "file_type": "draft",
                    "edits_applied": 0,
                    "new_length": 100,
                    "details": [],
                    "failed_edits": [{"index": 0, "op": "replace", "error": "原文不存在"}],
                    "partial_success": False,
                    "all_failed": True,
                    "warnings": [],
                }
            ),
            _message_end(),
        ],
    )

    tool_result = next(e for e in events if e.type == EventType.TOOL_RESULT)
    assert tool_result.data["status"] == "error"
    assert "原文不存在" in (tool_result.data["error"] or "")

    end = next(e for e in events if e.type == EventType.FILE_EDIT_END)
    assert end.data["all_failed"] is True
    assert end.data["failed_count"] == 1


@pytest.mark.asyncio
async def test_silent_skip_warnings_reach_the_end_event():
    """静默跳过（只进 warnings、不进 failed_edits）也必须上屏。"""
    adapter = _make_adapter()
    events = await _drive(
        adapter,
        [
            _edit_result_event(
                {
                    "id": "f-1",
                    "title": "第一章",
                    "file_type": "draft",
                    "edits_applied": 2,
                    "new_length": 100,
                    "details": [
                        {"op": "replace", "old_preview": "A", "new_preview": "B"},
                        {"op": "append", "text_preview": "新段落", "text_len": 3},
                    ],
                    "failed_edits": [],
                    "partial_success": False,
                    "all_failed": False,
                    "warnings": ["Edit 1: missing old for replace; skipped"],
                }
            ),
            _message_end(),
        ],
    )

    end = next(e for e in events if e.type == EventType.FILE_EDIT_END)
    assert end.data["warnings"] == ["Edit 1: missing old for replace; skipped"]
    assert end.data["failed_count"] == 0

    applied = [e for e in events if e.type == EventType.FILE_EDIT_APPLIED]
    # append/prepend 的 detail 没有 new_preview，回退到 text_preview
    assert applied[1].data["new_preview"] == "新段落"


# ------------------------------------------------------------------ 缺陷 #33 --


@pytest.mark.asyncio
async def test_usage_is_accumulated_across_agents():
    """多 agent 各发一次 MESSAGE_END：usage 必须累加而不是覆盖。

    另外：prompt/completion 这一族别名键必须先归一到规范键
    （input_tokens/output_tokens）再累加，否则两族键在同一个 dict 里并存，
    下游 writing_stats_service 只认规范键，别名那族被静默丢弃。
    """
    adapter = _make_adapter()
    await _drive(
        adapter,
        [
            _message_end(
                stop_reason="end_turn",
                usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            ),
            _message_end(
                stop_reason="end_turn",
                usage={"input_tokens": 300, "output_tokens": 50, "total_tokens": 350},
            ),
        ],
    )

    usage = adapter.get_last_message_metadata()["usage"]
    assert usage == {
        "input_tokens": 400,
        "output_tokens": 70,
        "total_tokens": 470,
    }


@pytest.mark.asyncio
async def test_usage_missing_on_later_agent_keeps_previous_total():
    """后续 agent 没回传 usage 时不能把已累计的统计清空。"""
    adapter = _make_adapter()
    await _drive(
        adapter,
        [
            _message_end(usage={"total_tokens": 120}),
            _message_end(stop_reason="end_turn"),
        ],
    )

    assert adapter.get_last_message_metadata()["usage"] == {"total_tokens": 120}


# ------------------------------------------------ 技能用量来自 load_skill --


def _load_skill_result(skill_id: str, skill_name: str, *, status: str = "success") -> WorkflowStreamEvent:
    if status == "success":
        payload = {
            "status": "success",
            "data": {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "source": "user",
                "instructions": "先强化钩子。",
                "resources": [],
            },
        }
    else:
        payload = {"status": "error", "error": "未找到已启用的技能"}
    return WorkflowStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "tool_use_id": f"call-{skill_id}",
            "name": "load_skill",
            "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
        },
    )


@pytest.mark.asyncio
async def test_load_skill_result_emits_skill_matched_once_per_skill():
    adapter = _make_adapter()
    events = await _drive(
        adapter,
        [
            _load_skill_result("s-1", "悬念大师"),
            _load_skill_result("s-1", "悬念大师"),
            _load_skill_result("s-2", "节奏控"),
            _message_end(),
        ],
    )

    matched = [e.data for e in events if e.type == EventType.SKILL_MATCHED]
    assert matched == [
        {"skill_id": "s-1", "skill_name": "悬念大师", "matched_trigger": "load_skill"},
        {"skill_id": "s-2", "skill_name": "节奏控", "matched_trigger": "load_skill"},
    ]
    # skill_matched 紧跟在对应的 tool_result 之后、done 之前
    assert events[-1].type == EventType.DONE


@pytest.mark.asyncio
async def test_failed_load_skill_does_not_emit_skill_matched():
    adapter = _make_adapter()
    events = await _drive(adapter, [_load_skill_result("s-1", "悬念大师", status="error"), _message_end()])

    assert not [e for e in events if e.type == EventType.SKILL_MATCHED]


@pytest.mark.asyncio
async def test_bracket_text_is_passed_through_verbatim():
    """适配器不再解析或剥离任何「技能标记」：模型输出的文本原样下发，一个字都不丢。"""
    adapter = _make_adapter()
    events = await _drive(
        adapter,
        [
            _text_event("[使用技"),
            _text_event("能: 悬念大师]\n夜色"),
            _text_event("渐深。"),
            _message_end(),
        ],
    )

    assert _chat(events) == "[使用技能: 悬念大师]\n夜色渐深。"
    assert not [e for e in events if e.type == EventType.SKILL_MATCHED]


# --------------------------------------------------- 缺陷 #3（端到端护栏） --
#
# 上面那组用例把 create_file 的返回值和 _save_file_content 都换成了 mock，
# 只锁住"适配器层的语义"。缺陷 #3 真正的伤害是**数据丢失**（实测 8000 字的
# 已完成分集被 19 字残稿整体覆盖），所以这里再补一组端到端用例：
# 真实的 invoke_project_tool("create_file") 走幂等复用分支、真实的
# StreamAdapter.process_workflow_events 跑流、最后直接断言库里的 File.content。
# 只要将来有人把覆盖保护重构掉，这三条会立刻变红。

_E2E_EPISODE_TITLE = "第3集 迷雾"
_E2E_OLD_BODY = "旧正文。" * 2000  # 已完成分集：8000 字
_E2E_TRUNCATED_PROSE = "（新版开场 300 字）"


@pytest.fixture
def _e2e_user(db_session: Session):
    from models import User

    user = User(
        email="round3_stream_e2e@example.com",
        username="round3_stream_e2e",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def _e2e_project(db_session: Session, _e2e_user):
    from models import File, Project

    project = Project(
        name="剧本项目",
        owner_id=_e2e_user.id,
        project_type="screenplay",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # 剧本项目的分集必须挂在剧本根目录下，复用分支才会命中。
    db_session.add(
        File(
            id=f"{project.id}-script-folder",
            project_id=project.id,
            title="剧本",
            file_type="folder",
            order=0,
            parent_id=None,
        )
    )
    db_session.commit()
    return project


@pytest.fixture
def _e2e_stream_save_session(monkeypatch):
    """让 StreamAdapter 的落库分支（独立 session）也落到测试库。

    _save_file_content_sync 走的是 database.get_session / create_session，
    不复用 ToolContext 里的 session，不重定向就会写到真实的 zenstory.db。
    """
    import database
    from tests.conftest import TestSessionLocal

    def _get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(database, "get_session", _get_session)
    monkeypatch.setattr(database, "create_session", TestSessionLocal)
    monkeypatch.setattr(database, "is_postgres", False)
    yield


def _first_index(chunks: list[str], needle: str) -> int:
    """返回第一个包含 needle 的下发片段下标，没有则 -1。"""
    return next((i for i, text in enumerate(chunks) if needle in text), -1)


def _seed_finished_episode(db_session: Session, project, body: str = _E2E_OLD_BODY) -> str:
    """预置一集"已经写满"的剧本，返回文件 id。"""
    from models import File

    episode = File(
        project_id=project.id,
        title=_E2E_EPISODE_TITLE,
        content=body,
        file_type="script",
        parent_id=f"{project.id}-script-folder",
        order=3,
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)
    return episode.id


async def _run_e2e_turn(
    project_id: str,
    user_id: str,
    title: str,
    *,
    close_file: bool = False,
    agent_boundary: bool = False,
    end_with_message_end: bool = True,
):
    """真实跑一轮"create_file + <file> 流式正文"。

    close_file           模型是否正常写出 </file>
    end_with_message_end 是否以 MESSAGE_END 收尾（False 表示流被直接掐断，
                         走 process_workflow_events 尾部的收尾分支）
    agent_boundary       MESSAGE_END 之后是否还有第二个 agent 的 turn
                         （走 MESSAGE_END 里的 agent 边界收尾分支）
    """
    from agent.openai_agents.events import mcp_text_result
    from agent.openai_agents.tools_adapter import invoke_project_tool

    adapter = StreamAdapter(
        StreamAdapterConfig(
            project_id=project_id,
            user_id=user_id,
            process_file_markers=True,
        )
    )
    args = json.dumps(
        {
            "title": title,
            "file_type": "script",
            "parent_id": f"{project_id}-script-folder",
        },
        ensure_ascii=False,
    )
    result_text = await invoke_project_tool("create_file", args)

    body = f"<file>{title}\n{_E2E_TRUNCATED_PROSE}"
    if close_file:
        body += "</file>"

    events = [
        WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": "call-e2e",
                "name": "create_file",
                "result": mcp_text_result(result_text),
            },
        ),
        _text_event(body),
    ]
    if end_with_message_end:
        events.append(_message_end(stop_reason="end_turn"))
    if agent_boundary:
        # 交接之后第二个 agent 继续说话，本轮流并未结束
        events.append(_text_event("接下来我继续。"))
        events.append(_message_end(stop_reason="end_turn"))

    sse_events = await _drive(adapter, events)
    return json.loads(result_text), sse_events


@pytest.mark.asyncio
async def test_reused_episode_body_survives_truncated_stream(
    db_session: Session, _e2e_user, _e2e_project, _e2e_stream_save_session
):
    """主护栏：复用已写满的分集 + 模型漏写 </file>，库里的 8000 字必须一字不少。

    这里不给 MESSAGE_END，流直接结束，走 process_workflow_events 尾部的
    finalize_on_stream_end 收尾分支。
    """
    from agent.tools.mcp_tools import ToolContext
    from models import File

    file_id = _seed_finished_episode(db_session, _e2e_project)
    ToolContext.set_context(db_session, _e2e_user.id, _e2e_project.id, None)

    payload, sse_events = await _run_e2e_turn(
        _e2e_project.id,
        _e2e_user.id,
        _E2E_EPISODE_TITLE,
        end_with_message_end=False,
    )
    assert payload["data"]["id"] == file_id, "没有命中幂等复用分支，用例失去意义"

    db_session.expire_all()
    stored = db_session.get(File, file_id)
    assert len(stored.content or "") == len(_E2E_OLD_BODY), (
        f"数据丢失：8000 字的分集被截断残稿覆盖成 {len(stored.content or '')} 字"
    )
    assert stored.content == _E2E_OLD_BODY
    # 作者知道本次没有保存；内部协议与保护诊断不进入聊天正文。
    chat = _chat(sse_events)
    assert "本次改写未保存，原文保持不变。" in chat
    assert "</file>" not in chat and "edit_file" not in chat


@pytest.mark.asyncio
async def test_reused_episode_normal_stream_still_persists(
    db_session: Session, _e2e_user, _e2e_project, _e2e_stream_save_session
):
    """对偶用例：同一条复用路径，模型正常写出 </file> 时新正文必须落库。

    防止后续把拒绝条件放宽成"只看 original_content_length > 0"而漏掉
    auto_completed —— 那样正常的整集重写也会被守卫拦下。
    """
    from agent.tools.mcp_tools import ToolContext
    from models import File

    file_id = _seed_finished_episode(db_session, _e2e_project)
    ToolContext.set_context(db_session, _e2e_user.id, _e2e_project.id, None)

    payload, _ = await _run_e2e_turn(
        _e2e_project.id,
        _e2e_user.id,
        _E2E_EPISODE_TITLE,
        close_file=True,
    )
    assert payload["data"]["id"] == file_id, "没有命中幂等复用分支，用例失去意义"

    db_session.expire_all()
    stored = db_session.get(File, file_id)
    assert stored.content == f"{_E2E_EPISODE_TITLE}\n{_E2E_TRUNCATED_PROSE}", (
        f"合法的整集重写被守卫误伤：{(stored.content or '')[:80]!r}"
    )


@pytest.mark.asyncio
async def test_truncated_overwrite_guard_covers_agent_boundary_path(
    db_session: Session, _e2e_user, _e2e_project, _e2e_stream_save_session
):
    """补分支：MESSAGE_END 的 agent 边界收尾也必须挡住整体覆盖。

    这条路径与上面那条"流被掐断"的收尾是 stream_adapter 里两处不同的
    finalize_on_stream_end 调用点，只覆盖其中一处等于漏了一半。
    断言警告文本出现在第二个 agent 的叙述**之前**，以此证明收尾确实发生在
    agent 边界上，而不是被推迟到流结束。
    """
    from agent.tools.mcp_tools import ToolContext
    from models import File

    file_id = _seed_finished_episode(db_session, _e2e_project)
    ToolContext.set_context(db_session, _e2e_user.id, _e2e_project.id, None)

    _, sse_events = await _run_e2e_turn(
        _e2e_project.id,
        _e2e_user.id,
        _E2E_EPISODE_TITLE,
        agent_boundary=True,
    )

    db_session.expire_all()
    stored = db_session.get(File, file_id)
    assert len(stored.content or "") == len(_E2E_OLD_BODY), (
        f"数据丢失（agent 边界路径）：只剩 {len(stored.content or '')} 字"
    )
    assert stored.content == _E2E_OLD_BODY

    chat_chunks = _texts(sse_events, EventType.CONTENT, "text")
    warning_index = _first_index(chat_chunks, "本次改写未保存")
    boundary_index = _first_index(chat_chunks, "接下来我继续")
    assert warning_index >= 0, "没有向作者说明本次改写未保存"
    assert boundary_index >= 0, "第二个 agent 的叙述被文件捕获吞掉了：边界没有收尾"
    assert warning_index < boundary_index, "收尾没有发生在 agent 边界上，而是被拖到了流结束"


@pytest.mark.asyncio
async def test_selected_skills_are_not_announced_again_by_load_skill():
    """显式选择的技能在流开头已发过 skill_matched，之后 load_skill 它不再重复发。"""
    adapter = StreamAdapter(StreamAdapterConfig(project_id="p", matched_skill_ids=("s-1",)))
    events = await _drive(
        adapter,
        [_load_skill_result("s-1", "悬念大师"), _load_skill_result("s-2", "节奏控"), _message_end()],
    )

    matched = [e.data["skill_id"] for e in events if e.type == EventType.SKILL_MATCHED]
    assert matched == ["s-2"]


@pytest.mark.asyncio
async def test_skill_tool_results_sent_to_client_omit_full_content():
    """浏览器只拿技能名/路径/大小；完整方法与资源正文只给模型（不经过适配器）。"""
    load_payload = {
        "status": "success",
        "data": {
            "skill_id": "s-1",
            "skill_name": "悬念大师",
            "source": "user",
            "instructions": "机密方法" * 100,
            "resources": [{"path": "references/a.md", "size": 3}],
        },
    }
    read_payload = {
        "status": "success",
        "data": {"skill_name": "悬念大师", "path": "references/a.md", "content": "机密正文"},
    }

    def _result(name: str, payload: dict) -> WorkflowStreamEvent:
        return WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": f"call-{name}",
                "name": name,
                "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
            },
        )

    events = await _drive(
        _make_adapter(),
        [_result("load_skill", load_payload), _result("read_skill_resource", read_payload), _message_end()],
    )

    results = {e.data["tool_name"]: e.data["data"] for e in events if e.type == EventType.TOOL_RESULT}
    assert results["load_skill"] == {
        "skill_id": "s-1",
        "skill_name": "悬念大师",
        "source": "user",
        "instructions_chars": 400,
        "resources": [{"path": "references/a.md", "size": 3}],
    }
    assert results["read_skill_resource"] == {
        "skill_name": "悬念大师",
        "path": "references/a.md",
        "size": len("机密正文".encode()),
    }
    assert "机密" not in json.dumps([e.data for e in events], ensure_ascii=False)
