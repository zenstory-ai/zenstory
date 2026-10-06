"""
情节点提取任务

核心任务:
- extract_chapter_plots_task: 从章节中提取情节点（数量基于章节字数动态计算）
- validate_plots_task: 情节点完整性校验
"""

import json
from typing import Any

from prefect import get_run_logger

from flows.database_session import get_db_session
from flows.utils import (
    api_task,
    call_deepseek_api,
    get_deepseek_client,
)
from flows.utils.helpers import calculate_plots_range
from flows.utils.validators import validate_plots_response
from prompts import create_plot_extraction_prompt
from services.material.chapters_service import ChaptersService
from services.material.plots_service import PlotsService


@api_task(name="extract_chapter_plots", retries=5)
def extract_chapter_plots_task(
    chapter_id: int,
) -> dict[str, Any]:
    """
    从章节中提取情节点（数量基于章节字数动态计算）

    规则：150-200字 → 1个情节点
    - 500字章节 → 3-5个情节点
    - 1000字章节 → 5-7个情节点
    - 3000字章节 → 15-20个情节点
    - 5000字章节 → 25-33个情节点

    Args:
        chapter_id: 章节ID

    Returns:
        Dict: 情节点提取结果
            - chapter_id: 章节ID
            - novel_id: 小说ID
            - plots: 情节点列表
    """
    logger = get_run_logger()

    logger.info(f"[情节点提取] 开始处理章节 {chapter_id}")

    # 获取章节内容
    with get_db_session() as db:
        ch_svc = ChaptersService()
        ch = ch_svc.get_by_id(db, chapter_id)
        if not ch:
            raise ValueError(f"章节 {chapter_id} 不存在")

        novel_id = ch.novel_id
        chapter_content = ch.original_content
        chapter_title = ch.title

        logger.info(
            f"[情节点提取] 章节信息: chapter_id={chapter_id}, novel_id={novel_id}, "
            f"title='{chapter_title}', content_length={len(chapter_content)}"
        )

    # 动态计算情节点数量范围（基于章节字数）
    word_count = len(chapter_content)
    min_plots, max_plots = calculate_plots_range(word_count)

    # 计算密度（避免除零）
    density_at_max = word_count / max_plots if max_plots > 0 else 0
    density_at_min = word_count / min_plots if min_plots > 0 else 0

    logger.info(
        f"[情节点提取] 动态计算: chapter_id={chapter_id}, word_count={word_count}, "
        f"plots_range={min_plots}-{max_plots} "
        f"(密度: {density_at_max:.0f}-{density_at_min:.0f}字/个)"
    )

    # 构建系统提示词（注入动态范围）
    system_prompt = create_plot_extraction_prompt(
        min_plots=min_plots,
        max_plots=max_plots,
        word_count=word_count,
    )

    # 调用 LLM
    user_message = f"""
章节标题: {chapter_title}

章节内容:
{chapter_content}

请提取 {min_plots}-{max_plots} 个关键情节点。
"""

    logger.info(
        f"[情节点提取] 调用 LLM: chapter_id={chapter_id}, "
        f"expected_plots={min_plots}-{max_plots}"
    )

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_chapter_id=chapter_id,
    )

    # 提取 JSON
    client = get_deepseek_client()
    logger.debug(f"[情节点提取] 开始解析 LLM 响应: chapter_id={chapter_id}")

    try:
        data = client.extract_json_from_response(response)
        logger.debug(f"[情节点提取] JSON 解析成功: chapter_id={chapter_id}, data_keys={list(data.keys())}")
    except Exception as e:
        logger.error(
            f"[情节点提取] JSON 解析失败: chapter_id={chapter_id}, error={str(e)}, "
            f"response_preview={response.content[:500]}"
        )
        raise

    # 验证数据
    logger.debug(f"[情节点提取] 开始验证数据: chapter_id={chapter_id}")

    try:
        plots = validate_plots_response(data)
        logger.info(f"[情节点提取] 验证成功: chapter_id={chapter_id}, plots_count={len(plots)}")
    except Exception as e:
        logger.error(
            f"[情节点提取] 验证失败: chapter_id={chapter_id}, error={str(e)}, "
            f"data={json.dumps(data, ensure_ascii=False)[:1000]}"
        )
        raise

    logger.info(f"[情节点提取] 完成: chapter_id={chapter_id}, plots_count={len(plots)}")

    return {
        "chapter_id": chapter_id,
        "novel_id": novel_id,
        "plots": plots,
    }


@api_task(name="validate_plots", retries=2)
def validate_plots_task(plots: list[dict[str, Any]]) -> dict[str, Any]:
    """
    校验情节点数据完整性

    Args:
        plots: 情节点列表

    Returns:
        Dict: 校验结果
    """
    logger = get_run_logger()
    errors = []
    warnings = []

    # 注意：不再验证数量，因为数量是基于章节字数动态计算的
    # Prompt 中已经严格要求了数量范围

    # 检查每个情节点
    for i, plot in enumerate(plots):
        # 必填字段（与 prompt 定义保持一致：description, sequence）
        required_fields = ["description", "sequence"]
        for field in required_fields:
            if not plot.get(field):
                errors.append(f"情节点 {i+1}: 缺少必填字段 {field}")

        # 重要性范围检查
        if "importance" in plot:
            importance = plot["importance"]
            if not isinstance(importance, (int, float)) or not (1 <= importance <= 10):
                errors.append(f"情节点 {i+1}: importance 必须在 1-10 之间")

    is_valid = len(errors) == 0

    logger.info(f"校验完成: {'通过' if is_valid else '失败'}, {len(errors)} 个错误, {len(warnings)} 个警告")

    return {
        "valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "plot_count": len(plots),
    }


@api_task(name="save_plots", retries=3)
def save_plots_task(
    novel_id: int,
    chapter_id: int,
    plots: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    保存情节点到数据库

    Args:
        novel_id: 小说ID
        chapter_id: 章节ID
        plots: 情节点列表

    Returns:
        Dict: 保存结果
    """
    logger = get_run_logger()

    logger.info(
        f"[情节点保存] 开始保存: chapter_id={chapter_id}, novel_id={novel_id}, plots_count={len(plots)}"
    )

    with get_db_session() as db:
        # 规范 plots 数据结构供 service 使用
        normalized = []
        for i, plot_data in enumerate(plots):
            normalized.append({
                "chapter_id": chapter_id,
                "index": plot_data.get("sequence", i),
                "description": plot_data.get("description", ""),
                "type": plot_data.get("plot_type", "OTHER"),
                "characters": plot_data.get("characters", []),
            })

        logger.debug(f"[情节点保存] 数据规范化完成: chapter_id={chapter_id}, normalized_count={len(normalized)}")

        svc = PlotsService()
        saved_count = svc.bulk_insert(db, novel_id, normalized)
        db.commit()

    logger.info(
        f"[情节点保存] 完成: chapter_id={chapter_id}, novel_id={novel_id}, saved_count={saved_count}"
    )

    return {
        "saved_count": saved_count,
        "novel_id": novel_id,
        "chapter_id": chapter_id,
    }
