#!/usr/bin/env python3
"""
Prefect 任务装饰器

参考 deepscript 的设计：
- 不在任务内部手工重试，交给 Prefect 调度
- 不持久化中间结果，减少内存占用
- 使用 get_run_logger 记录日志
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from prefect import get_run_logger, task
from prefect.tasks import task_input_hash

from flows.utils.helpers.exceptions import is_retryable_exception


def _log_execution_time(task_name: str, elapsed: float, logger: Any) -> None:
    """记录任务执行时间"""
    logger.info(f"任务 {task_name} 执行完成，耗时: {elapsed:.2f}秒")


def _log_error_with_context(exc: Exception, context: str, logger: Any) -> None:
    """记录错误及上下文"""
    logger.error(f"{context}: {type(exc).__name__}: {str(exc)}")


def retry_only_transient_failures(_task: Any, _task_run: Any, state: Any) -> bool:
    """
    Prefect ``retry_condition_fn``: retry unless the failure cannot change on retry.

    Prefect passes a Failed state whose ``data`` is the raised exception.
    Non-retryable LLM errors (401/402/403, context too long, unparsable
    output) and data-validation errors fail immediately instead of re-billing
    the same doomed call.
    """
    exc = getattr(state, "data", None)
    return is_retryable_exception(exc if isinstance(exc, BaseException) else None)


def smart_retry_task(
    retries: int = 3,
    retry_delay_seconds: int = 5,
    cache_expiration: timedelta | None = None,
    persist_result: bool = False,  # ✅ 默认不持久化
    log_prints: bool = True,
    name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    智能重试装饰器（Prefect 版本）

    注意：
        - 不在任务内部做手工重试，Prefect 会在任务失败后进行重试
        - 默认不持久化结果，减少内存占用

    Args:
        retries: 重试次数
        retry_delay_seconds: 每次重试的固定延迟秒数
        cache_expiration: 缓存过期时间
        persist_result: 是否持久化结果（默认 False）
        log_prints: 是否记录 print 输出
        name: 任务名称

    Returns:
        装饰后的 Prefect 任务
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        task_name = name or func.__name__

        if inspect.iscoroutinefunction(func):
            @task(
                retries=retries,
                retry_delay_seconds=retry_delay_seconds,
                cache_key_fn=task_input_hash if cache_expiration else None,
                cache_expiration=cache_expiration,
                persist_result=persist_result,
                log_prints=log_prints,
                name=task_name,
                retry_condition_fn=retry_only_transient_failures,
            )
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                logger = get_run_logger()
                logger.info(f"开始执行任务: {task_name}")

                start = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    _log_error_with_context(exc, f"任务 {task_name} 执行失败", logger)
                    raise
                finally:
                    elapsed = time.perf_counter() - start
                    _log_execution_time(task_name, elapsed, logger)

                return result

            return wrapper
        else:
            @task(
                retries=retries,
                retry_delay_seconds=retry_delay_seconds,
                cache_key_fn=task_input_hash if cache_expiration else None,
                cache_expiration=cache_expiration,
                persist_result=persist_result,
                log_prints=log_prints,
                name=task_name,
                retry_condition_fn=retry_only_transient_failures,
            )
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                logger = get_run_logger()
                logger.info(f"开始执行任务: {task_name}")

                start = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    _log_error_with_context(exc, f"任务 {task_name} 执行失败", logger)
                    raise
                finally:
                    elapsed = time.perf_counter() - start
                    _log_execution_time(task_name, elapsed, logger)

                return result

            return wrapper

    return decorator


def database_task(
    retries: int = 3,
    retry_delay_seconds: int = 2,
    cache_expiration: timedelta | None = None,
    name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    数据库操作专用装饰器

    约定：
        - 数据库类任务短重试窗口（2秒）
        - 不持久化结果，数据已入库无需额外存储
    """
    return smart_retry_task(
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
        cache_expiration=cache_expiration,
        persist_result=False,  # ✅ 数据库任务不持久化
        name=name,
    )


def api_task(
    retries: int = 5,
    retry_delay_seconds: int = 10,
    cache_expiration: timedelta | None = None,
    name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    API 调用专用装饰器

    约定：
        - 外部 API 波动较大，默认更高的重试次数与较长延迟
        - 不持久化结果，减少内存占用
    """
    return smart_retry_task(
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
        cache_expiration=cache_expiration,
        persist_result=False,  # ✅ API 任务不持久化
        name=name,
    )


def analysis_task(
    retries: int = 2,
    retry_delay_seconds: int = 3,
    cache_expiration: timedelta | None = None,
    name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    分析处理专用装饰器

    约定：
        - 分析类任务保持较低重试次数
        - 不持久化结果，减少内存占用
    """
    return smart_retry_task(
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
        cache_expiration=cache_expiration,
        persist_result=False,  # ✅ 分析任务不持久化
        name=name,
    )
