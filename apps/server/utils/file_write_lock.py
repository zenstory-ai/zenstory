"""In-process striped file write locks for SQLite read-modify-write serialization.

SQLite has no row-level locks, so same-file read-modify-write sections across
REST handlers, services, and agent tools share this low-level striped lock pool
(keeping ``api/`` and ``services/`` decoupled from ``agent/tools/``).
"""

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager

# SQLite has no row-level locks, so same-file read-modify-write sections are
# serialized with in-process striped locks instead (the default SQLite
# deployment runs a single server process; cross-process SQLite writers are
# not covered). Striping keeps memory bounded; distinct files may share a
# stripe, which only costs some extra serialization.
_FILE_WRITE_LOCK_STRIPES = 64
_file_write_locks = [threading.Lock() for _ in range(_FILE_WRITE_LOCK_STRIPES)]

# 事件循环线程上等待写锁的硬上限。持锁方可能是工作线程里的 commit + 版本快照
# （_create_version 会重放 diff 链并对整章正文跑 difflib），SQLite 写冲突时
# 还会撞上 PRAGMA busy_timeout=30000。在事件循环线程上同步等这么久等于整个
# 进程停摆：该 worker 的所有 SSE 流与 HTTP 请求一起卡住。因此事件循环线程上
# 只做有界等待，超时抛可重试错误，绝不把不确定时长的等待压在事件循环上。
EVENT_LOOP_LOCK_WAIT_SECONDS = 0.5


class FileWriteBusyError(RuntimeError):
    """写锁在有界等待内没拿到（文件正被另一个写入任务占用），可安全重试。"""


def file_write_lock(file_id: str) -> threading.Lock:
    """Return the in-process write lock striped by ``file_id``."""
    return _file_write_locks[hash(file_id) % _FILE_WRITE_LOCK_STRIPES]


def _running_on_event_loop_thread() -> bool:
    """当前线程是否正在跑 asyncio 事件循环。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@contextmanager
def acquire_file_write_lock(file_id: str) -> Iterator[None]:
    """获取按 ``file_id`` 分条带的进程内写锁，且不阻塞事件循环。

    - 工作线程（asyncio.to_thread / 线程池）：照常阻塞式获取，语义不变。
    - 事件循环线程：只等 ``EVENT_LOOP_LOCK_WAIT_SECONDS``，超时抛
      :class:`FileWriteBusyError`。写工具本应被 offload 到线程池执行，
      走到这里说明调用方仍在事件循环上同步调用；此时宁可让这一次编辑失败
      并提示重试，也不能让整个进程陪着等（最坏可达 SQLite busy_timeout 的 30 秒）。
    """
    lock = file_write_lock(file_id)
    if _running_on_event_loop_thread():
        if not lock.acquire(timeout=EVENT_LOOP_LOCK_WAIT_SECONDS):
            raise FileWriteBusyError(
                "该文件正被另一个写入任务占用，本次编辑未做任何修改，请稍后重试。"
            )
    else:
        lock.acquire()
    try:
        yield
    finally:
        lock.release()


__all__ = [
    "EVENT_LOOP_LOCK_WAIT_SECONDS",
    "FileWriteBusyError",
    "acquire_file_write_lock",
    "file_write_lock",
]
