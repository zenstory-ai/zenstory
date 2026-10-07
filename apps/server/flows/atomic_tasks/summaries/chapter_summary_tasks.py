"""
章节摘要任务

核心任务:
- generate_chapter_summary_task: 生成章节摘要
- update_chapter_summary_task: 回写章节摘要到数据库
"""

from typing import Any

from prefect import get_run_logger

from flows.database_session import get_db_session
from flows.utils import (
    api_task,
    call_deepseek_api,
    get_deepseek_client,
)
from prompts import create_chapter_summary_prompt
from services.material.chapters_service import ChaptersService


@api_task(name="generate_chapter_summary", retries=3)
def generate_chapter_summary_task(
    chapter_id: int,
) -> dict[str, Any]:
    """
    生成章节摘要

    Args:
        chapter_id: 章节ID

    Returns:
        Dict: 摘要生成结果
    """
    logger = get_run_logger()

    logger.info(f"[章节摘要] 开始处理章节 {chapter_id}")

    # 获取章节内容
    with get_db_session() as db:
        svc = ChaptersService()
        ch_fields = svc.get_chapter_core_fields(db, chapter_id)
        if not ch_fields:
            raise ValueError(f"章节 {chapter_id} 不存在")

        chapter_content = ch_fields["content"]
        chapter_title = ch_fields["title"]
        chapter_number = ch_fields["number"]

        logger.info(
            f"[章节摘要] 章节信息: chapter_id={chapter_id}, "
            f"number={chapter_number}, title='{chapter_title}', "
            f"content_length={len(chapter_content)}"
        )

    # 构建系统提示词
    system_prompt = create_chapter_summary_prompt()

    # 调用 LLM
    user_message = f"""
章节序号: 第 {chapter_number} 章
章节标题: {chapter_title}

章节内容:
{chapter_content}

请生成本章的摘要。
"""

    logger.info(f"[章节摘要] 调用 LLM: chapter_id={chapter_id}")

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_chapter_id=chapter_id,
    )

    # 提取 JSON
    client = get_deepseek_client()
    logger.debug(f"[章节摘要] 开始解析 LLM 响应: chapter_id={chapter_id}")

    try:
        data = client.extract_json_from_response(response)
        logger.debug(f"[章节摘要] JSON 解析成功: chapter_id={chapter_id}")
    except Exception as e:
        logger.error(
            f"[章节摘要] JSON 解析失败: chapter_id={chapter_id}, "
            f"error={str(e)}, response_preview={response.content[:500]}"
        )
        raise

    # 验证数据
    from flows.utils.validators import validate_chapter_summary_data

    try:
        validate_chapter_summary_data(data)
    except Exception as e:
        logger.error(
            f"[章节摘要] 验证失败: chapter_id={chapter_id}, "
            f"error={str(e)}, data={data}"
        )
        raise

    summary_text = data["summary"]

    logger.info(
        f"[章节摘要] 完成: chapter_id={chapter_id}, "
        f"summary_length={len(summary_text)}"
    )

    return {
        "chapter_id": chapter_id,
        "summary": summary_text,
    }


@api_task(name="update_chapter_summary", retries=2)
def update_chapter_summary_task(
    chapter_id: int,
    summary: str,
) -> dict[str, Any]:
    """
    回写章节摘要到数据库

    Args:
        chapter_id: 章节ID
        summary: 摘要内容

    Returns:
        Dict: 更新结果
    """
    logger = get_run_logger()

    logger.info(
        f"[章节摘要更新] 开始: chapter_id={chapter_id}, "
        f"summary_length={len(summary)}"
    )

    with get_db_session() as db:
        svc = ChaptersService()
        exists = svc.get_by_id(db, chapter_id)
        if not exists:
            raise ValueError(f"章节 {chapter_id} 不存在")
        svc.save_summary(db, chapter_id, summary)
        db.commit()

    logger.info(f"[章节摘要更新] 完成: chapter_id={chapter_id}")

    return {
        "chapter_id": chapter_id,
        "updated": True,
    }
