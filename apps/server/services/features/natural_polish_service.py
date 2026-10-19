"""
Natural polish service (single-round rewrite).

This service performs a lightweight, non-streaming rewrite for selected text,
without invoking the full agent workflow.

Prompt ownership is server-side only so every client uses the same prompt source.

模型不再整段重写，而是只列出要改的行（「原：/改：」成对）。服务端把这些改动套回原文，
没提到的行逐字节保留，所以段首缩进、换行、剧本的 △/场景标题/台词行格式由代码保证，
不靠模型自觉。去AI味只改写不删减：模型整句删掉的有内容的句子，由句子守卫按原位置放回。
"""

import difflib
import re
from dataclasses import dataclass
from typing import Literal

from agent.core.llm_client import get_llm_client
from core.error_handler import APIException
from models.file_model import FILE_TYPE_SCRIPT
from models.llm_usage import LLM_USAGE_SOURCE_POLISH
from services.usage.llm_usage_service import LLMUsageAttribution
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

DEFAULT_NATURAL_POLISH_PROMPT_ZH = """
你是一位中文小说编辑。给用户发来的「选中的文本」去AI味：表达自然，笔墨有重心，故事有依据。先通读整个选区，分别检查故事、叙事用笔、表达三层，再修改有具体阅读问题的地方；没有问题的文字原样保留。不以换词数量、删改比例或检测分数衡量完成度。

范围与文风：
- 你只有选中文本，不能查阅选区外的前文、设定或作者偏好，也不能把缺少这些资料等同于全书没有铺垫。沿用选区已有的叙述姿态、人物声口和语域，不统一改成口语、短句或纯动作描写。
- 长句、心理直写、作者评论、排比、比喻、停顿和慢场景都可能有功能。判断它是否重复、割裂叙述或遮住关键过程；不能仅凭某个词或句式出现就判错。

按以下顺序检查与改稿，判断过程只在心中完成：

1. 故事依据与信息边界
沿着选区中的行动检查：人物根据当时所知为什么选择这一步，前件的什么条件触发后件，对方为什么接受或改变，办法如何得到结果，关系或认知的变化是否有承接，谁在何时从哪里知道什么。日常、沉淀和氛围场景也有价值，不要求每场都有冲突、反转或代价。
只修选区内可核实的指代、主体、时序笔误，恢复已经给出的因果、条件和信息来源。资料不足或需要新动机、新证据、新事件才能成立的地方，保留未知，不擅自选定解释，不靠「他终于被打动」补因果，不提前揭开悬念。继续完成其他有依据的修改。

2. 叙事用笔与详略
先辨明这场戏让读者经历什么，再把篇幅落在关键的选择、交锋、判断变化、关系或情绪抵达上。压缩不改变处境和感受的开门倒水、逐件清点、轮流震惊与同一情绪反复标注；保护建立空间、声口、生活质感、关系和余韵的细节。
「商量妥了」「她终于想通了」吞掉关键过程时，只组织选区已经提供的条件、试探、权衡与反馈，不能编出说服理由。对白、叙述、心理共同承接场景，不把每项事实都改成一问一答，也不把内心活动一律换成身体反应。压缩后若只剩结果、丢了犹疑和情绪过程，就保留必要展开。

3. 表达与叙述连贯
优先治理电报体、短碎句和逐项播报。先找本段的行动或变化与每句主干，把背景、条件和缘由嵌进需要它的动作中，让下一句承接上一句；即使分句很长、主谓宾齐全，只用逗号串起一项项状态仍要重组。只删一个逗号、添虚词或把句子机械拉长，不能代替恢复信息主次。
例如「她推门。屋里没开灯。她摸到桌边。把药盒拿起来。」可组织成「进屋后，她摸黑走到桌边，拿起药盒。」光线嵌入已有行动；不能改成吞药或新增药量。承担紧张、发现、判断变化、短对白或有意留白的断句则保留。
再查套词、工整并列、否定后翻转、万能状语、模板比喻、公文腔和机械复现。像「几不可察」「指节泛白」「心头一紧」的写法先看功能：只重复标注情绪时删去重复或压回准确表达；承担动作条件、人物习惯或后果时保留，不换个身体部位继续套写。排比承担递进、人物声线或清晰比较时可留，不因恰有三项就删到一项；纠错、辩解或改变判断所需的对比也保留。
解释若补足用途、因果、记忆触发或情绪来由，就保留到足以理解；若只把刚发生的事再翻译成结论，就压缩重复。心理直写、带立场的叙述和结尾判词按其贡献判断，不机械套「展示代替讲述」。不要为去掉升华句凭空新增一个动作、画面或对白。准确的普通词无须换同义词，有作用的「才、还、又、只、似乎、原来、因为」不随手删。

硬性要求：
- 不丢有效内容：保护事实、伏笔、因果、空间方向、人物选择、关系、情绪体验和主题。承载这些内容的句子可以重组和改写，信息与作用必须保住，不能整句拿掉；只有整句或整行都是无功能重复、删后没有损失时才能删除。
- 不改事实与揭露：人物、称呼、地名、数字、时间、事件经过、每句对白的意思和说话人不变，不改变人物决定、结果或获知顺序，不把推测写成事实、打算写成已经发生、否定写成肯定，保留条件和原有视角。不新增微动作、感官、粗话、冲突、证据、承诺、设定或观察经历来制造真人感。
- 按行修改：原文的一行就是一个段落（剧本里就是一行）。可以在同一行内重组句子，但不能合并两行、拆成几行或搬动不同原行的内容。
- 引号一个都不换：原文是半角直引号 "" 就保持半角直引号，原文是弯引号 “” 就保持弯引号，原文是「」就保持「」；不要给没有引号的地方加引号。
- 本次检查提示只是定位线索，不是见词必改或必须减少排比项数的命令。提示为空也要完成三层检查。没有具体阅读问题就不改，不为制造差异更换标点、同义词或删改有效文字；用原文的语言输出。

输出前回读整个选区，核对事实、选择、因果、情绪承接、空间方向和悬念；确认没有新套式或新增的剧情依据。若信息齐全却仍逐项播报，或对白仍像提纲，继续重组。没有可依据的修改时，接受无改动。

输出格式（必须严格遵守）：
- 每改一行，输出两行：第一行以「原：」开头，后面照抄原文这一整行，一字不差；第二行以「改：」开头，后面写改好的这一整行。只有整行没有需要保留的内容和作用时，才让「改：」后面留空，表示删掉这一行。
- 每组之间空一行。没改的行不要输出。
- 如果一处都不需要改，只输出：无改动
- 不要输出任何解释、说明、标题、编号或其他文字，不要把「无改动」与修改对混用，不要调用工具。
- 格式不完整或多出任何文字都会让本次请求失败；不要退回整段正文作为兜底。
""".strip()

SCRIPT_NATURAL_POLISH_RULES_ZH = """
这段文字是短剧剧本，剧本格式一律不动：
- 以「△」开头的动作、画面行：保留行首的「△」，不能整行删掉，只能改这一行里的 AI 腔；
- 场景标题行（例如「第3集 重逢」「【场2】医院走廊 · 夜 · 内」）和「人物：」行不要列出来，一个字都不改；
- 「角色：台词」「角色（神态）：台词」格式的台词行：角色名、括号和冒号原样保留，不能整行删掉，只能改冒号后面台词里的 AI 腔；
- 不把动作行改成台词，也不把台词改成动作行或叙述。
""".strip()

DEFAULT_NATURAL_POLISH_PROMPT_EN = """
You are a fiction editor. Remove the AI tone from the user's "selected text": make the expression natural, give the scene a clear focus, and keep the story grounded. Read the entire selection, examine story logic, narrative focus, and expression separately, then fix concrete reading problems. Leave effective writing unchanged. Do not measure success by word substitutions, a percentage of cuts, or a detection score.

Scope and voice:
- You only have the selection, not earlier chapters, plans, or author preferences. Missing context does not prove that the whole story lacks setup. Preserve the selection's narrative stance, character voices, and register; do not turn everything into casual speech, short sentences, or physical action.
- Long sentences, direct thoughts, narrator commentary, parallelism, metaphors, pauses, and quiet scenes can all serve a purpose. Judge whether they repeat information, break the narrative, or obscure a key process. A word or sentence pattern alone is not a fault.

Examine and revise in this order. Keep your reasoning internal:

1. Story logic and information boundaries
Follow the actions: why does a character choose this step given what they know, which earlier condition causes the next event, why does another person agree or change course, how does a method produce its result, and what supports a change in a relationship or understanding? Track who learns what, when, and from where. Everyday life, reflection, and atmosphere need not contain a conflict, reversal, or cost.
Correct only references, subjects, or timeline slips that can be verified within the selection, and reconnect causes, conditions, or information sources already provided. If a repair needs missing context or a new motive, piece of evidence, or event, leave that uncertainty unresolved. Do not invent an explanation, write "he was finally persuaded" to bridge a missing cause, or reveal a withheld answer. Continue with other supported revisions.

2. Narrative focus and allocation of detail
Identify what the reader should experience in this scene. Give attention to the important choice, exchange, change of judgment, relationship, or emotional transition. Compress routine opening of doors, pouring of drinks, inventories, successive reactions, or repeated emotion when they add nothing; preserve details that establish space, voice, lived experience, relationships, or a lingering feeling.
When "they reached an agreement" or "she finally understood" skips the important process, organize only the conditions, attempts, deliberation, and feedback already supplied. Do not invent a reason that convinces someone. Let dialogue, narration, and thought work together; do not turn every fact into a question and answer, or every thought into a bodily reaction. If compression leaves only a result and loses hesitation or the emotional journey, retain the necessary development.

3. Expression and narrative flow
Prioritize telegraphic prose, fragmented sentences, and item-by-item reporting. Find the action or change and each sentence's main clause; embed background, conditions, and reasons where the action needs them, so one sentence leads into the next. Long clauses with complete subjects and verbs can still read like a list. Deleting a comma, adding connective words, or mechanically lengthening sentences does not establish the hierarchy of information.
For example, "She opened the door. The room was dark. She felt her way to the table. Picked up the medicine box." can become "After entering, she felt her way through the dark to the table and picked up the medicine box." This connects existing facts; it must not become taking medicine or adding a dose. Keep deliberate breaks that carry tension, discovery, a changed judgment, brief dialogue, or a meaningful pause.
Then examine stock phrasing, symmetrical lists, unnecessary negative-to-positive contrasts, generic modifiers, template metaphors, bureaucratic language, and mechanical repetition. Phrases such as "a barely perceptible smile", "knuckles turning white", or "her heart lurched" are clues: cut or compress them when they merely label the same emotion; keep them when they establish an action's condition, a character's habit, or a consequence. Do not replace one body part with another stock reaction. Keep parallelism that develops emotion, voice, or a clear comparison; three items need not become one. Keep contrasts needed to correct a mistake, defend a position, or change a judgment.
Retain explanations needed to understand a purpose, cause, memory trigger, or feeling; compress those that only restate what just happened. Judge direct thought, a narrator's stance, and closing judgments by their contribution rather than mechanically applying "show, don't tell." Do not invent an action, image, or dialogue to replace a moralizing closer. Accurate ordinary words need no synonym, and words carrying timing, degree, uncertainty, discovery, or causation must not be casually removed.

Hard requirements:
- Lose no effective content: preserve facts, foreshadowing, causes, spatial orientation, choices, relationships, emotional experience, and theme. Sentences carrying these may be reorganized or rewritten, but their information and function must survive; never simply drop them. Delete a whole sentence or line only when it is entirely redundant and nothing is lost.
- Preserve facts and disclosure: characters, names, places, numbers, timeline, events, and the meaning and speaker of dialogue stay the same. Do not change decisions, results, or the order of discovery. Do not turn a possibility into a fact, an intention into a completed event, or a negative into a positive; preserve conditions and viewpoint. Add no gestures, sensory details, profanity, conflicts, evidence, promises, setting, or observation experiences to manufacture a human feel.
- Edit line by line: each original line is one paragraph (or one script line). You may reorganize sentences within it, but never merge lines, split a line, or move content between original lines.
- Do not change a single quotation mark: straight quotes stay straight, curly quotes stay curly; do not add quotes where there are none.
- Checks appended to this prompt are location clues, not orders to remove every matching phrase or reduce every triplet. Complete all three layers even when no checks are appended. Leave writing without a concrete reading problem unchanged; do not change punctuation, swap synonyms, or cut effective words merely to create a difference. Write in the original's language.

Before output, reread the whole selection for facts, choices, causation, emotional continuity, spatial orientation, and suspense. Check that you added no new formula or story premise. If the facts are complete but the prose still reports items one by one, or dialogue reads like an outline, reorganize it. Accept no change when no supported revision is needed.

Output format (follow it exactly):
- For each line you change, output two lines: the first starts with "OLD:" followed by that entire original line copied character for character; the second starts with "NEW:" followed by the entire revised line. Leave "NEW:" empty only when the whole line has no content or function that needs preserving; that deletes the line.
- Put a blank line between pairs. Do not output unchanged lines.
- If nothing needs changing, output only: NO CHANGES
- Output nothing else: no explanations, notes, headings or numbering. Never mix "NO CHANGES" with edit pairs. Do not call tools.
- An incomplete pair or any extra text makes the request fail; never fall back to returning the full rewritten passage.
""".strip()

SCRIPT_NATURAL_POLISH_RULES_EN = """
This text is a short drama script. Leave the script format untouched:
- Action lines starting with "△": keep the leading "△" and never delete the line; only fix AI tone inside it.
- Scene headings (such as "Episode 3" or "[Scene 2] Hospital corridor · Night · Int.") and "Characters:" lines: do not list them; they stay exactly as written.
- Dialogue lines in "Name: line" or "Name (manner): line" form: keep the name, parentheses and colon, never delete the line; only fix AI tone in the spoken text after the colon.
- Never turn an action line into dialogue or dialogue into an action line or narration.
""".strip()

NATURAL_POLISH_MAX_TOKENS = 16000

# 判断「改写结果和原文没有实质区别」时忽略的差异：所有空白（含全角空格缩进和换行），
# 以及引号体例（直引号、弯引号、直角引号互换）。只差这些的改写不值一条 AI 消息。
_WHITESPACE_RE = re.compile(r"\s+")
_QUOTE_TRANSLATION = str.maketrans(
    {
        "“": '"',  # “
        "”": '"',  # ”
        "„": '"',  # „
        "「": '"',  # 「
        "」": '"',  # 」
        "‘": "'",  # ‘
        "’": "'",  # ’
        "『": "'",  # 『
        "』": "'",  # 』
    }
)


def normalize_for_noop_comparison(text: str) -> str:
    """Drop all whitespace and unify quotation marks for the no-change check."""
    return _WHITESPACE_RE.sub("", text).translate(_QUOTE_TRANSLATION)


def is_noop_rewrite(original: str, rewritten: str) -> bool:
    """True when the rewrite differs from the original only by whitespace or quote style."""
    return normalize_for_noop_comparison(original) == normalize_for_noop_comparison(rewritten)


# ---------------------------------------------------------------------------
# 行级改动协议：解析「原：/改：」并套回原文
# ---------------------------------------------------------------------------

_EDIT_OLD_RE = re.compile(r"^(原：|OLD:)(.*)$")
_EDIT_NEW_RE = re.compile(r"^(改：|NEW:)(.*)$")
_DELETE_PLACEHOLDERS = {"（删除）", "(删除)", "删除", "（删去）", "[delete]", "(delete)", "[deleted]"}

_SCRIPT_HEADING_RE = re.compile(r"^(?:【[^】]*】|第\s*[0-9一二三四五六七八九十百]+\s*[集场幕]|人物\s*[:：]|Characters?\s*:|Episode\s+\d+)", re.IGNORECASE)
_SCRIPT_DIALOGUE_PREFIX_RE = re.compile(r"^([^\s△：:（(]{1,12}(?:[（(][^）)]*[）)])?)\s*([：:])")


@dataclass(frozen=True)
class LineEdit:
    """One「原：/改：」pair returned by the model."""

    old: str
    new: str


NaturalPolishFailureStage = Literal[
    "model_call",
    "protocol_parse",
    "anchor_apply",
    "content_protection",
]
NaturalPolishFailureCategory = Literal[
    "model_call_failed",
    "empty_output",
    "invalid_marker",
    "truncated_output",
    "missing_anchor",
    "ambiguous_anchor",
    "duplicate_anchor",
    "anchor_apply_failed",
    "content_protection_failed",
]


class NaturalPolishProtocolError(ValueError):
    """The model response did not satisfy one fixed protocol rule."""

    def __init__(
        self,
        message: str,
        *,
        category: NaturalPolishFailureCategory = "invalid_marker",
    ) -> None:
        self.category = category
        super().__init__(message)


class NaturalPolishFailure(RuntimeError):
    """A safe, fixed diagnostic for one failed natural-polish stage.

    The exception deliberately carries no model output, selected text, identifiers,
    or original exception message. The original exception remains available only via
    chaining for local debugging; request logs must record ``stage`` and ``category``.
    """

    def __init__(
        self,
        *,
        stage: NaturalPolishFailureStage,
        category: NaturalPolishFailureCategory,
    ) -> None:
        self.stage = stage
        self.category = category
        super().__init__(f"natural polish failed at {stage}: {category}")


class _NaturalPolishContentProtectionError(RuntimeError):
    """Internal boundary for an unexpected sentence-protection failure."""


def _marker_payload(match: "re.Match[str]") -> str:
    """Return marker payload while accepting the prompt's optional one ASCII space."""
    payload = match.group(2)
    return payload[1:] if payload.startswith(" ") else payload


def parse_line_edits(output: str) -> list[LineEdit]:
    """Parse a complete ``原：/改：`` or ``OLD:/NEW:`` response.

    The model output is an internal wire format, not prose. Partial pairs, aliases,
    multiline revisions, mixed marker dialects and any explanatory text are rejected
    rather than being written into the author's document or downgraded to a no-op.
    """
    stripped = output.strip()
    if stripped in {"无改动", "NO CHANGES"}:
        return []
    if not stripped:
        raise NaturalPolishProtocolError(
            "empty natural polish response",
            category="empty_output",
        )

    lines = stripped.splitlines()
    edits: list[LineEdit] = []
    dialect: str | None = None
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue

        old_match = _EDIT_OLD_RE.fullmatch(lines[index])
        if old_match is None:
            raise NaturalPolishProtocolError(f"expected OLD marker at line {index + 1}")
        old = _marker_payload(old_match)
        if not old:
            raise NaturalPolishProtocolError(
                f"empty OLD anchor at line {index + 1}",
                category="missing_anchor",
            )

        index += 1
        if index >= len(lines):
            raise NaturalPolishProtocolError(
                "incomplete natural polish edit pair",
                category="truncated_output",
            )
        new_match = _EDIT_NEW_RE.fullmatch(lines[index])
        if new_match is None:
            raise NaturalPolishProtocolError(f"expected NEW marker at line {index + 1}")

        old_marker = old_match.group(1)
        new_marker = new_match.group(1)
        if (
            (old_marker == "原：") != (new_marker == "改：")
            or (dialect is not None and old_marker != dialect)
        ):
            raise NaturalPolishProtocolError(f"mixed edit marker dialect at line {index + 1}")
        dialect = old_marker
        edits.append(LineEdit(old=old, new=_marker_payload(new_match)))
        index += 1

        if index < len(lines) and lines[index].strip():
            raise NaturalPolishProtocolError(f"unexpected text at line {index + 1}")

    if not edits:
        raise NaturalPolishProtocolError("natural polish response contains no edit pairs")
    return edits


def _leading_ws(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]


def _trailing_ws(text: str) -> str:
    return text[len(text.rstrip()):]


def _restore_quote_style(text: str, original: str) -> str:
    """Map quote marks the model introduced back to the style the original uses."""
    has_straight = '"' in original
    has_curly = "“" in original or "”" in original
    has_corner = "「" in original or "」" in original

    if has_straight and not has_curly and not has_corner:
        return re.sub("[“”「」]", '"', text)
    if has_corner and not has_curly and not has_straight:
        text = text.replace("“", "「").replace("”", "」")
        return _alternate_straight_quotes(text, "「", "」")
    if has_curly and not has_straight and not has_corner:
        text = text.replace("「", "“").replace("」", "”")
        return _alternate_straight_quotes(text, "“", "”")
    return text


def _alternate_straight_quotes(text: str, open_mark: str, close_mark: str) -> str:
    if '"' not in text:
        return text
    out: list[str] = []
    is_open = True
    for char in text:
        if char == '"':
            out.append(open_mark if is_open else close_mark)
            is_open = not is_open
        else:
            out.append(char)
    return "".join(out)


_DELETE = object()


def _guard_line(original_line: str, proposed: str, *, is_script: bool, full_original: str) -> str | object:
    """Fit a proposed line back into the original's format; _DELETE removes the line."""
    content = proposed.strip()
    if content in _DELETE_PLACEHOLDERS:
        content = ""
    stripped_original = original_line.strip()

    is_action_line = stripped_original.startswith("△")
    dialogue_match = _SCRIPT_DIALOGUE_PREFIX_RE.match(stripped_original) if is_script else None

    if is_script and (_SCRIPT_HEADING_RE.match(stripped_original) or not re.search(r"\w", stripped_original)):
        # 场景标题、「人物：」行和「=====」这类分隔行是剧本结构，不接受任何改动。
        return original_line
    if not content:
        # 剧本的 △ 行和台词行只能改写不能删；删了会丢一个镜头或一句台词。
        if is_action_line or dialogue_match:
            return original_line
        return _DELETE

    if is_action_line and not content.startswith("△"):
        content = "△ " + content
    if dialogue_match:
        prefix = dialogue_match.group(0)
        if not content.startswith(prefix):
            spoken = re.split(r"[：:]", content, maxsplit=1)
            rest = spoken[1] if len(spoken) == 2 and len(spoken[0]) <= 16 else content
            content = prefix + rest.lstrip()

    content = _restore_quote_style(content, full_original)
    return _leading_ws(original_line) + content + _trailing_ws(original_line)


def apply_line_edits(original: str, edits: list[LineEdit], *, file_type: str | None = None) -> tuple[str, int]:
    """Apply「原：/改：」pairs to the original; unmentioned lines stay byte-identical.

    Returns ``(text, 0)`` for the existing helper contract. Invalid or ambiguous
    anchors now raise ``NaturalPolishProtocolError`` instead of being dropped.
    """
    is_script = file_type == FILE_TYPE_SCRIPT
    lines = original.split("\n")
    current: list[str | object] = list(lines)
    keys = [normalize_for_noop_comparison(line) for line in lines]
    replaced: set[int] = set()
    restored = 0
    seen_substring_anchors: set[str] = set()

    for edit in edits:
        old_line = edit.old.strip()
        old_key = normalize_for_noop_comparison(old_line)
        if not old_key:
            raise NaturalPolishProtocolError(
                "empty normalized OLD anchor",
                category="missing_anchor",
            )
        # Marker parsing may consume one boundary space; indentation is not identity.
        # Internal whitespace and quote styles still distinguish complete lines.
        # Count all original lines so a previous edit cannot resolve a genuine ambiguity.
        matching_lines = [i for i, line in enumerate(lines) if line.strip() == old_line]
        if not matching_lines:
            matching_lines = [i for i, key in enumerate(keys) if key == old_key]
        if len(matching_lines) > 1:
            raise NaturalPolishProtocolError(
                "ambiguous OLD anchor",
                category="ambiguous_anchor",
            )
        if matching_lines:
            index = matching_lines[0]
            if index in replaced:
                raise NaturalPolishProtocolError(
                    "duplicate OLD anchor",
                    category="duplicate_anchor",
                )
            replaced.add(index)
            guarded = _guard_line(lines[index], edit.new, is_script=is_script, full_original=original)
            try:
                current[index], count = _keep_protected_sentences(lines[index], guarded)
            except Exception as error:
                raise _NaturalPolishContentProtectionError from error
            restored += count
            continue

        if old_key in seen_substring_anchors:
            raise NaturalPolishProtocolError(
                "duplicate OLD anchor",
                category="duplicate_anchor",
            )
        seen_substring_anchors.add(old_key)

        # 模型只抄了一句而不是整行：在唯一包含这句话的行里做子串替换。
        # 引号映射是一字符换一字符，按映射后的位置切原串不会错位，
        # 所以模型抄的时候顺手换了引号也能对上。
        needle = edit.old.strip().translate(_QUOTE_TRANSLATION)
        candidates: list[tuple[int, int]] = []
        for i, line in enumerate(current):
            if not isinstance(line, str) or not needle:
                continue
            normalized_line = line.translate(_QUOTE_TRANSLATION)
            occurrences = normalized_line.count(needle)
            candidates.extend((i, occurrence) for occurrence in range(occurrences))
        if len(candidates) != 1:
            reason = "unmatched" if not candidates else "ambiguous"
            raise NaturalPolishProtocolError(
                f"{reason} OLD anchor",
                category="missing_anchor" if reason == "unmatched" else "ambiguous_anchor",
            )
        index = candidates[0][0]
        working = current[index]
        assert isinstance(working, str)
        position = working.translate(_QUOTE_TRANSLATION).find(needle)
        proposed = working[:position] + edit.new.strip() + working[position + len(needle):]
        guarded = _guard_line(lines[index], proposed, is_script=is_script, full_original=original)
        # 和原行（不是这一行已经改过的版本）比，同一行先后几处改动合起来删掉的句子也能找回。
        try:
            current[index], count = _keep_protected_sentences(lines[index], guarded)
        except Exception as error:
            raise _NaturalPolishContentProtectionError from error
        restored += count

    if restored:
        log_with_context(
            logger,
            30,  # WARNING
            "Natural polish restored sentences the model dropped",
            restored=restored,
            total=len(edits),
        )

    result: list[str] = []
    for i, line in enumerate(current):
        if line is _DELETE:
            # 整行删掉时连带去掉它后面多出来的空行，免得留下连续两个空段。
            continue
        if not isinstance(line, str):
            continue
        if not line.strip() and result and not result[-1].strip() and i > 0 and current[i - 1] is _DELETE:
            continue
        result.append(line)

    original_ends_blank = bool(lines) and not lines[-1].strip()
    while result and not result[-1].strip() and not original_ends_blank:
        result.pop()
    while result and not result[0].strip() and lines and lines[0].strip():
        result.pop(0)
    return "\n".join(result), 0


def _keep_protected_sentences(original_line: str, guarded: str | object) -> tuple[str | object, int]:
    """Run the sentence guard on a line the format guard produced (``_DELETE`` = line deleted)."""
    new_line = "" if guarded is _DELETE else guarded
    assert isinstance(new_line, str)
    line, restored = guard_line_sentences(original_line, new_line)
    return (line, restored) if restored else (guarded, 0)


# ---------------------------------------------------------------------------
# 本次检查提示：定位可能需要复核的表达
# ---------------------------------------------------------------------------
# 词表只能定位候选表达，是否需要修改仍由模型结合选区语境判断。

_STOCK_PHRASE_PATTERNS_ZH = [
    re.compile(p)
    for p in (
        r"几不可察",
        r"唇角.{0,6}?勾",
        r"嘴角.{0,6}?(?:勾|弧度)",
        r"眼底.{0,3}?寒(?:光|意)",
        r"眸光一闪",
        r"指节泛白",
        r"心中一震",
        r"心头一紧",
        r"空气仿佛凝固",
        r"倒吸一口凉气",
        r"一字一顿",
    )
]
_STOCK_PHRASE_PATTERNS_EN = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"barely perceptible",
        r"corner of (?:his|her|their|my) mouth (?:quirked|twitched|curled)",
        r"knuckles (?:turning|turned|went|white)",
        r"cold glint",
        r"heart lurched",
        r"breath (?:she|he|they|I) didn't know",
        r"air seemed to freeze",
    )
]
# 顿号连起来、同一个字打头的三连短语，例如「最冷、最长、最难熬」。
_TRIPLET_ZH_RE = re.compile(
    r"([一-鿿])[一-鿿]{0,4}、\1[一-鿿]{0,4}、\1[一-鿿]{0,4}"
)
_HINT_LINE_MAX_CHARS = 300


def build_attention_hints(selected_text: str, language: str, file_type: str | None = None) -> str:
    """Locate candidate phrases (and, in Chinese, triplets) for contextual review.

    选区的最后一句不再单独点给模型：那条提示让模型把结尾的主题句当成升华句删掉
    （2026-10-09 第二轮审计 N1），也让本来干净的结尾被顺手改写。
    """
    del file_type  # 保留参数，调用方不变；剧本和正文用同一套提示。
    is_english = language.lower().startswith("en")
    patterns = _STOCK_PHRASE_PATTERNS_EN if is_english else _STOCK_PHRASE_PATTERNS_ZH
    hits: list[tuple[str, list[str]]] = []
    triplets: list[tuple[str, list[str]]] = []
    for line in selected_text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > _HINT_LINE_MAX_CHARS:
            continue
        found = [m.group(0) for pattern in patterns for m in pattern.finditer(stripped)]
        if found:
            hits.append((stripped, found))
        if not is_english:
            stacked = [m.group(0) for m in _TRIPLET_ZH_RE.finditer(stripped)]
            if stacked:
                triplets.append((stripped, stacked))

    if not hits and not triplets:
        return ""
    if is_english:
        parts = [
            "Checks for this selection:",
            "These lines contain possible formulaic phrasing. Check its function in context; edit only empty "
            "embellishment or redundant explanation. Preserve meaningful details and unchanged lines; "
            "output OLD:/NEW: pairs only for lines you actually change:",
        ]
        parts.extend(f'- "{line}" — {", ".join(found)}' for line, found in hits)
    else:
        parts = ["本次检查提示："]
        if hits:
            parts.append(
                "下面这些行有可能套式化的表达。先结合语境判断其作用，只改空泛修饰或重复解释，保留有作用的细节；需要修改的行才输出「原：/改：」，不必逐项改掉："
            )
            parts.extend(f"- 「{line}」：{'、'.join(found)}" for line, found in hits)
        if triplets:
            parts.append("下面这些行有三连排比。只合并同义重复，保留各项独立信息；需要时调整连接和节奏，没有问题就保留：")
            parts.extend(f"- 「{line}」：{'；'.join(found)}" for line, found in triplets)
    return "\n\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# 整句删除守卫：模型删掉的有内容的句子，按原位置放回去
# ---------------------------------------------------------------------------
# 第二轮审计（N1）里，模型把「她宁可让我恨她，也不肯让我知道，我恨错了人」当升华句删掉了。
# 提示词已经改成只改写，这里再用代码兜底：逐行按句子对照原文和结果，原文里一句
# 去掉套话后还有 12 个以上汉字（或 12 个以上英文单词）的句子，如果在结果里既找不到
# 对应的句子、内容也没并进同一行的别的句子，就把原句放回原来的位置。

_PROTECTED_MIN_CJK = 12
_PROTECTED_MIN_WORDS = 12
# 改写后的句子至少要有原句（去掉套话后）三分之一的分量，才算「改写」而不是「删到只剩几个字」。
_REWRITE_MIN_SHARE = 1 / 3
_SENTENCE_ENDERS = "。！？!?…"
_SENTENCE_CLOSERS = "”’」』）)】》\"'"
_CJK_CHAR_RE = re.compile(r"[㐀-䶿一-鿿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_NON_WORD_RE = re.compile(r"[\W_]+")


def split_sentences(text: str) -> list[str]:
    """Split one line into sentences; ``"".join(result) == text``.

    句末标点后面的右引号、右括号和空白都算在这一句里。英文句点只有后面是空白、
    行尾或右引号时才断句，避免把小数拆开。
    """
    sentences: list[str] = []
    start = 0
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        is_end = char in _SENTENCE_ENDERS or (
            char == "." and (i + 1 == length or text[i + 1].isspace() or text[i + 1] in _SENTENCE_CLOSERS)
        )
        if not is_end:
            i += 1
            continue
        j = i + 1
        while j < length and (text[j] in _SENTENCE_ENDERS or text[j] == "."):
            j += 1
        while j < length and text[j] in _SENTENCE_CLOSERS:
            j += 1
        while j < length and text[j] in " \t　":
            j += 1
        sentences.append(text[start:j])
        start = i = j
    if start < length:
        sentences.append(text[start:])
    return sentences


def _strip_stock_phrases(sentence: str) -> str:
    spans = [
        m.span()
        for pattern in (*_STOCK_PHRASE_PATTERNS_ZH, *_STOCK_PHRASE_PATTERNS_EN)
        for m in pattern.finditer(sentence)
    ]
    if not spans:
        return sentence
    keep = [True] * len(sentence)
    for begin, end in spans:
        for k in range(begin, end):
            keep[k] = False
    return "".join(char for char, kept in zip(sentence, keep, strict=True) if kept)


def _content_weight(sentence: str) -> int:
    """How much the sentence says once stock phrases are removed (CJK chars, or English words)."""
    rest = _strip_stock_phrases(sentence)
    return max(len(_CJK_CHAR_RE.findall(rest)), len(_LATIN_WORD_RE.findall(rest)))


def is_protected_sentence(sentence: str) -> bool:
    """A sentence the polish may rewrite but never drop: it still says something without its stock phrases."""
    rest = _strip_stock_phrases(sentence)
    return len(_CJK_CHAR_RE.findall(rest)) >= _PROTECTED_MIN_CJK or len(_LATIN_WORD_RE.findall(rest)) >= _PROTECTED_MIN_WORDS


def _compare_key(text: str) -> str:
    return _NON_WORD_RE.sub("", text.translate(_QUOTE_TRANSLATION)).lower()


def _shared_chars(a: str, b: str) -> int:
    """Characters of ``a`` found in ``b`` in order, counting only runs of two or more."""
    if not a or not b:
        return 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks() if block.size >= 2)


def _is_same_sentence(original_key: str, new_key: str) -> bool:
    """The new sentence is the original one edited in place (trimmed, joined, a phrase swapped)."""
    shared = _shared_chars(original_key, new_key)
    if not shared:
        return False
    return shared >= 0.4 * len(original_key) or (shared >= 6 and shared >= 0.8 * len(new_key))


def restore_dropped_sentences(original_sentences: list[str], new_sentences: list[str]) -> tuple[list[str], int]:
    """Put back protected original sentences that the rewrite dropped.

    Returns (sentences, restored). Unprotected sentences (short ones, or ones made of stock
    phrases) may still be dropped, and a protected sentence may be rewritten in other words
    as long as something of comparable weight stands in its place.
    """
    original_keys = [_compare_key(s) for s in original_sentences]
    new_keys = [_compare_key(s) for s in new_sentences]
    whole_new_key = "".join(new_keys)

    # 每个新句子认领和它最像的原句；认不出的新句子给一个独有的负编号（视为改写出来的新句）。
    new_ids: list[int] = []
    for j, new_key in enumerate(new_keys):
        best, best_shared = -1 - j, 0
        for i, original_key in enumerate(original_keys):
            if original_key and _is_same_sentence(original_key, new_key):
                shared = _shared_chars(original_key, new_key)
                if shared > best_shared:
                    best, best_shared = i, shared
        new_ids.append(best)

    def lost(i: int) -> bool:
        if not is_protected_sentence(original_sentences[i]):
            return False
        # 内容并进了同一行的别的句子（例如两句被合成一句），不算丢。
        return _shared_chars(original_keys[i], whole_new_key) < 0.5 * len(original_keys[i])

    result: list[str] = []
    restored = 0
    matcher = difflib.SequenceMatcher(None, list(range(len(original_sentences))), new_ids, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "insert"):
            result.extend(new_sentences[j1:j2])
            continue
        if tag == "delete":
            for i in range(i1, i2):
                if lost(i):
                    result.append(original_sentences[i])
                    restored += 1
            continue
        # replace：原句按顺序和改写出来的新句一一对应；新句太短、或者没有新句顶替时放回原句。
        for k in range(max(i2 - i1, j2 - j1)):
            i = i1 + k if k < i2 - i1 else None
            j = j1 + k if k < j2 - j1 else None
            if i is not None and lost(i):
                weight = _content_weight(original_sentences[i])
                if j is None or _content_weight(new_sentences[j]) < weight * _REWRITE_MIN_SHARE:
                    result.append(original_sentences[i])
                    restored += 1
                    continue
            if j is not None:
                result.append(new_sentences[j])
    return result, restored


def guard_line_sentences(original_line: str, new_line: str) -> tuple[str, int]:
    """Apply ``restore_dropped_sentences`` to one line; an empty ``new_line`` means the line was deleted.

    Returns (line, restored). When nothing is restored ``new_line`` comes back untouched;
    otherwise the result keeps the original line's leading and trailing whitespace.
    """
    original_core = original_line.strip()
    new_core = new_line.strip()
    if not original_core or original_core == new_core:
        return new_line, 0
    # 「角色：」「△ 」这类行首标记两边都有时先拿掉，放回句子时就不会多出第二个。
    prefix = ""
    marker = _SCRIPT_DIALOGUE_PREFIX_RE.match(original_core)
    if marker and new_core.startswith(marker.group(0)):
        prefix = marker.group(0)
    elif original_core.startswith("△") and new_core.startswith("△"):
        prefix = "△"
    original_body = original_core[len(prefix):].lstrip()
    new_body = new_core[len(prefix):].lstrip() if new_core else ""
    if prefix == "△":
        prefix = original_core[: len(original_core) - len(original_body)]
    sentences, restored = restore_dropped_sentences(
        split_sentences(original_body), split_sentences(new_body) if new_body else []
    )
    if not restored:
        return new_line, 0
    body = join_sentences(sentences).strip()
    return _leading_ws(original_line) + prefix + body + _trailing_ws(original_line), restored


# 英文句末标点（可带右引号、右括号），且标点前不是汉字；下一句以拉丁字母或数字开头（可带左引号、左括号）。
_SENTENCE_CLOSERS = "\"'”’)]"
_SENTENCE_TERMINALS = ".!?…"


def _is_cjk_char(char: str) -> bool:
    return "\u3400" <= char <= "\u4dbf" or "\u4e00" <= char <= "\u9fff"


def _ends_latin_sentence(text: str) -> bool:
    """True when ``text`` ends with sentence punctuation (plus optional closing quotes or
    brackets) that is not attached to a CJK character. A backward linear scan: an
    end-anchored regex search here is quadratic on long runs of punctuation."""
    index = len(text)
    while index > 0 and text[index - 1] in _SENTENCE_CLOSERS:
        index -= 1
    run_start = index
    while run_start > 0 and text[run_start - 1] in _SENTENCE_TERMINALS:
        run_start -= 1
    if run_start == index:
        return False
    return run_start == 0 or not _is_cjk_char(text[run_start - 1])
_LATIN_SENTENCE_START_RE = re.compile(r"^[\"'“‘(\[]*[A-Za-z0-9]")


def join_sentences(sentences: list[str]) -> str:
    """Join sentences back into one line.

    改写结果的最后一句没有句末空白，原句放回到它后面时会和它粘在一起。英文这类用空格
    隔开句子的文字，在两句都没有空白的拉丁边界补一个空格；中文边界照旧直接相连。
    """
    out: list[str] = []
    for sentence in sentences:
        if not sentence:
            continue
        if out:
            previous = out[-1]
            if (
                not previous[-1].isspace()
                and not sentence[0].isspace()
                and _ends_latin_sentence(previous)
                and _LATIN_SENTENCE_START_RE.match(sentence)
            ):
                out.append(" ")
        out.append(sentence)
    return "".join(out)


@dataclass
class NaturalPolishResult:
    """Natural polish result payload."""

    polished_text: str
    model: str | None = None


class NaturalPolishService:
    """Single-round natural polish generation service."""

    @staticmethod
    def _resolve_prompt(language: str, file_type: str | None = None) -> str:
        is_english = language.lower().startswith("en")
        prompt = DEFAULT_NATURAL_POLISH_PROMPT_EN if is_english else DEFAULT_NATURAL_POLISH_PROMPT_ZH
        if file_type == FILE_TYPE_SCRIPT:
            script_rules = SCRIPT_NATURAL_POLISH_RULES_EN if is_english else SCRIPT_NATURAL_POLISH_RULES_ZH
            prompt = f"{prompt}\n\n{script_rules}"
        return prompt

    async def natural_polish(
        self,
        *,
        selected_text: str,
        language: str,
        file_type: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> NaturalPolishResult:
        """Generate polished text with a single non-streaming LLM call."""
        llm_client = get_llm_client()
        model_name = getattr(llm_client, "MODEL_QUALITY", None)
        # 静态提示词在前、本次提示在后，前缀缓存仍然命中。
        prompt = self._resolve_prompt(language, file_type) + build_attention_hints(
            selected_text, language, file_type
        )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": selected_text},
        ]

        try:
            raw_output = await llm_client.acomplete(
                messages=messages,
                model=model_name,
                max_tokens=NATURAL_POLISH_MAX_TOKENS,
                thinking_enabled=False,
                usage_attribution=LLMUsageAttribution(
                    user_id=user_id,
                    source=LLM_USAGE_SOURCE_POLISH,
                    project_id=project_id,
                ),
            )
        except APIException:
            raise
        except Exception as error:
            raise NaturalPolishFailure(
                stage="model_call",
                category="model_call_failed",
            ) from error

        try:
            edits = parse_line_edits(raw_output)
        except NaturalPolishProtocolError as error:
            raise NaturalPolishFailure(
                stage="protocol_parse",
                category=error.category,
            ) from error

        try:
            polished_text, _ = apply_line_edits(selected_text, edits, file_type=file_type)
        except NaturalPolishProtocolError as error:
            raise NaturalPolishFailure(
                stage="anchor_apply",
                category=error.category,
            ) from error
        except _NaturalPolishContentProtectionError as error:
            raise NaturalPolishFailure(
                stage="content_protection",
                category="content_protection_failed",
            ) from error
        except Exception as error:
            raise NaturalPolishFailure(
                stage="anchor_apply",
                category="anchor_apply_failed",
            ) from error

        return NaturalPolishResult(
            polished_text=polished_text,
            model=model_name,
        )


natural_polish_service = NaturalPolishService()
