"""回复停在写完的 <file>…</file> 上的文字信号（agent/loop/completion_evidence.py）。

单循环 agent 据此安排文件流续写（见 test_loop_round_endings.py 与 test_loop_agent.py）。
"""

from __future__ import annotations

import pytest

from agent.loop.completion_evidence import ends_on_file_block, ends_on_unclosed_file_block

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("<file>\n正文\n</file>\n", True),
        ("<file>\n正文\n</file>\n[TASK_COMPLETE]", True),
        ("<file>\n正文\n</file>\n<file>\n</file>\n", True),
        ("<file>\n正文\n</file>\n写好了第2集。", False),
        ("<file>\n\n</file>\n", False),
        ("只是一句话", False),
        ("", False),
    ],
)
async def test_ends_on_file_block(tail, expected):
    assert ends_on_file_block(tail) is expected



@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("<file>\n正文\n", True),
        ("<file>\n\n", False),
        ("<file>\n正文\n</file>\n", False),
        ("只是一句话", False),
        ("", False),
    ],
)
async def test_ends_on_unclosed_file_block(tail, expected):
    assert ends_on_unclosed_file_block(tail) is expected

