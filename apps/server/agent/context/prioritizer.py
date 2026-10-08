"""
Context prioritization based on content type and relevance.

Implements the priority system:
- CRITICAL: Focus content, must include
- CONSTRAINT: Character settings, high-importance lore, previous chapter
- RELEVANT: Retrieved snippets, sibling chapters, related outlines
- INSPIRATION: Low-importance lore, general references
"""


from ..schemas.context import ContextItem, ContextPriority


class ContextPrioritizer:
    """
    Prioritizes context items based on type and relevance.

    Uses the priority system to ensure important context
    is included within token budget.
    """

    def __init__(self):
        """Initialize prioritizer with default rules."""
        pass

    @staticmethod
    def _is_user_attached(item: ContextItem) -> bool:
        """Whether the user explicitly attached/quoted this item."""
        metadata = item.metadata or {}
        return bool(metadata.get("attached") or metadata.get("is_quote"))

    @classmethod
    def _intent_rank(cls, item: ContextItem) -> int:
        """
        Ordering rank inside a priority group: user intent before relevance.

        组内顺序决定预算耗尽时谁被截断/丢弃，因此焦点文件必须排在最前
        （否则 query recall 加成可能把相关度抬到 1.0 以上的其他条目排到
        焦点之前），其次是用户显式附加/引用的内容。
        """
        if item.is_focus:
            return 0
        return 1 if cls._is_user_attached(item) else 2

    @staticmethod
    def _is_chapter_item(item: ContextItem) -> bool:
        """非焦点的章节类条目（前一章 / 兄弟 / 子章节的大纲或正文）。

        父大纲（relation="parent"）承载全书背景，不算在内。
        """
        if item.is_focus or item.type != "outline":
            return False
        return (item.metadata or {}).get("relation") != "parent"

    @classmethod
    def _tier_rank(cls, item: ContextItem, priority: ContextPriority) -> int:
        """
        档内的类型排序：CONSTRAINT 档里角色卡与高重要度设定排在章节之前。

        长篇续写时前一章（以及历史上同档的兄弟章节）动辄数千字，组内按相关度
        排序会让章节（0.8）先于角色卡（0.7）花光这一档的预算，所有角色卡被
        丢弃。章节正文缺了还能按 id 读，角色设定缺了模型往往不会去读，
        设定就开始走样，因此约束类条目先拿预算。
        """
        if priority != ContextPriority.CONSTRAINT:
            return 0
        return 1 if cls._is_chapter_item(item) else 0

    @classmethod
    def _allows_parent_upgrade(cls, items: list[ContextItem]) -> bool:
        """
        父大纲能否升级到 CRITICAL：批次里没有用户显式附加/引用的内容时才可以。

        升级的唯一目的是借用 TokenBudget.select_items 里 CRITICAL 的池化预算；
        但池化额度本来就是留给用户显式附加内容的，一旦两者同处 CRITICAL，
        相关度更高的父大纲会把附加文件截断甚至整体挤出上下文。此时让父大纲
        留在 CONSTRAINT 档，它仍有该档的保底份额。
        """
        return not any(
            cls._is_user_attached(item) and not item.is_focus for item in items
        )

    def classify_priority(
        self,
        item: ContextItem,
        *,
        allow_parent_upgrade: bool = True,
    ) -> ContextPriority:
        """
        Classify the priority of a context item.

        Args:
            item: Context item to classify
            allow_parent_upgrade: Whether relation="parent" outlines may be
                upgraded to CRITICAL (see _allows_parent_upgrade)

        Returns:
            ContextPriority level
        """
        # Focus content is always critical
        if item.is_focus:
            return ContextPriority.CRITICAL

        # 兄弟章节是「只升不降」的唯一例外：它们是按最近修改时间挑出来的，
        # 与本轮任务的关系最弱，留在 CONSTRAINT 会和前一章一起把角色卡、
        # 高重要度设定挤出上下文。工厂方法 from_outline 给所有非焦点章节预设了
        # CONSTRAINT，所以这里必须显式降到 RELEVANT。用户附加的文件 relation
        # 是 "attached"，不受影响。
        if (
            item.type == "outline"
            and (item.metadata or {}).get("relation") == "sibling"
            and not self._is_user_attached(item)
        ):
            return ContextPriority.RELEVANT

        # Type/relation rules may upgrade a preset priority (e.g. parent
        # outlines carry whole-story background and must reach CRITICAL so
        # they can draw on the pooled budget in TokenBudget.select_items),
        # but never downgrade one — user-attached files and retrieval
        # snippets keep the tier the assembler explicitly assigned.
        type_priority = self._classify_by_type(item)

        # CRITICAL 是 _classify_by_type 里唯一的升级目标（relation="parent"）；
        # 拒绝升级时退到 CONSTRAINT（与 previous/child 同档）而不是回落到预设，
        # 以保持"只升不降"不变式。
        if not allow_parent_upgrade and type_priority == ContextPriority.CRITICAL:
            type_priority = ContextPriority.CONSTRAINT

        order = ContextPriority.priority_order()
        if order.index(type_priority) < order.index(item.priority):
            return type_priority
        return item.priority

    def _classify_by_type(self, item: ContextItem) -> ContextPriority:
        """Classify based on item type and metadata."""
        item_type = item.type

        # Outlines
        if item_type == "outline":
            relation = item.metadata.get("relation", "")
            if relation == "parent":
                return ContextPriority.CRITICAL
            elif relation in ("child", "previous"):
                return ContextPriority.CONSTRAINT
            return ContextPriority.RELEVANT

        # Characters are always constraints
        if item_type == "character":
            return ContextPriority.CONSTRAINT

        # Lore depends on importance
        if item_type == "lore":
            importance = item.metadata.get("importance", "low")
            if importance == "high":
                return ContextPriority.CONSTRAINT
            elif importance == "medium":
                return ContextPriority.RELEVANT
            return ContextPriority.INSPIRATION

        # Snippets depend on relevance score
        if item_type == "snippet":
            if item.relevance_score and item.relevance_score > 0.7:
                return ContextPriority.RELEVANT
            elif item.relevance_score and item.relevance_score > 0.4:
                return ContextPriority.INSPIRATION
            return ContextPriority.INSPIRATION

        # Default
        return ContextPriority.INSPIRATION

    def prioritize(
        self,
        items: list[ContextItem],
    ) -> list[ContextItem]:
        """
        Sort items by priority and relevance.

        Args:
            items: List of context items

        Returns:
            Sorted list (highest priority first)
        """
        # Assign priorities
        allow_parent_upgrade = self._allows_parent_upgrade(items)
        for item in items:
            item.priority = self.classify_priority(
                item, allow_parent_upgrade=allow_parent_upgrade
            )

        # Priority order
        priority_order = {
            ContextPriority.CRITICAL: 0,
            ContextPriority.CONSTRAINT: 1,
            ContextPriority.RELEVANT: 2,
            ContextPriority.INSPIRATION: 3,
        }

        # Sort by:
        # 1. Priority (CRITICAL first)
        # 2. User intent (focus, then user-attached/quoted content)
        # 3. Tier rank (inside CONSTRAINT: characters/high lore before chapters)
        # 4. Relevance score (higher first)
        # 5. Type (outline > snippet > character > lore)
        type_order = {
            "outline": 0,
            "snippet": 1,
            "character": 2,
            "lore": 3,
        }

        return sorted(
            items,
            key=lambda x: (
                priority_order.get(x.priority, 4),
                self._intent_rank(x),
                self._tier_rank(x, x.priority),
                -(x.relevance_score or 0),
                type_order.get(x.type, 4),
            )
        )

    def group_by_priority(
        self,
        items: list[ContextItem],
    ) -> dict[ContextPriority, list[ContextItem]]:
        """
        Group items by priority level.

        Args:
            items: List of context items

        Returns:
            Dict mapping priority to items
        """
        groups: dict[ContextPriority, list[ContextItem]] = {
            p: [] for p in ContextPriority
        }

        allow_parent_upgrade = self._allows_parent_upgrade(items)
        for item in items:
            priority = self.classify_priority(
                item, allow_parent_upgrade=allow_parent_upgrade
            )
            groups[priority].append(item)

        # Sort within each group by user intent, tier rank, then relevance.
        # TokenBudget.select_items 按这个顺序花预算，靠后的条目才会被截断/丢弃。
        for priority, group in groups.items():
            group.sort(
                key=lambda x, p=priority: (
                    self._intent_rank(x),
                    self._tier_rank(x, p),
                    -(x.relevance_score or 0),
                )
            )

        return groups

    def get_budget_allocation(
        self,
        max_tokens: int,
        custom_allocation: dict[ContextPriority, float] | None = None,
    ) -> dict[ContextPriority, int]:
        """
        Get token budget allocation per priority.

        Args:
            max_tokens: Total token budget
            custom_allocation: Optional custom percentages

        Returns:
            Dict mapping priority to token count
        """
        allocation = custom_allocation or ContextPriority.get_budget_allocation()

        return {
            priority: int(max_tokens * pct)
            for priority, pct in allocation.items()
        }
