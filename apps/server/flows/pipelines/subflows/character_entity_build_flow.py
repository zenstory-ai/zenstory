"""
角色实体构建流程（阶段2）

职责:
- 从 character_mentions 表汇总角色信息
- 按角色维度并行构建实体
- 生成完整、连贯的角色描述
"""

import time
from typing import Any

from prefect import flow, get_run_logger
from prefect.task_runners import ConcurrentTaskRunner

from config.material_settings import material_settings as settings
from flows.atomic_tasks.entities.character_tasks_v2 import (
    build_character_entity_task,
)
from flows.utils.helpers import create_performance_monitor

# 并发任务运行器
RUNTIME_TASK_RUNNER: Any = ConcurrentTaskRunner(max_workers=settings.MAX_CONCURRENT_CHAPTERS)

_def_now = time.perf_counter


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


@flow(
    name="character_entity_build_flow",
    retries=0,  # 见 novel_ingestion_v3：流程级重试会与用户重试并跑
    task_runner=RUNTIME_TASK_RUNNER,  # type: ignore[arg-type]
    persist_result=False,
)
def character_entity_build_flow(
    novel_id: int,
    correlation_id: str | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    """
    角色实体构建流程

    从 character_mentions 表汇总角色信息，按角色维度并行构建实体

    Args:
        novel_id: 小说ID

    Returns:
        Dict: 构建结果统计
    """
    logger = get_run_logger()
    flow_start = _def_now()

    logger.info(
        "[角色实体构建流程] 开始: novel_id=%s",
        novel_id,
    )

    try:
        # 初始化监控
        monitor = create_performance_monitor("character_entity_build_flow")

        # 查询所有角色提及，按角色主名分组
        from flows.database_session import get_db_session
        from services.material.character_mentions_service import CharacterMentionsService

        # 【关键修复】在 Session 内完成所有数据提取，转换为 Python 原生类型
        with get_db_session() as db:
            mention_svc = CharacterMentionsService()
            all_mentions = mention_svc.get_by_novel(db, novel_id)

            # 提取需要的数据为 Python 原生类型（避免 DetachedInstanceError）
            mention_data = [
                {
                    "character_name": m.character_name,
                    "importance": m.importance,
                }
                for m in all_mentions
            ]

            logger.info(
                f"[角色实体构建流程] 发现 {len(mention_data)} 条提及记录"
            )

        if not mention_data:
            logger.warning("[角色实体构建流程] 未发现任何角色提及，跳过")
            return {
                "novel_id": novel_id,
                "created_count": 0,
                "updated_count": 0,
                "failed_count": 0,
                "total_count": 0,
                "elapsed_ms": _elapsed_ms(flow_start),
            }

        # ========================================
        # 【优化】筛选重要角色（基于出现频次和戏份）
        # ========================================

        # 统计每个角色的出现频次和戏份权重
        character_stats = {}
        importance_scores = {"major": 3, "supporting": 2, "minor": 1}

        for mention in mention_data:
            char_name = mention["character_name"]
            if char_name not in character_stats:
                character_stats[char_name] = {
                    "count": 0,
                    "total_score": 0,
                    "max_importance": "minor",
                }

            character_stats[char_name]["count"] += 1
            score = importance_scores.get(mention["importance"] or "minor", 1)
            character_stats[char_name]["total_score"] += score

            # 更新最高戏份
            if score > importance_scores.get(character_stats[char_name]["max_importance"], 0):
                character_stats[char_name]["max_importance"] = mention["importance"] or "minor"

        # 计算综合权重（出现频次 * 平均戏份）
        for _char_name, stats in character_stats.items():
            avg_score = stats["total_score"] / stats["count"]
            stats["weight"] = stats["count"] * avg_score

        # 按权重排序，选择前N个重要角色
        sorted_characters = sorted(
            character_stats.items(),
            key=lambda x: x[1]["weight"],
            reverse=True
        )

        # 动态确定提取数量（10-30个，视总人数而定）
        total_chars = len(sorted_characters)
        if total_chars <= 15:
            top_n = total_chars  # 少于15个，全部提取
        elif total_chars <= 50:
            top_n = min(20, total_chars)  # 15-50个，提取20个
        else:
            top_n = min(30, total_chars)  # 超过50个，提取30个

        important_characters = [char_name for char_name, _ in sorted_characters[:top_n]]

        logger.info(
            f"[角色实体构建流程] 从 {total_chars} 个角色中筛选出 {top_n} 个重要角色进行构建"
        )
        logger.debug(
            f"[角色实体构建流程] 重要角色列表: {important_characters[:10]}..."
        )

        # 按角色维度并行构建实体
        logger.info(f"[角色实体构建流程] 并行构建 {len(important_characters)} 个角色实体")

        entity_futures = []
        character_future_map = {}  # 映射 future -> character_name
        batch = settings.MAX_CONCURRENT_CHAPTERS

        with monitor.measure("character_entity_build"):
            for i in range(0, len(important_characters), batch):
                for character_name in important_characters[i:i+batch]:
                    future = build_character_entity_task.submit(
                        novel_id=novel_id,
                        character_name=character_name,
                    )
                    entity_futures.append(future)
                    character_future_map[future] = character_name

        # 等待所有构建完成
        entity_results = []
        failed_characters = []
        skipped_characters = []  # 无提及记录的角色

        for future in entity_futures:
            try:
                result = future.result()
                entity_results.append(result)

                # 记录无提及记录的角色（不应该发生，但做防御性处理）
                if result.get("status") == "no_mentions":
                    character_name = character_future_map.get(future, result.get("character_name", "unknown"))
                    skipped_characters.append(character_name)
                    logger.warning(
                        f"角色 {character_name} 无提及记录（不应该发生，可能是数据不一致）"
                    )
            except Exception as e:
                character_name = character_future_map.get(future, "unknown")
                logger.error(
                    f"角色 {character_name} 实体构建失败: {e}",
                    exc_info=True
                )
                failed_characters.append(character_name)

        # 统计结果
        created_count = sum(1 for r in entity_results if r.get("status") == "created")
        updated_count = sum(1 for r in entity_results if r.get("status") == "updated")
        skipped_count = len(skipped_characters)
        failed_count = len(failed_characters)

        logger.info(
            "[角色实体构建流程] 完成: created=%s, updated=%s, skipped=%s, failed=%s, elapsed_ms=%s",
            created_count,
            updated_count,
            skipped_count,
            failed_count,
            _elapsed_ms(flow_start),
        )

        monitor.print_summary()

        return {
            "novel_id": novel_id,
            "total_characters": total_chars,  # 总角色数
            "selected_characters": top_n,  # 筛选出的重要角色数
            "created_count": created_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "failed_characters": failed_characters,
            "skipped_characters": skipped_characters,
            "total_count": created_count + updated_count,
            "status": "completed_with_errors" if failed_count > 0 else "completed",
            "elapsed_ms": _elapsed_ms(flow_start),
        }

    except Exception as e:
        logger.error("角色实体构建流程失败: %s", str(e), exc_info=True)
        raise
