#!/usr/bin/env python3
"""
结果构建模块

负责构建最终的流程结果数据。
统一处理结果数据的格式和结构。
"""

from __future__ import annotations

from typing import Any


class ResultBuilder:
    """
    结果构建器

    封装最终结果的构建逻辑，提供清晰的构建接口。

    使用示例:
        >>> builder = ResultBuilder()
        >>> result = builder.build_final_result(
        ...     novel_id=1,
        ...     job_id=10,
        ...     chapter_ids=[1, 2, 3],
        ...     stage1_result={...},
        ...     story_result={...},
        ...     relationship_result={...},
        ...     character_entity_result={...},
        ...     elapsed_ms=5000
        ... )
    """

    @staticmethod
    def build_final_result(
        novel_id: int,
        job_id: int | None,
        chapter_ids: list[int],
        stage1_result: dict[str, Any] | None,
        story_result: dict[str, Any],
        relationship_result: dict[str, Any],
        character_entity_result: dict[str, Any],
        status: str,
        elapsed_ms: int,
    ) -> dict[str, Any]:
        """
        构建最终结果数据

        将各个阶段的结果汇总为统一的结果结构。

        Args:
            novel_id: 小说ID
            job_id: 任务ID
            chapter_ids: 章节ID列表
            stage1_result: 阶段1结果（章节提取）
            story_result: 剧情结果（阶段2A）
            relationship_result: 关系结果（阶段2B）
            character_entity_result: 角色实体结果（阶段2C）
            elapsed_ms: 总耗时（毫秒）

        Returns:
            Dict[str, Any]: 最终结果，包含以下字段：
                - novel_id: 小说ID
                - job_id: 任务ID
                - chapters_count: 章节数量
                - summaries_count: 摘要数量
                - plots_count: 情节点数量
                - mentions_extracted: 是否提取了角色提及
                - synopsis_generated: 是否生成了小说概要
                - stories_count: 剧情数量
                - storylines_count: 剧情线数量
                - relationships_count: 人物关系数量
                - characters_created: 新创建的角色数量
                - characters_updated: 更新的角色数量
                - failed_count: 总失败计数（当前汇总已知失败）
                - failed_chapters: 阶段1失败章节
                - failed_mention_chapters: 角色提及失败章节
                - failed_stories: 聚合失败的剧情
                - failed_characters: 角色实体构建失败角色
                - status: completed / completed_with_errors / failed
                - elapsed_ms: 总耗时（毫秒）
        """
        # 安全获取 stage1_result（可能为 None）
        stage1_data = stage1_result or {}
        failed_chapters = stage1_data.get("failed_chapters", [])
        failed_mention_chapters = stage1_data.get("failed_mention_chapters", [])
        failed_stories = story_result.get("failed_stories", [])
        failed_characters = character_entity_result.get("failed_characters", [])
        meta_extraction = stage1_data.get("meta_extraction")
        meta_failed = bool(meta_extraction and meta_extraction.get("succeeded") is False)
        failed_count = (
            int(stage1_data.get("failed_count", 0) or 0)
            + len(failed_stories)
            + int(character_entity_result.get("failed_count", 0) or 0)
            + int(meta_failed)
        )

        result = {
            "novel_id": novel_id,
            "job_id": job_id,
            "chapters_count": len(chapter_ids),
            "summaries_count": stage1_data.get("summaries_count", 0),
            "plots_count": stage1_data.get("plots_count", 0),
            "mentions_extracted": stage1_data.get("mentions_extracted", False),
            "synopsis_generated": story_result.get("synopsis_generated", False),
            "stories_count": story_result.get("stories_count", 0),
            "storylines_count": story_result.get("storylines_count", 0),
            "relationships_count": relationship_result.get("relationships_count", 0),
            "characters_created": character_entity_result.get("created_count", 0),
            "characters_updated": character_entity_result.get("updated_count", 0),
            "failed_count": failed_count,
            "failed_chapters": failed_chapters,
            "failed_mention_chapters": failed_mention_chapters,
            "failed_stories": failed_stories,
            "failed_characters": failed_characters,
            "status": status,
            "elapsed_ms": elapsed_ms,
        }
        if meta_extraction is not None:
            result["meta_extraction"] = meta_extraction
        return result
