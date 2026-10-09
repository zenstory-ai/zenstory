"""SSE 出口泵：心跳帧 + 整个请求的墙钟时限。

/agent/stream 的生成器在后台 task 里驱动，消费端按 ``heartbeat_interval_s``
等待下一个事件：等不到就发一个 SSE 注释帧 ``: ping``（前端解析器与浏览器
EventSource 都会忽略以冒号开头的行），避免 router 排队、工具参数生成等长时间
无输出时被代理当作闲置连接切断。

时限到期时取消后台 task（生成器按「取消」路径收尾：后台落库部分历史、补存
进行中的文件正文、释放会话持有），然后抛出 ``StreamDeadlineExceeded``，由
API 层发送终止帧并决定计费。作者主动停止（``request_stop``）走同一条收尾，
抛出 ``StreamStoppedByUser``。

``on_item`` 在后台 task 里对源生成器产出的每一项调用（先于入队）：计费要按
「这次运行实际产出了什么」判定，而不是「客户端恰好收到了什么」——断线时队列里
没送出去的帧照样算数。

源生成器的迭代全部在同一个后台 task 里进行：它在各步之间通过 contextvars
传递的请求上下文（ToolContext、日志上下文）保持一致。（api/agent.py 会在路由里
先取出第一个事件以便把「会话忙」转成 409，那一步发生在 ToolContext 建立之前。）
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

HEARTBEAT_FRAME = ": ping\n\n"

_DONE = object()
_STOP = object()


class StreamDeadlineExceeded(Exception):
    """整个流式请求超过了墙钟时限。"""


class StreamStoppedByUser(Exception):
    """作者主动停止了这次运行（见 agent.core.run_stop）。"""


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
        on_item: Callable[[Any], None] | None = None,
    ) -> None:
        self._source = source
        self._on_item = on_item
        self._stop_event = asyncio.Event()
        self._heartbeat_interval_s = max(0.01, float(heartbeat_interval_s))
        self._deadline_s = deadline_s if deadline_s and deadline_s > 0 else None
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=32)
        self._task: asyncio.Task[None] | None = None

    async def _produce(self) -> None:
        try:
            try:
                async for item in self._source:
                    if self._on_item is not None:
                        self._on_item(item)
                    await self._queue.put(item)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - 原样转交给消费端
                await self._queue.put(_SourceFailed(exc))
            else:
                await self._queue.put(_DONE)
        except asyncio.CancelledError as cancellation:
            # Backpressure can suspend here instead of inside the source iterator.
            # Close it in this same context so its finalization still runs.
            close_source = getattr(self._source, "aclose", None)
            try:
                if close_source is not None:
                    await close_source()
            finally:
                raise cancellation

    def request_stop(self) -> None:
        """作者要求停止：消费端尽快取消后台 task 并抛出 StreamStoppedByUser。"""
        self._stop_event.set()

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
                    item = await self._next_item(wait_s)
                except TimeoutError:
                    if deadline_at is not None and loop.time() >= deadline_at:
                        await self._stop_for_deadline()
                    yield HEARTBEAT_FRAME
                    continue
                if item is _STOP:
                    await self._cancel_producer()
                    raise StreamStoppedByUser()
                if item is _DONE:
                    return
                if isinstance(item, _SourceFailed):
                    raise item.exc
                yield item
        finally:
            self.cancel()

    async def _next_item(self, timeout: float) -> Any:
        """下一项；作者要求停止时返回 _STOP（优先于已排队的帧），超时抛 TimeoutError。"""
        if self._stop_event.is_set():
            return _STOP
        if not self._queue.empty():
            return self._queue.get_nowait()
        get_task = asyncio.ensure_future(self._queue.get())
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {get_task, stop_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            # 被取消的 Queue.get 不会丢项：项留在队列里，下一次 get 照样拿到。
            for task in (get_task, stop_task):
                if not task.done():
                    task.cancel()
        if stop_task in done:
            return _STOP
        if get_task in done:
            return get_task.result()
        raise TimeoutError

    async def _cancel_producer(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            # 等生成器跑完同步的取消收尾（调度后台落库/释放持有），再上报。
            # asyncio.wait 不会把 task 的 CancelledError 抛给这里，而消费端自己
            # 被取消时仍会照常传播。
            await asyncio.wait({task})

    async def _stop_for_deadline(self) -> None:
        await self._cancel_producer()
        raise StreamDeadlineExceeded(f"stream exceeded {self._deadline_s}s")
