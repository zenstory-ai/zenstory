"""
基于章节摘要的剧情聚合任务

核心任务:
- identify_story_frameworks_with_chunking: 基于智能分块的剧情框架识别
- identify_story_frameworks_from_summaries: 从章节摘要识别剧情框架
- aggregate_plots_for_story_framework: 根据剧情框架聚合情节点
- extract_storylines_from_stories: 从剧情中提炼剧情线
- save_stories_with_plots: 保存剧情及其情节点关联
- save_storylines_with_stories: 保存剧情线及其剧情关联
- summary_based_story_aggregation: 主流程编排

流程:
1. 智能分块（可选）
2. 从章节摘要识别剧情框架
3. 根据章节范围聚合情节点
4. 从剧情中提炼剧情线
5. 保存到数据库
"""

import json
from typing import Any

from prefect import get_run_logger

from flows.database_session import get_db_session
from flows.utils import api_task, call_deepseek_api, database_task, get_deepseek_client

from .intelligent_chunking import intelligent_chunking_task

# ==================== 核心任务 ====================

@api_task(name="identify_story_frameworks_with_chunking", retries=3)
def identify_story_frameworks_with_chunking(
    novel_id: int,
    enable_chunking: bool = True,
    max_chunks: int = 15,  # 增加最大块数，与intelligent_chunking保持一致
) -> dict[str, Any]:
    """
    基于智能分块的剧情框架识别（推荐）

    流程:
    1. LLM 智能分块
    2. 块内剧情识别
    3. 跨块剧情合并

    Args:
        novel_id: 小说ID
        enable_chunking: 是否启用智能分块
        max_chunks: 最大分块数量

    Returns:
        Dict: 剧情框架列表
    """
    logger = get_run_logger()

    if not enable_chunking:
        logger.info("智能分块未启用，使用原有逻辑")
        return identify_story_frameworks_from_summaries(novel_id)

    logger.info(f"开始基于智能分块的剧情识别: novel_id={novel_id}")

    # 步骤1: 智能分块
    logger.info("步骤1: 执行智能分块")
    chunking_result = intelligent_chunking_task(
        novel_id=novel_id,
        max_chunks=max_chunks
    )

    chunks = chunking_result["chunks"]
    logger.info(
        f"智能分块完成: {len(chunks)} 个块, "
        f"策略: {chunking_result['chunking_strategy']}"
    )

    # 步骤2: 块内剧情识别
    logger.info("步骤2: 块内剧情识别")
    chunk_frameworks = []

    # 优化: 一次性获取所有章节,避免循环中重复查询数据库
    from services.material.chapters_service import ChaptersService
    with get_db_session() as db:
        all_chapters = ChaptersService().list_by_novel_ordered(db, novel_id)
        chapter_map = {ch.chapter_number: ch.id for ch in all_chapters}

    for chunk in chunks:
        logger.info(
            f"处理块 {chunk['chunk_id']}: {chunk['title']} "
            f"(第{chunk['start_chapter']}-{chunk['end_chapter']}章)"
        )

        # 优化: 使用预加载的映射获取章节ID,避免重复数据库查询
        chunk_chapter_ids = [
            chapter_map[num]
            for num in range(chunk['start_chapter'], chunk['end_chapter'] + 1)
            if num in chapter_map
        ]

        # 识别块内剧情
        chunk_result = identify_story_frameworks_from_summaries(
            novel_id=novel_id,
            chapter_ids=chunk_chapter_ids
        )

        # 为每个剧情添加块信息
        for framework in chunk_result["story_frameworks"]:
            framework["source_chunk_id"] = chunk["chunk_id"]
            framework["source_chunk_title"] = chunk["title"]

        chunk_frameworks.extend(chunk_result["story_frameworks"])

        logger.info(f"块 {chunk['chunk_id']} 识别出 {len(chunk_result['story_frameworks'])} 个剧情")

    # 步骤3: 跨块剧情合并
    logger.info("步骤3: 跨块剧情合并")
    merged_frameworks = _merge_cross_chunk_stories(chunk_frameworks, chunks, novel_id=novel_id)

    logger.info(
        f"剧情识别完成: 块内剧情 {len(chunk_frameworks)} 个, "
        f"合并后 {len(merged_frameworks)} 个"
    )

    return {
        "story_frameworks": merged_frameworks,
        "novel_id": novel_id,
        "chunking_info": {
            "chunk_count": len(chunks),
            "chunking_strategy": chunking_result["chunking_strategy"],
            "chunks": chunks
        }
    }


@api_task(name="identify_story_frameworks_from_summaries", retries=3)
def identify_story_frameworks_from_summaries(
    novel_id: int,
    chapter_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    从章节摘要中识别剧情框架及其章节范围

    Args:
        novel_id: 小说ID
        chapter_ids: 章节ID列表（可选）

    Returns:
        Dict: 剧情框架列表
    """
    logger = get_run_logger()

    logger.info(f"开始从章节摘要识别剧情框架: novel_id={novel_id}")

    # 获取章节摘要 - 已修复: 在会话内立即转换为字典,避免 DetachedInstanceError
    from services.material.chapters_service import ChaptersService
    with get_db_session() as db:
        chapters = ChaptersService().list_by_novel_ordered(db, novel_id, chapter_ids)
        chapter_summaries = [
            {
                "id": c.id,
                "chapter_number": c.chapter_number,
                "title": c.title,
                "summary": c.summary or "",
            }
            for c in chapters
        ]

    if not chapter_summaries:
        logger.warning("没有找到章节摘要")
        return {"story_frameworks": [], "novel_id": novel_id}

    logger.info(f"获取到 {len(chapter_summaries)} 个章节摘要")

    # 构建系统提示词（传递章节数以动态计算期望剧情数）
    from prompts import create_story_framework_identification_prompt
    system_prompt = create_story_framework_identification_prompt(num_chapters=len(chapter_summaries))

    # 构建用户消息
    summaries_json = json.dumps(chapter_summaries, ensure_ascii=False, indent=2)

    user_message = f"""
小说ID: {novel_id}
章节数量: {len(chapter_summaries)}

章节摘要:
{summaries_json}

请分析这些章节摘要，识别出贯穿多个章节的剧情，并标注每个剧情的章节范围。
"""

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_novel_id=novel_id,
    )

    # 提取 JSON
    client = get_deepseek_client()
    data = client.extract_json_from_response(response)

    story_frameworks = data.get("story_frameworks", [])

    logger.info(f"识别出 {len(story_frameworks)} 个剧情框架")

    # 验证章节覆盖率（降低到85%）
    coverage = _validate_coverage(story_frameworks, chapter_summaries)
    logger.info(f"剧情框架章节覆盖率: {coverage:.1%}")

    if coverage < 0.85:
        logger.warning(
            f"剧情框架覆盖率不足 ({coverage:.1%} < 85%), "
            f"可能存在未归类的章节,将在后续孤儿情节点处理中补充"
        )

    return {
        "story_frameworks": story_frameworks,
        "novel_id": novel_id,
        "coverage": coverage,
    }


@api_task(name="aggregate_plots_for_story_framework", retries=3)
def aggregate_plots_for_story_framework(
    story_framework: dict[str, Any],
    novel_id: int,
) -> dict[str, Any]:
    """
    根据剧情框架的章节范围，聚合对应的情节点

    Args:
        story_framework: 剧情框架（包含章节范围）
        novel_id: 小说ID

    Returns:
        Dict: 完整剧情数据
    """
    logger = get_run_logger()

    chapter_ids = story_framework.get("chapter_ids", [])

    logger.info(
        f"聚合剧情情节点: {story_framework.get('title')}, "
        f"章节范围: {len(chapter_ids)} 个章节"
    )

    # 获取对应章节的情节点 - 已修复: 在会话内立即转换为字典,避免 DetachedInstanceError
    from services.material.plots_service import PlotsService
    with get_db_session() as db:
        plots = PlotsService().list_by_chapter_ids(db, chapter_ids)
        plot_list = [
            {
                "id": p.id,
                "chapter_id": p.chapter_id,
                "index": p.index,
                "plot_type": p.plot_type,
                "description": p.description,
                "characters": p.characters or [],
            }
            for p in plots
        ]

    logger.info(f"获取到 {len(plot_list)} 个情节点")

    # 构建系统提示词
    from prompts import create_plot_aggregation_prompt
    system_prompt = create_plot_aggregation_prompt()

    # 构建用户消息
    framework_json = json.dumps(story_framework, ensure_ascii=False, indent=2)
    plots_json = json.dumps(plot_list, ensure_ascii=False, indent=2)

    user_message = f"""
剧情框架:
{framework_json}

对应章节的情节点:
{plots_json}

请根据剧情框架，从这些情节点中筛选出真正属于该剧情的情节点，并完善剧情的详细信息。
"""

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_novel_id=novel_id,
    )

    # 提取 JSON
    client = get_deepseek_client()
    data = client.extract_json_from_response(response)

    story_data = data.get("story", {})

    # 展开 ID 区间为完整的 ID 列表
    plot_id_ranges = story_data.get("plot_id_ranges", [])
    plot_ids = _expand_id_ranges(plot_id_ranges)

    # 验证 ID 的有效性
    valid_ids = {p["id"] for p in plot_list}
    plot_ids = [pid for pid in plot_ids if pid in valid_ids]

    # 将展开后的 plot_ids 添加到 story_data
    story_data["plot_ids"] = plot_ids

    logger.info(
        f"剧情聚合完成: {story_data.get('title')}, "
        f"ID区间: {len(plot_id_ranges)} 个, 展开后: {len(plot_ids)} 个情节点"
    )

    return {
        "story": story_data,
        "novel_id": novel_id,
    }


@api_task(name="extract_storylines_from_stories", retries=3)
def extract_storylines_from_stories(
    stories: list[dict[str, Any]],
    novel_id: int,
) -> dict[str, Any]:
    """
    从完整剧情中提炼剧情线

    Args:
        stories: 完整剧情列表
        novel_id: 小说ID

    Returns:
        Dict: 剧情线列表
    """
    logger = get_run_logger()

    logger.info(f"开始提炼剧情线: {len(stories)} 个剧情")

    # 支持短篇小说：即使只有1个剧情也可以提炼剧情线
    if len(stories) < 1:
        logger.warning("没有剧情，无法提炼剧情线")
        return {
            "storylines": [],
            "novel_id": novel_id,
        }

    # 构建系统提示词
    from prompts import create_storyline_extraction_prompt
    system_prompt = create_storyline_extraction_prompt()

    # 构建用户消息
    stories_context = json.dumps(stories, ensure_ascii=False, indent=2)

    user_message = f"""
小说ID: {novel_id}
剧情数量: {len(stories)}

剧情信息:
{stories_context}

请从这些剧情中识别和提炼剧情线。
"""

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_novel_id=novel_id,
    )

    # 提取 JSON
    client = get_deepseek_client()
    data = client.extract_json_from_response(response)

    storylines = data.get("storylines", [])

    logger.info(f"提炼出 {len(storylines)} 条剧情线")

    return {
        "storylines": storylines,
        "novel_id": novel_id,
    }


@database_task(name="save_stories_with_plots", retries=3)
def save_stories_with_plots(
    stories: list[dict[str, Any]],
    novel_id: int,
) -> dict[str, Any]:
    """
    保存剧情及其情节点关联到数据库

    Args:
        stories: 剧情列表
        novel_id: 小说ID

    Returns:
        Dict: 保存结果,包含 story_id_map 用于精确回填
    """
    logger = get_run_logger()

    logger.info(f"开始保存 {len(stories)} 个剧情")

    story_ids: list[int] = []
    story_id_map: dict[str, int] = {}  # 标题到ID的映射

    from services.material.stories_service import StoriesService
    with get_db_session() as db:
        stories_svc = StoriesService()
        for idx, story_data in enumerate(stories):
            title = story_data.get("title")
            if not title:
                continue

            story_id = stories_svc.upsert_story(db, novel_id, story_data)
            plot_ids = story_data.get("plot_ids", [])
            stories_svc.attach_plots_to_story(db, story_id, plot_ids)
            story_ids.append(story_id)

            # 修复: 使用"标题+索引"作为key,避免重名冲突
            # 如果标题唯一则直接用标题,如果重名则加上索引后缀
            map_key = title
            if title in story_id_map:
                # 发现重名,使用索引区分
                map_key = f"{title}#{idx}"
                logger.warning(f"检测到重名剧情: '{title}', 使用索引区分: {map_key}")
            story_id_map[map_key] = story_id

        db.commit()

    logger.info(f"剧情保存完成: {len(story_ids)} 个")

    return {
        "saved_count": len(story_ids),
        "created_count": len(story_ids),
        "updated_count": 0,
        "story_ids": story_ids,
        "story_id_map": story_id_map,  # 修复: 返回映射关系
    }


@database_task(name="save_storylines_with_stories", retries=3)
def save_storylines_with_stories(
    storylines: list[dict[str, Any]],
    novel_id: int,
) -> dict[str, Any]:
    """
    保存剧情线及其剧情关联到数据库

    Args:
        storylines: 剧情线列表
        novel_id: 小说ID

    Returns:
        Dict: 保存结果
    """
    logger = get_run_logger()

    logger.info(f"开始保存 {len(storylines)} 条剧情线")

    storyline_ids: list[int] = []

    from services.material.stories_service import StoriesService
    with get_db_session() as db:
        stories_svc = StoriesService()
        for storyline_data in storylines:
            title = storyline_data.get("title")
            if not title:
                continue

            storyline_id = stories_svc.create_storyline(db, novel_id, storyline_data)
            stories_svc.attach_stories_to_storyline(
                db, storyline_id, storyline_data.get("story_ids", [])
            )
            storyline_ids.append(storyline_id)

        db.commit()

    logger.info(f"剧情线保存完成: {len(storyline_ids)} 条")

    return {
        "saved_count": len(storyline_ids),
        "created_count": len(storyline_ids),
        "updated_count": 0,
        "storyline_ids": storyline_ids,
    }


@api_task(name="summary_based_story_aggregation", retries=2)
def summary_based_story_aggregation(
    novel_id: int,
    chapter_ids: list[int] | None = None,
) -> dict[str, Any]:
    """
    基于章节摘要的剧情聚合主流程

    流程:
    1. 从章节摘要识别剧情框架
    2. 根据章节范围聚合情节点
    3. 保存剧情到数据库
    4. 从剧情中提炼剧情线
    5. 保存剧情线到数据库

    Args:
        novel_id: 小说ID
        chapter_ids: 章节ID列表（可选）

    Returns:
        Dict: 聚合结果
    """
    logger = get_run_logger()

    logger.info(f"开始基于章节摘要的剧情聚合: novel_id={novel_id}")

    # 第一步：识别剧情框架
    framework_result = identify_story_frameworks_from_summaries(
        novel_id=novel_id,
        chapter_ids=chapter_ids,
    )

    story_frameworks = framework_result["story_frameworks"]
    logger.info(f"识别出 {len(story_frameworks)} 个剧情框架")

    if not story_frameworks:
        logger.warning("未识别出任何剧情框架")
        return {
            "novel_id": novel_id,
            "stories_count": 0,
            "storylines_count": 0,
        }

    # 第二步：聚合情节点
    stories = []
    for framework in story_frameworks:
        try:
            result = aggregate_plots_for_story_framework(
                story_framework=framework,
                novel_id=novel_id,
            )
            stories.append(result["story"])
        except Exception as e:
            logger.error(f"聚合剧情失败: {framework.get('title')}, error={e}")
            continue

    logger.info(f"成功聚合 {len(stories)} 个剧情")

    if not stories:
        logger.warning("未成功聚合任何剧情")
        return {
            "novel_id": novel_id,
            "stories_count": 0,
            "storylines_count": 0,
        }

    # 第三步：保存剧情
    save_result = save_stories_with_plots(
        stories=stories,
        novel_id=novel_id,
    )

    # 第四步：提炼剧情线
    storyline_result = extract_storylines_from_stories(
        stories=stories,
        novel_id=novel_id,
    )

    storylines = storyline_result["storylines"]
    logger.info(f"提炼出 {len(storylines)} 条剧情线")

    # 第五步：保存剧情线
    storyline_save_result = save_storylines_with_stories(
        storylines=storylines,
        novel_id=novel_id,
    )

    logger.info("基于章节摘要的剧情聚合完成")

    return {
        "novel_id": novel_id,
        "frameworks_count": len(story_frameworks),
        "stories_count": save_result["created_count"],
        "storylines_count": storyline_save_result["created_count"],
        "story_ids": save_result["story_ids"],
        "storyline_ids": storyline_save_result["storyline_ids"],
    }


# ==================== 辅助函数 ====================

def _get_chunk_chapter_ids(_novel_id: int, chunk: dict[str, Any]) -> list[int]:
    """获取块内的章节ID列表"""
    from services.material.chapters_service import ChaptersService
    with get_db_session() as db:
        return ChaptersService().list_ids_by_number_range(
            db,
            _novel_id,
            chunk["start_chapter"],
            chunk["end_chapter"],
        )


@api_task(name="merge_cross_chunk_stories", retries=2)
def _merge_cross_chunk_stories(
    chunk_frameworks: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    novel_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    跨块剧情合并：只处理块边界被割裂的剧情

    设计理念：
    - 跨块合并的唯一目的是修复被分块割裂的剧情
    - 不做主题聚合，不做相似剧情合并
    - 使用LLM判断，而非工程规则
    """
    logger = get_run_logger()

    if len(chunk_frameworks) <= 1:
        return chunk_frameworks

    logger.info(f"开始跨块剧情合并: {len(chunk_frameworks)} 个剧情")

    try:
        # 第一步：找出所有跨块边界的剧情对
        boundary_pairs = _find_boundary_story_pairs(chunk_frameworks, chunks)
        logger.info(f"发现 {len(boundary_pairs)} 对跨块边界的剧情候选")

        if not boundary_pairs:
            logger.info("没有跨块边界的剧情对，无需合并")
            return chunk_frameworks

        # 第二步：让LLM逐对判断
        merge_decisions = []
        for story1, story2 in boundary_pairs:
            decision = _llm_judge_merge(story1, story2, novel_id=novel_id)
            merge_decisions.append({
                "story1": story1,
                "story2": story2,
                "should_merge": decision["should_merge"],
                "reason": decision["reason"]
            })

            logger.info(
                f"{'✓ 合并' if decision['should_merge'] else '✗ 保持独立'}: "
                f"'{story1.get('title')}' + '{story2.get('title')}' - {decision['reason']}"
            )

        # 第三步：执行合并
        merged_stories = _execute_merge_decisions(chunk_frameworks, merge_decisions)

        logger.info(f"跨块合并完成: {len(chunk_frameworks)} 个剧情 → {len(merged_stories)} 个剧情")
        return merged_stories

    except Exception as e:
        logger.error(f"LLM跨块合并失败，保持原样: {e}")
        return chunk_frameworks


def _find_boundary_story_pairs(
    chunk_frameworks: list[dict[str, Any]],
    _chunks: list[dict[str, Any]]
) -> list[tuple]:
    """
    找出所有跨块边界的剧情对

    只考虑：
    1. 来自相邻块的剧情
    2. 章节范围接近块边界（±5章以内）

    Args:
        chunk_frameworks: 剧情框架列表
        chunks: 块列表

    Returns:
        剧情对列表 [(story1, story2), ...]
    """
    # 按块分组剧情
    stories_by_chunk = {}
    for framework in chunk_frameworks:
        chunk_id = framework.get("source_chunk_id")
        if chunk_id is None:
            # 如果没有source_chunk_id，跳过（不参与跨块合并）
            continue
        if chunk_id not in stories_by_chunk:
            stories_by_chunk[chunk_id] = []
        stories_by_chunk[chunk_id].append(framework)

    pairs = []
    MAX_BOUNDARY_DISTANCE = 5  # 块边界附近的范围（±5章）
    MAX_CHAPTER_GAP = 5  # 候选剧情对的最大章节间隔

    # 遍历相邻的块
    sorted_chunk_ids = sorted(stories_by_chunk.keys())
    for i in range(len(sorted_chunk_ids) - 1):
        chunk1_id = sorted_chunk_ids[i]
        chunk2_id = sorted_chunk_ids[i + 1]

        chunk1_stories = stories_by_chunk.get(chunk1_id, [])
        chunk2_stories = stories_by_chunk.get(chunk2_id, [])

        if not chunk1_stories or not chunk2_stories:
            continue

        # 获取块边界
        chunk1_end = max(
            max(s.get("chapter_ids", [0])) if s.get("chapter_ids") else 0
            for s in chunk1_stories
        )
        chunk2_start = min(
            min(s.get("chapter_ids", [999999])) if s.get("chapter_ids") else 999999
            for s in chunk2_stories
        )

        # 找出接近边界的剧情
        for s1 in chunk1_stories:
            chapter_ids_1 = s1.get("chapter_ids", [])
            if not chapter_ids_1:
                continue

            s1_end = max(chapter_ids_1)

            # s1必须延伸到块1末尾附近
            if s1_end < chunk1_end - MAX_BOUNDARY_DISTANCE:
                continue

            for s2 in chunk2_stories:
                chapter_ids_2 = s2.get("chapter_ids", [])
                if not chapter_ids_2:
                    continue

                s2_start = min(chapter_ids_2)

                # s2必须从块2开头附近开始
                if s2_start > chunk2_start + MAX_BOUNDARY_DISTANCE:
                    continue

                # 检查章节间隔
                gap = s2_start - s1_end
                if gap <= MAX_CHAPTER_GAP:
                    pairs.append((s1, s2))

    return pairs


def _llm_judge_merge(
    story1: dict[str, Any],
    story2: dict[str, Any],
    novel_id: int | None = None,
) -> dict[str, Any]:
    """
    使用LLM判断两个剧情是否应该合并

    Args:
        story1: 第一个剧情
        story2: 第二个剧情

    Returns:
        dict: {"should_merge": bool, "reason": str}
    """
    from prompts import (
        create_cross_chunk_merge_judgment_prompt,
        format_cross_chunk_merge_user_message,
    )

    # 获取章节范围
    chapter_ids_1 = story1.get("chapter_ids", [])
    chapter_ids_2 = story2.get("chapter_ids", [])

    range_1 = f"{min(chapter_ids_1)}-{max(chapter_ids_1)}章" if chapter_ids_1 else "未知"
    range_2 = f"{min(chapter_ids_2)}-{max(chapter_ids_2)}章" if chapter_ids_2 else "未知"

    # 构建系统提示词和用户消息
    system_prompt = create_cross_chunk_merge_judgment_prompt()
    user_message = format_cross_chunk_merge_user_message(
        story1_title=story1.get('title', '未知'),
        story1_range=range_1,
        story1_objective=story1.get('core_objective', '未知'),
        story1_conflict=story1.get('core_conflict', '未知'),
        story1_synopsis=story1.get('synopsis', story1.get('description', '未知')),
        story2_title=story2.get('title', '未知'),
        story2_range=range_2,
        story2_objective=story2.get('core_objective', '未知'),
        story2_conflict=story2.get('core_conflict', '未知'),
        story2_synopsis=story2.get('synopsis', story2.get('description', '未知')),
    )

    try:
        response = call_deepseek_api(
            messages=[{"role": "user", "content": user_message}],
            system_prompt=system_prompt,
            temperature=0.1,  # 低温度=更保守
            usage_novel_id=novel_id,
        )

        # 解析响应：call_deepseek_api 返回 LLMResponse 对象，必须用客户端方法提取 JSON。
        # （此前这里把 LLMResponse 当字符串做 re.search，必然抛 TypeError 并被下方 except
        # 吞掉，导致合并判断永远返回 should_merge=False。改为与本文件其它调用点一致的写法。）
        client = get_deepseek_client()
        result = client.extract_json_from_response(response)

        # 验证返回格式
        if "should_merge" not in result or "reason" not in result:
            return {"should_merge": False, "reason": "LLM返回格式错误"}

        return result

    except Exception as e:
        # 失败时保守处理：不合并
        return {"should_merge": False, "reason": f"LLM调用失败: {str(e)}"}


def _execute_merge_decisions(
    chunk_frameworks: list[dict[str, Any]],
    merge_decisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    根据LLM的判断执行合并

    Args:
        chunk_frameworks: 原始剧情列表
        merge_decisions: LLM的合并决策列表

    Returns:
        list: 合并后的剧情列表
    """
    # 构建合并映射
    merge_map = {}  # story_id -> merged_story_id

    for decision in merge_decisions:
        if decision["should_merge"]:
            s1_id = id(decision["story1"])
            s2_id = id(decision["story2"])

            # 合并到s1
            merge_map[s2_id] = s1_id

    # 执行合并
    merged_stories = []
    processed = set()

    for story in chunk_frameworks:
        story_id = id(story)

        if story_id in processed:
            continue

        # 检查是否需要合并
        if story_id in merge_map:
            # 这个剧情被合并到其他剧情了，跳过
            processed.add(story_id)
            continue

        # 检查是否有其他剧情要合并到这个剧情
        to_merge = [story]
        for decision in merge_decisions:
            if decision["should_merge"] and id(decision["story1"]) == story_id:
                to_merge.append(decision["story2"])
                processed.add(id(decision["story2"]))

        # 合并
        if len(to_merge) > 1:
            merged_story = _merge_story_group(to_merge)
            merged_stories.append(merged_story)
        else:
            merged_stories.append(story)

        processed.add(story_id)

    return merged_stories


def _merge_story_group(stories: list[dict[str, Any]]) -> dict[str, Any]:
    """
    合并一组剧情

    Args:
        stories: 要合并的剧情列表

    Returns:
        dict: 合并后的剧情
    """
    if len(stories) == 1:
        return stories[0]

    # 合并章节ID
    all_chapter_ids = set()
    for s in stories:
        all_chapter_ids.update(s.get("chapter_ids", []))

    # 合并主题
    all_themes = set()
    for s in stories:
        all_themes.update(s.get("themes", []))

    # 使用第一个剧情的标题和描述
    merged = {
        "title": stories[0].get("title", ""),
        "synopsis": stories[0].get("synopsis", stories[0].get("description", "")),
        "description": stories[0].get("description", ""),
        "core_objective": stories[0].get("core_objective", ""),
        "core_conflict": stories[0].get("core_conflict", ""),
        "story_type": stories[0].get("story_type", ""),
        "chapter_ids": sorted(all_chapter_ids),
        "themes": list(all_themes),
        "cross_chunk": True,
        "merged_from": [s.get("title", "") for s in stories],
    }

    return merged


def _validate_coverage(story_frameworks: list[dict], chapter_summaries: list[dict]) -> float:
    """
    验证剧情框架的章节覆盖率

    Args:
        story_frameworks: 剧情框架列表
        chapter_summaries: 章节摘要列表（包含实际的章节ID）

    Returns:
        float: 覆盖率 (0.0 ~ 1.0)
    """
    if not chapter_summaries:
        return 0.0

    # 获取所有实际存在的章节ID
    all_chapter_ids = {ch["id"] for ch in chapter_summaries}

    # 获取被剧情覆盖的章节ID
    covered_chapter_ids = set()
    for framework in story_frameworks:
        covered_chapter_ids.update(framework.get("chapter_ids", []))

    # 计算覆盖率：被覆盖的章节数 / 总章节数
    coverage = len(covered_chapter_ids & all_chapter_ids) / len(all_chapter_ids)
    return coverage


def _expand_id_ranges(ranges: list[list[int]]) -> list[int]:
    """
    将 ID 区间列表展开为完整的 ID 列表

    Args:
        ranges: ID 区间列表，格式为 [[start1, end1], [start2, end2], ...]

    Returns:
        List[int]: 展开后的 ID 列表（去重并排序）

    Examples:
        >>> _expand_id_ranges([[554, 565], [646, 652]])
        [554, 555, 556, ..., 565, 646, 647, ..., 652]
    """
    plot_ids = []

    for range_item in ranges:
        if not isinstance(range_item, list) or len(range_item) != 2:
            continue

        # 修复: 自动修正顺序,避免 start > end 导致空列表
        start, end = min(range_item), max(range_item)

        if not isinstance(start, int) or not isinstance(end, int):
            continue

        # 展开区间（包含起始和结束）
        plot_ids.extend(range(start, end + 1))

    # 去重并排序
    return sorted(set(plot_ids))
