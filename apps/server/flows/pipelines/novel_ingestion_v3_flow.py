#!/usr/bin/env python3
"""
小说导入主流程 V3

流程设计:
┌─────────────────────────────────────────────────────────────┐
│ 阶段0: 文件验证与检查点                                       │
│  - 文件验证(格式、编码、大小)                                 │
│  - 检查断点续传                                              │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 阶段1: 文件摄取与章节建立                                     │
│  - 章节解析(自动识别章节标题)                                 │
│  - 创建Novel/Chapter记录                                     │
│  - 创建IngestionJob任务记录                                  │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 阶段2: 使用StageExecutor执行章节提取                         │
│  - 章节摘要生成                                              │
│  - 情节点提取(可选,默认关闭)                                  │
│  - 角色提及提取(可选)                                         │
│  - 元信息提取(并行,可选)                                      │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 阶段3: 使用StageExecutor执行剧情聚合                         │
│  - 小说概要生成                                              │
│  - 剧情聚合                                                  │
│  - 剧情线生成                                                │
│  - 角色实体构建(并行)                                        │
│  - 人物关系提取(串行,依赖角色实体)                           │
└─────────────────────────────────────────────────────────────┘

断点续传:
- 每个阶段完成后自动保存checkpoint
- 支持从任意阶段恢复:stage0/stage1/stage2
- 保留历史检查点,按能力补跑失败或未完成的任务
"""

from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from prefect import flow, get_run_logger
from prefect.task_runners import ConcurrentTaskRunner

from config.datetime_utils import utcnow
from config.material_settings import material_settings as settings
from core.error_codes import ErrorCode
from flows.database_session import get_prefect_db_session
from flows.utils.helpers import (
    calculate_checksum,
    create_checkpoint_manager,
    normalize_filename,
    parse_novel_chapters,
    validate_input,
)
from flows.utils.helpers.novel_parser import read_novel_text
from services.material.job_errors import MaterialPipelineError, job_error_code_for_exception
from services.material.novel_text import NovelDecodeError

from .helpers import ProgressPublisher
from .stages import StageExecutor

# 并发任务运行器
RUNTIME_TASK_RUNNER: Any = ConcurrentTaskRunner(max_workers=settings.MAX_CONCURRENT_WORKFLOWS)


def _elapsed_ms(start: float) -> int:
    """计算从start到现在的毫秒数"""
    return int((time.perf_counter() - start) * 1000)


def _ensure_file_local(file_path: str, user_id: str, logger) -> str:
    """确保文件在本地可用，如果不存在则从 API Server 下载。"""
    if os.path.isfile(file_path):
        return file_path

    api_base = os.environ.get("API_SERVER_INTERNAL_URL", "").rstrip("/")
    if not api_base:
        raise FileNotFoundError(
            f"文件不存在: {file_path}，且未配置 API_SERVER_INTERNAL_URL"
        )

    internal_token = os.environ.get("MATERIAL_INTERNAL_TOKEN", "")
    if not internal_token:
        raise FileNotFoundError(
            f"文件不存在: {file_path}，且未配置 MATERIAL_INTERNAL_TOKEN"
        )

    filename = os.path.basename(file_path)
    encoded_filename = urllib.parse.quote(filename, safe="")
    encoded_user_id = urllib.parse.quote(str(user_id), safe="")
    download_url = (
        f"{api_base}/api/v1/materials/internal/system/files/{encoded_filename}"
        f"?user_id={encoded_user_id}"
    )

    os.makedirs(os.path.dirname(file_path) or "uploads", exist_ok=True)
    logger.info(f"文件不在本地，从 API Server 下载: {download_url}")
    request = urllib.request.Request(
        download_url,
        headers={"X-Internal-Token": internal_token},
    )
    with urllib.request.urlopen(request, timeout=30) as response, open(file_path, "wb") as output:
        output.write(response.read())
    logger.info(f"文件下载完成: {file_path}")
    return file_path


# retries=0: a flow-level retry re-ran the same job 30s after it had been marked
# failed, overlapping a user retry (two flows, double charge). Transient errors
# are retried by tasks; a failed job is resumed by the user retry endpoint.
@flow(
    name="novel_ingestion_v3",
    retries=0,
    task_runner=RUNTIME_TASK_RUNNER,  # type: ignore[arg-type]
    persist_result=False,
)
def novel_ingestion_v3(
    file_path: str,
    user_id: str,
    novel_title: str | None = None,
    author: str | None = None,
    resume_from_checkpoint: bool = True,
    novel_id: int | None = None,
    job_id: int | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """
    小说导入主流程 V3 (简化版Pipeline模式)

    Args:
        file_path: 小说文件路径(支持.txt格式)
        user_id: 用户ID(用于素材库隔离)
        novel_title: 小说标题(可选,如果不提供则从文件内容推断)
        author: 作者名称(可选)
        resume_from_checkpoint: 是否启用断点续传(默认True)
        novel_id: 指定小说ID(可选,用于重新处理已存在的小说)
        job_id: 当前导入任务ID(新API显式传入,旧调用兼容)
        correlation_id: 关联ID(可选,用于Redis Pub/Sub进度推送)

    Returns:
        Dict[str, Any]: 导入结果统计
    """
    logger = get_run_logger()
    if correlation_id is None:
        try:
            from prefect.runtime import flow_run

            correlation_id = str(flow_run.id) if flow_run.id else None
        except Exception:
            correlation_id = None
    publisher = ProgressPublisher(correlation_id, logger)
    flow_start = time.perf_counter()

    logger.info(
        "event=novel_ingestion_v3_start file=%s title=%s author=%s",
        file_path,
        novel_title,
        author,
    )

    if job_id is not None:
        if novel_id is None:
            raise ValueError("传入 job_id 时必须同时传入 novel_id")
        if not _validate_and_repair_job_identity(novel_id, job_id, correlation_id):
            # The job was already finished or reconciled as failed (e.g. the run
            # sat in the queue past the watchdog and the quota was refunded).
            logger.warning(
                "event=novel_ingestion_v3_skipped novel_id=%s job_id=%s reason=job_not_runnable",
                novel_id,
                job_id,
            )
            return {"novel_id": novel_id, "job_id": job_id, "status": "skipped"}

    persisted_chapter_ids: list[int] | None = None
    if novel_id is not None:
        from models.material_models import Novel
        from services.material.chapters_service import ChaptersService

        with get_prefect_db_session() as session:
            existing_novel = session.get(Novel, novel_id)
            if existing_novel is None:
                raise ValueError(f"预创建的小说不存在: novel_id={novel_id}")
            if existing_novel.user_id != user_id:
                raise ValueError(f"novel_id={novel_id} 不属于 user_id={user_id}")
            if existing_novel.deleted_at is not None:
                raise ValueError(f"小说已删除: novel_id={novel_id}")

            persisted_chapters = ChaptersService().list_by_novel_ordered(
                session, novel_id
            )
            if persisted_chapters and all(
                chapter.original_content for chapter in persisted_chapters
            ):
                persisted_chapter_ids = [
                    chapter.id
                    for chapter in persisted_chapters
                    if chapter.id is not None
                ]

    normalized_path: str | None = None
    content_hash: str | None = None
    encoding: str | None = None
    try:
        # =================================================================
        # 阶段0: 初始化和文件验证
        # =================================================================
        publisher.publish(
            "flow_started",
            status="processing",
            file_path=file_path,
            progress=0,
            message="开始解析小说文件..."
        )

        if persisted_chapter_ids is None:
            # 文件名标准化（解决中文文件名问题）
            logger.info("[阶段0] 确保文件可用并标准化")
            file_path = _ensure_file_local(file_path, user_id, logger)
            temp_dir = Path(file_path).parent / "temp"
            normalized_path = normalize_filename(file_path, str(temp_dir))

            # 复制原文件到新位置
            import shutil
            shutil.copy2(file_path, normalized_path)
            logger.info(f"文件已复制到: {normalized_path}")

            # 文件验证
            logger.info("[阶段0] 文件验证")
            validated = validate_input(normalized_path)

            # 计算文件哈希
            checksums = calculate_checksum(normalized_path)
            content_hash = checksums["md5_checksum"]

            # 解码：与上传接口预检使用同一个函数，API 接受的文件这里一定能解码
            try:
                _, encoding = read_novel_text(normalized_path)
            except NovelDecodeError as decode_error:
                raise MaterialPipelineError(
                    ErrorCode.MATERIAL_FILE_UNREADABLE, str(decode_error)
                ) from decode_error

            logger.info("文件验证通过: %s", validated["file_path"])
        else:
            logger.info(
                "[阶段0] 复用持久化章节，跳过原始文件读取: novel_id=%s chapters=%s",
                novel_id,
                len(persisted_chapter_ids),
            )

        # =================================================================
        # 阶段1: 检查断点续传
        # =================================================================
        if resume_from_checkpoint and not novel_id:
            # 仅在 novel_id 未由 upload 端点预创建时，才通过 content_hash 查找断点
            # 当 novel_id 已传入时，跳过 content_hash 查找，避免覆盖为其他 novel
            if content_hash is None:
                raise ValueError("缺少原始文件哈希")
            resume_result = _check_and_resume_from_checkpoint(
                content_hash=content_hash,
                user_id=user_id,
                novel_id=novel_id,
                job_id=job_id,
                correlation_id=correlation_id,
                flow_start=flow_start,
                logger=logger,
                publisher=publisher,
            )

            # 如果已完成或成功恢复,直接返回
            if resume_result.get("completed"):
                return resume_result["result"]

            # 如果找到可恢复的小说,更新novel_id
            if resume_result.get("novel_id"):
                novel_id = resume_result["novel_id"]

        # =================================================================
        # 阶段2: 创建小说和章节(如果需要)
        # =================================================================
        if novel_id:
            # 检查该 novel 是否已有章节
            if persisted_chapter_ids is not None:
                chapter_ids = persisted_chapter_ids
            else:
                with get_prefect_db_session() as session:
                    from services.material.novels_service import NovelsService
                    chapter_ids = NovelsService().list_chapter_ids(session, novel_id)

            if chapter_ids:
                # 已有章节，直接复用
                if job_id is None:
                    job_id = _resolve_legacy_job_id(novel_id, logger)
                checkpoint_manager = create_checkpoint_manager(novel_id, job_id)
                if job_id is not None:
                    _validate_and_repair_job_identity(
                        novel_id,
                        job_id,
                        correlation_id,
                        total_chapters=len(chapter_ids),
                    )
            else:
                # novel 已存在但无章节（由 upload 端点预创建），执行 stage0 填充章节
                if normalized_path is None or content_hash is None or encoding is None:
                    raise ValueError("缺少已验证的原始文件信息")
                stage0_result = _execute_stage0(
                    file_path=normalized_path,
                    novel_title=novel_title,
                    author=author,
                    user_id=user_id,
                    content_hash=content_hash,
                    encoding=encoding,
                    file_size=validated["file_size"],
                    correlation_id=correlation_id,
                    logger=logger,
                    publisher=publisher,
                    existing_novel_id=novel_id,
                    job_id=job_id,
                )
                chapter_ids = stage0_result["chapter_ids"]
                checkpoint_manager = stage0_result["checkpoint_manager"]
                job_id = stage0_result["job_id"]
        else:
            if normalized_path is None or content_hash is None or encoding is None:
                raise ValueError("缺少已验证的原始文件信息")
            stage0_result = _execute_stage0(
                file_path=normalized_path,
                novel_title=novel_title,
                author=author,
                user_id=user_id,
                content_hash=content_hash,
                encoding=encoding,
                file_size=validated["file_size"],
                correlation_id=correlation_id,
                logger=logger,
                publisher=publisher,
                job_id=job_id,
            )
            novel_id = stage0_result["novel_id"]
            chapter_ids = stage0_result["chapter_ids"]
            checkpoint_manager = stage0_result["checkpoint_manager"]
            job_id = stage0_result["job_id"]

        # 确保novel_id不为None
        if novel_id is None:
            raise ValueError("novel_id不能为None")

        # =================================================================
        # 阶段3: 使用StageExecutor执行章节提取(阶段1)
        # =================================================================
        logger.info("=" * 60)
        logger.info("[阶段3] 使用StageExecutor执行章节提取")
        logger.info("=" * 60)

        executor = StageExecutor(
            novel_id=novel_id,
            chapter_ids=chapter_ids,
            checkpoint_manager=checkpoint_manager,
            correlation_id=correlation_id,
            job_id=job_id,
        )
        executor.record_enabled_stages()

        stage1_result = executor.execute_stage1()

        # =================================================================
        # 阶段4: 使用StageExecutor执行剧情聚合(阶段2)
        # =================================================================
        logger.info("=" * 60)
        logger.info("[阶段4] 使用StageExecutor执行剧情聚合")
        logger.info("=" * 60)

        final_result = executor.execute_stage2(stage1_result, flow_start)

        # =================================================================
        # 完成
        # =================================================================
        logger.info(
            "event=novel_ingestion_v3_complete novel_id=%s chapters=%s elapsed_ms=%s",
            novel_id,
            len(chapter_ids),
            final_result.get("elapsed_ms", 0),
        )

        # 处理完成后清理临时文件
        try:
            import shutil
            if normalized_path and Path(normalized_path).exists():
                Path(normalized_path).unlink()
                logger.info("已删除临时文件: %s", normalized_path)

            # 如果temp目录为空，删除temp目录
            temp_dir = Path(normalized_path).parent if normalized_path else None
            if temp_dir and temp_dir.exists() and temp_dir.name == "temp" and not any(temp_dir.iterdir()):
                temp_dir.rmdir()
                logger.info("已删除空临时目录: %s", temp_dir)
        except Exception as cleanup_error:
            logger.warning("清理临时文件时出错: %s", cleanup_error)

        return final_result

    except Exception as e:
        logger.error("小说导入失败: %s", str(e), exc_info=True)

        # 标记失败（只写错误码；原始异常只进日志）
        _mark_job_as_failed(
            novel_id=novel_id,
            job_id=job_id,
            correlation_id=correlation_id,
            error_code=job_error_code_for_exception(e),
            exception_type=type(e).__name__,
            flow_start=flow_start,
            logger=logger,
            publisher=publisher,
        )

        # 即使出错也清理临时文件
        try:
            import shutil
            if normalized_path and Path(normalized_path).exists():
                Path(normalized_path).unlink()
                logger.info("已删除临时文件: %s", normalized_path)

            # 如果temp目录为空，删除temp目录
            temp_dir = Path(normalized_path).parent if normalized_path else None
            if temp_dir and temp_dir.exists() and temp_dir.name == "temp" and not any(temp_dir.iterdir()):
                temp_dir.rmdir()
                logger.info("已删除空临时目录: %s", temp_dir)
        except Exception as cleanup_error:
            logger.warning("清理临时文件时出错: %s", cleanup_error)

        raise


def _check_and_resume_from_checkpoint(
    content_hash: str,
    user_id: str,
    novel_id: int | None,  # noqa: ARG001
    correlation_id: str | None,
    flow_start: float,
    logger: Any,
    publisher: ProgressPublisher,  # noqa: ARG001
    job_id: int | None = None,
) -> dict[str, Any]:
    """
    检查断点续传,如果可以恢复则直接执行

    Returns:
        Dict[str, Any]: {
            "completed": bool,  # 是否已完成或成功恢复
            "result": Dict,     # 如果completed=True,返回最终结果
            "novel_id": int,    # 如果找到可恢复的小说,返回novel_id
        }
    """
    logger.info("[断点检查] 检查是否可以从断点恢复")

    with get_prefect_db_session() as session:
        from services.material.novels_service import NovelsService

        # 检查是否存在相同content_hash的小说
        existing_novel = NovelsService().get_by_content_hash(session, content_hash, user_id)

        if not existing_novel:
            logger.info("[断点检查] 未发现已存在的小说")
            return {"completed": False}

        logger.info("[断点检查] 发现已存在的小说: novel_id=%s", existing_novel.id)

        # 创建checkpoint_manager
        if job_id is None:
            job_id = _resolve_legacy_job_id(existing_novel.id, logger)
        checkpoint_manager = create_checkpoint_manager(existing_novel.id, job_id)

        # 获取章节ID（提前获取，用于已完成判断）
        chapter_ids = NovelsService().list_chapter_ids(session, existing_novel.id)

        # 【修复】优先检查是否已完成（避免 can_resume 的 completed 状态误判）
        latest_checkpoint = checkpoint_manager.get_latest_checkpoint()
        if latest_checkpoint and latest_checkpoint.stage == 'completed' and latest_checkpoint.stage_status == 'completed':
            logger.info("[断点检查] 任务已完成,直接返回")
            return {
                "completed": True,
                "result": {
                    "novel_id": existing_novel.id,
                    "chapters_count": len(chapter_ids),
                    "status": "already_completed",
                    "elapsed_ms": _elapsed_ms(flow_start),
                }
            }

        if not checkpoint_manager.can_resume():
            logger.info("[断点检查] 检查点不可用")
            return {"completed": False, "novel_id": existing_novel.id}

        resume_point = checkpoint_manager.get_resume_point()
        stage = resume_point.get('stage') if resume_point else None

        logger.info(
            "[断点检查] 发现可恢复的检查点: stage=%s, status=%s",
            stage, resume_point.get('status', 'unknown') if resume_point else 'unknown'
        )

        # 处理不同的恢复点
        if stage == 'failed':
            # Preserve successful capability history for partial retries.
            logger.info("[断点检查] 保留失败检查点,准备按能力恢复")
            return {"completed": False, "novel_id": existing_novel.id}

        elif stage == 'completed':
            # 任务已完成
            logger.info("[断点检查] 任务已完成,直接返回")
            return {
                "completed": True,
                "result": {
                    "novel_id": existing_novel.id,
                    "chapters_count": len(chapter_ids),
                    "status": "already_completed",
                    "elapsed_ms": _elapsed_ms(flow_start),
                }
            }

        elif stage in ['stage1', 'stage2', 'stage2a', 'stage2b', 'stage2c']:
            # 从阶段1或阶段2恢复
            logger.info("[断点检查] 从%s恢复", stage)

            # 创建StageExecutor
            executor = StageExecutor(
                novel_id=existing_novel.id,
                chapter_ids=chapter_ids,
                checkpoint_manager=checkpoint_manager,
                correlation_id=correlation_id,
                job_id=job_id,
            )
            executor.record_enabled_stages()

            # 根据恢复点执行对应阶段
            if stage == 'stage1':
                # 从阶段1恢复
                stage1_result = executor.execute_stage1()
                final_result = executor.execute_stage2(stage1_result, flow_start)
            else:
                # 从阶段2恢复
                final_result = executor.execute_stage2(None, flow_start)

            return {"completed": True, "result": final_result}

        else:
            # 其他情况,继续创建新小说
            logger.info("[断点检查] 未知的恢复点,继续创建新小说")
            return {"completed": False, "novel_id": existing_novel.id}


def _execute_stage0(
    file_path: str,
    novel_title: str | None,
    author: str | None,
    user_id: str,
    content_hash: str,
    encoding: str,
    file_size: int,
    correlation_id: str | None,
    logger: Any,
    publisher: ProgressPublisher,
    existing_novel_id: int | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    """
    执行阶段0: 文件摄取与章节建立

    Args:
        existing_novel_id: 由 upload 端点预创建的 novel ID，传入时复用该记录

    Returns:
        Dict[str, Any]: {
            "novel_id": int,
            "chapter_ids": List[int],
            "checkpoint_manager": CheckpointManager,
        }
    """
    logger.info("=" * 60)
    logger.info("[阶段0] 文件摄取与章节建立")
    logger.info("=" * 60)

    # 解析章节
    parse_result = parse_novel_chapters(file_path, encoding)
    chapters_data = parse_result["chapters"]
    inferred_title = parse_result["novel_title"]

    logger.info("[阶段0] 解析完成: %d 个章节", len(chapters_data))
    if not chapters_data:
        raise MaterialPipelineError(ErrorCode.MATERIAL_NO_CHAPTERS, "no chapters parsed")
    if len(chapters_data) > settings.MAX_CHAPTERS_PER_NOVEL:
        raise MaterialPipelineError(
            ErrorCode.MATERIAL_TOO_MANY_CHAPTERS,
            f"{len(chapters_data)} chapters exceed {settings.MAX_CHAPTERS_PER_NOVEL}",
        )

    # 创建小说和章节记录
    with get_prefect_db_session() as session:
        from models.material_models import Novel
        from services.material.chapters_service import ChaptersService
        from services.material.checkpoint_service import CheckpointService
        from services.material.ingestion_jobs_service import IngestionJobsService
        from services.material.novels_service import NovelsService

        job_record = None

        if existing_novel_id:
            # 复用 upload 端点预创建的 Novel 和 IngestionJob
            novel = session.get(Novel, existing_novel_id)
            if not novel:
                raise ValueError(f"预创建的小说不存在: novel_id={existing_novel_id}")
            novel_id = novel.id

            # 更新 novel 的 source_meta（补充 content_hash 等信息）
            import json as _json
            try:
                source_meta = (
                    _json.loads(novel.source_meta)
                    if isinstance(novel.source_meta, str)
                    else dict(novel.source_meta or {})
                )
            except (TypeError, ValueError):
                source_meta = {}
            source_meta.setdefault("file_path", file_path)
            source_meta.setdefault("file_size", file_size)
            source_meta.update(
                {
                    "encoding": encoding,
                    "md5_checksum": content_hash,
                }
            )
            novel.source_meta = _json.dumps(source_meta)

            # 更新已有的 IngestionJob 状态
            if job_id is not None:
                from models.material_models import IngestionJob

                existing_job = session.get(IngestionJob, job_id)
                if existing_job is None or existing_job.novel_id != novel_id:
                    raise ValueError(
                        f"job_id={job_id} 不属于 novel_id={novel_id}"
                    )
            else:
                logger.warning(
                    "[阶段0] legacy V3 caller omitted job_id; resolving latest once for novel_id=%s",
                    novel_id,
                )
                existing_job = IngestionJobsService().get_latest_by_novel(session, novel_id)
            if existing_job:
                existing_job.status = "processing"
                existing_job.total_chapters = len(chapters_data)
                if getattr(existing_job, "started_at", None) is None:
                    existing_job.started_at = utcnow()
                if correlation_id:
                    existing_job.correlation_id = correlation_id
                job_id = existing_job.id
                job_record = existing_job
            else:
                job = IngestionJobsService().create_job(
                    session, novel_id,
                    total_chapters=len(chapters_data),
                    source_path=file_path,
                    status="processing",
                    correlation_id=correlation_id,
                )
                job_id = job.id
                job_record = job

            logger.info("[阶段0] 复用预创建小说: novel_id=%s, job_id=%s", novel_id, job_id)
        else:
            # 检查是否已存在（按 content_hash 去重）
            existing_novel = NovelsService().get_by_content_hash(session, content_hash, user_id)

            if existing_novel:
                logger.info("[阶段0] 小说已存在: novel_id=%s", existing_novel.id)
                chapter_ids = NovelsService().list_chapter_ids(session, existing_novel.id)
                checkpoint_manager = create_checkpoint_manager(existing_novel.id, job_id)

                return {
                    "novel_id": existing_novel.id,
                    "chapter_ids": chapter_ids,
                    "checkpoint_manager": checkpoint_manager,
                    "job_id": job_id,
                }

            # 创建新小说
            novel = NovelsService().create_novel(session, {
                "title": novel_title or inferred_title,
                "author": author,
                "user_id": user_id,
                "source_meta": {
                    "file_path": file_path,
                    "file_size": file_size,
                    "encoding": encoding,
                    "md5_checksum": content_hash,
                },
            })
            novel_id = novel.id

            # 创建任务记录
            job = IngestionJobsService().create_job(
                session, novel_id,
                total_chapters=len(chapters_data),
                source_path=file_path,
                status="processing",
                correlation_id=correlation_id,
            )
            job_id = job.id
            job_record = job

        # 创建章节记录
        chapter_ids = ChaptersService().create_chapters(
            session,
            novel,
            [
                {
                    "chapter_number": ch["chapter_number"],
                    "title": ch["title"],
                    "content": ch["content"],
                    "content_hash": hashlib.md5(ch["content"].encode()).hexdigest(),
                }
                for ch in chapters_data
            ],
        )

        # 创建checkpoint
        CheckpointService().upsert(
            session,
            novel_id,
            "stage0",
            {},
            status="completed",
            job_id=job_id,
        )

        if job_record:
            if getattr(job_record, "started_at", None) is None:
                job_record.started_at = utcnow()
            if hasattr(job_record, "update_stage_progress"):
                job_record.update_stage_progress(
                    "stage0",
                    "completed",
                    total_chapters=len(chapters_data),
                    chapters_created=len(chapter_ids),
                )

        session.commit()

    logger.info("[阶段0] 创建 %d 个章节记录", len(chapter_ids))

    # 发布进度
    publisher.publish(
        "job_created",
        status="processing",
        novel_id=novel_id,
        job_id=job_id,
        total_chapters=len(chapters_data),
        progress=5,
        message=f"已创建解析任务,共 {len(chapters_data)} 章节"
    )

    # 创建checkpoint_manager
    checkpoint_manager = create_checkpoint_manager(novel_id, job_id)

    return {
        "novel_id": novel_id,
        "chapter_ids": chapter_ids,
        "checkpoint_manager": checkpoint_manager,
        "job_id": job_id,
    }


def _mark_job_as_failed(
    novel_id: int | None,
    error_code: str,
    flow_start: float,
    logger: Any,
    publisher: ProgressPublisher,
    correlation_id: str | None = None,
    _correlation_id: str | None = None,
    job_id: int | None = None,
    exception_type: str | None = None,
) -> None:
    """
    标记任务为失败状态

    error_message 只写错误码；平台侧失败（LLM 不可用、文件无法解析等）由
    IngestionJobsService.fail_job 退还该任务占用的额度。
    """
    elapsed_ms = _elapsed_ms(flow_start)
    resolved_correlation_id = correlation_id or _correlation_id

    # 发布失败进度
    publisher.publish(
        "failed",
        status="failed",
        novel_id=novel_id,
        error=error_code,
        elapsed_ms=elapsed_ms,
        message=error_code,
    )

    # 更新任务状态
    if novel_id:
        try:
            from services.material.ingestion_jobs_service import IngestionJobsService

            with get_prefect_db_session() as session:
                if job_id is not None:
                    from models.material_models import IngestionJob

                    job = session.get(IngestionJob, job_id)
                    if job is not None and job.novel_id != novel_id:
                        job = None
                else:
                    logger.warning(
                        "[失败处理] legacy V3 caller omitted job_id; resolving latest once"
                    )
                    job = IngestionJobsService().get_latest_by_novel(session, novel_id)
                if job:
                    job_id = job.id
                    IngestionJobsService().fail_job(
                        session,
                        job,
                        error_code=error_code,
                        stage="flow",
                        reason=error_code,
                        details={
                            "elapsed_ms": elapsed_ms,
                            "exception_type": exception_type,
                            "correlation_id": resolved_correlation_id,
                        },
                    )
        except Exception as e:
            logger.warning(f"更新任务状态失败: {e}")

        # 标记checkpoint失败
        try:
            checkpoint_manager = create_checkpoint_manager(novel_id, job_id)
            checkpoint_manager.mark_stage_failed("failed", error_code)
        except Exception as e:
            logger.warning(f"标记checkpoint失败: {e}")


def _resolve_legacy_job_id(novel_id: int, logger: Any) -> int | None:
    """Resolve a missing legacy job exactly once and keep that ID for the run."""
    from services.material.ingestion_jobs_service import IngestionJobsService

    logger.warning(
        "legacy V3 caller omitted job_id; resolving latest once for novel_id=%s",
        novel_id,
    )
    with get_prefect_db_session() as session:
        job = IngestionJobsService().get_latest_by_novel(session, novel_id)
        return job.id if job else None


def _validate_and_repair_job_identity(
    novel_id: int,
    job_id: int,
    correlation_id: str | None,
    total_chapters: int | None = None,
) -> bool:
    """
    Validate ownership and let the accepted flow run repair correlation state.

    Returns False (and changes nothing) when the job is already terminal, so a
    run that starts after its job was reconciled as failed does no work.
    """
    from models.material_models import IngestionJob
    from services.material.ingestion_jobs_service import TERMINAL_JOB_STATUSES

    with get_prefect_db_session() as session:
        job = session.get(IngestionJob, job_id)
        if job is None or job.novel_id != novel_id:
            raise ValueError(f"job_id={job_id} 不属于 novel_id={novel_id}")
        if job.status in TERMINAL_JOB_STATUSES:
            return False
        changed = False
        if correlation_id and job.correlation_id != correlation_id:
            job.correlation_id = correlation_id
            changed = True
        if total_chapters is not None and job.total_chapters != total_chapters:
            job.total_chapters = total_chapters
            changed = True
        if changed:
            session.add(job)
            session.commit()
        return True
