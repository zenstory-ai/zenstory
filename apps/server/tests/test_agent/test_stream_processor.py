"""Regression tests for the file-streaming state machine."""

import pytest

from agent.core.stream_processor import (
    BUFFER_MAX_SIZE,
    StreamProcessor,
    StreamState,
)


def _drive(chunks):
    """Feed chunks to a fresh processor in WAITING_START; return (results, proc)."""
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    results = [proc.process_content(c) for c in chunks]
    return results, proc


def _all_conversation(results):
    return "".join(
        r.conversation_content + r.conversation_content_after_file for r in results
    )


def test_canonical_marker_in_one_chunk():
    results, _ = _drive(["<file>hello world</file>"])
    assert any(r.file_complete for r in results)
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == "hello world"
    assert "hello world" not in _all_conversation(results)


def test_uppercase_start_marker_split_across_chunks_is_detected():
    # "<FILE>" split as "<FI" + "LE>" must still route the body into the file,
    # not the chat transcript (the per-chunk normalize alone could not see it).
    results, proc = _drive(["<FI", "LE>hello", "</file>"])
    assert proc.state == StreamState.IDLE
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == "hello"
    assert "hello" not in _all_conversation(results)


def test_whitespace_end_marker_split_across_chunks_is_detected():
    results, proc = _drive(["<file>body text", "< /fil", "e >tail"])
    completed = next((r for r in results if r.file_complete), None)
    assert completed is not None
    assert completed.final_content == "body text"
    # Trailing text after the (variant) end marker is normal conversation.
    assert _all_conversation(results).endswith("tail")


def test_overflow_drains_tail_instead_of_leaking_to_chat():
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    proc.process_content("<file>")

    big = "x" * (BUFFER_MAX_SIZE + 100)
    overflow = proc.process_content(big)
    assert overflow.file_complete is True
    assert overflow.buffer_exceeded is True
    assert proc.state == StreamState.DRAINING

    # The still-streaming remainder of the oversized body must be swallowed.
    tail = proc.process_content("LEAKED_TAIL_SHOULD_NOT_APPEAR")
    assert tail.conversation_content == ""

    # Content after the closing marker resumes as normal conversation.
    closing = proc.process_content("</file>after the file")
    assert proc.state == StreamState.IDLE
    assert closing.conversation_content == "after the file"
    assert "LEAKED_TAIL" not in closing.conversation_content


def test_stream_end_while_draining_discards_tail():
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    proc.process_content("<file>")
    proc.process_content("x" * (BUFFER_MAX_SIZE + 100))
    assert proc.state == StreamState.DRAINING
    proc.process_content("unterminated overflow tail")
    final = proc.finalize_on_stream_end()
    assert final.conversation_content == ""
    assert proc.state == StreamState.IDLE


def test_inline_code_end_marker_in_body_is_content():
    # 正文里用行内代码讨论 `</file>` 不能提前结束文件：
    # 截断的文件 + 剩余正文/真实结束标记泄漏进对话流。
    results, proc = _drive([
        "<file>",
        "正文第一段。用法示例：`</file>` 表示结束。",
        "后半部分正文",
        "</file>",
    ])
    assert proc.state == StreamState.IDLE
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == "正文第一段。用法示例：`</file>` 表示结束。后半部分正文"
    assert _all_conversation(results) == ""


def test_fenced_code_end_marker_in_body_is_content():
    body = "第一段\n```\n</file>\n```\n第二段"
    results, proc = _drive(["<file>" + body, "</file>"])
    assert proc.state == StreamState.IDLE
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == body
    assert _all_conversation(results) == ""


def test_fenced_code_end_marker_survives_flush_boundary():
    # 围栏开口先被 flush 出 temp_buffer，之后才出现 </file>：
    # 围栏开合状态必须跨 flush 保留，否则围栏内的字面量被当成真实标记。
    head = "A" * 100 + "\n```python\n"
    body = head + "x" * 100 + "\n</file> 还在代码块里\n" + "```\n尾段"
    results, proc = _drive([
        "<file>" + head,
        "x" * 100 + "\n</file> 还在代码块里\n",
        "```\n尾段",
        "</file>",
    ])
    assert proc.state == StreamState.IDLE
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == body
    assert _all_conversation(results) == ""


def test_inline_code_start_marker_in_narration_does_not_start_file():
    # 叙述里行内代码 `<file>` 不是真实开始标记；真实标记随后到达时，
    # 前面的叙述应作为对话输出，文件内容从真实标记之后开始。
    results, proc = _drive([
        "我会用 `<file>` 标记开始输出。",
        "<file>正文",
        "</file>",
    ])
    assert proc.state == StreamState.IDLE
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == "正文"
    convo = _all_conversation(results)
    assert "`<file>`" in convo
    assert "正文" not in convo


def test_backtick_and_end_marker_arriving_in_separate_chunks():
    # 行内代码的反引号与 </file> 分属不同 chunk，重组后仍应识别为字面量
    results, proc = _drive([
        "<file>用法：`",
        "</file>",
        "` 表示结束。正文继续",
        "</file>",
    ])
    assert proc.state == StreamState.IDLE
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == "用法：`</file>` 表示结束。正文继续"
    assert _all_conversation(results) == ""


def test_pending_inline_backtick_resolves_to_real_marker():
    # 反引号后紧跟 </file>，但下一个字符不是反引号 -> 是真实结束标记
    results, proc = _drive(["<file>末尾带`", "</file>", "后记"])
    assert proc.state == StreamState.IDLE
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == "末尾带`"
    assert _all_conversation(results) == "后记"


def test_pending_inline_backtick_resolves_real_at_stream_end():
    # 流结束时反引号不会再闭合，挂起的 </file> 应按真实结束标记处理
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    proc.process_content("<file>正文`")
    proc.process_content("</file>")
    final = proc.finalize_on_stream_end()
    assert final.file_complete is True
    assert final.final_content == "正文`"
    assert proc.state == StreamState.IDLE


def test_unclosed_fence_before_real_start_marker_resolves_at_stream_end():
    # 叙述里出现一个未闭合的 ```：开始标记在流结束前始终无法判定（围栏可能
    # 在后续 chunk 闭合）。流结束时围栏不会再闭合，必须按 at_eof 语义复扫，
    # 正文写入文件，而不是把带 <file> 原始标记的整段正文倒进聊天。
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    results = [
        proc.process_content("好的，输出格式```\n"),
        proc.process_content("<file>这是正文第一段。"),
        proc.process_content("</file>已完成。"),
    ]
    final = proc.finalize_on_stream_end()
    assert final.file_complete is True
    assert final.final_content == "这是正文第一段。"
    convo = _all_conversation([*results, final])
    assert convo == "好的，输出格式```\n已完成。"
    assert "<file>" not in convo
    assert proc.state == StreamState.IDLE


def test_fence_wrapped_file_block_is_written_at_stream_end():
    # 模型把整块 <file>…</file> 包在 ``` 围栏里输出（提示词范例本身就是这种
    # 写法）：围栏保护让开始标记全程被判为字面量。流结束时应放宽围栏保护把
    # 正文写入文件，而不是让整章正文连标记一起变成聊天消息、文件留空。
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    results = [
        proc.process_content("```markdown\n<file>"),
        proc.process_content("第一段正文。"),
        proc.process_content("</file>\n```"),
    ]
    final = proc.finalize_on_stream_end()
    assert final.file_complete is True
    assert final.final_content == "第一段正文。"
    convo = _all_conversation([*results, final])
    assert "第一段正文。" not in convo
    assert "<file>" not in convo
    assert proc.state == StreamState.IDLE


def test_fenced_format_explanation_without_end_marker_stays_conversation():
    # 只在围栏里解释格式、没有配对的 </file>：不是文件正文，流结束时仍按叙述
    # flush 进对话，文件保持未写入（交由工作流的空文件纠正循环重跑 writer）。
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    narration = "格式示例：\n```\n<file>\n```\n请确认后我再写。"
    proc.process_content(narration)
    final = proc.finalize_on_stream_end()
    assert final.file_complete is False
    assert final.conversation_content == narration
    assert proc.state == StreamState.IDLE


def test_at_eof_rescan_still_honours_control_marker_guard():
    # at_eof 复扫命中开始标记后走的是 WRITING finalize 路径，因此仍受 control
    # marker 污染守卫约束：交接叙述不得被当成正文落库。
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    proc.process_content("输出格式```\n")
    proc.process_content("<file>正文文件已创建。\n\n")
    proc.process_content("### 已交接给 writer。[TASK_COMPLETE]")
    final = proc.finalize_on_stream_end()
    assert final.file_complete is False
    assert final.conversation_content == (
        "输出格式```\n正文文件已创建。\n\n### 已交接给 writer。[TASK_COMPLETE]"
    )
    assert proc.state == StreamState.IDLE


def test_inline_code_narration_stays_conversation_at_stream_end():
    # 行内代码里的 `<file>` 到流结束仍是字面量，不能被 at_eof 复扫误判成
    # 真实开始标记而把叙述写进文件。
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    narration = "我会用 `<file>` 标记开始输出，请稍等。"
    proc.process_content(narration)
    final = proc.finalize_on_stream_end()
    assert final.file_complete is False
    assert final.conversation_content == narration
    assert proc.state == StreamState.IDLE


def test_end_marker_split_across_chunks_still_completes():
    results, proc = _drive(["<file>你好", "</fi", "le>尾巴"])
    assert proc.state == StreamState.IDLE
    completed = next(r for r in results if r.file_complete)
    assert completed.final_content == "你好"
    assert _all_conversation(results) == "尾巴"


def test_drain_ignores_inline_code_end_marker():
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    proc.process_content("<file>")
    proc.process_content("x" * (BUFFER_MAX_SIZE + 100))
    assert proc.state == StreamState.DRAINING
    leaked = proc.process_content("尾部提到 `</file>` 仍是溢出内容")
    assert leaked.conversation_content == ""
    assert proc.state == StreamState.DRAINING
    closing = proc.process_content("</file>之后的对话")
    assert closing.conversation_content == "之后的对话"
    assert proc.state == StreamState.IDLE


# ---------------------------------------------------------------------------
# 正文以真实 </file> 收尾后，模型偶尔在同一回复里再补一对空的 <file></file>，
# 且被切成很碎的 chunk（生产 sse-loop1.txt 事件 18015–18022）。处理器此时已回到
# IDLE，这对标签会原样进聊天气泡并写入历史。只丢弃"紧随真实收尾、仅由空白隔开"
# 的精确空标签对；叙述/代码里的字面标签、非空块、被截断的前缀一律原样保留。
# ---------------------------------------------------------------------------

# 生产流的实际切分：正文末尾 chunk 带 </file>，之后是 "\n\n" 和六个碎 chunk
PROD_EMPTY_PAIR_CHUNKS = [
    "<file>\n　　我叫温知夏。\n",
    "就没打算",
    "松开我的手。\n\n　　只是上一世，雨太大，我没看清。\n",
    "</file>\n\n",
    "<",
    "file",
    ">\n",
    "</",
    "file",
    ">",
]


def _chat_through_eof(results, proc):
    """所有 chunk 的对话输出 + 流结束 finalize 交还的内容（不得有丢失）。"""
    tail = proc.finalize_on_stream_end()
    return _all_conversation(results + [tail]), tail


def test_prod_split_empty_pair_after_close_is_dropped():
    results, proc = _drive(PROD_EMPTY_PAIR_CHUNKS)
    completed = [r for r in results if r.file_complete]
    assert len(completed) == 1
    assert completed[0].final_content == (
        "\n　　我叫温知夏。\n就没打算松开我的手。\n\n　　只是上一世，雨太大，我没看清。\n"
    )
    chat, tail = _chat_through_eof(results, proc)
    assert chat == "\n\n"
    assert not tail.file_complete
    assert not proc.is_active


@pytest.mark.parametrize(
    "chunk",
    [
        "</file>\n\n<file>\n</file>",
        "</file><FILE></File>",
        "</file> < file >\n< /file >",
    ],
)
def test_empty_pair_in_same_chunk_as_close_is_dropped(chunk):
    results, proc = _drive(["<file>正文", chunk])
    assert next(r for r in results if r.file_complete).final_content == "正文"
    chat, _ = _chat_through_eof(results, proc)
    assert "file" not in chat.lower()
    assert chat.strip() == ""


def test_text_after_dropped_pair_is_kept():
    results, proc = _drive(["<file>正文</file>\n<file></file>", "\n\n已写完。"])
    chat, _ = _chat_through_eof(results, proc)
    assert chat == "\n\n\n已写完。"


def test_two_empty_pairs_after_close_are_dropped_and_prose_kept():
    results, proc = _drive(["<file>正文</file>", "<file></file>\n<file></file>说明"])
    chat, _ = _chat_through_eof(results, proc)
    assert chat == "\n说明"


# ---- 守卫：以下输入修复前后都必须原样输出、不丢字 ----


def test_narration_first_then_literal_pair_is_kept():
    results, proc = _drive(["<file>正文</file>", "\n\n空标签写法是 ", "<file></file>", "。"])
    chat, _ = _chat_through_eof(results, proc)
    assert chat == "\n\n空标签写法是 <file></file>。"
    assert not proc.is_active


@pytest.mark.parametrize("literal", ["`<file></file>`", "```\n<file></file>\n```"])
def test_code_literal_pair_after_close_split_per_char_is_kept(literal):
    results, proc = _drive(["<file>正文</file>\n", *literal])
    chat, _ = _chat_through_eof(results, proc)
    assert chat == "\n" + literal


def test_non_empty_stray_block_after_close_is_kept():
    results, proc = _drive(["<file>正文</file>\n", "<file>", "多出来的正文", "</file>"])
    chat, _ = _chat_through_eof(results, proc)
    assert chat == "\n<file>多出来的正文</file>"


@pytest.mark.parametrize(
    "chunks, expected",
    [
        (["<", "br>换行"], "<br>换行"),
        (["<file/>"], "<file/>"),
        (["<", "-- 注意"], "<-- 注意"),
        (["<file>", "\n后面是叙述"], "<file>\n后面是叙述"),
    ],
)
def test_other_text_after_close_is_kept_verbatim(chunks, expected):
    results, proc = _drive(["<file>正文</file>", *chunks])
    chat, _ = _chat_through_eof(results, proc)
    assert chat == expected
    assert not proc.is_active


@pytest.mark.parametrize("partial", ["\n<fi", "<file>\n</fi", "\n<file>\n", "<file></"])
def test_unfinished_pair_prefix_is_released_at_stream_end(partial):
    results, proc = _drive(["<file>正文</file>", partial])
    chat, _ = _chat_through_eof(results, proc)
    assert chat == partial
    assert not proc.is_active


def test_overlong_whitespace_padded_prefix_is_not_held():
    # 暂扣有长度上限：超长空白填充的前缀不再等待，当场原样输出
    padded = "<file>" + " " * 80
    results, proc = _drive(["<file>A</file>", padded])
    assert results[-1].conversation_content == padded
    assert not proc.is_active


def test_unfinished_prefix_is_released_before_next_capture():
    # 适配器在 create_file 前会 finalize（_flush_active_capture），暂扣前缀必须交还
    results, proc = _drive(["<file>A</file>", "<file>\n"])
    chat, _ = _chat_through_eof(results, proc)
    assert chat == "<file>\n"
    proc.start_file_write("file-2")
    r = proc.process_content("<file>B</file>")
    assert r.file_complete and r.final_content == "B"


def test_eof_confirmed_close_releases_held_prefix():
    # 围栏未闭合使 </file> 悬置，流结束复扫才确认为真实收尾；其后的前缀照样交还
    results, proc = _drive(["<file>```\n正文\n", "</file>\n<file>"])
    tail = proc.finalize_on_stream_end()
    assert tail.file_complete and tail.final_content == "```\n正文\n"
    assert _all_conversation(results + [tail]) == "\n<file>"
    assert not proc.is_active


def test_empty_pair_without_preceding_close_is_untouched():
    proc = StreamProcessor()
    assert proc.process_content("<file></file>").conversation_content == "<file></file>"
    assert not proc.is_active


def test_window_closes_at_agent_boundary():
    results, proc = _drive(["<file>正文</file>"])
    assert proc.finalize_on_stream_end().conversation_content == ""
    assert not proc.is_active
    assert proc.process_content("<file></file>").conversation_content == "<file></file>"


def test_auto_completed_capture_does_not_open_window():
    _, proc = _drive(["<file>正文"])
    final = proc.finalize_on_stream_end()
    assert final.file_complete and final.auto_completed
    assert proc.process_content("<file></file>").conversation_content == "<file></file>"
    assert not proc.is_active


def test_draining_close_does_not_open_window():
    proc = StreamProcessor()
    proc.start_file_write("file-1")
    proc.process_content("<file>")
    proc.process_content("x" * (BUFFER_MAX_SIZE + 100))
    assert proc.state == StreamState.DRAINING
    closing = proc.process_content("</file><file></file>")
    assert closing.conversation_content == "<file></file>"
    assert not proc.is_active
