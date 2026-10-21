"""发布交接：旧 API 进程收到 SIGTERM 后，让进行中的生成在关机宽限内自己收尾。

Railway 换部署时先启动新实例接管新请求，再给旧进程发 SIGTERM；Uvicorn 停止接收新连接，
进行中的请求最多再等 ``--timeout-graceful-shutdown``（295 秒），之后直接取消，Railway
在 ``drainingSeconds``（300 秒）时杀进程。生成的墙钟时限是 780 秒，SIGTERM 时还要跑很久的
一轮会在 295 秒处被硬切断：没有终止帧，部分历史、计费结算和终态写回的后台任务也可能来不及跑完。

收到 SIGTERM 后：
- 每次模型调用都按「时间快到了」提醒收尾，工作流不再开新的 agent run（见 ``AgentRunMeter.past_wrap_up``），
  多数生成会在几十秒内自然结束；
- ``api.agent`` 把进行中的 SSE 泵的时限提前到 ``AGENT_DRAIN_STOP_AFTER_S`` 之后，仍没结束的一轮走既有的
  时限收尾（先发终止帧，再按取消路径落库部分历史、结算），终止帧换成「服务正在更新」；
- lifespan 关闭时在 ``shutdown_budget_remaining_s`` 之内等后台的退还与终态任务落定。
"""

from __future__ import annotations

import signal
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import Any

from config.agent_runtime import AGENT_DRAIN_SHUTDOWN_BUDGET_S

_draining_since: float | None = None
_listeners: list[Callable[[], None]] = []


def is_draining() -> bool:
    return _draining_since is not None


def add_drain_listener(listener: Callable[[], None]) -> None:
    """注册一次：进程开始收尾时（在事件循环线程里）调用。"""
    _listeners.append(listener)


def begin_drain() -> None:
    """进入收尾状态并通知监听者；重复调用无效果。必须在事件循环线程里调用。"""
    global _draining_since
    if _draining_since is not None:
        return
    _draining_since = time.monotonic()
    for listener in list(_listeners):
        listener()


def shutdown_budget_remaining_s() -> float:
    """离 Railway 杀进程还剩多少秒可用于收尾（未收到 SIGTERM 时返回整份预算）。"""
    if _draining_since is None:
        return float(AGENT_DRAIN_SHUTDOWN_BUDGET_S)
    return max(0.0, AGENT_DRAIN_SHUTDOWN_BUDGET_S - (time.monotonic() - _draining_since))


def install_sigterm_drain(call_soon_threadsafe: Callable[..., Any]) -> None:
    """把收尾挂在当前 SIGTERM 处理器（Uvicorn 的 handle_exit）之前。

    Uvicorn 在 lifespan 启动之前装好自己的信号处理器、退出时再还原，所以在 lifespan
    启动里调用即可；原处理器照常运行，Uvicorn 的优雅关机不受影响。信号处理器里不直接
    动事件循环，只把 ``begin_drain`` 排进循环。
    """
    if threading.current_thread() is not threading.main_thread():
        # Signals can only be handled in the main thread (tests run the app elsewhere).
        return
    previous = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(signum: int, frame: FrameType | None) -> None:
        call_soon_threadsafe(begin_drain)
        if callable(previous):
            previous(signum, frame)

    signal.signal(signal.SIGTERM, _on_sigterm)
