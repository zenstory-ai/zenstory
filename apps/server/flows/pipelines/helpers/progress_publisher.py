#!/usr/bin/env python3
"""
进度发布模块

负责通过 Redis Pub/Sub 向前端推送流程进度消息。
支持多种事件类型，提供统一的进度消息格式。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from typing import Any

import redis


class ProgressPublisher:
    """
    进度发布器

    封装 Redis Pub/Sub 逻辑，提供语义化的进度发布方法。
    支持自动添加时间戳、优雅的错误处理等。

    使用示例:
        >>> publisher = ProgressPublisher(correlation_id="task-123", logger=logger)
        >>> publisher.publish("flow_started", progress=0, message="开始处理...")
        >>> publisher.publish("stage1_completed", progress=50, summaries_count=100)
        >>> publisher.publish_completion(novel_id, chapter_ids, result)
    """

    def __init__(self, correlation_id: str | None, logger: Any):
        """
        初始化进度发布器

        Args:
            correlation_id: 关联ID（用于构建 Redis 频道名称）
            logger: 日志对象（Prefect logger）
        """
        self.correlation_id = correlation_id
        self.logger = logger
        self._client = self._create_redis_client()

    def _create_redis_client(self) -> redis.Redis | None:
        """
        创建 Redis 客户端（带重试机制）

        从环境变量读取配置，支持 Prefect Worker 的动态配置。

        Returns:
            Redis 客户端实例，连接失败时返回 None
        """
        if not self.correlation_id:
            return None

        max_retries = 3
        retry_delay = 1.0  # 秒

        for attempt in range(max_retries):
            client: redis.Redis | None = None
            try:
                from config.material_settings import material_settings as settings

                redis_url = (os.getenv("REDIS_URL") or "").strip()
                explicit_flag = (os.getenv("MATERIAL_REDIS_ENABLED") or "").strip().lower() or None
                if explicit_flag in {"0", "false", "no", "off"}:
                    self.logger.debug("[PubSub] Redis 已通过 MATERIAL_REDIS_ENABLED 显式禁用，跳过连接")
                    return None
                enabled_by_flag = (
                    explicit_flag in {"1", "true", "yes", "on"}
                    if explicit_flag is not None
                    else bool(settings.REDIS_ENABLED)
                )

                # 【优雅降级】既无 REDIS_URL 也未开启 MATERIAL_REDIS_ENABLED 时跳过连接
                if not redis_url and not enabled_by_flag:
                    self.logger.debug("[PubSub] Redis 未启用，跳过连接")
                    return None

                if redis_url:
                    client = redis.from_url(
                        redis_url,
                        decode_responses=True,
                        socket_timeout=2.0,
                        socket_connect_timeout=2.0,
                    )
                else:
                    # 运行时读取环境变量，确保在 Prefect Worker 中使用正确的 Redis 主机
                    redis_host = os.getenv("REDIS_HOST", settings.REDIS_HOST)
                    redis_port = int(os.getenv("REDIS_PORT", str(settings.REDIS_PORT)))
                    redis_password = os.getenv("REDIS_PASSWORD", settings.REDIS_PASSWORD)

                    client = redis.Redis(
                        host=redis_host,
                        port=redis_port,
                        password=redis_password,
                        decode_responses=True,
                        socket_timeout=2.0,
                        socket_connect_timeout=2.0,
                    )

                # 测试连接
                client.ping()
                self.logger.debug(f"[PubSub] Redis 连接成功 (尝试 {attempt + 1}/{max_retries})")
                return client

            except Exception as e:
                if client is not None:
                    with contextlib.suppress(Exception):
                        client.close()
                if attempt < max_retries - 1:
                    self.logger.warning(
                        f"[PubSub] Redis 连接失败 (尝试 {attempt + 1}/{max_retries}): {type(e).__name__}, {retry_delay}秒后重试"
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    self.logger.error(f"[PubSub] Redis 连接失败，放弃重试: {type(e).__name__}")
                    return None

        return None

    def publish(self, event_type: str, **kwargs) -> None:
        """
        发布进度消息

        Args:
            event_type: 事件类型（如 flow_started, stage1_completed 等）
            **kwargs: 额外的消息字段（如 progress, message, novel_id 等）

        常用事件类型:
            - flow_started: 流程开始
            - job_created: 任务创建
            - stage1_started: 阶段1开始
            - stage1_completed: 阶段1完成
            - stage2_started: 阶段2开始
            - stage2_completed: 阶段2完成
            - completed: 流程完成
            - failed: 流程失败
        """
        if not self.correlation_id:
            return

        # 构建消息负载
        payload = {
            "type": event_type,
            "timestamp": int(time.time()),
            **kwargs
        }

        try:
            if self._client is None:
                self.logger.warning(f"[PubSub] Redis 不可用，跳过发布: {event_type}")
                return

            channel = f"ingestion:{self.correlation_id}"
            self._client.publish(channel, json.dumps(payload, ensure_ascii=False))

            self.logger.debug(f"[PubSub] 发布成功: channel={channel}, type={event_type}")

        except Exception as e:
            self.logger.warning(f"[PubSub] 发布失败: {e}")

    def publish_completion(
        self,
        novel_id: int,
        chapter_ids: list,
        result: dict[str, Any],
    ) -> None:
        """
        发布完成消息（包含 novel_summary）

        这是一个特殊的完成消息，会额外查询小说信息并构建 novel_summary。

        Args:
            novel_id: 小说ID
            chapter_ids: 章节ID列表
            result: 最终结果数据
        """

        self.logger.info(
            "event=novel_ingestion_v3_done novel_id=%s chapters=%s total_ms=%s",
            novel_id,
            len(chapter_ids),
            result.get("elapsed_ms", 0),
        )

        # 构建 novel_summary
        novel_summary = self._build_novel_summary(novel_id, len(chapter_ids))

        # "completed" 表示流程已终止；具体完成状态以最终结果为准。
        completion_payload = {
            **result,
            "status": result.get("status") or "completed",
            "progress": 100,
            "message": "小说解析完成！",
            "novel_summary": novel_summary,
        }
        self.publish("completed", **completion_payload)

    def _build_novel_summary(
        self,
        novel_id: int,
        chapters_count: int,
    ) -> dict[str, Any] | None:
        """
        构建 novel_summary 供前端使用

        Args:
            novel_id: 小说ID
            chapters_count: 章节数量

        Returns:
            novel_summary 字典或 None（查询失败时）
        """
        try:
            from sqlmodel import select

            from flows.database_session import get_prefect_db_session
            from models.material_models import Novel

            with get_prefect_db_session() as session:
                novel = session.exec(select(Novel).where(Novel.id == novel_id)).first()
                if novel:
                    novel_summary = {
                        "id": novel.id,
                        "title": novel.title,
                        "author": novel.author,
                        "synopsis": novel.synopsis,
                        "chapters_count": chapters_count,
                    }
                    self.logger.info(f"[SSE] 构建 novel_summary: {novel_summary}")
                    return novel_summary
        except Exception as e:
            self.logger.warning(f"[SSE] 查询小说信息失败: {e}")

        return None
