"""SSE 出口泵：心跳帧 + 整个请求的墙钟时限。

/agent/stream 的生成器在后台 task 里驱动，消费端按 ``heartbeat_interval_s``
等待下一个事件：等不到就发一个 SSE 注释帧 ``: ping``（前端解析器与浏览器
EventSource 都会忽略以冒号开头的行），避免 router 排队、工具参数生成等长时间
无输出时被代理当作闲置连接切断。

时限到期时取消后台 task（生成器按「取消」路径收尾：后台落库部分历史、补存
进行中的文件正文、释放会话持有），然后抛出 ``StreamDeadlineExceeded``，由
API 层发送终止帧并决定计费。

源生成器的迭代全部在同一个后台 task 里进行：它在各步之间通过 contextvars
传递的请求上下文（ToolContext、日志上下文）保持一致。（api/agent.py 会在路由里
先取出第一个事件以便把「会话忙」转成 409，那一步发生在 ToolContext 建立之前。）
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

HEARTBEAT_FRAME = ": ping\n\n"

_DONE = object()


class StreamDeadlineExceeded(Exception):
    """整个流式请求超过了墙钟时限。"""


class _SourceFailed:
    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


class SSEStreamPump:
    """在后台 task 里驱动 SSE 源生成器，对外交错输出事件与心跳帧。"""

    def __init__(
        self,
        source: AsyncIterator[str],
        *,
        heartbeat_interval_s: float,
        deadline_s: float | None = None,
    ) -> None:
        self._source = source
        self._heartbeat_interval_s = max(0.01, float(heartbeat_interval_s))
        self._deadline_s = deadline_s if deadline_s and deadline_s > 0 else None
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def _produce(self) -> None:
        try:
            async for item in self._source:
                self._queue.put_nowait(item)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - 原样转交给消费端
            self._queue.put_nowait(_SourceFailed(exc))
        else:
            self._queue.put_nowait(_DONE)

    def cancel(self) -> None:
        """同步取消后台 task（可在 GeneratorExit / CancelledError 路径中调用）。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def __aiter__(self) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        deadline_at = loop.time() + self._deadline_s if self._deadline_s else None
        self._task = asyncio.create_task(self._produce())
        try:
            while True:
                wait_s = self._heartbeat_interval_s
                if deadline_at is not None:
                    remaining = deadline_at - loop.time()
                    if remaining <= 0:
                        await self._stop_for_deadline()
                    wait_s = min(wait_s, remaining)
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=wait_s)
                except TimeoutError:
                    if deadline_at is not None and loop.time() >= deadline_at:
                        await self._stop_for_deadline()
                    yield HEARTBEAT_FRAME
                    continue
                if item is _DONE:
                    return
                if isinstance(item, _SourceFailed):
                    raise item.exc
                yield item
        finally:
            self.cancel()

    async def _stop_for_deadline(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            # 等生成器跑完同步的取消收尾（调度后台落库/释放持有），再上报时限。
            # asyncio.wait 不会把 task 的 CancelledError 抛给这里，而消费端自己
            # 被取消时仍会照常传播。
            await asyncio.wait({task})
        raise StreamDeadlineExceeded(f"stream exceeded {self._deadline_s}s")
