"""
剧情聚合流程 (阶段2A)

职责:
1. 生成小说概要 (基于章节摘要,一次性生成)
2. 剧情聚合 (基于摘要+情节点)
3. 剧情线生成 (基于剧情)

这是主流程的阶段2A,串行执行
"""

import json
import time
from typing import Any

from prefect import flow, get_run_logger
from prefect.task_runners import ConcurrentTaskRunner

from config.material_settings import material_settings as settings
from config.material_settings import resolve_enabled_stages
from flows.atomic_tasks.narrative import (
    aggregate_plots_to_stories_task,
    generate_storylines_task,
    handle_orphan_plots_task,
    identify_story_frameworks_with_chunking,
    save_stories_task,
    save_storylines_task,
)
from flows.atomic_tasks.summaries import (
    generate_novel_synopsis_task,
    update_novel_synopsis_task,
)
from flows.database_session import get_db_session
from flows.utils.helpers import create_checkpoint_manager, create_performance_monitor

_def_now = time.perf_counter


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


# 并发任务运行器
RUNTIME_TASK_RUNNER = ConcurrentTaskRunner(max_workers=settings.MAX_CONCURRENT_WORKFLOWS)

@flow(
    name="story_aggregate_flow",
    retries=0,  # 见 novel_ingestion_v3：流程级重试会与用户重试并跑
    task_runner=RUNTIME_TASK_RUNNER,  # type: ignore[arg-type]
    persist_result=False,
)
def story_aggregate_flow(
    novel_id: int,
    chapter_ids: list[int],
    correlation_id: str | None = None,  # noqa: ARG001
    job_id: int | None = None,
) -> dict[str, Any]:
    """
    剧情聚合流程

    流程:
    1. 生成小说概要 (基于章节摘要)
    2. 剧情聚合 (基于摘要+情节点)
    3. 剧情线生成 (基于剧情)

    Args:
        novel_id: 小说ID
        chapter_ids: 章节ID列表

    Returns:
        Dict: 聚合结果统计
    """
    logger = get_run_logger()
    flow_start = _def_now()
    stages = resolve_enabled_stages(settings)

    logger.info(
        "[剧情聚合流程] 开始: novel_id=%s, chapters_count=%s",
        novel_id,
        len(chapter_ids),
    )

    try:
        # 初始化监控与 checkpoint
        monitor = create_performance_monitor("story_aggregate_flow")
        checkpoint = create_checkpoint_manager(novel_id, job_id=job_id)

        # 【修复 Bug #2】使用独立的 stage2a checkpoint，避免与 relationship_flow 冲突
        checkpoint_stage = "stage2a"

        # ========================================
        # 步骤1: 生成小说概要 + 提取元数据
        # ========================================

        synopsis_generated = False
        meta_extracted = False

        # 1.1 生成小说概要
        if stages.synopsis:
            logger.info("步骤1.1: 生成小说概要")

            # 获取章节摘要
            from services.material.chapters_service import ChaptersService
            with get_db_session() as db:
                chapters = ChaptersService().list_by_novel_ordered(db, novel_id, chapter_ids)

                chapter_summaries = [
                    {
                        "chapter_id": ch.id,
                        "chapter_number": ch.chapter_number,
                        "chapter_title": ch.title,
                        "summary": ch.summary,
                    }
                    for ch in chapters
                ]

            # 生成概要
            with monitor.measure("novel_synopsis"):
                synopsis_result = generate_novel_synopsis_task(
                    novel_id=novel_id,
                    chapter_summaries=chapter_summaries,
                )

                # 回写概要
                update_novel_synopsis_task(
                    novel_id=novel_id,
                    synopsis=synopsis_result["synopsis"],
                )

                synopsis_generated = True

            checkpoint.update_checkpoint(
                checkpoint_stage,
                status="processing",
                data={"synopsis_generated": True},
            )
            logger.info("步骤1.1完成: 小说概要已生成")
        else:
            logger.info("步骤1.1跳过: 小说概要功能未启用")

        # 1.2 元数据应由阶段1统一触发，这里仅根据checkpoint判断是否已完成
        if stages.meta:
            resume_cp = checkpoint.get_checkpoint("stage2") if hasattr(checkpoint, "get_checkpoint") else None
            _raw = getattr(resume_cp, "checkpoint_data", None) if resume_cp else None
            if isinstance(_raw, str):
                try:
                    _raw = json.loads(_raw)
                except (json.JSONDecodeError, TypeError):
                    _raw = {}
            cp_data: dict[str, Any] = _raw if isinstance(_raw, dict) else {}
            if cp_data.get("meta_extracted"):
                logger.info("步骤1.2跳过: 检测到元数据已提取 (checkpoint)")
                meta_extracted = True
            else:
                logger.info("步骤1.2跳过: 未检测到checkpoint标记，将由上游阶段负责触发元数据提取")
        else:
            logger.info("步骤1.2跳过: 元数据提取功能未启用")

        # ========================================
        # 步骤2: 剧情聚合
        # ========================================

        stories_count = 0
        stories = []
        failed_stories: list[str] = []
        if stages.stories:
            logger.info("步骤2: 剧情聚合（智能分块）")

            # 2.1 智能分块 + 识别剧情框架
            frameworks_result = identify_story_frameworks_with_chunking(
                novel_id=novel_id,
                enable_chunking=True,
                max_chunks=10,
            )

            story_frameworks = frameworks_result.get("story_frameworks", [])
            chunking_info = frameworks_result.get("chunking_info", {})

            logger.info(
                "识别到 %d 个剧情框架 (分块策略: %s, 共 %d 块)",
                len(story_frameworks),
                chunking_info.get("chunking_strategy", "N/A"),
                chunking_info.get("chunk_count", 0),
            )

            # 2.2 聚合情节点为剧情（真正的分批并行处理）
            if story_frameworks:
                logger.info("开始分批并行聚合 %d 个剧情框架", len(story_frameworks))

                # 修复: 真正的分批处理 - 每批提交后等待完成,避免资源耗尽
                batch_size = settings.MAX_CONCURRENT_WORKFLOWS
                total_batches = (len(story_frameworks) + batch_size - 1) // batch_size

                for batch_idx in range(0, len(story_frameworks), batch_size):
                    batch_num = batch_idx // batch_size + 1
                    batch_frameworks = story_frameworks[batch_idx:batch_idx + batch_size]

                    logger.info(
                        "处理批次 %d/%d: %d 个剧情框架",
                        batch_num, total_batches, len(batch_frameworks)
                    )

                    # 提交当前批次的任务
                    batch_futures = []
                    with monitor.measure(f"aggregate_stories_batch_{batch_num}"):
                        for idx, framework in enumerate(batch_frameworks, batch_idx + 1):
                            future = aggregate_plots_to_stories_task.submit(
                                story_framework=framework,
                                novel_id=novel_id,
                            )
                            batch_futures.append((idx, framework.get("title", "未命名"), future))

                        # 等待当前批次完成
                        for idx, title, future in batch_futures:
                            try:
                                story_result = future.result()
                                stories.append(story_result["story"])
                                logger.info("剧情 %d/%d 聚合完成: %s", idx, len(story_frameworks), title)
                            except Exception as e:
                                logger.error("剧情 %d 聚合失败: %s - %s", idx, title, str(e))
                                failed_stories.append(f"{idx}:{title}")

                # 2.3 保存剧情
                if stories:
                    save_result = save_stories_task(
                        stories=stories,
                        novel_id=novel_id,
                    )

                    stories_count = save_result["saved_count"]
                    story_id_map = save_result.get("story_id_map", {})

                    # 修复: 使用标题映射精确回填ID,支持重名剧情的索引区分
                    for idx, story in enumerate(stories):
                        story_title = story.get("title")
                        if not story_title:
                            continue

                        # 优先使用标题匹配,如果失败则尝试"标题#索引"
                        if story_title in story_id_map:
                            story["id"] = story_id_map[story_title]
                        elif f"{story_title}#{idx}" in story_id_map:
                            story["id"] = story_id_map[f"{story_title}#{idx}"]
                        else:
                            logger.warning(f"剧情 '{story_title}' (索引{idx}) 未找到对应的数据库ID")

                    checkpoint.update_checkpoint(
                        checkpoint_stage,
                        status="processing",
                        data={"stories_saved": stories_count},
                    )
                    logger.info("步骤2完成: 聚合并保存 %d 个剧情", stories_count)
                else:
                    logger.warning("步骤2完成: 所有剧情聚合均失败")
            else:
                logger.warning("步骤2跳过: 未识别到剧情框架")
        else:
            logger.info("步骤2跳过: 剧情聚合功能未启用")

        # ========================================
        # 步骤2.5: 处理未归类的情节点（孤儿情节点）
        # ========================================

        orphan_handled = 0
        if stages.stories and stories_count > 0:
            logger.info("步骤2.5: 处理未归类情节点")

            with monitor.measure("handle_orphan_plots"):
                orphan_result = handle_orphan_plots_task(
                    novel_id=novel_id,
                    stories=stories,
                    min_orphan_ratio=0.05,  # 孤儿比例低于5%则跳过
                )

                orphan_handled = orphan_result.get("assigned_count", 0)

                if orphan_result.get("skipped"):
                    logger.info(
                        "步骤2.5跳过: 孤儿情节点占比 %.1f%% 低于阈值",
                        orphan_result.get("orphan_ratio", 0) * 100
                    )
                else:
                    logger.info(
                        "步骤2.5完成: 处理 %d 个孤儿情节点 (分配=%d, 未分配=%d)",
                        orphan_result.get("orphan_count", 0),
                        orphan_result.get("assigned_count", 0),
                        orphan_result.get("unassigned_count", 0),
                    )
        else:
            logger.info("步骤2.5跳过: 无剧情或剧情聚合未启用")

        # ========================================
        # 步骤3: 剧情线生成
        # ========================================

        storylines_count = 0
        if stages.storylines and stories_count > 0:
            logger.info("步骤3: 剧情线生成")

            # 生成剧情线
            with monitor.measure("storyline_generation"):
                storylines_result = generate_storylines_task(
                    stories=stories,
                    novel_id=novel_id,
                )

                # 保存剧情线
                save_storylines_result = save_storylines_task(
                    storylines=storylines_result["storylines"],
                    novel_id=novel_id,
                )

                storylines_count = save_storylines_result["saved_count"]

            checkpoint.update_checkpoint(
                checkpoint_stage,
                status="processing",
                data={"storylines_saved": storylines_count},
            )
            logger.info("步骤3完成: 生成并保存 %d 条剧情线", storylines_count)
        else:
            logger.info("步骤3跳过: 剧情线生成功能未启用或无剧情")

        # ========================================
        # 返回结果
        # ========================================

        result = {
            "novel_id": novel_id,
            "synopsis_generated": synopsis_generated,
            "meta_extracted": meta_extracted,
            "stories_count": stories_count,
            "orphan_handled": orphan_handled,
            "storylines_count": storylines_count,
            "failed_stories": failed_stories,
            "status": "completed_with_errors" if failed_stories else "completed",
            "elapsed_ms": _elapsed_ms(flow_start),
        }

        monitor.print_summary()

        # 【修复 Bug #8】子流程保持 processing 状态，由主流程统一标记 completed
        checkpoint.update_checkpoint(checkpoint_stage, status="processing", data=result)

        logger.info(
            "event=story_aggregate_done novel_id=%s synopsis=%s stories=%s storylines=%s total_ms=%s",
            novel_id,
            synopsis_generated,
            stories_count,
            storylines_count,
            _elapsed_ms(flow_start),
        )

        return result

    except Exception as e:
        logger.error("剧情聚合流程失败: %s", str(e), exc_info=True)
        raise
