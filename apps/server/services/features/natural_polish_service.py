"""
Natural polish service (single-round rewrite).

This service performs a lightweight, non-streaming rewrite for selected text,
without invoking the full agent workflow.

Prompt ownership is server-side only so every client uses the same prompt source.

模型不再整段重写，而是只列出要改的行（「原：/改：」成对）。服务端把这些改动套回原文，
没提到的行逐字节保留，所以段首缩进、换行、剧本的 △/场景标题/台词行格式由代码保证，
不靠模型自觉。
"""

import re
from dataclasses import dataclass

from agent.core.llm_client import get_llm_client
from models.file_model import FILE_TYPE_SCRIPT
from models.llm_usage import LLM_USAGE_SOURCE_POLISH
from services.usage.llm_usage_service import LLMUsageAttribution
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

DEFAULT_NATURAL_POLISH_PROMPT_ZH = """
你是一位中文小说编辑。任务是给用户发来的「选中的文本」去AI味：只改一眼就能看出是 AI 写的地方，其余的字一个都不动。

做法：先逐句对照下面六类 AI 腔检查，每一处命中都要改掉；没命中的句子一个字都不动。

1. 升华式收尾句：在段落或全文末尾替读者总结、点题、下判断的句子，例如「那一刻，他终于明白了……」「原来，最深的爱，往往藏在最笨拙的沉默里」「这，才是她最珍贵的东西」。选中文本的最后一两句最容易是这种句子，必须单独检查：只要它在替读者说出道理或结论，就删掉，或换成一个具体的动作、画面或一句对白。
2. 被逗号切碎的句子：一句话被逗号切成好几截，读起来一顿一顿，例如「他，站在那里，很久，没有动」。按正常语序把它连起来。
3. 套话：几不可察、唇角一勾、嘴角勾起一抹弧度、眼底寒光、眸光一闪、指节泛白、心中一震、心头一紧、空气仿佛凝固、倒吸一口凉气、一字一顿。这些词只要出现就必须改：换成贴合这个人物、这个场景的具体描写，或者直接删掉。改完的文字里一个都不能留。
4. 三连排比：连着三个结构相同的短语或短句堆在一起，例如「最冷、最长、最难熬」「她哭了，她笑了，她走了」。只留最有力的一个，或者改成长短不一的说法。
5. 情绪演完又直说：动作、对白已经把情绪演出来了，后面又补一句解释，例如「他很愤怒」「她心里一阵酸楚」「他终于明白，她一直在等他」。删掉这句解释。
6. 同一意象反复出现：同一个比喻、物件或形容词在相邻几段里反复用。保留最好的一处，其余的换掉或删掉。

硬性要求：
- 不改事实：人物、称呼、地名、数字、时间、事件经过、每句对白的意思和说话人都不变；不新增原文没有的情节、设定和信息。
- 按行修改：原文的一行就是一个段落（剧本里就是一行）。只能整行改写或整行删掉，不合并两行，也不把一行拆成几行。
- 引号一个都不换：原文是半角直引号 "" 就保持半角直引号，原文是弯引号 “” 就保持弯引号，原文是「」就保持「」；不要给没有引号的地方加引号。
- 没有命中上面六类的行不要列出来。篇幅和原文相近，不扩写，不加新的描写。

输出格式（必须严格遵守）：
- 每改一行，输出两行：第一行以「原：」开头，后面照抄原文这一整行，一字不差；第二行以「改：」开头，后面写改好的这一整行。要把这一行整行删掉，就让「改：」后面留空。
- 每组之间空一行。没改的行不要输出。
- 如果一处都不需要改，只输出：无改动
- 不要输出任何解释、说明、标题、编号或其他文字，不要调用工具。
""".strip()

SCRIPT_NATURAL_POLISH_RULES_ZH = """
这段文字是短剧剧本，剧本格式一律不动：
- 以「△」开头的动作、画面行：保留行首的「△」，不能整行删掉，只能改这一行里的 AI 腔；
- 场景标题行（例如「第3集 重逢」「【场2】医院走廊 · 夜 · 内」）和「人物：」行不要列出来，一个字都不改；
- 「角色：台词」「角色（神态）：台词」格式的台词行：角色名、括号和冒号原样保留，不能整行删掉，只能改冒号后面台词里的 AI 腔；
- 不把动作行改成台词，也不把台词改成动作行或叙述。
""".strip()

DEFAULT_NATURAL_POLISH_PROMPT_EN = """
You are a fiction editor. Your job is to remove the AI tone from the user's "selected text": change only the spots that obviously read as AI-written and leave every other word exactly as it is.

Method: check every sentence against the six kinds of AI tone below and fix every hit; leave each sentence that has no hit exactly as it is.

1. Moralizing closers: a summary, lesson or verdict at the end of a paragraph or passage that spells out the meaning for the reader ("In that moment, he finally understood...", "Love, it turned out, had been hiding in the silence all along"). The last one or two sentences of the selection are the most likely place for one, so check them separately. Delete it, or replace it with a concrete action, image or line of dialogue.
2. Sentences chopped up by commas into stuttering fragments ("He, standing there, did not move"). Join them back into a normal sentence.
3. Stock phrases: "a barely perceptible smile", "the corner of his mouth quirked", "a cold glint in her eyes", "knuckles turning white", "her heart lurched", "the air seemed to freeze", "a breath she didn't know she was holding", "every word deliberate", and the like. Every one of them must go: replace it with a concrete detail that fits this character and scene, or cut it. None may remain.
4. Triplets: three parallel phrases or short sentences stacked together. Keep the strongest one, or vary the rhythm.
5. Emotion shown and then told: the action or dialogue already shows the feeling, and a following sentence explains it anyway ("He was furious." "She felt a wave of sadness."). Delete the explanation.
6. A repeated image: the same metaphor, object or adjective used again and again within a few paragraphs. Keep the best instance and change or cut the rest.

Hard requirements:
- Do not change facts: characters, names, places, numbers, timeline, events, and the meaning and speaker of every line of dialogue stay the same. Add no plot, setting or information that is not in the original.
- Edit line by line: each line of the original is one paragraph (or one script line). Rewrite or delete whole lines only; never merge two lines or split one line into several.
- Do not change a single quotation mark: straight quotes stay straight, curly quotes stay curly; do not add quotes where there are none.
- Write in the same language as the original.
- Do not list lines with no hit. Keep the length close to the original; do not expand it.

Output format (follow it exactly):
- For each line you change, output two lines: the first starts with "OLD:" followed by that entire original line copied character for character; the second starts with "NEW:" followed by the entire revised line. To delete the line, leave "NEW:" empty.
- Put a blank line between pairs. Do not output unchanged lines.
- If nothing needs changing, output only: NO CHANGES
- Output nothing else: no explanations, notes, headings or numbering. Do not call tools.
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

_EDIT_OLD_RE = re.compile(r"^\s*(?:原|OLD)\s*[:：]\s?(.*)$", re.IGNORECASE)
_EDIT_NEW_RE = re.compile(r"^\s*(?:改|NEW)\s*[:：]\s?(.*)$", re.IGNORECASE)
_NO_CHANGE_RE = re.compile(r"^\W*(无改动|没有改动|无需改动|NO\s+CHANGES?)\W*$", re.IGNORECASE)
_DELETE_PLACEHOLDERS = {"（删除）", "(删除)", "删除", "（删去）", "[delete]", "(delete)", "[deleted]"}

_SCRIPT_HEADING_RE = re.compile(r"^(?:【[^】]*】|第\s*[0-9一二三四五六七八九十百]+\s*[集场幕]|人物\s*[:：]|Characters?\s*:|Episode\s+\d+)", re.IGNORECASE)
_SCRIPT_DIALOGUE_PREFIX_RE = re.compile(r"^([^\s△：:（(]{1,12}(?:[（(][^）)]*[）)])?)\s*([：:])")


@dataclass(frozen=True)
class LineEdit:
    """One「原：/改：」pair returned by the model."""

    old: str
    new: str


def parse_line_edits(output: str) -> list[LineEdit] | None:
    """Parse the model's edit list.

    Returns the edits ([] for an explicit「无改动」), or None when the output does not
    follow the protocol at all (the caller then treats it as a full rewrite).
    """
    edits: list[LineEdit] = []
    old: str | None = None
    new_parts: list[str] | None = None
    new_closed = False

    def flush() -> None:
        nonlocal old, new_parts, new_closed
        if old is not None and new_parts is not None:
            edits.append(LineEdit(old=old, new="".join(new_parts)))
        old, new_parts, new_closed = None, None, False

    for raw in output.splitlines():
        old_match = _EDIT_OLD_RE.match(raw)
        if old_match:
            flush()
            old = old_match.group(1)
            continue
        new_match = _EDIT_NEW_RE.match(raw)
        if new_match and old is not None and new_parts is None:
            new_parts = [new_match.group(1)]
            continue
        if new_parts is not None and not new_closed:
            if raw.strip():
                # 「改：」后面意外换了行：拼回同一行，绝不让一行变成两段。
                new_parts.append(raw.strip())
            else:
                new_closed = True
    flush()

    if edits:
        return edits
    if _NO_CHANGE_RE.match(output.strip()):
        return []
    return None


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

    Returns (text, dropped) where dropped counts edits that could not be anchored.
    """
    is_script = file_type == FILE_TYPE_SCRIPT
    lines = original.split("\n")
    current: list[str | object] = list(lines)
    keys = [normalize_for_noop_comparison(line) for line in lines]
    replaced: set[int] = set()
    dropped = 0

    for edit in edits:
        old_key = normalize_for_noop_comparison(edit.old)
        if not old_key:
            dropped += 1
            continue
        index = next(
            (i for i, key in enumerate(keys) if key == old_key and i not in replaced and current[i] is not _DELETE),
            None,
        )
        if index is not None:
            replaced.add(index)
            current[index] = _guard_line(lines[index], edit.new, is_script=is_script, full_original=original)
            continue

        # 模型只抄了一句而不是整行：在唯一包含这句话的行里做子串替换。
        # 引号映射是一字符换一字符，按映射后的位置切原串不会错位，
        # 所以模型抄的时候顺手换了引号也能对上。
        needle = edit.old.strip().translate(_QUOTE_TRANSLATION)
        candidates = [
            i
            for i, line in enumerate(current)
            if isinstance(line, str) and needle and needle in line.translate(_QUOTE_TRANSLATION)
        ]
        if len(candidates) != 1:
            dropped += 1
            continue
        index = candidates[0]
        working = current[index]
        assert isinstance(working, str)
        position = working.translate(_QUOTE_TRANSLATION).find(needle)
        proposed = working[:position] + edit.new.strip() + working[position + len(needle):]
        current[index] = _guard_line(lines[index], proposed, is_script=is_script, full_original=original)

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
    return "\n".join(result), dropped


def apply_full_rewrite(original: str, rewritten: str, *, file_type: str | None = None) -> str:
    """Fallback when the model ignored the edit protocol and returned the whole text.

    With the same line count, each changed line goes through the same format guard;
    otherwise the rewrite is kept as-is apart from the quote style.
    """
    is_script = file_type == FILE_TYPE_SCRIPT
    original_lines = original.split("\n")
    rewritten_lines = rewritten.strip("\n").split("\n")
    if len(original_lines) != len(rewritten_lines):
        return _restore_quote_style(rewritten, original)

    result: list[str] = []
    for original_line, rewritten_line in zip(original_lines, rewritten_lines, strict=True):
        if rewritten_line == original_line or (not original_line.strip() and not rewritten_line.strip()):
            result.append(original_line)
            continue
        guarded = _guard_line(original_line, rewritten_line, is_script=is_script, full_original=original)
        result.append(original_line if guarded is _DELETE else guarded)  # type: ignore[arg-type]
    return "\n".join(result)


# ---------------------------------------------------------------------------
# 本次检查提示：把套话所在的行和最后一句直接点给模型
# ---------------------------------------------------------------------------
# 关闭思考、温度 1.0 时，模型对清单的召回不稳定：同一段剧本两次请求，一次改掉
# 「指节泛白」，一次漏掉。套话能确定性地找出来，就别让模型自己找。

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
_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?…])|(?<=\.)\s")
_CLOSER_HINT_MAX_CHARS = 80
_CLOSER_HINT_MAX_CHARS_EN = 200
_HINT_LINE_MAX_CHARS = 300


def _last_sentence(text: str) -> str:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    # 丢掉只剩标点的碎片（例如句号后面单独的右引号）。
    sentences = [s.strip() for s in _SENTENCE_END_RE.split(lines[-1]) if s and re.search(r"\w", s)]
    return sentences[-1] if sentences else lines[-1]


def build_attention_hints(selected_text: str, language: str, file_type: str | None = None) -> str:
    """Point the model at the lines that contain stock phrases and at the closing sentence."""
    is_english = language.lower().startswith("en")
    patterns = _STOCK_PHRASE_PATTERNS_EN if is_english else _STOCK_PHRASE_PATTERNS_ZH
    hits: list[tuple[str, list[str]]] = []
    for line in selected_text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > _HINT_LINE_MAX_CHARS:
            continue
        found = [m.group(0) for pattern in patterns for m in pattern.finditer(stripped)]
        if found:
            hits.append((stripped, found))

    closer = "" if file_type == FILE_TYPE_SCRIPT else _last_sentence(selected_text)
    if len(closer) > (_CLOSER_HINT_MAX_CHARS_EN if is_english else _CLOSER_HINT_MAX_CHARS):
        closer = ""

    if not hits and not closer:
        return ""
    if is_english:
        parts = ["Checks for this selection:"]
        if hits:
            parts.append("These lines contain stock phrases from the list. Fix every one (output OLD:/NEW: for each line):")
            parts.extend(f'- "{line}" — {", ".join(found)}' for line, found in hits)
        if closer:
            parts.append(
                f'The selection ends with: "{closer}". Decide on its own whether it is a moralizing closer; '
                "if it is, delete or rewrite it, otherwise leave it."
            )
    else:
        parts = ["本次检查提示："]
        if hits:
            parts.append("下面这些行含有清单里的套话，每一处都必须改掉（每行都输出一组「原：/改：」）：")
            parts.extend(f"- 「{line}」：{'、'.join(found)}" for line, found in hits)
        if closer:
            parts.append(f"选中文本的最后一句是：「{closer}」。单独判断它是不是升华式收尾句：是就删掉或改写，不是就不动。")
    return "\n\n" + "\n".join(parts)


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

        edits = parse_line_edits(raw_output)
        if edits is None:
            polished_text = apply_full_rewrite(selected_text, raw_output, file_type=file_type)
        else:
            polished_text, dropped = apply_line_edits(selected_text, edits, file_type=file_type)
            if dropped:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Natural polish dropped edits that did not match the selection",
                    dropped=dropped,
                    total=len(edits),
                )

        return NaturalPolishResult(
            polished_text=polished_text,
            model=model_name,
        )


natural_polish_service = NaturalPolishService()
