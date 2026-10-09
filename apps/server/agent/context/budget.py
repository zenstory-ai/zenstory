"""
Token budget management for context assembly.

Handles token estimation, budget allocation, and content truncation
to fit within prompt limits.
"""

from collections.abc import Callable
from typing import Any

from agent.utils.token_utils import _chars_per_token_for, estimate_text_tokens

from ..schemas.context import ContextItem, ContextPriority

# Shared prompt-token ledger ceiling for a single request's input side.
#
# 组装上下文（AGENT_CONTEXT_TOKEN_BUDGET，默认 32k）与聊天历史
# （AGENT_CHAT_HISTORY_TOKEN_BUDGET，默认 32k）共用这一个台账：已知的 prompt 成本
# （系统提示 + 技能目录/正文 + 组装上下文 + 跨轮工作集）先从上限里扣掉，历史窗口
# 只能用剩下的额度。
#
# deepseek-flash 的上下文窗口是 1M token，且有自动前缀缓存（命中价约为未命中的
# 1/50）。上限定在 160k：远低于模型窗口、给本轮工具结果留足空间，同时明显高于
# 「系统提示 + 32k 上下文 + 32k 历史」的常规总量，所以台账只在其他 prompt 成本
# 真的很大时才收缩历史，绝不放大历史预算。旧值 24k（按 6k 预算定的）会把上下文
# 与历史压到模型只能靠反复 query_files 重读来补，重读的成本远高于多带的前缀。
DEFAULT_PROMPT_TOKEN_LEDGER_CEILING = 160000

# Always leave at least this many tokens for chat history so a large context
# block can never starve history to zero (which would erase cross-turn memory).
MIN_HISTORY_TOKEN_FLOOR = 512

# 裁剪长文本时追加的提示后缀，让模型知道这里被截断过而不是原文如此。
TRUNCATION_SUFFIX = "…（内容过长已截断）"

# CRITICAL 档预算不足时，向下位档借用后必须给它们留下的地板比例
# （相对各自 min(名义份额, 真实需求)）。
LOWER_TIER_FLOOR_RATIO = 0.5

# CRITICAL 档内每个条目的保底 token 数：预算紧张时宁可每条都截断，
# 也不让用户显式附加/引用的条目整条消失。
MIN_CRITICAL_ITEM_TOKENS = 200

# 非 CRITICAL 档截断后显示的正文少于这么多字、且不到原文一半时，视为「残片」
# 直接丢弃：几百字的章节开头或半张角色卡帮不上忙，反而占预算、诱导模型
# 以为这就是设定原文。被丢弃的条目仍在文件清单里（带 id），需要时按 id 读取；
# 不再出现在检索排除名单里，混合检索还能把相关片段捞回来。
# CRITICAL 档（焦点 / 用户附加 / 引用）不受影响，那里宁可截断也不丢。
MIN_TRUNCATED_ITEM_CHARS = 500


def truncate_text_to_tokens(
    text: str,
    max_tokens: int,
    suffix: str = TRUNCATION_SUFFIX,
) -> str:
    """把一段纯文本裁剪到 ``max_tokens`` 以内（含后缀开销）。

    与 :meth:`TokenBudget.truncate_item` 的区别：那个方法处理的是带标题的
    ContextItem，这里处理的是拼进 header 的裸文本（项目简介 / 备注等）。
    两者共用同一套「语言感知 chars/token 比例 + 收缩校验」策略，
    避免中文按 4 chars/token 估算时留下约 2 倍超额的字符。

    Args:
        text: 待裁剪文本。
        max_tokens: 允许占用的最大 token 数。
        suffix: 截断提示后缀。

    Returns:
        裁剪后的文本；本来就在预算内时原样返回。
    """
    if not text:
        return text

    max_tokens = max(0, int(max_tokens or 0))
    if max_tokens <= 0:
        return ""

    if estimate_text_tokens(text) <= max_tokens:
        return text

    cpt = _chars_per_token_for(text)
    body_budget = max_tokens - estimate_text_tokens(suffix)
    if body_budget <= 0:
        # 连后缀都放不下：按字符比例硬裁，不再追加后缀
        return text[: max(1, int(max_tokens * cpt))]

    body = text[: max(1, int(body_budget * cpt))]

    # 估算器与启发式比例可能不一致，收缩到真正落在预算内为止
    guard = 0
    while body and estimate_text_tokens(body) > body_budget and guard < 40:
        body = body[: max(1, int(len(body) * 0.85))]
        guard += 1

    return body.rstrip() + suffix


def compute_history_token_budget(
    *,
    configured_history_budget: int,
    reserved_prompt_tokens: int,
    ceiling: int = DEFAULT_PROMPT_TOKEN_LEDGER_CEILING,
    min_history_floor: int = MIN_HISTORY_TOKEN_FLOOR,
) -> int:
    """
    Compute the chat-history token budget under a single shared prompt ledger.

    The history window must share one ceiling with the other already-known
    prompt costs (system prompt + skill catalog/reference + assembled context),
    instead of being budgeted independently. This returns the smaller of the
    configured history budget and whatever room remains under the ceiling after
    subtracting ``reserved_prompt_tokens``.

    Behavior is intentionally safe:
    - Missing/None inputs are coerced to 0 and never crash.
    - History never exceeds ``configured_history_budget`` (the ledger only
      shrinks history; it never grows it).
    - History never drops below ``min_history_floor`` so a huge context block
      cannot erase cross-turn memory entirely.

    Args:
        configured_history_budget: The independently configured history budget
            (e.g. AGENT_CHAT_HISTORY_TOKEN_BUDGET).
        reserved_prompt_tokens: Tokens already committed to the rest of the
            prompt (system prompt + skill catalog/reference + assembled context).
        ceiling: The shared prompt-token ledger ceiling.
        min_history_floor: Minimum tokens to always reserve for history.

    Returns:
        The effective history token budget under the shared ceiling.
    """
    history_budget = max(0, int(configured_history_budget or 0))
    reserved = max(0, int(reserved_prompt_tokens or 0))
    ceiling = max(0, int(ceiling or 0))
    floor = max(0, int(min_history_floor or 0))

    remaining = ceiling - reserved
    if remaining < floor:
        remaining = floor

    return min(history_budget, remaining)


class TokenBudget:
    """
    Manages token budget for context assembly.

    Features:
    - Token estimation for text
    - Priority-based budget allocation
    - Smart truncation with preserved meaning
    """

    # Default budget allocation percentages
    DEFAULT_ALLOCATION = {
        ContextPriority.CRITICAL: 0.30,
        ContextPriority.CONSTRAINT: 0.35,
        ContextPriority.RELEVANT: 0.25,
        ContextPriority.INSPIRATION: 0.10,
    }

    def __init__(
        self,
        max_tokens: int = 4000,
        allocation: dict[ContextPriority, float] | None = None,
        item_overhead: Callable[[ContextItem], int] | None = None,
    ):
        """
        Initialize budget manager.

        Args:
            max_tokens: Maximum total tokens
            allocation: Custom priority allocation percentages
            item_overhead: 条目渲染时「标题 + 正文」之外的额外 token（如标题行的
                id / [全文] 标注）；提供时计入每个条目的占用与截断预算
        """
        self.max_tokens = max_tokens
        self.allocation = allocation or self.DEFAULT_ALLOCATION
        self.item_overhead = item_overhead
        self.used: dict[ContextPriority, int] = dict.fromkeys(ContextPriority, 0)
        # 本次 select_items 因截断后只剩残片而丢弃的条目（原条目，未截断）
        self.dropped_stubs: list[ContextItem] = []

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.

        Uses simple character-based estimation.
        For more accurate results, use tiktoken.

        Args:
            text: Text to estimate

        Returns:
            Estimated token count
        """
        return estimate_text_tokens(text)

    def estimate_item_tokens(self, item: ContextItem) -> int:
        """
        Estimate tokens for a context item.

        Includes title and content.

        Args:
            item: Context item

        Returns:
            Estimated token count
        """
        return self.estimate_tokens(f"{item.title}\n{item.content}") + self._overhead(item)

    def _overhead(self, item: ContextItem) -> int:
        if self.item_overhead is None:
            return 0
        return max(0, int(self.item_overhead(item) or 0))

    def get_budget(self, priority: ContextPriority) -> int:
        """
        Get token budget for a priority level.

        Args:
            priority: Priority level

        Returns:
            Token budget
        """
        pct = self.allocation.get(priority, 0.1)
        return int(self.max_tokens * pct)

    def get_remaining(self, priority: ContextPriority) -> int:
        """
        Get remaining budget for a priority.

        Args:
            priority: Priority level

        Returns:
            Remaining tokens
        """
        return self.get_budget(priority) - self.used.get(priority, 0)

    def get_total_remaining(self) -> int:
        """Get total remaining budget across all priorities."""
        return self.max_tokens - sum(self.used.values())

    def allocate(
        self,
        priority: ContextPriority,
        tokens: int,
    ) -> bool:
        """
        Allocate tokens from a priority budget.

        Args:
            priority: Priority level
            tokens: Tokens to allocate

        Returns:
            True if allocation succeeded
        """
        remaining = self.get_remaining(priority)
        if tokens <= remaining:
            self.used[priority] = self.used.get(priority, 0) + tokens
            return True
        return False

    def can_fit(
        self,
        item: ContextItem,
        priority: ContextPriority | None = None,
    ) -> bool:
        """
        Check if an item can fit in the budget.

        Args:
            item: Context item
            priority: Priority level (uses item's priority if not specified)

        Returns:
            True if item fits
        """
        priority = priority or item.priority
        tokens = self.estimate_item_tokens(item)
        return tokens <= self.get_remaining(priority)

    def select_items(
        self,
        items: list[ContextItem],
        priority_groups: dict[ContextPriority, list[ContextItem]] | None = None,
    ) -> tuple[list[ContextItem], dict[ContextPriority, int]]:
        """
        Select items that fit within budget.

        Uses priority-based allocation to ensure important
        content is included first.

        Args:
            items: All context items (will be grouped by priority)
            priority_groups: Pre-grouped items (optional)

        Returns:
            Tuple of (selected items, budget usage)
        """
        selected: list[ContextItem] = []
        self.dropped_stubs = []

        # Group items by priority if not provided
        if priority_groups is None:
            priority_groups = {}
            for item in items:
                p = item.priority
                if p not in priority_groups:
                    priority_groups[p] = []
                priority_groups[p].append(item)

        order = ContextPriority.priority_order()

        # Each priority's actual demand (how much it would use if unconstrained).
        demand: dict[ContextPriority, int] = {
            p: sum(self.estimate_item_tokens(it) for it in priority_groups.get(p, []))
            for p in order
        }

        budgets = self._allocate_tier_budgets(demand)

        # Process each priority level in order (highest first).
        for priority in order:
            group_items = priority_groups.get(priority, [])
            budget = budgets[priority]
            used = 0

            for index, item in enumerate(group_items):
                tokens = self.estimate_item_tokens(item)

                # CRITICAL 档内的逐项保底：这一档装的全是用户显式意图
                # （焦点文件 / 手动附加的文件 / 手动选中的引用文本）。
                # 没有保底时靠前的条目会把整档预算吃光，后面的条目**整条消失**，
                # 用户看到的现象是「我明明把第 79 章附上去了，AI 完全没参考」。
                # 因此给组内尚未处理的条目各留 MIN_CRITICAL_ITEM_TOKENS，
                # 宁可让每条都被截断，也不让任何一条被静默丢弃。
                # 预算本身就很小时（不足两条保底）不启用，保持原有行为。
                effective_budget = budget
                if priority == ContextPriority.CRITICAL:
                    pending = len(group_items) - index - 1
                    reserve = min(
                        pending * MIN_CRITICAL_ITEM_TOKENS,
                        max(0, budget - used - MIN_CRITICAL_ITEM_TOKENS),
                    )
                    effective_budget = budget - reserve

                if used + tokens <= effective_budget:
                    selected.append(item)
                    used += tokens
                else:
                    # Try to fit a truncated version
                    remaining = effective_budget - used
                    if remaining > 50:  # Only if meaningful space
                        truncated = self.truncate_item(item, remaining)
                        if truncated and priority != ContextPriority.CRITICAL and self.is_stub(truncated):
                            self.dropped_stubs.append(item)
                        elif truncated:
                            selected.append(truncated)
                            used += self.estimate_item_tokens(truncated)
                    # Keep scanning remaining items in this priority group.
                    # A later item may still fit even if the current one doesn't.
                    continue

            self.used[priority] = used

        return selected, dict(self.used)

    @staticmethod
    def is_stub(item: ContextItem) -> bool:
        """截断后的条目是否只剩残片（见 MIN_TRUNCATED_ITEM_CHARS）。"""
        metadata = item.metadata or {}
        if not metadata.get("truncated"):
            return False
        shown = metadata.get("shown_chars")
        total = metadata.get("original_chars")
        if not isinstance(shown, int) or not isinstance(total, int):
            return False
        return shown < MIN_TRUNCATED_ITEM_CHARS and shown * 2 < total

    def _allocate_tier_budgets(
        self,
        demand: dict[ContextPriority, int],
    ) -> dict[ContextPriority, int]:
        """按各档真实需求分配预算，保证总和不超过 max_tokens。

        CRITICAL is the "must include" tier: it holds the focus file, user
        text quotes, and user-attached files/materials. Previously it was
        hard-capped at its nominal share (30%), so explicitly-attached content
        was truncated or dropped even when the rest of the budget sat empty.

        分配规则：
        1. CRITICAL 先拿「名义份额」与「下位档按 min(份额, 真实需求) 预留后剩下的
           额度」二者的较大值（原有行为）。
        2. 若 CRITICAL 的真实需求仍然装不下，允许它进一步向下位档借用，
           但必须给下位档留下 LOWER_TIER_FLOOR_RATIO 比例的地板——
           用户显式附加的内容应当压过自动收集的 40 条设定，
           但不能把角色/世界观约束整个挤没。
        3. 下位档按名义份额比例分摊 CRITICAL 实际占用后剩余的额度，
           且各自不超过自己的名义份额。
        4. 下位档里没人用到的额度按 CONSTRAINT → RELEVANT → INSPIRATION 的
           顺序补给仍有缺口的档。各档实际占用不超过真实需求，
           因此实际总占用恒 <= max_tokens。
        """
        critical = ContextPriority.CRITICAL
        lower = [p for p in ContextPriority.priority_order() if p != critical]

        reserved_for_lower = sum(min(self.get_budget(p), demand.get(p, 0)) for p in lower)
        critical_budget = max(
            self.get_budget(critical),
            self.max_tokens - reserved_for_lower,
        )

        critical_demand = demand.get(critical, 0)
        if critical_demand > critical_budget:
            soft_floor = sum(
                int(min(self.get_budget(p), demand.get(p, 0)) * LOWER_TIER_FLOOR_RATIO)
                for p in lower
            )
            critical_budget = max(
                critical_budget,
                min(critical_demand, self.max_tokens - soft_floor),
            )

        # 用「实际会被占用的额度」而不是「授权额度」来算剩余，
        # 否则 CRITICAL 需求很小时下位档会被凭空砍掉。
        critical_alloc = min(critical_budget, critical_demand)
        leftover = max(0, self.max_tokens - critical_alloc)
        lower_share_sum = sum(self.allocation.get(p, 0.1) for p in lower) or 1.0

        budgets: dict[ContextPriority, int] = {critical: critical_budget}
        for p in lower:
            share = self.allocation.get(p, 0.1) / lower_share_sum
            budgets[p] = min(self.get_budget(p), int(leftover * share))

        # 二次分配：下位档用不完的额度（需求小于份额的档、以及份额之和本身
        # 小于 leftover 的部分）按档位顺序补给还有缺口的档，CONSTRAINT 优先。
        # 否则长篇续写时 INSPIRATION 空着一截，前一章却被截断。各档实际占用
        # 不超过各自需求，因此实际总占用仍 <= max_tokens。
        slack = leftover - sum(min(budgets[p], demand.get(p, 0)) for p in lower)
        for p in lower:
            if slack <= 0:
                break
            unmet = demand.get(p, 0) - budgets[p]
            if unmet > 0:
                extra = min(unmet, slack)
                budgets[p] += extra
                slack -= extra

        return budgets

    def truncate_item(
        self,
        item: ContextItem,
        max_tokens: int,
    ) -> ContextItem | None:
        """
        Truncate an item to fit within token limit.

        Args:
            item: Item to truncate
            max_tokens: Maximum tokens

        Returns:
            Truncated item or None if too small
        """
        # 渲染开销（标题行的 id / 截断标注）先从可用额度里扣掉
        max_tokens = max_tokens - self._overhead(item)
        if max_tokens < 20:
            return None

        # Reserve tokens for title
        title_tokens = self.estimate_tokens(item.title) + 5  # +5 for formatting
        content_tokens = max_tokens - title_tokens

        if content_tokens < 20:
            return None

        # Truncate content using the SAME language-aware chars/token ratio the
        # estimator uses (~2 chars/token for CJK, ~4 for Latin). A fixed ratio
        # of 4 would keep ~2x too many characters for Chinese content, so the
        # "truncated" item would still overflow the budget it was told to fit.
        cpt = _chars_per_token_for(item.content)
        max_chars = max(1, int(content_tokens * cpt))
        content = item.content[:max_chars]

        # Guard against estimator/heuristic mismatch (tiktoken's true CJK ratio
        # is lower than the heuristic): shrink until it actually measures within
        # the token budget（截断后缀也计入预算）.
        guard = 0
        while (
            content
            and self.estimate_tokens(f"{item.title}\n{content}{TRUNCATION_SUFFIX}") > max_tokens
            and guard < 40
        ):
            content = content[: max(1, int(len(content) * 0.85))]
            guard += 1

        # Try to cut at a sentence or word boundary near the end.
        for sep in ["。", ".", "！", "!", "？", "?", "\n", "，", ",", " "]:
            last_sep = content.rfind(sep)
            if last_sep > len(content) * 0.6:
                content = content[:last_sep + 1]
                break

        content = content.rstrip()
        shown_chars = len(content)
        # 原文总字数：重复截断时保留第一次记录的真实长度。
        original_chars = item.metadata.get("original_chars")
        if not isinstance(original_chars, int) or original_chars < len(item.content):
            original_chars = len(item.content)

        # 不再用裸 "..." 收尾：模型分不清「原文如此」还是「被截断」，只能
        # query_files 重读确认。显式后缀 + 元数据（渲染时转成
        # 「[已截断：显示 x/y 字；全文用 query_files(id=…)]」）告诉它缺了多少、去哪取。
        return ContextItem(
            id=item.id,
            type=item.type,
            title=item.title,
            content=content + TRUNCATION_SUFFIX,
            relevance_score=item.relevance_score,
            priority=item.priority,
            metadata={
                **item.metadata,
                "truncated": True,
                "shown_chars": shown_chars,
                "original_chars": original_chars,
            },
        )

    def get_usage_report(self) -> dict[str, Any]:
        """
        Get a report of budget usage.

        Returns:
            Dict with usage statistics
        """
        return {
            "max_tokens": self.max_tokens,
            "used_tokens": sum(self.used.values()),
            "remaining_tokens": self.get_total_remaining(),
            "by_priority": {
                p.value: {
                    "budget": self.get_budget(p),
                    "used": self.used.get(p, 0),
                    "remaining": self.get_remaining(p),
                }
                for p in ContextPriority
            },
        }
