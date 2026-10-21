"""
数据验证工具

用于验证从 LLM 提取的数据格式
"""
from typing import Any

from flows.utils.helpers.exceptions import ValidationError


def validate_chapter_summary_data(data: dict[str, Any]) -> bool:
    """
    验证章节摘要数据格式

    Args:
        data: 章节摘要数据字典

    Returns:
        是否有效

    Raises:
        ValidationError: 数据格式无效
    """
    # 必需字段: summary
    if "summary" not in data:
        raise ValidationError("章节摘要缺少必需字段: summary")

    # 验证字段类型
    if not isinstance(data["summary"], str) or not data["summary"].strip():
        raise ValidationError("章节摘要必须是非空字符串")

    # 验证长度（100-300字，允许一定弹性）
    summary_length = len(data["summary"])
    if summary_length < 50:
        raise ValidationError(f"章节摘要过短（{summary_length}字），建议至少100字")

    if summary_length > 500:
        raise ValidationError(f"章节摘要过长（{summary_length}字），建议不超过300字")

    return True


def validate_novel_synopsis_data(data: dict[str, Any]) -> bool:
    """
    验证小说梗概数据格式

    Args:
        data: 小说梗概数据字典

    Returns:
        是否有效

    Raises:
        ValidationError: 数据格式无效
    """
    # 必需字段: synopsis
    if "synopsis" not in data:
        raise ValidationError("小说梗概缺少必需字段: synopsis")

    # 验证字段类型
    if not isinstance(data["synopsis"], str) or not data["synopsis"].strip():
        raise ValidationError("小说梗概必须是非空字符串")

    # 验证长度（500-1000字，允许一定弹性）
    synopsis_length = len(data["synopsis"])
    if synopsis_length < 200:
        raise ValidationError(f"小说梗概过短（{synopsis_length}字），建议至少500字")

    if synopsis_length > 1500:
        raise ValidationError(f"小说梗概过长（{synopsis_length}字），建议不超过1000字")

    return True


def validate_plot_data(plot: dict[str, Any]) -> bool:
    """
    验证情节点数据格式

    Args:
        plot: 情节点数据字典

    Returns:
        是否有效

    Raises:
        ValidationError: 数据格式无效
    """
    # 必需字段: sequence, plot_type, description
    required_fields = ["sequence", "plot_type", "description"]

    for field in required_fields:
        if field not in plot:
            raise ValidationError(f"情节点缺少必需字段: {field}")

    # 验证字段类型
    if not isinstance(plot["description"], str) or not plot["description"].strip():
        raise ValidationError("情节点描述必须是非空字符串")

    if not isinstance(plot["sequence"], int) or plot["sequence"] < 1:
        raise ValidationError("情节点序号必须是正整数")

    # plot_type 验证
    if not isinstance(plot["plot_type"], str) or not plot["plot_type"].strip():
        raise ValidationError("情节点类型必须是非空字符串")

    # 标准类型列表（精简后）
    standard_types = [
        "CONFLICT", "TURNING_POINT", "REVEAL", "ACTION",
        "DIALOGUE", "SETUP", "RESOLUTION", "OTHER"
    ]
    if plot["plot_type"] not in standard_types:
        # 只记录警告，不抛出异常
        import logging
        logging.getLogger(__name__).warning(
            f"非标准情节点类型: {plot['plot_type']}，建议使用: {', '.join(standard_types)}"
        )

    # 验证可选字段
    if "characters" in plot:
        if not isinstance(plot["characters"], list):
            raise ValidationError("情节点人物列表必须是数组")
        # 验证每个人物名都是字符串
        for char in plot["characters"]:
            if not isinstance(char, str):
                raise ValidationError("情节点人物名必须是字符串")

    return True


def validate_character_data(character: dict[str, Any]) -> bool:
    """
    验证人物数据格式（与 character_extraction.py prompt 对齐）

    Args:
        character: 人物数据字典

    Returns:
        是否有效

    Raises:
        ValidationError: 数据格式无效
    """
    # 必需字段: name, archetype, description
    required_fields = ["name", "archetype", "description"]

    for field in required_fields:
        if field not in character:
            raise ValidationError(f"人物缺少必需字段: {field}")

    # 验证 name
    if not isinstance(character["name"], str) or not character["name"].strip():
        raise ValidationError("人物名称必须是非空字符串")

    valid_archetypes = ["protagonist", "supporting", "antagonist", "minor"]
    if character["archetype"] not in valid_archetypes:
        raise ValidationError(
            f"人物 archetype 必须是以下之一: {', '.join(valid_archetypes)}"
        )

    # 验证 description
    if not isinstance(character["description"], str) or not character["description"].strip():
        raise ValidationError("人物描述必须是非空字符串")

    # 放宽长度要求：仅要求非空，去除字数上下限约束
    # 如需恢复可在此处添加范围检查

    # 验证可选字段
    if "aliases" in character:
        if not isinstance(character["aliases"], list):
            raise ValidationError("人物别名必须是数组")
        # 验证每个别名都是字符串
        for alias in character["aliases"]:
            if not isinstance(alias, str):
                raise ValidationError("人物别名必须是字符串")

    return True


def validate_golden_finger_data(golden_finger: dict[str, Any]) -> bool:
    """
    验证金手指数据格式（与 meta_extraction.py prompt 对齐）

    Args:
        golden_finger: 金手指数据字典

    Returns:
        是否有效

    Raises:
        ValidationError: 数据格式无效
    """
    if golden_finger is None:
        return True  # 允许为空

    # 必需字段: name, type, description
    required_fields = ["name", "type", "description"]

    for field in required_fields:
        if field not in golden_finger:
            raise ValidationError(f"金手指缺少必需字段: {field}")

    # 验证 name
    if not isinstance(golden_finger["name"], str) or not golden_finger["name"].strip():
        raise ValidationError("金手指名称必须是非空字符串")

    # 验证 type
    valid_types = ["system", "space", "rebirth", "transmigration", "special_physique",
                  "artifact", "bloodline", "other"]
    if golden_finger["type"] not in valid_types:
        raise ValidationError(f"金手指类型必须是: {', '.join(valid_types)}")

    # 验证 description
    if not isinstance(golden_finger["description"], str) or not golden_finger["description"].strip():
        raise ValidationError("金手指描述必须是非空字符串")

    # 验证长度（放宽限制，允许更长的描述）
    desc_length = len(golden_finger["description"])
    if desc_length < 50:
        raise ValidationError(f"金手指描述过短（{desc_length}字），建议至少200字")

    if desc_length > 2000:
        raise ValidationError(f"金手指描述过长（{desc_length}字），建议不超过2000字")

    return True


def validate_world_view_data(world_view: dict[str, Any]) -> bool:
    """
    验证世界观数据格式（与 meta_extraction.py prompt 对齐）

    Args:
        world_view: 世界观数据字典

    Returns:
        是否有效

    Raises:
        ValidationError: 数据格式无效
    """
    if world_view is None:
        return True  # 允许为空

    # 验证 power_system（文本格式）
    if "power_system" in world_view and world_view["power_system"] is not None and not isinstance(world_view["power_system"], str):
        raise ValidationError("power_system 必须是字符串")

    # 验证 world_structure（文本格式）
    if "world_structure" in world_view and world_view["world_structure"] is not None and not isinstance(world_view["world_structure"], str):
        raise ValidationError("world_structure 必须是字符串")

    # 验证 key_factions（简单数组）
    if "key_factions" in world_view and world_view["key_factions"] is not None:
        if not isinstance(world_view["key_factions"], list):
            raise ValidationError("key_factions 必须是数组")
        # 验证每个势力名都是字符串
        for faction in world_view["key_factions"]:
            if not isinstance(faction, str):
                raise ValidationError("key_factions 中的势力名必须是字符串")

    # 验证 special_rules（文本格式）
    if "special_rules" in world_view and world_view["special_rules"] is not None and not isinstance(world_view["special_rules"], str):
        raise ValidationError("special_rules 必须是字符串")

    return True


def validate_story_data(story: dict[str, Any]) -> bool:
    """
    验证剧情数据格式

    Args:
        story: 剧情数据字典

    Returns:
        是否有效

    Raises:
        ValidationError: 数据格式无效
    """
    required_fields = ["title", "synopsis", "core_objective", "core_conflict", "story_type", "themes"]

    for field in required_fields:
        if field not in story:
            raise ValidationError(f"剧情缺少必需字段: {field}")

    # 验证 title
    if not isinstance(story["title"], str) or not story["title"].strip():
        raise ValidationError("剧情标题必须是非空字符串")

    # 验证 synopsis
    if not isinstance(story["synopsis"], str) or not story["synopsis"].strip():
        raise ValidationError("剧情概述必须是非空字符串")

    synopsis_length = len(story["synopsis"])
    if synopsis_length < 50:
        raise ValidationError(f"剧情概述过短（{synopsis_length}字），建议至少100字")
    if synopsis_length > 500:
        raise ValidationError(f"剧情概述过长（{synopsis_length}字），建议不超过300字")

    # 验证 core_objective
    if not isinstance(story["core_objective"], str) or not story["core_objective"].strip():
        raise ValidationError("核心目标必须是非空字符串")

    # 验证 core_conflict
    if not isinstance(story["core_conflict"], str) or not story["core_conflict"].strip():
        raise ValidationError("核心冲突必须是非空字符串")

    # 验证 story_type
    valid_types = ["main", "romance", "growth", "revenge", "treasure", "conflict", "mystery", "other"]
    if story["story_type"] not in valid_types:
        raise ValidationError(f"剧情类型必须是以下之一: {', '.join(valid_types)}")

    # 验证 themes
    if not isinstance(story["themes"], list) or len(story["themes"]) == 0:
        raise ValidationError("剧情主题必须是非空数组")
    if len(story["themes"]) > 3:
        raise ValidationError("剧情主题不应超过3个")

    # 验证 plot_ids（可选）
    if "plot_ids" in story and not isinstance(story["plot_ids"], list):
        raise ValidationError("plot_ids 必须是数组")

    return True


def validate_plots_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    验证情节点提取响应

    Args:
        data: LLM 响应数据

    Returns:
        验证后的情节点列表

    Raises:
        ValidationError: 数据格式无效
    """
    if "plots" not in data:
        raise ValidationError("响应缺少 'plots' 字段")

    plots = data["plots"]
    if not isinstance(plots, list):
        raise ValidationError("'plots' 必须是数组")

    if len(plots) == 0:
        raise ValidationError("情节点列表不能为空")

    # 验证每个情节点
    for i, plot in enumerate(plots):
        try:
            validate_plot_data(plot)
        except ValidationError as e:
            raise ValidationError(f"情节点 {i+1} 验证失败: {str(e)}") from e

    return plots


def validate_characters_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    验证人物提取响应

    Args:
        data: LLM 响应数据

    Returns:
        验证后的人物列表

    Raises:
        ValidationError: 数据格式无效
    """
    if "characters" not in data:
        raise ValidationError("响应缺少 'characters' 字段")

    characters = data["characters"]
    if not isinstance(characters, list):
        raise ValidationError("'characters' 必须是数组")

    # 验证每个人物
    for i, character in enumerate(characters):
        try:
            validate_character_data(character)
        except ValidationError as e:
            raise ValidationError(f"人物 {i+1} 验证失败: {str(e)}") from e

    return characters


def validate_meta_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    验证金手指和世界观提取响应

    Args:
        data: LLM 响应数据

    Returns:
        验证后的数据

    Raises:
        ValidationError: 数据格式无效
    """
    result = {
        "golden_fingers": [],  # 默认空数组
        "world_view": None,    # 默认 null
    }

    # 验证金手指（支持数组格式）
    if "golden_fingers" in data:
        golden_fingers = data["golden_fingers"]
        if not isinstance(golden_fingers, list):
            raise ValidationError("golden_fingers 必须是数组")

        validated_gfs = []
        for idx, gf in enumerate(golden_fingers):
            try:
                validate_golden_finger_data(gf)
                validated_gfs.append(gf)
            except ValidationError as e:
                raise ValidationError(f"金手指[{idx}]验证失败: {str(e)}") from e

        result["golden_fingers"] = validated_gfs

    # 兼容旧格式（单个金手指对象）
    elif "golden_finger" in data:
        try:
            validate_golden_finger_data(data["golden_finger"])
            # 转换为数组格式
            result["golden_fingers"] = [data["golden_finger"]] if data["golden_finger"] else []
        except ValidationError as e:
            raise ValidationError(f"金手指验证失败: {str(e)}") from e

    # 验证世界观
    if "world_view" in data:
        try:
            validate_world_view_data(data["world_view"])
            result["world_view"] = data["world_view"]
        except ValidationError as e:
            raise ValidationError(f"世界观验证失败: {str(e)}") from e

    return result


def validate_relationship_data(relationship: dict[str, Any]) -> bool:
    """
    验证人物关系数据格式（与 relationship_extraction.py prompt 对齐）

    Args:
        relationship: 人物关系数据字典

    Returns:
        是否有效

    Raises:
        ValidationError: 数据格式无效
    """
    # 必需字段: character_a, character_b, relationship_type, sentiment, description
    required_fields = ["character_a", "character_b", "relationship_type", "sentiment", "description"]

    for field in required_fields:
        if field not in relationship:
            raise ValidationError(f"人物关系缺少必需字段: {field}")

    # 验证 character_a
    if not isinstance(relationship["character_a"], str) or not relationship["character_a"].strip():
        raise ValidationError("character_a 必须是非空字符串")

    # 验证 character_b
    if not isinstance(relationship["character_b"], str) or not relationship["character_b"].strip():
        raise ValidationError("character_b 必须是非空字符串")

    # 验证关系类型
    valid_types = ["family", "master_disciple", "friend", "enemy", "lover", "colleague", "superior_subordinate", "business", "other"]
    if relationship["relationship_type"] not in valid_types:
        raise ValidationError(f"relationship_type 必须是: {', '.join(valid_types)}")

    # 验证情感倾向
    valid_sentiments = ["positive", "negative", "neutral", "complex"]
    if relationship["sentiment"] not in valid_sentiments:
        raise ValidationError(f"sentiment 必须是: {', '.join(valid_sentiments)}")

    # 验证 description
    if not isinstance(relationship["description"], str) or not relationship["description"].strip():
        raise ValidationError("关系描述必须是非空字符串")

    # 验证长度（50-200字，允许一定弹性）
    desc_length = len(relationship["description"])
    if desc_length < 20:
        raise ValidationError(f"关系描述过短（{desc_length}字），建议至少50字")

    if desc_length > 300:
        raise ValidationError(f"关系描述过长（{desc_length}字），建议不超过200字")

    return True


def validate_relationships_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    验证人物关系提取响应

    Args:
        data: LLM 响应数据

    Returns:
        验证后的数据

    Raises:
        ValidationError: 数据格式无效
    """
    if "relationships" not in data:
        raise ValidationError("响应缺少 'relationships' 字段")

    relationships = data["relationships"]
    if not isinstance(relationships, list):
        raise ValidationError("'relationships' 必须是数组")

    # 验证每个关系
    for i, relationship in enumerate(relationships):
        try:
            validate_relationship_data(relationship)
        except ValidationError as e:
            raise ValidationError(f"人物关系 {i+1} 验证失败: {str(e)}") from e

    return data
