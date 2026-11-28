"""Structured UI action contracts for single-loop agent turns.

Follows AGENTS.md "Agent semantic decisions": UI buttons send an explicit,
Pydantic-validated ``ui_action`` scoped strictly to the current request rather
than relying on natural-language keyword matching. Instructions are injected
into this turn's ``<turn_context>`` only — never into the static system prompt,
so cross-turn prompt-cache prefixes stay intact.
"""

from __future__ import annotations

from typing import Literal

from sqlmodel import Session, select

UIAction = Literal["natural_polish"]
UI_ACTION_NATURAL_POLISH: UIAction = "natural_polish"
OFFICIAL_NATURAL_POLISH_SKILL_NAME = "去AI味"

# Single fallback editorial method used only when the official "去AI味" row is
# absent from the database (e.g. an unseeded local/test database). When the
# approved official PublicSkill row exists, its stored instructions are the
# single authoritative source of truth and are loaded directly without
# requiring UserAddedSkill.
DEFAULT_NATURAL_POLISH_METHOD_ZH = """
给目标文本去AI味：表达自然，笔墨有重心，故事有依据。先通读目标范围，分别检查故事依据与信息边界、叙事用笔与详略、表达与叙述连贯三层，只修改有具体阅读问题的地方；没有问题的文字原样保留。不以换词数量、删改比例或检测分数衡量完成度。

1. 故事依据与信息边界
- 沿着人物行动检查因果、条件和信息来源；只修范围里可核实的指代、主体、时序笔误，恢复已经给出的因果与承接。
- 资料不足或需要新动机、新证据、新事件才能成立的地方，不擅自编造原因，不靠「他终于被打动」补因果，不提前揭开悬念。

2. 叙事用笔与详略
- 辨明这一段让读者经历什么，把篇幅落在关键选择、交锋、判断变化、关系或情绪抵达上。
- 压缩不改变处境和感受的机械过场、逐件清点、轮流震惊与同一情绪反复标注；保护建立空间、声口、生活质感、关系和余韵的细节。
- 对白、叙述、心理共同承接场景，不把每项事实都改成一问一答，也不把内心活动一律换成身体反应。

3. 表达与叙述连贯
- 优先治理电报体、短碎句和逐项播报：找准本段的行动或变化主干，把背景、条件和缘由嵌进需要它的动作中，让下一句承接上一句；保留承担紧张、发现、短对白或有意留白的断句。
- 排查套词、工整并列、否定后翻转、万能状语、模板比喻、公文腔和机械复现：先看功能，只重复标注情绪时删去重复或压回准确表达，承担动作条件、人物习惯或后果时保留。
- 解释若只把刚发生的事再翻译成结论，就压缩重复；不要为去掉升华句凭空新增动作、画面或对白；准确的普通词和有作用的副词/连词无须为了制造差异而替换。

内容保真：
- 不丢有效内容：承载事实、伏笔、因果、空间方向、人物选择、关系、情绪体验和主题的句子可以重组和改写，但信息与作用必须保住，不能整句拿掉。
- 不改事实与揭露：人物、称呼、地名、数字、时间、事件经过、每句对白的意思和说话人不变，不新增设定、动作、粗话或剧情依据。
- 保持原有引号体例（半角直引号 ""、弯引号 “”、直角引号 「」 均按原文保留）与正文语言。
""".strip()

DEFAULT_NATURAL_POLISH_METHOD_EN = """
Remove AI tone from the target text: make expression natural, give scenes clear narrative focus, and keep story logic grounded. Read the target span first, inspect story logic and information boundaries, narrative focus and detail allocation, and expression and flow, then revise only concrete reading problems. Leave effective writing unchanged. Do not measure completion by word swaps, percentage of cuts, or detection scores.

1. Story logic and information boundaries
- Follow each character's actions, causes, conditions, and who learns what when. Fix only verifiable reference, subject, or timeline slips and reconnect causes already present.
- Where context is missing, do not invent motives, evidence, or bridge phrases like "he was finally persuaded", and do not reveal withheld suspense early.

2. Narrative focus and allocation of detail
- Identify what the reader should experience and put weight on the key choice, exchange, shift in judgment, relationship, or emotional arrival.
- Compress mechanical transitions, item-by-item inventories, successive reactions, and repeated emotion labels; preserve details that build space, voice, lived texture, relationships, and resonance.
- Let dialogue, narration, and direct thought work together without mechanically converting every thought into a bodily reaction.

3. Expression and narrative flow
- Prioritize telegraphic prose, choppy fragments, and item-by-item reporting: embed background, conditions, and reasons into the action that needs them so sentences flow into each other. Keep deliberate breaks that carry tension, discovery, brief dialogue, or pauses.
- Trim stock phrasing, symmetrical lists, unnecessary negative-to-positive contrasts, template metaphors, bureaucratic phrasing, and mechanical repetition based on function rather than reflexively deleting every instance.
- Compress explanations that only restate what just happened; never invent a gesture, image, or line of dialogue to replace a closing summary. Keep accurate ordinary words and meaningful qualifiers intact.

Fidelity:
- Lose no effective content: facts, foreshadowing, causation, spatial orientation, choices, relationships, emotional continuity, and theme must survive revision; never drop substantive sentences.
- Preserve all characters, names, places, numbers, timeline, events, dialogue meaning and speakers, and existing quotation mark style. Write in the manuscript's language.
""".strip()

SCREENPLAY_POLISH_RULES_ZH = """
短剧剧本格式保护（剧本稿件必须严格遵守）：
- 以「△」开头的动作/画面行：必须保留行首的「△」，不得整行删除，只改这一行里的 AI 腔；
- 场景标题行（如「第3集 重逢」「【场2】医院走廊 · 夜 · 内」）和「人物：」行：一字不改；
- 「角色：台词」「角色（神态）：台词」格式的台词行：角色名、括号与冒号原样保留，不得整行删除，只改冒号后方台词里的 AI 腔；
- 不得把动作行改成台词，也不得把台词改成动作行或小说叙述。
""".strip()

SCREENPLAY_POLISH_RULES_EN = """
Short-drama script format preservation (strictly required for screenplay files):
- Action lines starting with "△": keep the leading "△" and never delete the line; only fix AI tone inside it.
- Scene headings (e.g. "Episode 3", "[Scene 2] Hospital corridor · Night · Int.") and "Characters:" / "人物：" lines: leave completely untouched.
- Dialogue lines in "Name: line" or "Name (manner): line" form: keep the speaker name, parentheses, and colon intact, never delete the line, and only revise AI tone in the spoken line after the colon.
- Never turn an action line into dialogue or dialogue into an action line or prose narration.
""".strip()


def resolve_official_natural_polish_skill(session: Session):
    """Load the approved official '去AI味' PublicSkill row without requiring UserAddedSkill."""
    from models import PublicSkill

    return session.exec(
        select(PublicSkill)
        .where(
            PublicSkill.source == "official",
            PublicSkill.status == "approved",
            PublicSkill.name == OFFICIAL_NATURAL_POLISH_SKILL_NAME,
        )
        .order_by(PublicSkill.updated_at.desc(), PublicSkill.id)
        .limit(1)
    ).first()


def resolve_official_natural_polish_instructions(session: Session) -> str | None:
    """Load the approved official '去AI味' skill instructions without requiring UserAddedSkill."""
    skill = resolve_official_natural_polish_skill(session)
    if skill is None:
        return None
    text = (skill.instructions or "").strip()
    return text or None


def build_ui_action_turn_section(
    session: Session,
    *,
    ui_action: UIAction,
    force_en: bool,
    focus_file_id: str | None,
    focus_file_title: str | None,
    focus_file_type: str | None,
    has_selected_text: bool,
) -> list[str]:
    """Build the per-turn `<turn_context>` section for a validated structured UI action."""
    if ui_action != UI_ACTION_NATURAL_POLISH or not focus_file_id:
        return []

    official_instructions = resolve_official_natural_polish_instructions(session)
    method_text = official_instructions or (
        DEFAULT_NATURAL_POLISH_METHOD_EN if force_en else DEFAULT_NATURAL_POLISH_METHOD_ZH
    )
    is_script = focus_file_type == "script"
    title_label = (focus_file_title or "").strip() or focus_file_id

    if force_en:
        scope_rule = (
            "- **Scope (selected passage only)**: the author highlighted a specific passage (`Selected text`). "
            "Edit ONLY within that selected passage and leave every character outside the selection completely untouched."
            if has_selected_text
            else "- **Scope (full open file)**: no passage was selected, so polish the entire current open file in place."
        )
        screenplay_block = (
            ["", SCREENPLAY_POLISH_RULES_EN]
            if is_script
            else ["", f"If the target text is a short-drama script, also obey:\n{SCREENPLAY_POLISH_RULES_EN}"]
        )
        return [
            "",
            "## Current UI Action: Remove AI Tone (`ui_action=natural_polish`)",
            "",
            f"Target file: `{title_label}` (id={focus_file_id}). This action applies to this turn only.",
            "",
            "### Authoritative Method",
            "",
            method_text,
            *screenplay_block,
            "",
            "### Execution Rules (Strictly Required)",
            scope_rule,
            "- **Use a single batched `edit_file` call**: apply localized paragraph-level `replace` edits on the target file in one batched `edit_file` call whenever possible.",
            "- **Do NOT call `write_chapter`** to rewrite the whole chapter or replace the whole file.",
            "- **Do NOT call `load_skill`** for Remove AI Tone / 去AI味 — the authoritative method is already provided above.",
            "- **Do NOT run unnecessary `read_files` / `search_files`**: the full text of the focus file is already in `<turn_context>` above; only read or search another file if a specific cross-chapter fact genuinely requires checking.",
            "- **Zero-change rule**: if the target text has no concrete AI-tone problems worth fixing, do NOT call `edit_file` and do NOT make cosmetic or punctuation-only edits; briefly explain to the author that the text already reads naturally and no changes were made.",
            *(
                [
                    "- **Language fence**: the authoritative method above is written in Chinese; apply only the methodology and produce all replies and edits strictly in the manuscript's / author's language."
                ]
                if official_instructions
                else []
            ),
        ]

    scope_rule = (
        "- **修改范围（仅限选区）**：作者选中了特定片段（见下方「选中的文本」）。**只允许修改选区内的文字**，选区之外的正文一字不动。"
        if has_selected_text
        else "- **修改范围（整章原地润色）**：作者未选中局部片段，直接对当前打开的焦点文件整篇原地去AI味。"
    )
    screenplay_block = (
        ["", SCREENPLAY_POLISH_RULES_ZH]
        if is_script
        else ["", f"若当前稿件包含或属于短剧剧本，同样必须遵守：\n{SCREENPLAY_POLISH_RULES_ZH}"]
    )
    return [
        "",
        "## 本轮界面动作：去AI味（`ui_action=natural_polish`）",
        "",
        f"目标文件：《{title_label}》（id={focus_file_id}）。本动作仅作用于这一轮，不延续到后续消息。",
        "",
        "### 去AI味方法",
        "",
        method_text,
        *screenplay_block,
        "",
        "### 执行要求（必须遵守）",
        scope_rule,
        "- **优先单次批量 `edit_file`**：用一次包含多处逐段定位 `replace` 的 `edit_file` 完成目标文件的局部改写，减少往返轮次。",
        "- **禁止调用 `write_chapter` 整章重写**，也不要把整篇正文作为单条 replace 整份覆盖。",
        "- **不要调用 `load_skill` 加载「去AI味」**：权威方法已在上方给出。",
        "- **不要做非必要的 `read_files` / `search_files`**：当前焦点文件的全文已在上方 `<turn_context>` 中，除非确有必要核对跨章人物/设定事实，否则直接基于当前全文改稿。",
        "- **无改动不强改**：如果三层检查后没有具体阅读问题，**不要调用 `edit_file`**，也不要为了制造差异改标点或换同义词；直接简短告知作者这段已经比较自然、原文未做改动。",
    ]
