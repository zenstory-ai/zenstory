"""
元信息提取任务(合并金手指和世界观)

核心任务:
- extract_novel_meta_task: 一次性提取金手指和世界观
- build_meta_entities_task: 入库金手指和世界观
"""

from typing import Any

from prefect import get_run_logger
from sqlmodel import col, select

from flows.database_session import get_db_session
from flows.utils import (
    api_task,
    call_deepseek_api,
    database_task,
    get_deepseek_client,
)
from flows.utils.validators import validate_meta_response
from models.material_models import Chapter
from prompts import create_meta_extraction_prompt

META_EXTRACTION_WINDOW = 20


@api_task(name="extract_novel_meta", retries=5)
def extract_novel_meta_task(
    novel_id: int,
) -> dict[str, Any]:
    """
    一次性提取金手指和世界观信息(节省 LLM 调用)

    基于小说前N章或全文提取

    Args:
        novel_id: 小说ID

    Returns:
        Dict: 金手指和世界观提取结果
    """
    logger = get_run_logger()

    logger.info(f"开始提取小说 {novel_id} 的元信息")

    # 获取小说内容（前N章，默认20）
    with get_db_session() as db:
        window = META_EXTRACTION_WINDOW
        chapters = db.exec(
            select(Chapter.chapter_number, Chapter.title, Chapter.original_content)
            .where(Chapter.novel_id == novel_id)
            .order_by(col(Chapter.chapter_number))
            .limit(window)
        ).all()

        if not chapters:
            raise ValueError(f"小说 {novel_id} 没有章节")

        novel_content = "\n\n".join([
            f"第{ch.chapter_number}章 {ch.title}\n{ch.original_content}"
            for ch in chapters
        ])
        chapter_range = f"前{len(chapters)}章"

    # 构建系统提示词
    system_prompt = create_meta_extraction_prompt()

    # 调用 LLM
    user_message = f"""
章节范围: {chapter_range}

小说内容:
{novel_content}

请提取金手指和世界观信息。
"""

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_novel_id=novel_id,
    )

    # 提取 JSON
    client = get_deepseek_client()
    data = client.extract_json_from_response(response)

    # 验证数据
    validated_data = validate_meta_response(data)

    golden_fingers = validated_data.get("golden_fingers", [])
    world_view = validated_data.get("world_view")

    logger.info(f"成功提取: 金手指数量={len(golden_fingers)}, 世界观={'有' if world_view else '无'}")

    return {
        "golden_fingers": golden_fingers,
        "world_view": world_view,
        "novel_id": novel_id,
    }


@database_task(name="build_meta_entities", retries=3)
def build_meta_entities_task(
    meta_data: dict[str, Any],
    novel_id: int,
    first_chapter_id: int | None = None,
) -> dict[str, Any]:
    """
    入库金手指和世界观

    Args:
        meta_data: 元信息提取结果
        novel_id: 小说ID
        first_chapter_id: 首次出现章节ID

    Returns:
        Dict: 入库结果
    """
    logger = get_run_logger()

    golden_fingers = meta_data.get("golden_fingers", [])
    world_view = meta_data.get("world_view")

    gf_actions = []
    wv_action = None

    from services.material.golden_finger_service import GoldenFingerService
    from services.material.world_view_service import WorldViewService
    with get_db_session() as db:
        # 1. 入库金手指（支持多个）
        for golden_finger in golden_fingers:
            if not isinstance(golden_finger, dict):
                continue

            gf_name = golden_finger.get("name")
            if not gf_name:
                # 生成占位名称，避免因为缺少 name 而完全跳过入库
                gf_type = golden_finger.get("type")
                gf_desc = golden_finger.get("description")
                if gf_type:
                    placeholder = f"{gf_type}-金手指"
                elif gf_desc:
                    placeholder = gf_desc[:12]
                else:
                    placeholder = "未命名金手指"
                logger.warning(f"金手指缺少 name 字段，使用占位名入库: {placeholder}")
                gf_name = placeholder

            action = GoldenFingerService().upsert(db, novel_id, {
                "name": gf_name,
                "type": golden_finger.get("type"),
                "description": golden_finger.get("description"),
                "first_appearance_chapter_id": first_chapter_id,
            })
            gf_actions.append(action)
            logger.debug(("创建" if action == "created" else "更新") + f"金手指: {gf_name}")

        # 2. 入库世界观
        if world_view:
            action = WorldViewService().upsert(db, novel_id, world_view)
            wv_action = action
            logger.debug(("创建" if action == "created" else "更新") + "世界观")

        db.commit()

    logger.info(f"元信息入库完成: 金手指 {len(gf_actions)}个 {gf_actions}, 世界观 {wv_action}")

    return {
        "golden_finger_actions": gf_actions,
        "world_view_action": wv_action,
    }
