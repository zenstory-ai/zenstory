"""单循环 agent 上线后真实生产故障的回归测试（2026-11-18）。

覆盖四类生产事故（均用合成文本，不含真实用户数据）：
1. 中文会话在 ~30 万实测 token 时因 cl100k_base 估算破 80 万被逐轮砍掉旧消息、死锁压缩；
2. 模型输出夹带 NUL 字节（\\x00）在 PostgreSQL 触发 DataError 导致落库失败，且未落库的流式正文仍触发计费；
3. 低字符集退化复读（如 dl/dl/… 或 18 字短句循环）因去重字符数 < 30 绕过重复检测器；
4. MiniMax 分隔符 ``]<]minimax[>[`` 吞掉 ``<`` 变成 ``]<]minimax[>[file>`` 漏捕获，或将文字版工具调用漏进对话气泡。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from agent.core.stream_billing import StreamBillingTracker
from agent.core.stream_processor import (
    PREBODY_FILE_ID,
    StreamProcessor,
    extract_reply_prebody,
    find_repetition_start,
)
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.compaction import compact_history, should_compact
from agent.loop.completion_evidence import unexecuted_tool_protocol
from agent.loop.history import LoopHistory, estimate_items_tokens
from agent.openai_agents.intra_run_trimmer import COMPACTION_REASON, IntraRunToolOutputTrimmer
from core.error_codes import ErrorCode
from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _seed_user_project_file(db_session: Session) -> tuple[User, Project, File]:
    user = User(
        email="loop_post_launch@example.com",
        username="looppostlaunch",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="合成测试作品", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    f = File(
        project_id=project.id,
        title="第1章 初稿",
        file_type="draft",
        content="",
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return user, project, f


# ---------------------------------------------------------------------------
# 1. 历史压缩门限与禁砍旧消息（P0-1）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compaction_default_threshold_is_300k_measured_tokens():
    """默认在实测 300k prompt_tokens 触发轮间压缩，不等到 600k。"""
    below = LoopHistory(
        items=[{"role": "user", "content": "写第1章"}],
        last_message_id="m1",
        last_prompt_tokens=280_000,
    )
    above = LoopHistory(
        items=[{"role": "user", "content": "写第1章"}],
        last_message_id="m1",
        last_prompt_tokens=310_000,
    )
    assert should_compact(below) is False
    assert should_compact(above) is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_compaction_summary_call_receives_full_chinese_history_at_400k_measured_tokens():
    """实测 40 万 token 的中文历史（cl100k_base 估算已超 100 万）送入摘要调用时不得先被截掉开头。"""
    chunk = "这是一段用于测试中文编码膨胀系数的合成设定与章节回执内容。" * 550
    items = [{"role": "user", "content": f"第{i}轮：{chunk}"} for i in range(60)]
    # 确认 cl100k_base 口径已经显著超过旧的 900k 估算上限，而真实实测只有 395,917。
    assert estimate_items_tokens(items) > 950_000

    history = LoopHistory(
        items=items,
        last_message_id="msg-60",
        last_prompt_tokens=395_917,
        author_messages=["写下一章"],
    )
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="合成交接摘要"), finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=396_000,
            completion_tokens=800,
            total_tokens=396_800,
            prompt_tokens_details=SimpleNamespace(cached_tokens=350_000),
        ),
    )
    mock_call = AsyncMock(return_value=(completion, SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")))
    with patch("agent.core.llm_failover.acreate_chat_completion", mock_call):
        outcome = await compact_history(history, system_prompt="系统提示", session_id="sess-1")

    assert outcome.record is not None
    sent_messages = mock_call.await_args.kwargs["messages"]
    # system + 60 条完整历史 + COMPACT_PROMPT = 62 条，第一条历史未被截掉。
    assert len(sent_messages) == 62
    assert sent_messages[1]["content"].startswith("第0轮：")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_process_stream_never_drops_chinese_history_below_real_provider_window(
    db_session: Session, writing_prompt_configs, shell_engine
):
    """实测 28 万 token（或 35 万跳过压缩一次）但 cl100k_base > 85 万时，绝不滑窗丢弃旧消息。"""
    from agent.service import AgentService

    user, project, _ = _seed_user_project_file(db_session)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="长连载会话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    from datetime import datetime, timedelta

    base_time = datetime(2026, 11, 18, 0, 0, 0)
    big_text = "合成连载历史内容，检验不会因为本地分词器虚高而砍掉旧消息。" * 500
    for idx in range(55):
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="user",
                content=f"用户第{idx}条：{big_text}",
                created_at=base_time + timedelta(seconds=idx * 2),
            )
        )
        is_last = idx == 54
        db_session.add(
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content=f"助手第{idx}条回复。",
                created_at=base_time + timedelta(seconds=idx * 2 + 1),
                message_metadata=json.dumps(
                    {
                        "engine": "loop",
                        "prompt_tokens": 280_000 if is_last else 100_000,
                        "display_events": [{"type": "content", "content": f"助手第{idx}条回复。"}],
                    },
                    ensure_ascii=False,
                ),
            )
        )
    db_session.commit()

    captured_histories: list[list[dict[str, Any]]] = []

    def fake_loop(state, **_kwargs):
        captured_histories.append(list(state.get("history_items") or []))

        async def _gen():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "本轮完成。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _gen()

    with (
        patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        chunks = [
            c
            async for c in AgentService().process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="继续写下一章",
                session=db_session,
                session_id=chat.id,
                metadata={},
            )
        ]

    assert chunks
    assert len(captured_histories) == 1
    # 55 轮 user + 55 轮 assistant = 110 项全部原样保留，第 0 条仍在（cl100k_base 已超 850k）。
    assert estimate_items_tokens(captured_histories[0]) > 850_000
    assert len(captured_histories[0]) == 110
    assert captured_histories[0][0]["content"].startswith("用户第0条：")

    # 极端兜底：当压缩连续失败、实测已达 960k（超过 800k 上限）时，只按比例裁掉最老约 17%，不会一刀砍掉大半。
    from agent.loop.history import bound_history_for_provider

    over_history = LoopHistory(items=captured_histories[0], last_prompt_tokens=960_000)
    degraded = bound_history_for_provider(over_history, captured_histories[0], 800_000)
    assert 80 <= len(degraded) < 110
    assert degraded[0].get("type") != "function_call_output"


@pytest.mark.unit
def test_intra_run_trimmer_stays_above_compaction_and_recognizes_write_chapter():
    """100 万字符（约 30 万实测 token）内不折叠任何历史；超安全阀时 write_chapter 也作为写工具折叠。"""
    from agents.run_config import CallModelData, ModelInputData

    trimmer = IntraRunToolOutputTrimmer()
    assert trimmer.max_input_chars >= 1_500_000

    items: list[dict[str, Any]] = [
        {
            "type": "function_call",
            "call_id": "wc-1",
            "name": "write_chapter",
            "arguments": json.dumps(
                {"chapter": 1, "title": "第1章", "content": "正文" * 2000},
                ensure_ascii=False,
            ),
        },
        {
            "type": "function_call_output",
            "call_id": "wc-1",
            "output": json.dumps(
                {"status": "success", "data": {"id": "ch-1", "title": "第1章", "note": "回执" * 2000}},
                ensure_ascii=False,
            ),
        },
        {
            "type": "function_call",
            "call_id": "q-last",
            "name": "query_files",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "q-last",
            "output": json.dumps({"status": "success", "data": {"text": "保留" * 1500}}, ensure_ascii=False),
        },
    ]
    # 常态安全阀下原样返回同一对象
    md_normal = ModelInputData(input=items, instructions="系统" * 50_000)
    assert trimmer(CallModelData(model_data=md_normal, agent=None, context=None)) is md_normal

    # 显式压低安全阀时，顶层 write_chapter 的参数与成功回执都可按写工具折叠
    tight = IntraRunToolOutputTrimmer(max_input_chars=6_000)
    compacted = tight(CallModelData(model_data=ModelInputData(input=items, instructions=""), agent=None, context=None)).input
    assert COMPACTION_REASON in compacted[0]["arguments"]
    assert COMPACTION_REASON in compacted[1]["output"]


# ---------------------------------------------------------------------------
# 2. NUL 字节清洗与流式落库失败免扣费（P0-2）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_file_ops_and_message_manager_strip_nul_bytes(db_session: Session):
    """create_file / update_file / edit_file / replace_body / MessageManager 落库前剥离 \\x00。"""
    from agent.core.message_manager import MessageManager
    from agent.tools.file_ops import FileToolExecutor
    from agent.tools.mcp_tools import ToolContext

    user, project, draft = _seed_user_project_file(db_session)
    ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id="s-test")
    try:
        executor = FileToolExecutor(session=db_session, user_id=user.id)

        created = executor.create_file(
            project_id=project.id,
            title="大纲\x00总表",
            file_type="outline",
            content="第一条\x00设定\n第二条",
        )
        assert "\x00" not in created["title"]
        assert created["content"] == "第一条设定\n第二条"

        updated = executor.update_file(id=draft.id, content="　　第一章开头\x00没有空字节。\n\n　　第二段。")
        assert "\x00" not in updated["content"]

        edited = executor.edit_file(
            id=draft.id,
            edits=[{"op": "replace", "old": "没有空字节。", "new": "依然\x00干净。"}],
        )
        assert edited["edits_applied"] == 1
        db_session.refresh(draft)
        assert "\x00" not in (draft.content or "")

        replaced = executor.replace_body(
            id=draft.id,
            content="　　整章替换的新正文\x00也不含空字节。",
            expected_updated_at=draft.updated_at.isoformat(),
        )
        assert replaced["mutation_applied"] is True
        db_session.refresh(draft)
        assert "\x00" not in (draft.content or "")

        chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
        db_session.add(chat)
        db_session.commit()
        db_session.refresh(chat)
        mm = MessageManager(project_id=project.id, user_id=user.id)
        msg_id = mm._save_messages_with_session(
            db_session,
            chat.id,
            "用户消息\x00测试",
            "助手回复\x00测试",
            None,
            "思考过程\x00测试",
        )
        saved_msg = db_session.get(ChatMessage, msg_id)
        assert saved_msg is not None
        assert "\x00" not in saved_msg.content
        assert "\x00" not in (saved_msg.reasoning_content or "")
    finally:
        ToolContext.clear_context()


@pytest.mark.unit
def test_failed_streamed_file_save_refunds_and_keeps_placeholder_removable():
    """流式发过 file_content 但最终落库失败（ERR_AGENT_FILE_SAVE_FAILED / 回执 None）时，不算已产出，照常退还并回收空文件。"""
    tracker = StreamBillingTracker()
    tracker.observe(_sse("session_started", {"session_id": "s1"}))
    tracker.observe(
        _sse(
            "tool_result",
            {
                "tool_name": "create_file",
                "status": "success",
                "data": {
                    "id": "outline-1",
                    "title": "总纲",
                    "file_type": "outline",
                    "content": "",
                    "updated_at": "2026-11-18T01:20:00",
                },
            },
        )
    )
    # <file> 之前的短引子不算完整回复：落库失败回滚后 ended_on_reply 仍为 False
    tracker.observe(_sse("content", {"text": "总纲如下：\n"}))
    tracker.observe(_sse("file_content", {"file_id": "outline-1", "chunk": "流式了一半的大纲正文"}))
    tracker.observe(
        _sse(
            "error",
            {
                "message": "文件保存失败，请重试",
                "code": ErrorCode.AGENT_FILE_SAVE_FAILED,
                "retryable": True,
                "refundable": True,
                "file_id": "outline-1",
            },
        )
    )
    tracker.note_saved_receipts({"outline-1": None})

    assert tracker.file_written is False
    assert tracker.write_succeeded is False
    assert tracker.produced_output is False
    assert tracker.decide(client_disconnected=False, unexpected_exception=False) == ("internal_error", True)
    assert tracker.removable_placeholders() == [
        {"id": "outline-1", "title": "总纲", "updated_at": "2026-11-18T01:20:00"}
    ]


# ---------------------------------------------------------------------------
# 3. 低字符集退化复读检测（P1-1）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_repetition_detector_catches_short_token_and_short_sentence_loops():
    """dl/dl/… 与不足 30 个不同字符的短句死循环必须被截停，而纯分隔线仍放行。"""
    prefix = "【写法与基调】\n第一章已定下的调子，后续各章沿用。\n"
    dl_loop = "dl/" * 1000
    assert find_repetition_start(prefix + dl_loop) == len(prefix) + 3

    sentence = "第二章节的正文写完后，桌上就只剩这支笔。\n\n"
    assert len(set(sentence) - set(" \t\r\n")) < 30
    repeated = "【引子】" + sentence * 120
    cut = find_repetition_start(repeated)
    assert cut == len("【引子】") + len(sentence)
    assert repeated[:cut].count("桌上就只剩这支笔") == 1

    # 纯标点分隔线、Markdown 表格分隔行、空行仍不算循环
    assert find_repetition_start("——\n\n" * 3000) is None
    assert find_repetition_start("---\n" * 3000) is None
    assert find_repetition_start("| --- | --- |\n" * 500) is None


# ---------------------------------------------------------------------------
# 4. MiniMax 协议分隔符泄漏兼容与剥离（P1-2）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stream_processor_captures_minimax_mangled_file_tags_across_chunks():
    """MiniMax 把 <file> 输出成 ]<]minimax[>[file> 时，WAITING_START 与 prebody 均正常捕获正文。"""
    proc = StreamProcessor(project_id="p", user_id="u")
    proc.start_file_write("f1")

    raw = "先建分集大纲。\n]<]minimax[>[file>\n【45-52集 分集大纲】\n第一条内容。\n]<]minimax[>[/file>\n收尾说明。"
    conv = []
    final = ""
    # 按 5 字符切片验证跨 chunk 重组
    for i in range(0, len(raw), 5):
        r = proc.process_content(raw[i : i + 5])
        if r.conversation_content:
            conv.append(r.conversation_content)
        if r.conversation_content_after_file:
            conv.append(r.conversation_content_after_file)
        if r.file_complete:
            final = r.final_content
    tail = proc.finalize_on_stream_end()
    if tail.conversation_content:
        conv.append(tail.conversation_content)

    assert final == "【45-52集 分集大纲】\n第一条内容。\n"
    assert "".join(conv) == "先建分集大纲。\n\n收尾说明。"

    # prebody_capture（先写正文、再调 create_file）同样能认出独占一行的 ]<]minimax[>[file>
    prebody_text = "]<]minimax[>[file>\n【预写正文】\n段落一。\n</file>"
    assert extract_reply_prebody(prebody_text) == "【预写正文】\n段落一。\n"

    proc_pre = StreamProcessor(project_id="p", user_id="u", prebody_capture=True)
    completed = None
    for i in range(0, len(prebody_text), 4):
        res = proc_pre.process_content(prebody_text[i : i + 4])
        if res.file_complete:
            completed = res
    assert completed is not None
    assert completed.file_id == PREBODY_FILE_ID
    assert completed.final_content == "【预写正文】\n段落一。\n"


@pytest.mark.unit
def test_stream_processor_strips_leaked_minimax_tool_call_blocks_from_chat():
    """对话里的 ]<]minimax[>[<tool_call>…（含完整块与末尾未闭合残片）不漏给作者，代码块与文件正文原样保留。"""
    proc = StreamProcessor(project_id="p", user_id="u")
    leaked = (
        "正在按细纲准备正文，先核对边界。\n\n"
        ']<]minimax[>[<tool_call>\n]<]minimax[>[<invoke name="query_files">]'
        "<]minimax[>[<id>abc</id>]<]minimax[>[</invoke>\n]<]minimax[>[</tool_call>\n\n"
        "核对完毕，已保存。"
    )
    emitted: list[str] = []
    for i in range(0, len(leaked), 6):
        res = proc.process_content(leaked[i : i + 6])
        if res.conversation_content:
            emitted.append(res.conversation_content)
    end = proc.finalize_on_stream_end()
    if end.conversation_content:
        emitted.append(end.conversation_content)

    joined = "".join(emitted)
    assert "minimax" not in joined
    assert "<tool_call>" not in joined
    assert "正在按细纲准备正文，先核对边界。" in joined
    assert "核对完毕，已保存。" in joined

    # 末尾未闭合的残片（生产样本 cab00f8c：]<]minimax[>[<tool_call>\n<）同样剥离
    proc2 = StreamProcessor(project_id="p", user_id="u")
    trunc = "我先把大纲条目补进文件。\n\n]<]minimax[>[<tool_call>\n<"
    out2 = [proc2.process_content(trunc[i : i + 4]).conversation_content for i in range(0, len(trunc), 4)]
    out2.append(proc2.finalize_on_stream_end().conversation_content)
    joined2 = "".join(out2)
    assert "minimax" not in joined2
    assert "tool_call" not in joined2
    assert joined2.strip() == "我先把大纲条目补进文件。"

    # 代码围栏内讨论该字符串时原样保留
    proc3 = StreamProcessor(project_id="p", user_id="u")
    fenced = "日志示例：\n```text\n]<]minimax[>[<tool_call>\n```\n结束。"
    out3 = proc3.process_content(fenced).conversation_content + proc3.finalize_on_stream_end().conversation_content
    assert "]<\x5dminimax[>[<tool_call>" in out3

    # completion_evidence 同样能识别吞掉 < 的 ]<]minimax[>[tool_call>
    swallowed = '好的。\n]<]minimax[>[tool_call>]<]minimax[>[invoke name="query_files">]<]minimax[>[/invoke>]<]minimax[>[/tool_call>'
    assert unexecuted_tool_protocol(swallowed, {"query_files"}) == ["query_files"]


# ---------------------------------------------------------------------------
# 5. 存量超长老会话未折叠工具载荷超 1M 窗口自愈（生产会话 a494f728 / 658fb656）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_compaction_folds_untrimmed_db_tool_payloads_and_escapes_960k_skip_deadlock():
    """库内未折叠工具载荷 > 115 万真实 token、但上一轮内存折叠后 last_prompt_tokens 仅 32~76 万（甚至 skipped=960k）时：
    1) should_compact 不会卡死在 960k + 100k > 1M；
    2) compact_history 先折叠超预算旧工具载荷，保留第 0 轮起的作者消息，不把 >1M 原始载荷直接发给供应商；
    3) bound_history_for_provider 对未折叠原始历史也会按真实估算兜底裁剪。
    """
    from agent.loop.history import bound_history_for_provider, estimate_provider_items_tokens

    big_read = json.dumps({"status": "success", "data": {"content": "章节设定与正文片段" * 3500}}, ensure_ascii=False)
    items: list[dict[str, Any]] = []
    for idx in range(75):
        items.append({"role": "user", "content": f"第{idx}轮指令：推进剧情"})
        items.append({"type": "function_call", "call_id": f"q-{idx}", "name": "query_files", "arguments": "{}"})
        items.append({"type": "function_call_output", "call_id": f"q-{idx}", "output": big_read})
        items.append({"role": "assistant", "content": f"第{idx}轮处理完毕。"})

    raw_provider_tokens = estimate_provider_items_tokens(items)
    assert raw_provider_tokens > 1_100_000

    # 模拟上一轮内存折叠后记下的较小 prompt_tokens（322,605），以及此前在 960,416 处失败留下的 skipped 记录。
    history = LoopHistory(
        items=items,
        last_message_id="msg-last",
        last_prompt_tokens=322_605,
        compaction_skipped_at_tokens=960_416,
        author_messages=["第0轮指令：推进剧情", "第74轮指令：推进剧情"],
    )
    assert should_compact(history) is True

    # 若不折叠而直接走 run 兜底，bound_history_for_provider 也会识破未折叠原始体积 > 750k 并对齐到 user 边界裁剪。
    run_bounded = bound_history_for_provider(history, history.items, 750_000)
    assert len(run_bounded) < len(items)
    assert run_bounded[0].get("role") == "user"
    assert estimate_provider_items_tokens(run_bounded) <= 800_000

    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="超长老会话自愈交接摘要"), finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=180_000,
            completion_tokens=900,
            total_tokens=180_900,
            prompt_tokens_details=SimpleNamespace(cached_tokens=120_000),
        ),
    )
    mock_call = AsyncMock(return_value=(completion, SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")))
    with patch("agent.core.llm_failover.acreate_chat_completion", mock_call):
        outcome = await compact_history(history, system_prompt="系统提示", session_id="sess-giant")

    assert outcome.record is not None
    assert outcome.record["summary"] == "超长老会话自愈交接摘要"
    sent_messages = mock_call.await_args.kwargs["messages"]
    sent_chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in sent_messages)
    # 折叠旧 query_files 输出后总字符远低于 150 万，且第 0 轮作者指令完整保留在开头！
    assert sent_chars < 1_500_000
    assert sent_messages[1] == {"role": "user", "content": "第0轮指令：推进剧情"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_compaction_halves_input_budget_and_succeeds_after_400_context_overflow():
    """首次摘要请求若仍被供应商以 HTTP 400 超窗拒绝，第二次尝试自动将输入预算减半并成功自愈。"""
    chunk = "非工具纯对话历史段落，不可被工具折叠器压缩。" * 350
    items: list[dict[str, Any]] = []
    for idx in range(80):
        items.append({"role": "user", "content": f"用户第{idx}轮：{chunk}"})
        items.append({"role": "assistant", "content": f"助手第{idx}轮：{chunk}"})

    history = LoopHistory(
        items=items,
        last_message_id="msg-80",
        last_prompt_tokens=680_000,
        author_messages=["用户第79轮"],
    )

    class _FakeBadRequestError(Exception):
        def __init__(self) -> None:
            super().__init__("context window exceeds limit (2013)")
            self.status_code = 400

    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="减半窗口后的交接摘要"), finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=320_000,
            completion_tokens=600,
            total_tokens=320_600,
            prompt_tokens_details=None,
        ),
    )
    provider = SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")
    mock_call = AsyncMock(side_effect=[_FakeBadRequestError(), (completion, provider)])

    with patch("agent.core.llm_failover.acreate_chat_completion", mock_call):
        outcome = await compact_history(history, system_prompt="系统提示", session_id="sess-400")

    assert outcome.record is not None
    assert outcome.record["summary"] == "减半窗口后的交接摘要"
    assert mock_call.await_count == 2
    first_msgs = mock_call.await_args_list[0].kwargs["messages"]
    second_msgs = mock_call.await_args_list[1].kwargs["messages"]
    assert len(second_msgs) < len(first_msgs)
    assert second_msgs[1]["role"] == "user"
    assert mock_call.await_args_list[1].kwargs["reasoning_effort"] == "low"

