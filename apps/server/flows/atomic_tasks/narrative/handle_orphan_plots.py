"""
处理未被任何剧情收录的"孤儿"情节点

使用LLM智能分析情节点与剧情的关联性，将其分配到最合适的剧情中
"""

import json
from typing import Any

from prefect import get_run_logger

from flows.database_session import get_db_session
from flows.utils import call_deepseek_api, get_deepseek_client
from flows.utils.decorators import api_task, database_task
from prompts import create_orphan_assignment_prompt


@database_task(name="handle_orphan_plots", retries=2)
def handle_orphan_plots_task(
    novel_id: int,
    stories: list[dict[str, Any]],
    min_orphan_ratio: float = 0.05,
) -> dict[str, Any]:
    """
    处理未被任何剧情收录的"孤儿"情节点

    使用LLM智能分析情节点与现有剧情的关联性，将其分配到最合适的剧情中。
    如果无法分配，则创建新的补充剧情。

    Args:
        novel_id: 小说ID
        stories: 已保存的剧情列表
        min_orphan_ratio: 最小孤儿比例阈值，低于此值则跳过处理

    Returns:
        Dict: 处理结果统计
    """
    logger = get_run_logger()

    # 1. 获取所有情节点
    from models.material_models import Chapter, Plot, StoryPlotLink
    from services.material.plots_service import PlotsService

    with get_db_session() as db:
        all_plots = PlotsService().list_by_novel(db, novel_id)
        # 在会话内将 ORM 对象投影为纯字典，避免跨任务传递导致 DetachedInstanceError
        all_plots_plain = [
            {
                "id": p.id,
                "chapter_id": p.chapter_id,
                "index": getattr(p, "index", None),
                "plot_type": getattr(p, "plot_type", None),
                "description": getattr(p, "description", None),
                "characters": getattr(p, "characters", []) or [],
            }
            for p in all_plots
        ]
        all_plot_ids = {p["id"] for p in all_plots_plain}

        # 2. 从数据库获取已被收录的情节点（更准确）
        # 查询当前小说的所有已关联到剧情的情节点
        from sqlalchemy import distinct
        from sqlalchemy import select as sa_select
        assigned_links = db.execute(
            sa_select(distinct(StoryPlotLink.plot_id))
            .join(Plot, StoryPlotLink.plot_id == Plot.id)
            .join(Chapter, Plot.chapter_id == Chapter.id)
            .where(Chapter.novel_id == novel_id)
        ).scalars().all()
        assigned_plot_ids = set(assigned_links)

    # 3. 找出孤儿情节点
    orphan_plot_ids = all_plot_ids - assigned_plot_ids

    orphan_ratio = len(orphan_plot_ids) / len(all_plot_ids) if all_plot_ids else 0

    logger.info(
        f"情节点统计: 总数={len(all_plot_ids)}, "
        f"已归类={len(assigned_plot_ids)}, "
        f"未归类={len(orphan_plot_ids)} ({orphan_ratio:.1%})"
    )

    # 4. 判断是否需要处理
    if not orphan_plot_ids:
        logger.info("✅ 所有情节点均已归类，无需处理")
        return {
            "orphan_count": 0,
            "assigned_count": 0,
            "new_stories_count": 0,
            "orphan_ratio": 0,
        }

    if orphan_ratio < min_orphan_ratio:
        logger.info(
            f"⏭️  孤儿情节点占比 {orphan_ratio:.1%} 低于阈值 {min_orphan_ratio:.1%}，跳过处理"
        )
        return {
            "orphan_count": len(orphan_plot_ids),
            "assigned_count": 0,
            "new_stories_count": 0,
            "orphan_ratio": orphan_ratio,
            "skipped": True,
        }

    # 5. 使用LLM智能分配孤儿情节点
    logger.info(f"🤖 开始使用LLM智能分配 {len(orphan_plot_ids)} 个孤儿情节点")

    result = assign_orphans_with_llm_task(
        novel_id=novel_id,
        orphan_plot_ids=list(orphan_plot_ids),
        stories=stories,
        all_plots=all_plots_plain,
    )

    logger.info(
        f"✅ 孤儿情节点处理完成: "
        f"已分配={result['assigned_count']}, "
        f"未分配={result['unassigned_count']}"
    )

    return {
        "orphan_count": len(orphan_plot_ids),
        "assigned_count": result["assigned_count"],
        "unassigned_count": result["unassigned_count"],
        "orphan_ratio": orphan_ratio,
    }


@api_task(name="assign_orphans_with_llm", retries=1)
def assign_orphans_with_llm_task(
    novel_id: int,
    orphan_plot_ids: list[int],
    stories: list[dict[str, Any]],
    all_plots: list,
    batch_size: int = 100,  # 每批处理100个情节点
) -> dict[str, Any]:
    """
    使用LLM智能分配孤儿情节点到现有剧情（支持并发分批处理）

    Args:
        novel_id: 小说ID
        orphan_plot_ids: 孤儿情节点ID列表
        stories: 现有剧情列表
        all_plots: 所有情节点列表
        batch_size: 每批处理的情节点数量

    Returns:
        Dict: 分配结果
    """
    logger = get_run_logger()

    # 修复: 改为串行分批处理,避免数据库并发冲突和死锁
    total_batches = (len(orphan_plot_ids) + batch_size - 1) // batch_size
    logger.info(f"📦 开始串行分批处理 {total_batches} 个批次，每批 {batch_size} 个情节点")

    total_assigned = 0
    total_unassigned = 0

    # 串行处理每个批次,确保数据库操作的安全性
    for i in range(0, len(orphan_plot_ids), batch_size):
        batch_ids = orphan_plot_ids[i:i + batch_size]
        batch_num = i // batch_size + 1

        try:
            result = _assign_single_batch_task(
                novel_id=novel_id,
                orphan_plot_ids=batch_ids,
                stories=stories,
                all_plots=all_plots,
                batch_num=batch_num,
                total_batches=total_batches,
            )
            total_assigned += result["assigned_count"]
            total_unassigned += result["unassigned_count"]
        except Exception as e:
            logger.error(f"❌ 批次 {batch_num} 处理失败: {e}")

    logger.info(
        f"✅ 所有批次处理完成: "
        f"已分配={total_assigned}, 未分配={total_unassigned}"
    )

    return {
        "assigned_count": total_assigned,
        "unassigned_count": total_unassigned,
    }


@api_task(name="assign_single_batch", retries=3)
def _assign_single_batch_task(
    novel_id: int,
    orphan_plot_ids: list[int],
    stories: list[dict[str, Any]],
    all_plots: list,
    batch_num: int,
    total_batches: int,
) -> dict[str, Any]:
    """
    处理单批孤儿情节点（优化版：精简输入输出）

    Args:
        novel_id: 小说ID（用于把 LLM 用量记到小说所有者名下）
        orphan_plot_ids: 本批次的孤儿情节点ID列表
        stories: 现有剧情列表
        all_plots: 所有情节点列表
        batch_num: 当前批次号
        total_batches: 总批次数

    Returns:
        Dict: 本批次的处理结果
    """
    logger = get_run_logger()

    logger.info(
        f"📦 处理第 {batch_num}/{total_batches} 批: "
        f"{len(orphan_plot_ids)} 个情节点"
    )

    # 1. 准备孤儿情节点数据（精简版）
    orphan_plots = []
    for p in all_plots:
        if p.get("id") not in orphan_plot_ids:
            continue

        # 精简描述（只保留前50字）
        desc = p.get("description", "")
        if len(desc) > 50:
            desc = desc[:50] + "..."

        orphan_plots.append({
            "id": p.get("id"),
            "chapter_id": p.get("chapter_id"),
            "description": desc,
            "characters": p.get("characters", []),
        })

    # 2. 准备现有剧情数据（精简版：移除sample_plots）
    from models.material_models import Chapter

    existing_stories = []
    for s in stories:
        if not s.get("id"):
            continue

        story_plot_ids = s.get("plot_ids", [])

        # 提取核心角色（从所有情节点中统计）
        core_characters = set()
        for p in all_plots:
            if p.get("id") in story_plot_ids:
                core_characters.update(p.get("characters", []))

        # 计算章节范围
        chapter_ids = set()
        for p in all_plots:
            if p.get("id") in story_plot_ids:
                chapter_ids.add(p.get("chapter_id"))

        # 获取章节号范围
        with get_db_session() as db:
            from sqlmodel import select
            chapters = db.exec(
                select(Chapter.chapter_number).where(Chapter.id.in_(chapter_ids))
            ).all()
            chapter_numbers = list(chapters)

        chapter_range = [min(chapter_numbers), max(chapter_numbers)] if chapter_numbers else [0, 0]

        # 精简synopsis（只保留前100字）
        synopsis = s.get("synopsis", "")
        if len(synopsis) > 100:
            synopsis = synopsis[:100] + "..."

        existing_stories.append({
            "id": s.get("id"),
            "title": s.get("title"),
            "synopsis": synopsis,
            "themes": s.get("themes", []),
            "core_characters": sorted(core_characters),
            "chapter_range": chapter_range,
        })

    # 3. 构建Prompt
    system_prompt = create_orphan_assignment_prompt()

    user_message = f"""
# 现有剧情列表
共 {len(existing_stories)} 个剧情：

{json.dumps(existing_stories, ensure_ascii=False, indent=2)}

# 未归类情节点列表
共 {len(orphan_plots)} 个情节点：

{json.dumps(orphan_plots, ensure_ascii=False, indent=2)}

请分析这些未归类的情节点，将它们分配到最合适的现有剧情中，或创建新剧情收录。
"""

    # 4. 调用LLM
    logger.info(f"📤 调用LLM处理 {len(orphan_plots)} 个情节点...")
    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_novel_id=novel_id,
    )

    client = get_deepseek_client()
    data = client.extract_json_from_response(response)

    # 5. 解析新格式的返回结果
    # 格式：{"assign": {"456": [123, 124]}, "unassigned": [100, 101]}
    assign_map = data.get("assign", {})
    unassigned_ids = data.get("unassigned", [])

    # 转换为assignments列表（用于复用现有逻辑）
    assignments = []
    for story_id_str, plot_ids in assign_map.items():
        story_id = int(story_id_str)
        for plot_id in plot_ids:
            assignments.append({
                "plot_id": plot_id,
                "story_id": story_id,
            })

    logger.info(
        f"📥 LLM返回: "
        f"{len(assignments)} 个分配, "
        f"{len(unassigned_ids)} 个未分配"
    )

    # 6. 执行分配（只接受本批次的剧情与孤儿情节点，LLM 返回的其他 ID 一律丢弃）
    assigned_count = _execute_assignments(
        assignments,
        allowed_story_ids={int(s["id"]) for s in existing_stories},
        allowed_plot_ids={int(pid) for pid in orphan_plot_ids},
    )

    return {
        "assigned_count": assigned_count,
        "unassigned_count": len(unassigned_ids),
    }





def _execute_assignments(
    assignments: list[dict[str, Any]],
    *,
    allowed_story_ids: set[int],
    allowed_plot_ids: set[int],
) -> int:
    """
    执行情节点分配

    Args:
        assignments: 分配列表，每项包含 plot_id, story_id（来自 LLM，不可信）
        allowed_story_ids: 本批次提供给 LLM 的剧情 ID；其他 story_id 一律丢弃
        allowed_plot_ids: 本批次的孤儿情节点 ID；其他 plot_id 一律丢弃

    Returns:
        int: 成功分配（新建链接）的数量；已存在的链接不重复创建
    """
    logger = get_run_logger()

    from sqlalchemy import select as sa_select

    from models.material_models import Chapter, Plot, StoryPlotLink
    from services.material.story_plots_service import StoryPlotsService

    assigned_count = 0
    affected_story_ids: set[int] = set()

    with get_db_session() as db:
        existing_pairs: set[tuple[int, int]] = set()
        if allowed_story_ids:
            existing_pairs = {
                (int(row.story_id), int(row.plot_id))
                for row in db.execute(
                    sa_select(StoryPlotLink.story_id, StoryPlotLink.plot_id).where(
                        StoryPlotLink.story_id.in_(allowed_story_ids)
                    )
                ).all()
            }

        # 1) 幂等写入所有分配（先不关心顺序）
        for assignment in assignments:
            try:
                plot_id = int(assignment.get("plot_id"))
                story_id = int(assignment.get("story_id"))
            except (TypeError, ValueError):
                logger.warning(f"⚠️  无效的分配: {assignment}")
                continue

            if story_id not in allowed_story_ids or plot_id not in allowed_plot_ids:
                logger.warning(f"⚠️  丢弃不属于本批次的分配: {assignment}")
                continue
            if (story_id, plot_id) in existing_pairs:
                continue

            try:
                StoryPlotsService().link_plot_to_story(
                    db,
                    story_id=story_id,
                    plot_id=plot_id,
                )
                existing_pairs.add((story_id, plot_id))
                assigned_count += 1
                affected_story_ids.add(story_id)
                logger.info(
                    f"✅ 分配成功: plot_id={plot_id} -> story_id={story_id}"
                )
            except Exception as e:
                logger.error(f"❌ 分配失败: plot_id={plot_id}, story_id={story_id}, error={e}")

        # 2) 对受影响的每个 story，按叙事顺序整体重排 order_index
        for sid in affected_story_ids:
            rows = db.execute(
                sa_select(
                    StoryPlotLink.id,
                    Plot.id.label("plot_id"),
                    Chapter.chapter_number,
                    Plot.index.label("plot_index")
                )
                .join(Plot, StoryPlotLink.plot_id == Plot.id)
                .join(Chapter, Plot.chapter_id == Chapter.id)
                .where(StoryPlotLink.story_id == sid)
            ).all()
            ordered = sorted(rows, key=lambda r: (r.chapter_number, r.plot_index, r.plot_id))
            for new_idx, row in enumerate(ordered):
                link = db.get(StoryPlotLink, row.id)
                if link:
                    link.order_index = new_idx
        db.commit()

    return assigned_count



