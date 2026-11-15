"""
人物提取过滤器

用于过滤不符合规则的人物名称和别名
"""


from .aliases import normalize_alias

# 通用称呼黑名单（这些称呼缺乏唯一性，不能作为别名或实体）
GENERIC_TITLES: set[str] = {
    # 亲属关系
    "大哥", "二哥", "三哥", "四哥", "五哥", "六哥", "七哥", "八哥", "九哥",
    "大姐", "二姐", "三姐", "姐姐", "妹妹", "哥哥", "弟弟",
    "叔叔", "阿姨", "伯伯", "舅舅", "姑姑", "姨妈",
    "爷爷", "奶奶", "外公", "外婆", "祖父", "祖母",
    "父亲", "母亲", "爸爸", "妈妈", "爹", "娘", "老爸", "老妈",
    "儿子", "女儿", "孩子", "小孩",

    # 社交关系
    "老同学", "同学", "朋友", "兄弟", "哥们", "姐妹", "闺蜜",
    "老乡", "邻居", "室友", "战友", "同事", "伙伴",

    # 角色身份
    "新娘", "新郎", "老板", "老师", "师傅", "徒弟", "学生",
    "医生", "护士", "律师", "警察", "士兵", "将军",
    "商人", "猎人", "农民", "工人", "渔夫",
    "仆人", "侍女", "丫鬟", "管家", "护卫", "侍卫",
    "掌柜", "小二", "店主", "老板娘",

    # 年龄/外貌
    "小子", "臭小子", "小丫头", "小姑娘", "小伙子", "少年", "青年",
    "老头", "老太太", "老人", "年轻人", "中年人",
    "小家伙", "小鬼", "小娃娃",

    # 尊称/贬称
    "先生", "女士", "小姐", "少爷", "公子", "大人", "阁下",
    "陛下", "殿下", "王爷", "皇上", "圣上",
    "家伙", "混蛋", "废物", "蠢货", "王八蛋",

    # 其他通用称呼
    "那人", "此人", "那家伙", "这家伙", "某人",
    "路人", "过客", "陌生人", "外人",

    # 纯职位称呼（不包含人名的职位）
    "科长", "副科长", "代理副科长",
    "处长", "副处长", "代理处长",
    "局长", "副局长", "代理局长",
    "秘书", "大秘", "小秘", "秘书长",
    "主任", "副主任", "办公室主任",
    "县长", "副县长", "代理县长",
    "镇长", "副镇长", "常务副镇长", "代理镇长",
    "书记", "副书记", "党委书记",
    "市长", "副市长", "代理市长",
    "省长", "副省长",
    "厅长", "副厅长",
    "董事长", "副董事长", "总经理", "副总经理",
    "经理", "副经理", "总监", "主管",
    "队长", "副队长", "组长", "副组长",
    "班长", "副班长",

    # 组合职位（不包含具体人名的）
    "县长秘书", "市长秘书", "省长秘书",
    "新县长秘书", "新市长秘书",
    "县长大秘", "市长大秘",
    "县政府办综合科副科长",
    "政府办主任", "办公室副主任",
}


def is_generic_title(name: str) -> bool:
    """
    判断是否为通用称呼

    Args:
        name: 人物名称或别名

    Returns:
        bool: True 表示是通用称呼，应该被过滤
    """
    if not name:
        return True

    name = name.strip()

    # 检查是否在黑名单中
    return name in GENERIC_TITLES


def is_valid_character_name(name: str, min_length: int = 2) -> bool:
    """
    判断是否为有效的人物名称

    Args:
        name: 人物名称
        min_length: 最小长度（默认2，过滤单字名）

    Returns:
        bool: True 表示是有效名称
    """
    if not name:
        return False

    name = name.strip()

    # 检查长度
    if len(name) < min_length:
        return False

    # 检查是否为通用称呼
    return not is_generic_title(name)


def filter_character_data(characters_data: list, logger=None) -> list:
    """
    过滤人物数据，移除不符合规则的人物和别名

    Args:
        characters_data: 人物数据列表
        logger: 日志记录器（可选）

    Returns:
        list: 过滤后的人物数据列表
    """
    filtered_characters = []

    for char in characters_data:
        raw_char_name = (char.get("name") or "").strip()
        char_name = normalize_alias(raw_char_name) if not is_generic_title(raw_char_name) else raw_char_name

        # 过滤无效的主名
        if not is_valid_character_name(char_name):
            if logger:
                logger.warning(
                    f"[人物过滤] 删除无效人物: name='{raw_char_name}' "
                    f"(原因: {'长度不足' if len(char_name) < 2 else '通用称呼'})"
                )
            continue

        char["name"] = char_name

        # 过滤并规范化别名
        raw_aliases = char.get("aliases", []) or []
        valid_aliases: list[str] = []
        seen_aliases: set[str] = set()
        rejected_count = 0

        if raw_aliases and isinstance(raw_aliases, list):
            for alias_item in raw_aliases:
                if isinstance(alias_item, dict):
                    raw_alias = (alias_item.get("alias") or "").strip()
                elif isinstance(alias_item, str):
                    raw_alias = alias_item.strip()
                else:
                    continue

                if is_generic_title(raw_alias):
                    if logger:
                        logger.debug(
                            f"[别名过滤] 角色 '{char_name}': 删除通用称呼别名 '{raw_alias}'"
                        )
                    rejected_count += 1
                    continue

                alias = normalize_alias(raw_alias)

                # 检查别名有效性
                if not is_valid_character_name(alias, min_length=1):
                    # 别名允许单字（如"雷"），但不允许通用称呼或空字符串
                    if logger:
                        logger.debug(
                            f"[别名过滤] 角色 '{char_name}': 删除通用称呼别名 '{raw_alias}'"
                        )
                    rejected_count += 1
                    continue

                # 检查单字别名（需要更严格的判断）
                if len(alias) == 1:
                    if isinstance(alias_item, dict):
                        confidence = alias_item.get("confidence", 0.0)
                        alias_type = alias_item.get("alias_type", "")

                        # 单字别名必须是 nickname 且 confidence >= 0.95
                        if alias_type != "nickname" or confidence < 0.95:
                            if logger:
                                logger.debug(
                                    f"[别名过滤] 角色 '{char_name}': 删除低置信度单字别名 '{alias}' "
                                    f"(type={alias_type}, confidence={confidence})"
                                )
                            rejected_count += 1
                            continue
                    else:
                        # 纯字符串单字别名无置信度依据，避免误匹配风险
                        if logger:
                            logger.debug(
                                f"[别名过滤] 角色 '{char_name}': 删除单字别名 '{alias}'"
                            )
                        rejected_count += 1
                        continue

                if alias not in seen_aliases:
                    valid_aliases.append(alias)
                    seen_aliases.add(alias)

        # 更新别名列表
        char["aliases"] = valid_aliases

        if rejected_count > 0 and logger:
            logger.info(
                f"[别名过滤] 角色 '{char_name}': 保留 {len(valid_aliases)} 个别名，"
                f"删除 {rejected_count} 个无效别名"
            )

        filtered_characters.append(char)

    return filtered_characters
