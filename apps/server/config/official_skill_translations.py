"""English names and summaries for official skills, keyed by the stored (Chinese) skill name.

Official skill rows live in the database in Chinese and are matched by name so ids, counters and
user collections stay untouched. Only the name and one-line description shown to English readers
change; the instructions the model loads are not translated. A renamed or new official skill falls
back to its stored text until it is added here.
"""

OFFICIAL_SKILL_EN: dict[str, tuple[str, str]] = {
    "续写正文": (
        "Continue the Draft",
        "Continue the part you name from the existing text and the characters' state, keeping cause and effect, "
        "viewpoint and emotion continuous.",
    ),
    "场景描写": (
        "Describe a Scene",
        "Make setting, senses and space serve the viewpoint character's goals, actions and shifting emotions.",
    ),
    "对话生成": (
        "Write Dialogue",
        "Write dialogue that moves the scene, driven by each character's aims, relationships and what they know, "
        "in voices readers can tell apart.",
    ),
    "开头生成": (
        "Write an Opening",
        "Open from the story's promise and the character's present situation, without forcing a twist or shock "
        "into the first three lines.",
    ),
    "创建大纲": (
        "Create an Outline",
        "Link goals, obstacles, active choices and consequences into a causal structure, planning only the span "
        "you ask for.",
    ),
    "冲突设计": (
        "Design Conflict",
        "Build conflict from incompatible goals, information and the cost of choices, pushing characters to act "
        "instead of just escalating enemies.",
    ),
    "钩子设计": (
        "Design Hooks",
        "Build anticipation through concrete risks, relationships or choices; pay off earlier promises before "
        "raising the next pressure.",
    ),
    "反转设计": (
        "Design a Twist",
        "Turn the story with evidence already on the page, or reinterpret earlier actions, relationships and "
        "facts, never a last-minute identity reveal.",
    ),
    "代入感强化": (
        "Deepen Immersion",
        "Draw readers in through what characters value, their concrete situation and their active choices, "
        "rather than telling readers what to feel.",
    ),
    "润色修改": (
        "Polish Prose",
        "Find mechanical repetition and clumsy phrasing and fix it locally, keeping the meaning, voice, clues "
        "and turning points.",
    ),
    "节奏控制": (
        "Control Pacing",
        "Pace each passage by its goal, new information and emotional shifts; cut repetition instead of "
        "scheduling climaxes at a fixed rate.",
    ),
    "创建角色": (
        "Create a Character",
        "Build rounded characters from their wants, fears, the cost of their choices and their relationships, "
        "keeping existing details and voice.",
    ),
    "世界观设定": (
        "Build the World",
        "Build workable rules from limits, resources and consequences, so the setting serves characters' "
        "choices instead of piling up lore.",
    ),
}


def localized_skill_text(
    *, source: str | None, name: str, description: str | None, language: str | None
) -> tuple[str, str | None]:
    """(name, description) for the reader: English for official skills when the reader uses English."""
    if source != "official" or not (language or "").lower().startswith("en"):
        return name, description
    english = OFFICIAL_SKILL_EN.get(name.strip())
    return english if english else (name, description)


def official_names_matching(search: str) -> list[str]:
    """Stored names of official skills whose English name or summary contains the search text."""
    needle = search.strip().lower()
    if not needle:
        return []
    return [
        stored
        for stored, (english_name, english_description) in OFFICIAL_SKILL_EN.items()
        if needle in english_name.lower() or needle in english_description.lower()
    ]
