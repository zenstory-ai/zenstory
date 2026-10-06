"""
Natural polish service (single-round rewrite).

This service performs a lightweight, non-streaming rewrite for selected text,
without invoking the full agent workflow.

Prompt ownership is server-side only so every client uses the same prompt source.
"""

from dataclasses import dataclass

from agent.core.llm_client import get_llm_client
from models.llm_usage import LLM_USAGE_SOURCE_POLISH
from services.usage.llm_usage_service import LLMUsageAttribution

DEFAULT_NATURAL_POLISH_PROMPT_ZH = """
请对下面的「选中的文本」进行自然润色（去 AI 味）。

硬性要求：
1) 只修改必须修改的句子；没有问题的句子尽量保持原句不变。
2) 不改变含义，不新增原文没有的情节/信息/设定，不改变角色关系、时间线与事实。
3) 语言更口语、更像人写，断句更符合朗读习惯，但保持原文的文学感染力。
4) 尽量保留专有名词、人名、地名、数字、章节标题等关键信息。
5) 字数尽量不减少：输出字数不得比原文减少超过 200 字。

输出要求：
- 只输出润色后的正文本身，不要输出任何解释、步骤、列表、标题、标记（包括“【修改后】”之类）。
- 不要调用任何工具，不要修改任何文件，只输出文本。
""".strip()

DEFAULT_NATURAL_POLISH_PROMPT_EN = """
Please rewrite the following "Selected text" to sound more natural and human-written.

Hard requirements:
1) Only change sentences that must be changed; keep good sentences as-is.
2) Do not change meaning or add new plot elements/facts not present in the original.
3) Make it more speakable and conversational, while keeping the original tone.
4) Preserve proper nouns, names, places, numbers, and key terms.
5) Keep length similar: do not shorten by more than 200 characters if possible.

Output:
- Output ONLY the rewritten text. No explanations, headings, or lists.
- Do NOT call tools or modify files. Text only.
""".strip()

NATURAL_POLISH_MAX_TOKENS = 16000


@dataclass
class NaturalPolishResult:
    """Natural polish result payload."""

    polished_text: str
    model: str | None = None


class NaturalPolishService:
    """Single-round natural polish generation service."""

    @staticmethod
    def _resolve_prompt(language: str) -> str:
        return (
            DEFAULT_NATURAL_POLISH_PROMPT_EN
            if language.lower().startswith("en")
            else DEFAULT_NATURAL_POLISH_PROMPT_ZH
        )

    async def natural_polish(
        self,
        *,
        selected_text: str,
        language: str,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> NaturalPolishResult:
        """Generate polished text with a single non-streaming LLM call."""
        llm_client = get_llm_client()
        model_name = getattr(llm_client, "MODEL_QUALITY", None)
        prompt = self._resolve_prompt(language)

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": selected_text},
        ]

        polished_text = await llm_client.acomplete(
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

        return NaturalPolishResult(
            polished_text=polished_text,
            model=model_name,
        )


natural_polish_service = NaturalPolishService()
