"""
人物关系流程 (阶段2B)

职责:
1. 提取人物关系 (基于情节点和实体)
2. 构建并持久化人物关系

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
)
from flows.utils.helpers import create_checkpoint_manager, create_performance_monitor

_def_now = time.perf_counter


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


DEFAULT_RELATIONSHIP_BATCH_SIZE = 5

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
    """提取并保存整本书的人物关系。"""
    logger = get_run_logger()
    flow_start = _def_now()

    logger.info(
        "[关系流程] 开始: novel_id=%s, chapters_count=%s",
        novel_id,
        len(chapter_ids),
    )

    try:
        monitor = create_performance_monitor("relationship_flow")
        checkpoint = create_checkpoint_manager(novel_id, job_id=job_id)
        checkpoint_stage = "stage2b"

        relationships_count = 0
        if resolve_enabled_stages(settings).relationships:
            logger.info(f"步骤1: 提取人物关系（每{DEFAULT_RELATIONSHIP_BATCH_SIZE}章一批，贯穿全书）")

            with monitor.measure("relationship_extract_build"):
                extract_result = extract_character_relationships_task(
                    novel_id=novel_id,
                    chapter_ids=chapter_ids,
                    batch_size=DEFAULT_RELATIONSHIP_BATCH_SIZE,
                )

                build_result = build_character_relationships_task(
                    novel_id=novel_id,
                    relationships_data=extract_result["relationships"],
                )

                relationships_count = build_result["saved_count"]

            checkpoint.update_checkpoint(
                checkpoint_stage,
                status="processing",
                data={"relationships_count": relationships_count},
            )
            logger.info(
                "步骤1完成: 处理 %d 个批次，提取并保存 %d 个人物关系",
                extract_result.get("batches_processed", 0),
                relationships_count,
            )
        else:
            logger.info("步骤1跳过: 人物关系提取功能未启用")

        result = {
            "novel_id": novel_id,
            "relationships_count": relationships_count,
            "status": "completed",
            "elapsed_ms": _elapsed_ms(flow_start),
        }

        monitor.print_summary()

        # 子流程保持 processing 状态，由主流程统一标记 completed
        checkpoint.update_checkpoint(checkpoint_stage, status="processing", data=result)

        logger.info(
            "event=relationship_flow_done novel_id=%s relationships=%s total_ms=%s",
            novel_id,
            relationships_count,
            _elapsed_ms(flow_start),
        )

        return result

    except Exception as e:
        logger.error("人物关系流程失败: %s", str(e), exc_info=True)
        raise
