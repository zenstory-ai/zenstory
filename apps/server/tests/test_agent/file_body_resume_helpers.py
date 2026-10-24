"""图单元测试的替身：没有数据库与 StreamAdapter 时，空文件纠偏轮怎样恢复正文写入。

真实路径（落库核验 + StreamAdapter 按 create_file 结果里的版本恢复捕获）见
test_file_body_resume.py。这里只替换这两步，让只测图里交接 / 范围 / 质检门的用例照常走到纠偏轮。
"""

from collections.abc import AsyncIterator
from unittest.mock import patch

from agent.core.workflow_events import StreamEvent, StreamEventType

RESUMABLE_TARGET = ("2026-10-24T00:00:00", "draft")


def resumable_target():
    """假定纠偏目标落库核验通过（同一项目、未删除、仍为空）。"""
    return patch("agent.graph.writing_graph._resume_target_version", return_value=RESUMABLE_TARGET)


async def accept_resumes(events: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent]:
    """像 StreamAdapter 成功恢复捕获那样回执 FILE_BODY_RESUME，其余事件原样交出。"""
    async for event in events:
        if event.type == StreamEventType.FILE_BODY_RESUME:
            event.data["request"].armed = True
        yield event
