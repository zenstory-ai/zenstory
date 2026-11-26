"""Unit tests for the MiniMax leaked tool-protocol filter state machine.

Covers ``agent.core.protocol_filter`` directly:
- ``MiniMaxLeakFilter`` streaming chunking (1-char splits, boundary splits, multi-block streams)
- Partial opening tags & ``_MAX_LOOKAHEAD`` release without stalling
- Normal text and non-MiniMax bracket passthrough
- Leaked ``<tool_call>`` / bare delimiter suppression and post-close ``\\n`` / ``\\r\\n`` handling
- Unclosed leaked blocks dropped cleanly on ``at_eof=True`` with warning logging & reset
- Code fence (``fence_open=True``) and single/double backtick inline code preservation
- ``contains_leaked_tool_protocol``, ``strip_minimax_tool_leaks``, and ``strip_code_spans``
"""

from __future__ import annotations

import pytest

from agent.core.protocol_filter import (
    _MAX_LOOKAHEAD,
    MINIMAX_DELIM,
    MiniMaxLeakFilter,
    contains_leaked_tool_protocol,
    strip_code_spans,
    strip_minimax_tool_leaks,
)


def _feed_chunks(chunks: list[str], *, fence_open: bool = False) -> tuple[str, MiniMaxLeakFilter]:
    flt = MiniMaxLeakFilter()
    pieces = [flt.feed(chunk, fence_open=fence_open) for chunk in chunks]
    pieces.append(flt.feed("", fence_open=fence_open, at_eof=True))
    return "".join(pieces), flt


@pytest.mark.unit
class TestMiniMaxLeakFilterStreaming:
    def test_plain_text_and_ordinary_brackets_pass_through_unchanged(self):
        text = "第一章 [雾夜] 来了]，接着看 [a][b] 和正常文字。"
        out, flt = _feed_chunks([text])
        assert out == text
        assert not flt.has_pending

    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 13, 64])
    def test_leaked_tool_call_block_stripped_across_arbitrary_chunk_sizes(self, chunk_size: int):
        leaked = (
            f"前文提示。{MINIMAX_DELIM}<tool_call>"
            '<invoke name="edit_file"><parameter name="id">ch1</parameter></invoke>'
            "</tool_call>\n后文正文。"
        )
        chunks = [leaked[i : i + chunk_size] for i in range(0, len(leaked), chunk_size)]
        out, flt = _feed_chunks(chunks)
        assert MINIMAX_DELIM not in out
        assert "tool_call" not in out
        assert "edit_file" not in out
        # Trailing '\n' is consumed when it lands in the same chunk as '</tool_call>';
        # if split onto the next chunk, at most the harmless newline remains.
        assert out in {"前文提示。后文正文。", "前文提示。\n后文正文。"}
        assert not flt.has_pending
        assert not flt.swallowing

    @pytest.mark.parametrize(
        "open_variant,close_variant,trailing,expected_after",
        [
            (f"{MINIMAX_DELIM}<tool_call>", "</tool_call>", "\n下一段", "下一段"),
            (f"{MINIMAX_DELIM}<tool_call>", "</tool_call>", "\r\n下一段", "下一段"),
            (f"{MINIMAX_DELIM}tool_call>", f"{MINIMAX_DELIM}</tool_call>", "\n下一段", "下一段"),
            (f"{MINIMAX_DELIM}  <tool_call id='1'>", f"{MINIMAX_DELIM}/tool_call>", "紧接文字", "紧接文字"),
        ],
    )
    def test_open_and_close_variants_and_newline_consumption(
        self, open_variant: str, close_variant: str, trailing: str, expected_after: str
    ):
        stream = f"开头。{open_variant}payload body{close_variant}{trailing}"
        # Same-chunk arrival consumes the immediate '\n' / '\r\n' after closing tag.
        out_single, flt_single = _feed_chunks([stream])
        assert out_single == f"开头。{expected_after}"
        assert not flt_single.has_pending

        # 1-char split strips all leaked protocol markup across every partial prefix/suffix boundary.
        out_split, flt_split = _feed_chunks(list(stream))
        assert "payload body" not in out_split
        assert MINIMAX_DELIM not in out_split
        assert "tool_call" not in out_split
        assert out_split.replace("\r\n", "").replace("\n", "") == f"开头。{expected_after}"
        assert not flt_split.has_pending

    def test_partial_delimiter_held_then_released_when_not_minimax(self):
        flt = MiniMaxLeakFilter()
        assert flt.feed("hello ]<]") == "hello "
        assert flt.has_pending is True
        # Next chunk diverges from MINIMAX_DELIM -> held prefix must be emitted verbatim.
        assert flt.feed("ordinary text") == "]<]ordinary text"
        assert flt.has_pending is False

    def test_partial_non_minimax_prefix_flushed_on_eof(self):
        flt = MiniMaxLeakFilter()
        assert flt.feed("end with ]<]mini") == "end with "
        assert flt.has_pending is True
        assert flt.feed("", at_eof=True) == "]<]mini"
        assert not flt.has_pending

    def test_bare_delimiter_stripped_both_mid_stream_and_at_eof(self):
        out_mid, flt_mid = _feed_chunks(["前", MINIMAX_DELIM[:6], MINIMAX_DELIM[6:], "后"])
        assert out_mid == "前后"
        assert not flt_mid.has_pending

        # Bare delimiter right at EOF is dropped rather than emitted.
        flt_eof = MiniMaxLeakFilter()
        assert flt_eof.feed("末尾" + MINIMAX_DELIM) == "末尾"
        assert flt_eof.has_pending is True
        assert flt_eof.feed("", at_eof=True) == ""
        assert not flt_eof.has_pending

    def test_lookahead_cap_releases_oversized_non_closing_candidate(self):
        flt = MiniMaxLeakFilter()
        # Starts like a tool_call open tag without '>' and exceeds _MAX_LOOKAHEAD.
        oversized = f"{MINIMAX_DELIM}<tool_call " + ("a" * (_MAX_LOOKAHEAD + 10))
        emitted = flt.feed(oversized) + flt.feed("", at_eof=True)
        # Bare MINIMAX_DELIM is stripped, remaining non-tag text is released rather than stuck forever.
        assert MINIMAX_DELIM not in emitted
        assert "<tool_call " in emitted
        assert not flt.has_pending

    def test_unclosed_leaked_tool_call_dropped_on_eof_and_logs_warning(self, caplog):
        flt = MiniMaxLeakFilter()
        with caplog.at_level("WARNING"):
            part1 = flt.feed(f"先说一句。{MINIMAX_DELIM}<tool_call>未闭合内容")
            assert part1 == "先说一句。"
            assert flt.swallowing is True
            assert flt.has_pending is True

            part2 = flt.feed("更多未闭合内容")
            assert part2 == ""
            assert flt.swallowing is True

            tail = flt.feed("", at_eof=True, project_id="proj-1", user_id="user-1")
            assert tail == ""

        assert not flt.swallowing
        assert not flt.has_pending
        assert any("Dropping an unclosed leaked MiniMax tool_call fragment" in r.getMessage() for r in caplog.records)

    def test_fenced_code_block_preserves_leaked_markup_verbatim(self):
        payload = f"{MINIMAX_DELIM}<tool_call>example</tool_call>"
        out, flt = _feed_chunks([payload[:10], payload[10:]], fence_open=True)
        assert out == payload
        assert not flt.has_pending

    def test_inline_backtick_code_preserves_markup_until_closing_tick_or_newline(self):
        leaked = f"{MINIMAX_DELIM}<tool_call>doc</tool_call>"
        # Inside single backticks: preserved verbatim; outside backticks: stripped.
        text = f"示例 `{leaked}` 正常。{leaked}\n结束"
        out_single, _ = _feed_chunks([text])
        assert out_single == f"示例 `{leaked}` 正常。结束"
        out_split, flt = _feed_chunks(list(text))
        assert out_split in {f"示例 `{leaked}` 正常。结束", f"示例 `{leaked}` 正常。\n结束"}
        assert not flt.has_pending

        # Unclosed single backtick terminates at newline so subsequent leaked block is still stripped.
        unclosed_tick = f"残留 `半个反引号\n{leaked}\n正文"
        out2_single, _ = _feed_chunks([unclosed_tick])
        assert out2_single == "残留 `半个反引号\n正文"
        out2_split, _ = _feed_chunks(list(unclosed_tick))
        assert out2_split in {"残留 `半个反引号\n正文", "残留 `半个反引号\n\n正文"}

    def test_double_backticks_do_not_enter_single_tick_inline_state(self):
        # Double backticks are treated as a non-single run so a leaked block afterward is still stripped.
        leaked = f"{MINIMAX_DELIM}<tool_call>x</tool_call>"
        text = f"``{leaked}之后"
        out, flt = _feed_chunks([text])
        assert out == "``之后"
        assert not flt.has_pending

    def test_reset_clears_all_internal_state(self):
        flt = MiniMaxLeakFilter()
        flt.feed(f"{MINIMAX_DELIM}<tool_call>unclosed")
        assert flt.swallowing is True
        assert flt.has_pending is True
        flt.reset()
        assert flt.swallowing is False
        assert flt.in_inline_code is False
        assert flt.buf == ""
        assert flt.has_pending is False


@pytest.mark.unit
class TestProtocolFilterHelpers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("", False),
            ("普通章节正文，没有任何协议标签。", False),
            (f"包含 {MINIMAX_DELIM} 分隔符", True),
            ("<tool_call><invoke name='edit_file'></invoke></tool_call>", True),
            ("裸结束标签 </tool_call>", True),
            ('<parameter name="id">123</parameter>', True),
            # Inside fenced code block or inline backtick span -> ignored (not a leak).
            (f"```xml\n{MINIMAX_DELIM}<tool_call><invoke name='x'></invoke></tool_call>\n```", False),
            (f"~~~\n{MINIMAX_DELIM}<tool_call>x</tool_call>\n~~~", False),
            (f"文档说明 `{MINIMAX_DELIM}<tool_call>` 仅作示例。", False),
            # Code span safe, but real leak outside code span -> detected.
            (f"`{MINIMAX_DELIM}` 之后泄漏 </tool_call>", True),
        ],
    )
    def test_contains_leaked_tool_protocol(self, text: str, expected: bool):
        assert contains_leaked_tool_protocol(text) is expected

    def test_strip_minimax_tool_leaks_preserves_code_spans(self):
        code_example = f"```text\n{MINIMAX_DELIM}<tool_call>keep</tool_call>\n```"
        inline_example = f"`{MINIMAX_DELIM}`"
        leaked = f"{MINIMAX_DELIM}<tool_call>drop me</tool_call>\n"
        text = f"{code_example}\n说明 {inline_example}\n{leaked}保留正文{MINIMAX_DELIM}。"

        cleaned = strip_minimax_tool_leaks(text)
        assert code_example in cleaned
        assert inline_example in cleaned
        assert "drop me" not in cleaned
        assert cleaned.endswith("保留正文。")

    def test_strip_code_spans_removes_fences_and_inline_backticks(self):
        text = "前 `inline` 中\n```py\nx = 1\n```\n后"
        assert strip_code_spans(text) == "前  中\n\n后"
