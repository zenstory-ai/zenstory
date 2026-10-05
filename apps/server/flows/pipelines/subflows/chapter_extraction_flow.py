"""
章节提取流程 (阶段1)

职责:
- 并行生成所有章节的摘要
- 并行提取所有章节的情节点
- 并行提取所有章节的实体 (可选)

这是主流程的阶段1,三类任务完全并行执行
"""

import time
from typing import Any

from prefect import flow, get_run_logger
from prefect.task_runners import ConcurrentTaskRunner

from config.material_settings import material_settings as settings
from config.material_settings import resolve_enabled_stages
from core.error_codes import ErrorCode
from flows.atomic_tasks.entities.character_tasks_v2 import (
    extract_character_mentions_task,
)
from flows.atomic_tasks.parsing import (
    extract_chapter_plots_task,
    save_plots_task,
    validate_plots_task,
)
from flows.atomic_tasks.summaries import (
    generate_chapter_summary_task,
    update_chapter_summary_task,
)
from flows.utils.helpers import create_checkpoint_manager, create_performance_monitor
from flows.utils.helpers.exceptions import is_llm_account_failure, is_llm_service_failure
from services.material.job_errors import MaterialPipelineError, job_error_code_for_exception

# 并发任务运行器
RUNTIME_TASK_RUNNER: Any = ConcurrentTaskRunner(max_workers=settings.MAX_CONCURRENT_CHAPTERS)

_def_now = time.perf_counter


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _raise_if_llm_account_failure(capability: str, error: Exception) -> None:
    """DeepSeek 鉴权/余额故障：后续章节只会同样失败，立即终止整次拆解。"""
    if is_llm_account_failure(error):
        raise MaterialPipelineError(
            ErrorCode.MATERIAL_LLM_UNAVAILABLE,
            f"{capability}: LLM account failure",
        ) from error


def _raise_if_all_failed(
    capability: str,
    attempted: int,
    errors: list[Exception],
) -> None:
    """
    某能力本轮提交的章节全部失败时判定整次拆解失败，不再标成「部分完成」。

    全部是调用失败（重试耗尽的超时/连接/5xx/429）视为 AI 服务不可用（可退额度）；
    否则为章节内容拆解失败。
    """
    if attempted == 0 or len(errors) < attempted:
        return
    code = (
        ErrorCode.MATERIAL_LLM_UNAVAILABLE
        if all(is_llm_service_failure(error) for error in errors)
        else ErrorCode.MATERIAL_EXTRACTION_FAILED
    )
    raise MaterialPipelineError(code, f"{capability}: all {attempted} chapters failed") from errors[-1]


def _sync_stage1_job_progress(
    novel_id: int,
    *,
    job_id: int | None = None,
    processed_chapters: int | None = None,
    stage_status: str = "processing",
    payload: dict[str, Any] | None = None,
    status: str = "processing",
    error_message: str | None = None,
) -> None:
    """Best-effort sync for ingestion job stage1 progress."""
    from flows.database_session import get_prefect_db_session
    from services.material.ingestion_jobs_service import IngestionJobsService

    try:
        with get_prefect_db_session() as session:
            svc = IngestionJobsService()
            if job_id is not None:
                from models.material_models import IngestionJob

                job = session.get(IngestionJob, job_id)
                if job is None or job.novel_id != novel_id:
                    return
            else:
                job = svc.get_latest_by_novel(session, novel_id)
            if not job:
                return
            svc.update_processed(
                session,
                job.id,
                processed_chapters=processed_chapters,
                status=status,
                stage="stage1",
                stage_status=stage_status,
                stage_data=payload or {},
                error_message=error_message,
            )
            session.commit()
    except Exception:
        # Keep stage processing resilient; progress sync failure must not fail the flow.
        return


@flow(
    name="chapter_extraction_flow",
    retries=0,  # 见 novel_ingestion_v3：流程级重试会与用户重试并跑
    task_runner=RUNTIME_TASK_RUNNER,  # type: ignore[arg-type]
    persist_result=False,
)
def chapter_extraction_flow(
    novel_id: int,
    chapter_ids: list[int],
    correlation_id: str | None = None,  # noqa: ARG001
    job_id: int | None = None,
) -> dict[str, Any]:
    """
    章节提取流程

    并行执行三类任务:
    1. 章节摘要生成
    2. 情节点提取
    3. 实体提取 (可选)

    Args:
        novel_id: 小说ID
        chapter_ids: 章节ID列表

    Returns:
        Dict: 提取结果统计
    """
    logger = get_run_logger()
    flow_start = _def_now()
    stages = resolve_enabled_stages(settings)

    if job_id is None:
        from flows.database_session import get_prefect_db_session
        from services.material.ingestion_jobs_service import IngestionJobsService

        logger.warning(
            "legacy chapter flow caller omitted job_id; resolving latest once for novel_id=%s",
            novel_id,
        )
        with get_prefect_db_session() as session:
            legacy_job = IngestionJobsService().get_latest_by_novel(session, novel_id)
            job_id = legacy_job.id if legacy_job else None

    # 全局失败集合，避免未定义
    failed_chapters: list[int] = []
    failed_plot_chapters: list[int] = []

    logger.info(
        "[章节提取流程] 开始: novel_id=%s, chapters_count=%s",
        novel_id,
        len(chapter_ids),
    )

    try:
        all_chapter_ids = chapter_ids.copy()
        # 初始化监控与 checkpoint
        monitor = create_performance_monitor("chapter_extraction_flow")
        # 初始化 checkpoint 管理器（用于逐章追踪）
        checkpoint = create_checkpoint_manager(novel_id, job_id)
        # Older test fakes may not implement get_completed_chapters.
        if hasattr(checkpoint, "get_completed_chapters"):
            enabled_capabilities = [
                capability
                for capability, enabled in (
                    ("summaries", stages.chapter_summaries),
                    ("plots", stages.plots),
                    ("mentions", stages.characters),
                )
                if enabled
            ]
            completed_sets = [
                set(checkpoint.get_completed_chapters("stage1", capability))
                for capability in enabled_capabilities
            ]
            baseline_completed = len(set.intersection(*completed_sets)) if completed_sets else 0
        else:
            baseline_completed = 0

        # Each capability resumes independently. An explicit empty list means zero
        # submissions and must never fall back to the full chapter list.
        summary_ids = checkpoint.get_pending_chapters(
            "stage1", all_chapter_ids=all_chapter_ids, capability="summaries"
        )
        plot_ids = checkpoint.get_pending_chapters(
            "stage1", all_chapter_ids=all_chapter_ids, capability="plots"
        )
        mention_ids = checkpoint.get_pending_chapters(
            "stage1", all_chapter_ids=all_chapter_ids, capability="mentions"
        )

        _sync_stage1_job_progress(
            novel_id,
            job_id=job_id,
            processed_chapters=baseline_completed,
            stage_status="processing",
            payload={
                "chapters_total": len(all_chapter_ids),
                "pending_chapters": max(len(summary_ids), len(plot_ids), len(mention_ids)),
            },
            status="processing",
        )

        # ========================================
        # 任务1: 章节摘要生成
        # ========================================

        summaries_count = 0
        if stages.chapter_summaries:
            logger.info("任务1: 并行生成 %d 个章节摘要", len(summary_ids))

            # 按并发上限分批提交，避免一次性堆积
            summary_futures = []
            batch = settings.MAX_CONCURRENT_CHAPTERS

            with monitor.measure("chapter_summaries"):
                for i in range(0, len(summary_ids), batch):
                    for chapter_id in summary_ids[i:i+batch]:
                        future = generate_chapter_summary_task.submit(chapter_id=chapter_id)
                        summary_futures.append(future)

            # 等待所有摘要生成完成（带错误处理）
            # 失败章节不写入任何摘要（不再用原文前 200 字冒充摘要），留给重试补跑。
            summary_results = []
            completed_chapter_ids = []
            failed_chapters = []
            summary_errors: list[Exception] = []

            for i, future in enumerate(summary_futures):
                try:
                    result = future.result()
                    summary_results.append(result)
                    completed_chapter_ids.append(result.get("chapter_id", summary_ids[i]))
                except Exception as e:
                    logger.error(
                        f"章节 {summary_ids[i]} 摘要生成失败: {e}",
                        exc_info=True
                    )
                    _raise_if_llm_account_failure("summaries", e)
                    failed_chapters.append(summary_ids[i])
                    summary_errors.append(e)

                if (i + 1) % batch == 0 or i == len(summary_futures) - 1:
                    _sync_stage1_job_progress(
                        novel_id,
                        job_id=job_id,
                        processed_chapters=baseline_completed + len(set(completed_chapter_ids)),
                        stage_status="processing",
                        payload={
                            "summaries_completed": len(set(completed_chapter_ids)),
                            "summary_failed": len(failed_chapters),
                            "pending_chapters": len(summary_ids),
                        },
                        status="processing",
                    )

            _raise_if_all_failed("summaries", len(summary_futures), summary_errors)

            # 回写摘要
            update_futures = []
            for result in summary_results:
                future = update_chapter_summary_task.submit(
                    chapter_id=result["chapter_id"],
                    summary=result["summary"],
                )
                update_futures.append(future)

            # 等待所有更新完成
            [f.result() for f in update_futures]

            # 更新 checkpoint：记录已完成/失败章节
            old_completed = set(checkpoint.get_completed_chapters("stage1", "summaries"))
            old_failed = set(checkpoint.get_failed_chapters("stage1", "summaries"))
            merged_completed = old_completed | set(completed_chapter_ids)
            merged_failed = (old_failed | set(failed_chapters)) - merged_completed
            checkpoint.update_checkpoint(
                "stage1",
                status="processing",
                data={
                    "completed_chapter_ids": sorted(merged_completed),
                    "failed_chapter_ids": sorted(merged_failed),
                },
            )

            summaries_count = len(summary_results)

            if failed_chapters:
                logger.warning(
                    f"任务1完成: 生成 {summaries_count} 个章节摘要，"
                    f"{len(failed_chapters)} 个失败"
                )
            else:
                logger.info("任务1完成: 生成 %d 个章节摘要", summaries_count)
        else:
            logger.info("任务1跳过: 章节摘要功能未启用")

        # ========================================
        # 任务2: 情节点提取
        # ========================================

        plots_count = 0
        if stages.plots:
            logger.info("任务2: 并行提取 %d 个章节的情节点", len(plot_ids))

            # 按并发上限分批提交
            plot_futures = []
            batch = settings.MAX_CONCURRENT_CHAPTERS

            with monitor.measure("plot_extraction"):
                for i in range(0, len(plot_ids), batch):
                    for chapter_id in plot_ids[i:i+batch]:
                        future = extract_chapter_plots_task.submit(chapter_id=chapter_id)
                        plot_futures.append(future)

            # 等待所有提取完成（带错误处理）
            plot_results = []
            failed_plot_chapters = []

            plot_errors: list[Exception] = []
            for i, future in enumerate(plot_futures):
                try:
                    result = future.result()
                    plot_results.append(result)
                except Exception as e:
                    logger.error(
                        f"章节 {plot_ids[i]} 情节点提取失败: {e}",
                        exc_info=True
                    )
                    _raise_if_llm_account_failure("plots", e)
                    failed_plot_chapters.append(plot_ids[i])
                    plot_errors.append(e)

                    # 记录失败章节，但不阻塞流程
                    # 后续可以通过检查点重试这些章节

            _raise_if_all_failed("plots", len(plot_futures), plot_errors)

            # 验证情节点
            validate_futures = []
            for result in plot_results:
                future = validate_plots_task.submit(plots=result["plots"])
                validate_futures.append(future)

            validated_results = [f.result() for f in validate_futures]

            # 保存情节点
            save_futures = []
            completed_plot_chapter_ids: list[int] = []
            for i, result in enumerate(plot_results):
                if validated_results[i]["valid"]:
                    future = save_plots_task.submit(
                        novel_id=novel_id,
                        chapter_id=result["chapter_id"],
                        plots=result["plots"],
                    )
                    save_futures.append(future)
                    completed_plot_chapter_ids.append(result["chapter_id"])
                else:
                    logger.warning(
                        f"章节 {result['chapter_id']} 情节点验证失败: "
                        f"{validated_results[i].get('errors', [])}"
                    )
                    failed_plot_chapters.append(result["chapter_id"])

            # 等待所有保存完成
            save_results = [f.result() for f in save_futures]
            plots_count = sum(r["saved_count"] for r in save_results)

            old_completed = set(checkpoint.get_completed_chapters("stage1", "plots"))
            old_failed = set(checkpoint.get_failed_chapters("stage1", "plots"))
            merged_completed = old_completed | set(completed_plot_chapter_ids)
            merged_failed = (old_failed | set(failed_plot_chapters)) - merged_completed
            checkpoint.update_checkpoint(
                "stage1",
                status="processing",
                data={
                    "completed_plot_chapter_ids": sorted(merged_completed),
                    "failed_plot_chapter_ids": sorted(merged_failed),
                },
            )

            if failed_plot_chapters:
                logger.warning(
                    f"任务2完成: 提取并保存 {plots_count} 个情节点，"
                    f"{len(failed_plot_chapters)} 个章节失败"
                )
            else:
                logger.info("任务2完成: 提取并保存 %d 个情节点", plots_count)
        else:
            logger.info("任务2跳过: 情节点提取功能未启用")

        # ========================================
        # 任务3: 角色提及提取 (阶段1，可选，按章节)
        # ========================================

        mentions_extracted = False
        failed_mention_chapters: list[int] = []
        if stages.characters:
            logger.info("任务3: 并行提取 %d 个章节的角色提及", len(mention_ids))

            # 提取角色提及（轻量级，仅本章信息）
            mention_futures = []
            batch = settings.MAX_CONCURRENT_CHAPTERS

            with monitor.measure("character_mention_extraction"):
                for i in range(0, len(mention_ids), batch):
                    for chapter_id in mention_ids[i:i+batch]:
                        future = extract_character_mentions_task.submit(chapter_id=chapter_id)
                        mention_futures.append(future)

            # 等待所有提及提取完成
            mention_results = []
            completed_mention_chapter_ids: list[int] = []
            mention_errors: list[Exception] = []
            for i, future in enumerate(mention_futures):
                try:
                    result = future.result()
                    mention_results.append(result)
                    completed_mention_chapter_ids.append(
                        result.get("chapter_id", mention_ids[i])
                    )
                except Exception as e:
                    logger.error(
                        f"章节 {mention_ids[i]} 角色提及提取失败: {e}",
                        exc_info=True
                    )
                    _raise_if_llm_account_failure("mentions", e)
                    failed_mention_chapters.append(mention_ids[i])
                    mention_errors.append(e)

            _raise_if_all_failed("mentions", len(mention_futures), mention_errors)

            old_completed = set(checkpoint.get_completed_chapters("stage1", "mentions"))
            old_failed = set(checkpoint.get_failed_chapters("stage1", "mentions"))
            merged_completed = old_completed | set(completed_mention_chapter_ids)
            merged_failed = (old_failed | set(failed_mention_chapters)) - merged_completed
            checkpoint.update_checkpoint(
                "stage1",
                status="processing",
                data={
                    "completed_mention_chapter_ids": sorted(merged_completed),
                    "failed_mention_chapter_ids": sorted(merged_failed),
                },
            )

            mentions_extracted = len(mention_results) > 0
            logger.info(
                "任务3完成: 提取 %d 个章节的角色提及，%d 个失败",
                len(mention_results),
                len(failed_mention_chapters)
            )
        else:
            logger.info("任务3跳过: 角色提及提取功能未启用")

        # ========================================
        # 返回结果
        # ========================================

        # 汇总失败信息
        all_failed_chapters = list(set(failed_chapters + failed_plot_chapters + failed_mention_chapters))
        total_failed = len(all_failed_chapters)

        completed_by_capability = []
        if stages.chapter_summaries:
            completed_by_capability.append(set(checkpoint.get_completed_chapters("stage1", "summaries")))
        if stages.plots:
            completed_by_capability.append(set(checkpoint.get_completed_chapters("stage1", "plots")))
        if stages.characters:
            completed_by_capability.append(set(checkpoint.get_completed_chapters("stage1", "mentions")))
        processed_chapters = (
            len(set.intersection(*completed_by_capability))
            if completed_by_capability
            else len(all_chapter_ids)
        )
        result = {
            "novel_id": novel_id,
            "summaries_count": summaries_count,
            "plots_count": plots_count,
            "mentions_extracted": mentions_extracted,
            "failed_chapters": all_failed_chapters,
            "failed_mention_chapters": failed_mention_chapters,
            "failed_count": total_failed,
            "processed_chapters": processed_chapters,
            "completed_chapter_ids": checkpoint.get_completed_chapters("stage1", "summaries"),
            "failed_chapter_ids": checkpoint.get_failed_chapters("stage1", "summaries"),
            "completed_plot_chapter_ids": checkpoint.get_completed_chapters("stage1", "plots"),
            "failed_plot_chapter_ids": checkpoint.get_failed_chapters("stage1", "plots"),
            "completed_mention_chapter_ids": checkpoint.get_completed_chapters("stage1", "mentions"),
            "failed_mention_chapter_ids": checkpoint.get_failed_chapters("stage1", "mentions"),
            "status": "completed_with_errors" if total_failed > 0 else "completed",
            "elapsed_ms": _elapsed_ms(flow_start),
        }

        if total_failed > 0:
            logger.warning(
                f"章节提取完成，但有 {total_failed} 个章节处理失败。"
                f"失败章节: {result['failed_chapters']}"
            )

        monitor.print_summary()

        logger.info(
            "event=chapter_extraction_done novel_id=%s summaries=%s plots=%s mentions=%s total_ms=%s",
            novel_id,
            summaries_count,
            plots_count,
            mentions_extracted,
            _elapsed_ms(flow_start),
        )

        _sync_stage1_job_progress(
            novel_id,
            job_id=job_id,
            processed_chapters=processed_chapters,
            stage_status=result["status"],
            payload={
                "summaries_count": summaries_count,
                "plots_count": plots_count,
                "failed_mention_chapters": failed_mention_chapters,
                "failed_count": total_failed,
                "elapsed_ms": result["elapsed_ms"],
            },
            status="processing",
        )

        return result

    except Exception as e:
        logger.error("章节提取流程失败: %s", str(e), exc_info=True)
        # 只记录阶段进度；任务的 failed 状态、错误码与退款由主流程统一处理。
        _sync_stage1_job_progress(
            novel_id,
            job_id=job_id,
            stage_status="failed",
            payload={"error_code": job_error_code_for_exception(e)},
            status="processing",
        )
        raise
