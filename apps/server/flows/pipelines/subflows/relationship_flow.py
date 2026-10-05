"""
人物关系流程 (阶段2B)

职责:
1. 提取人物关系 (基于情节点和实体)
2. 构建人物关系
3. 按章节存储到 Neo4j

这是主流程的阶段2B,串行执行
"""

import time
from typing import Any

from prefect import flow, get_run_logger
from prefect.task_runners import ConcurrentTaskRunner

from config.material_settings import material_settings as settings
from config.material_settings import resolve_enabled_stages
from flows.atomic_tasks.linking import (
    build_character_relationships_task,
    extract_character_relationships_task,
    persist_chapter_relationships_to_neo4j_task,
)
from flows.utils.helpers import create_checkpoint_manager, create_performance_monitor

_def_now = time.perf_counter


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


DEFAULT_RELATIONSHIP_BATCH_SIZE = 5

# 并发任务运行器
RUNTIME_TASK_RUNNER = ConcurrentTaskRunner(max_workers=settings.MAX_CONCURRENT_WORKFLOWS)

@flow(
    name="relationship_flow",
    retries=0,  # 见 novel_ingestion_v3：流程级重试会与用户重试并跑
    task_runner=RUNTIME_TASK_RUNNER,  # type: ignore[arg-type]
    persist_result=False,
)
def relationship_flow(
    novel_id: int,
    chapter_ids: list[int],
    correlation_id: str | None = None,  # noqa: ARG001
    job_id: int | None = None,
) -> dict[str, Any]:
    """
    人物关系流程

    流程:
    1. 提取人物关系
    2. 构建人物关系
    3. 按章节存储到 Neo4j

    Args:
        novel_id: 小说ID
        chapter_ids: 章节ID列表

    Returns:
        Dict: 关系处理结果
    """
    logger = get_run_logger()
    flow_start = _def_now()

    logger.info(
        "[关系流程] 开始: novel_id=%s, chapters_count=%s",
        novel_id,
        len(chapter_ids),
    )

    try:
        # 初始化监控与 checkpoint
        monitor = create_performance_monitor("relationship_flow")
        checkpoint = create_checkpoint_manager(novel_id, job_id=job_id)

        # 【修复 Bug #2】使用独立的 stage2b checkpoint，避免与 story_aggregate_flow 冲突
        checkpoint_stage = "stage2b"

        # ========================================
        # 步骤1: 提取人物关系
        # ========================================

        relationships_count = 0
        if resolve_enabled_stages(settings).relationships:
            logger.info(f"步骤1: 提取人物关系（每{DEFAULT_RELATIONSHIP_BATCH_SIZE}章一批，贯穿全书）")

            with monitor.measure("relationship_extract_build"):
                # 提取关系（分批处理）
                extract_result = extract_character_relationships_task(
                    novel_id=novel_id,
                    chapter_ids=chapter_ids,
                    batch_size=DEFAULT_RELATIONSHIP_BATCH_SIZE,
                )

                # 构建关系
                build_result = build_character_relationships_task(
                    novel_id=novel_id,
                    relationships_data=extract_result["relationships"],
                )

                relationships_count = build_result["saved_count"]

            # 更新 checkpoint：记录已提取/构建的关系数量
            checkpoint.update_checkpoint(
                checkpoint_stage,
                status="processing",
                data={"relationships_count": relationships_count},
            )
            logger.info(
                "步骤1完成: 处理 %d 个批次，提取并保存 %d 个人物关系",
                extract_result.get("batches_processed", 0),
                relationships_count
            )
        else:
            logger.info("步骤1跳过: 人物关系提取功能未启用")

        # ========================================
        # 步骤2: 批量并行存储到 Neo4j
        # ========================================

        neo4j_persisted = False
        neo4j_failed_chapters: list[int] = []
        if settings.ENABLE_NEO4J_STORAGE and relationships_count > 0:
            logger.info("步骤2: 批量并行存储到 Neo4j")

            # 【优化】降低 Neo4j 写入并发度，减少死锁（从 MAX_CONCURRENT_CHAPTERS 降到 3-5）
            # Neo4j 对并发写入同一节点非常敏感，需要限制并发数
            neo4j_batch_size = min(settings.MAX_CONCURRENT_CHAPTERS, 3)  # 最多3个章节并发
            total_batches = (len(chapter_ids) + neo4j_batch_size - 1) // neo4j_batch_size

            with monitor.measure("neo4j_persist"):
                for batch_idx in range(total_batches):
                    start_idx = batch_idx * neo4j_batch_size
                    end_idx = min(start_idx + neo4j_batch_size, len(chapter_ids))
                    batch_chapter_ids = chapter_ids[start_idx:end_idx]

                    logger.info(
                        f"处理批次 {batch_idx + 1}/{total_batches}: "
                        f"{len(batch_chapter_ids)} 个章节（降低并发以减少死锁）"
                    )

                    # 并行提交批次
                    futures = []
                    for chapter_id in batch_chapter_ids:
                        future = persist_chapter_relationships_to_neo4j_task.submit(
                            novel_id=novel_id,
                            chapter_id=chapter_id,
                        )
                        futures.append(future)

                    # 等待批次完成
                    for ch_id, future in zip(batch_chapter_ids, futures, strict=False):
                        try:
                            future.result()
                        except Exception as e:
                            logger.error(f"Neo4j存储失败: chapter_id={ch_id} err={e}")
                            neo4j_failed_chapters.append(ch_id)

                    # 更新 checkpoint：已经完成的批次数
                    checkpoint.update_checkpoint(
                        checkpoint_stage,
                        status="processing",
                        data={
                            "neo4j_batches_completed": batch_idx + 1,
                            "neo4j_total_batches": total_batches,
                            "neo4j_failed_chapters": neo4j_failed_chapters,
                        },
                    )

                    logger.info(
                        f"批次 {batch_idx + 1} 完成: "
                        f"已处理 {min(end_idx, len(chapter_ids))}/{len(chapter_ids)} 章节"
                    )

            neo4j_persisted = len(neo4j_failed_chapters) == 0
            checkpoint.update_checkpoint(
                checkpoint_stage,
                status="processing",
                data={"neo4j_persisted": neo4j_persisted},
            )
            logger.info("步骤2完成: 已存储 %d 个章节的关系到 Neo4j", len(chapter_ids))
        else:
            logger.info("步骤2跳过: Neo4j 存储功能未启用或无关系数据")

        # ========================================
        # 返回结果
        # ========================================

        result = {
            "novel_id": novel_id,
            "relationships_count": relationships_count,
            "neo4j_persisted": neo4j_persisted,
            "neo4j_failed_chapters": neo4j_failed_chapters,
            "status": "completed_with_errors" if neo4j_failed_chapters else "completed",
            "elapsed_ms": _elapsed_ms(flow_start),
        }

        monitor.print_summary()

        # 【修复 Bug #8】子流程保持 processing 状态，由主流程统一标记 completed
        checkpoint.update_checkpoint(checkpoint_stage, status="processing", data=result)

        logger.info(
            "event=relationship_flow_done novel_id=%s relationships=%s neo4j=%s total_ms=%s",
            novel_id,
            relationships_count,
            neo4j_persisted,
            _elapsed_ms(flow_start),
        )

        return result

    except Exception as e:
        logger.error("人物关系流程失败: %s", str(e), exc_info=True)
        raise
