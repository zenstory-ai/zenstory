"""
角色实体任务 V2（两阶段提取）

核心任务:
- extract_character_mentions_task: 阶段1 - 从章节中提取角色提及（轻量级）
- build_character_entities_from_mentions_task: 阶段2 - 从提及记录构建角色实体
"""

import json
from typing import Any

from prefect import get_run_logger

from flows.database_session import get_db_session
from flows.utils import (
    api_task,
    call_deepseek_api,
    database_task,
    get_deepseek_client,
)
from flows.utils.helpers import (
    filter_character_data,
    merge_aliases,
    normalize_alias,
)
from prompts import (
    create_character_consolidation_prompt,
    create_character_mention_prompt,
)


@api_task(name="extract_character_mentions_from_chapter", retries=5)
def extract_character_mentions_task(
    chapter_id: int,
) -> dict[str, Any]:
    """
    阶段1：从章节中提取角色提及（轻量级）

    策略：
    1. 仅提取本章出现的角色和局部信息
    2. 不读取历史描述，不做增量更新
    3. 输出轻量级结构，存入 character_mentions 表

    Args:
        chapter_id: 章节ID

    Returns:
        Dict: 角色提及提取结果
    """
    logger = get_run_logger()

    logger.info(f"[角色提及提取] 开始处理章节 {chapter_id}")

    # 获取章节内容
    from services.material.chapters_service import ChaptersService

    with get_db_session() as db:
        ch_svc = ChaptersService()
        ch = ch_svc.get_by_id(db, chapter_id)
        if not ch:
            raise ValueError(f"章节 {chapter_id} 不存在")

        novel_id = ch.novel_id
        chapter_content = getattr(ch, "content", None) or getattr(ch, "original_content", "")
        chapter_number = ch.chapter_number

        logger.info(
            f"[角色提及提取] 章节信息: chapter_id={chapter_id}, novel_id={novel_id}, "
            f"chapter_number={chapter_number}, content_length={len(chapter_content)}"
        )

    # 构建系统提示词（轻量级）
    system_prompt = create_character_mention_prompt()

    # 调用 LLM
    user_message = f"""
章节范围: 第{chapter_number}章

小说内容:
{chapter_content}

请提取本章出现的所有角色及其在本章的表现。
"""

    logger.info(f"[角色提及提取] 调用 LLM: chapter_id={chapter_id}")

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_chapter_id=chapter_id,
    )

    # 提取 JSON
    client = get_deepseek_client()
    logger.debug(f"[角色提及提取] 开始解析 LLM 响应: chapter_id={chapter_id}")

    try:
        data = client.extract_json_from_response(response)
        logger.debug(f"[角色提及提取] JSON 解析成功: chapter_id={chapter_id}")
    except Exception as e:
        logger.error(
            f"[角色提及提取] JSON 解析失败: chapter_id={chapter_id}, error={str(e)}, "
            f"response_preview={response.content[:500]}"
        )
        raise

    # 过滤无效人物和通用称呼
    characters_data = data.get("characters", [])
    logger.info(f"[角色提及提取] 过滤前人物数量: {len(characters_data)}")
    characters_data = filter_character_data(characters_data, logger=logger)
    logger.info(f"[角色提及提取] 过滤后人物数量: {len(characters_data)}")

    # 存入 character_mentions 表
    from services.material.character_mentions_service import CharacterMentionsService

    with get_db_session() as db:
        mention_svc = CharacterMentionsService()

        for char in characters_data:
            char_name = char.get("name", "").strip()
            if not char_name:
                continue

            # 【关键】规范化角色主名（避免"林雷"和"林 雷"被识别为不同角色）
            char_name = normalize_alias(char_name)
            if not char_name or len(char_name) < 2:
                logger.debug(f"[角色提及提取] 跳过无效角色名: {char.get('name')}")
                continue

            # 规范化别名
            raw_aliases = char.get("aliases", []) or []
            aliases = merge_aliases([], raw_aliases)

            mention_svc.upsert_mention(
                session=db,
                novel_id=novel_id,
                chapter_id=chapter_id,
                character_name=char_name,
                data={
                    "aliases": json.dumps(aliases, ensure_ascii=False) if aliases else None,
                    "chapter_description": char.get("chapter_description"),
                    "importance": char.get("chapter_importance"),
                    "first_line": char.get("first_appearance_line"),
                    "raw_data": json.dumps(char, ensure_ascii=False) if char else None,
                },
            )

        db.commit()

    logger.info(
        f"[角色提及提取] 完成: chapter_id={chapter_id}, characters_count={len(characters_data)}"
    )

    return {
        "chapter_id": chapter_id,
        "novel_id": novel_id,
        "chapter_number": chapter_number,
        "characters_count": len(characters_data),
    }


@api_task(name="build_character_entity_from_mentions", retries=3)
def build_character_entity_task(
    novel_id: int,
    character_name: str,
) -> dict[str, Any]:
    """
    阶段2：从提及记录构建单个角色实体

    策略：
    1. 查询该角色在所有章节的提及记录
    2. 汇总所有章节的局部描述
    3. 调用 LLM 生成完整、连贯的角色描述
    4. 创建或更新角色实体

    Args:
        novel_id: 小说ID
        character_name: 角色主名

    Returns:
        Dict: 角色实体构建结果
    """
    logger = get_run_logger()

    logger.info(f"[角色实体构建] 开始处理角色: {character_name}")

    # 查询该角色的所有提及记录
    from services.material.chapters_service import ChaptersService
    from services.material.character_mentions_service import CharacterMentionsService

    with get_db_session() as db:
        mention_svc = CharacterMentionsService()
        mentions = mention_svc.get_by_character_name_or_alias(db, novel_id, character_name)

        if not mentions:
            logger.warning(f"[角色实体构建] 未找到角色提及: {character_name}")
            return {
                "character_name": character_name,
                "status": "no_mentions",
            }

        # 构建章节信息
        ch_svc = ChaptersService()
        chapter_mentions = []
        all_aliases = []
        first_chapter_id = None
        importance_scores = {"major": 3, "supporting": 2, "minor": 1}
        max_importance = "minor"
        max_score = 0

        for mention in mentions:
            chapter = ch_svc.get_by_id(db, mention.chapter_id)
            if not chapter:
                continue

            chapter_mentions.append({
                "chapter_number": chapter.chapter_number,
                "chapter_description": mention.chapter_description or "",
                "chapter_importance": mention.importance or "minor",
            })

            # 合并别名
            if mention.aliases:
                all_aliases = merge_aliases(all_aliases, mention.aliases)

            # 记录首次出现章节
            if first_chapter_id is None:
                first_chapter_id = mention.chapter_id

            # 统计最高戏份
            score = importance_scores.get(mention.importance or "minor", 1)
            if score > max_score:
                max_score = score
                max_importance = mention.importance or "minor"

        # 构建章节范围
        chapter_numbers = [m["chapter_number"] for m in chapter_mentions]
        chapter_range = f"第{min(chapter_numbers)}章 - 第{max(chapter_numbers)}章"

        logger.info(
            f"[角色实体构建] 角色 {character_name}: "
            f"出现 {len(chapter_mentions)} 个章节, 范围 {chapter_range}"
        )

    # 调用 LLM 生成完整描述
    system_prompt = create_character_consolidation_prompt(
        character_name=character_name,
        chapter_mentions=chapter_mentions,
        chapter_range=chapter_range,
    )

    user_message = "请根据上述信息生成完整的角色描述。"

    logger.info(f"[角色实体构建] 调用 LLM: character_name={character_name}")

    response = call_deepseek_api(
        messages=[{"role": "user", "content": user_message}],
        system_prompt=system_prompt,
        usage_novel_id=novel_id,
    )

    # 提取 JSON
    client = get_deepseek_client()

    try:
        data = client.extract_json_from_response(response)
        logger.debug(f"[角色实体构建] JSON 解析成功: character_name={character_name}")
    except Exception as e:
        logger.error(
            f"[角色实体构建] JSON 解析失败: character_name={character_name}, error={str(e)}"
        )
        raise

    # 创建或更新角色实体
    from models.material_models import Character
    from services.material.characters_service import CharactersService

    with get_db_session() as db:
        char_svc = CharactersService()

        # 查找已存在的角色（通过主名或别名）
        existing_chars = char_svc.list_by_novel(db, novel_id)
        existing = None

        for char in existing_chars:
            # 主名匹配
            if normalize_alias(char.name) == normalize_alias(character_name):
                existing = char
                break
            # 别名匹配
            for alias in (char.aliases or []):
                if normalize_alias(alias) == normalize_alias(character_name):
                    existing = char
                    break
            if existing:
                break

        # 判断 archetype（优先使用 LLM 判断，降级到基于戏份的映射）
        archetype = data.get("archetype", "")

        # 验证 archetype 是否合法
        valid_archetypes = ["protagonist", "antagonist", "supporting", "minor"]
        if archetype not in valid_archetypes:
            # 降级：基于最高戏份映射到 archetype
            importance_to_archetype = {
                "major": "supporting",  # major 戏份 -> supporting（避免误判为主角）
                "supporting": "supporting",
                "minor": "minor",
            }
            archetype = importance_to_archetype.get(max_importance, "minor")
            logger.warning(
                f"[角色实体构建] LLM 返回的 archetype 无效: {data.get('archetype')}, "
                f"降级为: {archetype}"
            )

        description = data.get("description", "")

        if existing:
            # 更新已存在的角色
            logger.info(f"[角色实体构建] 更新已存在角色: {existing.name}")

            # 合并别名
            existing.aliases = merge_aliases(existing.aliases or [], all_aliases)

            # archetype 优先级更新
            priority = {
                "protagonist": 5,
                "antagonist": 4,
                "supporting": 3,
                "minor": 2,
            }
            old_priority = priority.get(existing.archetype or "minor", 0)
            new_priority = priority.get(archetype, 0)

            if new_priority > old_priority:
                existing.archetype = archetype
                logger.info(
                    f"[角色实体构建] {existing.name}: archetype "
                    f"{existing.archetype} -> {archetype}"
                )

            # 更新描述
            if description:
                existing.description = description

            # 保留最早的 first_appearance_chapter_id
            if not existing.first_appearance_chapter_id and first_chapter_id:
                existing.first_appearance_chapter_id = first_chapter_id

            db.flush()

            db.commit()

            return {
                "character_name": character_name,
                "character_id": existing.id,
                "status": "updated",
                "archetype": existing.archetype,
            }
        else:
            # 创建新角色
            logger.info(f"[角色实体构建] 创建新角色: {character_name}")

            new_char = Character(
                novel_id=novel_id,
                name=character_name,
                archetype=archetype,
                aliases=all_aliases,
                description=description,
                first_appearance_chapter_id=first_chapter_id,
            )
            db.add(new_char)
            db.flush()

            db.commit()

            return {
                "character_name": character_name,
                "character_id": new_char.id,
                "status": "created",
                "archetype": new_char.archetype,
            }


@database_task(name="build_all_character_entities_from_mentions", retries=3)
def build_all_character_entities_task(
    novel_id: int,
) -> dict[str, Any]:
    """
    阶段2：从所有提及记录构建角色实体（批量）

    策略：
    1. 查询该小说的所有角色提及
    2. 按角色主名分组
    3. 对每个角色调用 build_character_entity_task

    Args:
        novel_id: 小说ID

    Returns:
        Dict: 批量构建结果
    """
    logger = get_run_logger()

    logger.info(f"[批量角色实体构建] 开始处理小说: novel_id={novel_id}")

    # 查询所有提及记录
    from services.material.character_mentions_service import CharacterMentionsService

    with get_db_session() as db:
        mention_svc = CharacterMentionsService()
        all_mentions = mention_svc.get_by_novel(db, novel_id)

        # 按角色主名分组
        character_names = set()
        for mention in all_mentions:
            character_names.add(mention.character_name)

        logger.info(
            f"[批量角色实体构建] 发现 {len(character_names)} 个唯一角色, "
            f"共 {len(all_mentions)} 条提及记录"
        )

    # 对每个角色构建实体
    created_count = 0
    updated_count = 0
    failed_count = 0

    for character_name in character_names:
        try:
            result = build_character_entity_task(
                novel_id=novel_id,
                character_name=character_name,
            )

            if result.get("status") == "created":
                created_count += 1
            elif result.get("status") == "updated":
                updated_count += 1
        except Exception as e:
            logger.error(
                f"[批量角色实体构建] 角色 {character_name} 构建失败: {e}",
                exc_info=True
            )
            failed_count += 1

    logger.info(
        f"[批量角色实体构建] 完成: novel_id={novel_id}, "
        f"created={created_count}, updated={updated_count}, failed={failed_count}"
    )

    return {
        "novel_id": novel_id,
        "created_count": created_count,
        "updated_count": updated_count,
        "failed_count": failed_count,
        "total_count": created_count + updated_count,
    }
