"""
人物关系任务

核心任务:
- extract_character_relationships_task: 从文本提取人物关系
- build_character_relationships_task: 构建人物关系实体
"""

from typing import Any

from prefect import get_run_logger

from flows.database_session import get_prefect_db_session
from flows.utils.clients import LLMResponse, call_deepseek_api, get_deepseek_client
from flows.utils.decorators import api_task, database_task
from flows.utils.validators import validate_relationships_response
from prompts import create_relationship_extraction_prompt

DEFAULT_RELATIONSHIP_BATCH_SIZE = 5


@api_task(name="extract_character_relationships", retries=5)
def extract_character_relationships_task(
    novel_id: int,
    chapter_ids: list[int] = None,
    batch_size: int = None,
) -> dict[str, Any]:
    """
    从小说文本中提取人物关系（分批处理，贯穿全书）

    Args:
        novel_id: 小说ID
        chapter_ids: 章节ID列表(可选,默认全部章节)
        batch_size: 每批处理的章节数（默认5章）

    Returns:
        Dict: 人物关系提取结果
    """
    logger = get_run_logger()

    if batch_size is None:
        batch_size = DEFAULT_RELATIONSHIP_BATCH_SIZE

    logger.info(
        f"[关系提取] 开始: novel_id={novel_id}, batch_size={batch_size}, "
        f"chapter_ids_count={len(chapter_ids) if chapter_ids else 'all'}"
    )

    # 1. 获取所有章节（会话内转为字典，避免 DetachedInstanceError）
    with get_prefect_db_session() as session:
        from services.material.chapters_service import ChaptersService
        chapters_orm = ChaptersService().list_by_novel_ordered(session, novel_id, chapter_ids)
        chapters = [
            {"id": ch.id, "chapter_number": ch.chapter_number, "title": ch.title}
            for ch in chapters_orm
        ]
        total_chapters = len(chapters)
        chapter_number_by_id = {c["id"]: c["chapter_number"] for c in chapters}

        logger.info(
            f"[关系提取] 章节信息: novel_id={novel_id}, total_chapters={total_chapters}"
        )

    # 2. 分批提取人物关系
    all_relationships = []
    batch_count = (total_chapters + batch_size - 1) // batch_size

    for batch_idx in range(batch_count):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, total_chapters)
        batch_chapters = chapters[start_idx:end_idx]

        logger.info(
            f"[关系提取] 处理批次 {batch_idx + 1}/{batch_count}: "
            f"chapters={batch_chapters[0]['chapter_number']}-{batch_chapters[-1]['chapter_number']}"
        )

        # 获取该批次的情节点
        with get_prefect_db_session() as session:
            batch_chapter_ids = [ch["id"] for ch in batch_chapters]
            from services.material.plots_service import PlotsService
            plots = PlotsService().list_by_chapter_ids(session, batch_chapter_ids)

            # 构建上下文（避免访问懒加载的 plot.chapter）
            context_parts = []
            for plot in plots:
                # 仅使用我们在会话内缓存的章节编号映射
                ch_num = getattr(plot, "chapter_number", None) or chapter_number_by_id.get(plot.chapter_id)
                context_parts.append(
                    f"[章节{ch_num}] {plot.description}"
                )

            context = "\n".join(context_parts)

        if not context.strip():
            logger.warning(
                f"[关系提取] 批次 {batch_idx + 1} 无情节点，跳过"
            )
            continue

        # 3. 调用 LLM 提取关系
        system_prompt = create_relationship_extraction_prompt()

        messages = [
            {
                "role": "user",
                "content": f"""
请从以下情节点中提取人物关系（第{batch_chapters[0]['chapter_number']}-{batch_chapters[-1]['chapter_number']}章）:

{context}

请识别所有重要的人物关系，包括新出现的关系和关系的变化。
""",
            }
        ]

        try:
            logger.info(f"[关系提取] 调用 LLM: batch={batch_idx + 1}/{batch_count}")

            response: LLMResponse = call_deepseek_api(
                messages, system_prompt, usage_novel_id=novel_id
            )

            # 4. 提取和验证 JSON
            client = get_deepseek_client()
            logger.debug(f"[关系提取] 解析响应: batch={batch_idx + 1}")

            data = client.extract_json_from_response(response)
            validated = validate_relationships_response(data)

            batch_relationships = validated.get("relationships", [])
            all_relationships.extend(batch_relationships)

            logger.info(
                f"[关系提取] 批次完成: batch={batch_idx + 1}/{batch_count}, "
                f"relationships_count={len(batch_relationships)}"
            )

        except Exception as e:
            logger.error(
                f"[关系提取] 批次失败: batch={batch_idx + 1}/{batch_count}, error={str(e)}"
            )
            # 继续处理下一批次
            continue

    # 5. 去重合并（相同人物对的关系，保留最新的）
    unique_relationships = {}
    for rel in all_relationships:
        key = (rel.get("character_a"), rel.get("character_b"))
        # 保留最后出现的关系（代表最新状态）
        unique_relationships[key] = rel

    final_relationships = list(unique_relationships.values())

    logger.info(
        f"[关系提取] 完成: novel_id={novel_id}, total_relationships={len(all_relationships)}, "
        f"unique_relationships={len(final_relationships)}, batches_processed={batch_count}"
    )

    return {
        "relationships": final_relationships,
        "novel_id": novel_id,
        "total_count": len(final_relationships),
        "batches_processed": batch_count,
    }


@database_task(name="build_character_relationships", retries=3)
def build_character_relationships_task(
    novel_id: int,
    relationships_data: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    构建人物关系实体并入库

    Args:
        novel_id: 小说ID
        relationships_data: 人物关系列表

    Returns:
        Dict: 入库结果统计
    """
    logger = get_run_logger()

    relationships = relationships_data
    created_count = 0
    updated_count = 0

    from services.material.relationships_service import RelationshipsService
    with get_prefect_db_session() as session:
        svc = RelationshipsService()
        created_count, updated_count = svc.upsert_relationships(session, novel_id, relationships)
        session.commit()

    logger.info(
        f"[关系实体构建] 完成: novel_id={novel_id}, created={created_count}, "
        f"updated={updated_count}, total={created_count + updated_count}"
    )

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "saved_count": created_count + updated_count,
    }
