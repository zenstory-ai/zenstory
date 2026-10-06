"""
小说梗概任务

核心任务:
- generate_novel_synopsis_task: 生成小说梗概
- update_novel_synopsis_task: 回写小说梗概到数据库
"""

from typing import Any

from prefect import get_run_logger

from flows.database_session import get_db_session
from flows.utils import (
    api_task,
    call_deepseek_api,
    get_deepseek_client,
)
from prompts import create_novel_synopsis_prompt


@api_task(name="generate_novel_synopsis", retries=3)
def generate_novel_synopsis_task(
    novel_id: int,
    chapter_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    生成小说梗概

    Args:
        novel_id: 小说ID
        chapter_summaries: 章节摘要列表

    Returns:
        Dict: 梗概生成结果
    """
    logger = get_run_logger()

    logger.info(
        f"[小说梗概] 开始生成: novel_id={novel_id}, chapter_summaries_count={len(chapter_summaries)}"
    )

    # 获取小说信息
    from services.material.novels_service import NovelsService
    with get_db_session() as db:
        novel = NovelsService().get_by_id(db, novel_id)
        if not novel:
            raise ValueError(f"小说 {novel_id} 不存在")

        novel_title = novel.title
        novel_author = novel.author or "未知"

    # 构建章节摘要文本
    summaries_text = "\n\n".join([
        f"第 {s['chapter_number']} 章 - {s['chapter_title']}:\n{s['summary']}"
        for s in chapter_summaries
    ])

    # 构建系统提示词
    system_prompt = create_novel_synopsis_prompt()

    # 调用 LLM
    user_message = f"""
小说标题: {novel_title}
作者: {novel_author}
总章节数: {len(chapter_summaries)}

各章节摘要:
{summaries_text}

请基于以上章节摘要,生成小说的整体梗概。
"""

    logger.info(f"[小说梗概] 调用 LLM: novel_id={novel_id}")

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_novel_id=novel_id,
    )

    # 提取 JSON
    client = get_deepseek_client()
    logger.debug(f"[小说梗概] 开始解析 LLM 响应: novel_id={novel_id}")

    try:
        data = client.extract_json_from_response(response)
    except Exception as e:
        logger.error(
            f"[小说梗概] JSON 解析失败: novel_id={novel_id}, error={str(e)}, "
            f"response_preview={response.content[:500]}"
        )
        raise

    # 验证数据
    from flows.utils.validators import validate_novel_synopsis_data

    try:
        validate_novel_synopsis_data(data)
    except Exception as e:
        logger.error(
            f"[小说梗概] 验证失败: novel_id={novel_id}, "
            f"error={str(e)}, data={data}"
        )
        raise

    synopsis_text = data["synopsis"]

    logger.info(
        f"[小说梗概] 完成: novel_id={novel_id}, synopsis_length={len(synopsis_text)}"
    )

    return {
        "novel_id": novel_id,
        "synopsis": synopsis_text,
    }


@api_task(name="update_novel_synopsis", retries=2)
def update_novel_synopsis_task(
    novel_id: int,
    synopsis: str,
) -> dict[str, Any]:
    """
    回写小说梗概到数据库

    Args:
        novel_id: 小说ID
        synopsis: 梗概内容

    Returns:
        Dict: 更新结果
    """
    logger = get_run_logger()

    logger.info(
        f"[小说梗概更新] 开始: novel_id={novel_id}, synopsis_length={len(synopsis)}"
    )

    from services.material.novels_service import NovelsService
    with get_db_session() as db:
        NovelsService().update_synopsis(db, novel_id, synopsis)
        db.commit()

    logger.info(f"[小说梗概更新] 完成: novel_id={novel_id}")

    return {
        "novel_id": novel_id,
        "updated": True,
    }
