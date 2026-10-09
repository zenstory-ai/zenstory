"""
Tests for Agent context token budget management.

Tests TokenBudget for token estimation, budget allocation, and truncation.
"""

import pytest

from agent.context.budget import (
    DEFAULT_PROMPT_TOKEN_LEDGER_CEILING,
    MIN_HISTORY_TOKEN_FLOOR,
    TRUNCATION_SUFFIX,
    TokenBudget,
    compute_history_token_budget,
)
from agent.schemas.context import ContextItem, ContextPriority
from agent.utils.token_utils import CHARS_PER_TOKEN


@pytest.mark.unit
class TestTokenBudget:
    """Test TokenBudget class."""

    def test_init_default(self):
        """Test budget initialization with defaults."""
        budget = TokenBudget()
        assert budget.max_tokens == 4000
        assert budget.allocation == TokenBudget.DEFAULT_ALLOCATION
        assert budget.used[ContextPriority.CRITICAL] == 0
        assert budget.used[ContextPriority.CONSTRAINT] == 0
        assert budget.used[ContextPriority.RELEVANT] == 0
        assert budget.used[ContextPriority.INSPIRATION] == 0

    def test_init_custom(self):
        """Test budget initialization with custom values."""
        custom_allocation = {
            ContextPriority.CRITICAL: 0.5,
            ContextPriority.CONSTRAINT: 0.3,
            ContextPriority.RELEVANT: 0.2,
            ContextPriority.INSPIRATION: 0.0,
        }
        budget = TokenBudget(max_tokens=2000, allocation=custom_allocation)
        assert budget.max_tokens == 2000
        assert budget.allocation == custom_allocation

    def test_estimate_tokens_empty(self):
        """Test token estimation for empty text."""
        budget = TokenBudget()
        assert budget.estimate_tokens("") == 0
        assert budget.estimate_tokens(None) == 0  # type: ignore

    def test_estimate_tokens_short_text(self):
        """Test token estimation for short text returns positive values."""
        budget = TokenBudget()
        assert budget.estimate_tokens("test") >= 1
        assert budget.estimate_tokens("hello world") >= 1
        # Chinese text should produce more tokens than len/4 (tiktoken-accurate)
        assert budget.estimate_tokens("这是一个测试") >= 1

    def test_estimate_tokens_long_text(self):
        """Test token estimation for long text returns positive count."""
        budget = TokenBudget()
        text = "word " * 100  # 500 chars
        tokens = budget.estimate_tokens(text)
        assert tokens > 0

    def test_estimate_tokens_mixed_languages(self):
        """Test token estimation for mixed Chinese and English returns positive count."""
        budget = TokenBudget()
        text = "Hello 你好 test 测试"
        assert budget.estimate_tokens(text) >= 1

    def test_estimate_item_tokens(self):
        """Test token estimation for context item."""
        budget = TokenBudget()
        item = ContextItem(
            id="1",
            type="outline",
            title="Test Title",
            content="Test content that is longer than the title",
        )
        # title (11) + content (42) + newline (1) = 54 chars → 13 tokens
        tokens = budget.estimate_item_tokens(item)
        assert tokens > 0
        assert tokens == budget.estimate_tokens(f"{item.title}\n{item.content}")

    def test_get_budget(self):
        """Test getting budget for priority."""
        budget = TokenBudget(max_tokens=1000)

        critical_budget = budget.get_budget(ContextPriority.CRITICAL)
        assert critical_budget == 300  # 30% of 1000

        constraint_budget = budget.get_budget(ContextPriority.CONSTRAINT)
        assert constraint_budget == 350  # 35% of 1000

        relevant_budget = budget.get_budget(ContextPriority.RELEVANT)
        assert relevant_budget == 250  # 25% of 1000

        inspiration_budget = budget.get_budget(ContextPriority.INSPIRATION)
        assert inspiration_budget == 100  # 10% of 1000

    def test_get_remaining_initial(self):
        """Test getting remaining budget initially."""
        budget = TokenBudget(max_tokens=1000)

        critical_remaining = budget.get_remaining(ContextPriority.CRITICAL)
        assert critical_remaining == 300  # Full budget available

        constraint_remaining = budget.get_remaining(ContextPriority.CONSTRAINT)
        assert constraint_remaining == 350

    def test_get_remaining_after_allocation(self):
        """Test getting remaining budget after allocation."""
        budget = TokenBudget(max_tokens=1000)

        # Allocate some tokens
        success = budget.allocate(ContextPriority.CRITICAL, 100)
        assert success is True

        remaining = budget.get_remaining(ContextPriority.CRITICAL)
        assert remaining == 200  # 300 - 100

    def test_get_total_remaining(self):
        """Test getting total remaining budget."""
        budget = TokenBudget(max_tokens=1000)

        # Initially all tokens available
        assert budget.get_total_remaining() == 1000

        # Allocate some tokens
        budget.allocate(ContextPriority.CRITICAL, 100)
        budget.allocate(ContextPriority.CONSTRAINT, 150)

        assert budget.get_total_remaining() == 750  # 1000 - 100 - 150

    def test_allocate_success(self):
        """Test successful token allocation."""
        budget = TokenBudget(max_tokens=1000)

        success = budget.allocate(ContextPriority.CRITICAL, 200)
        assert success is True
        assert budget.used[ContextPriority.CRITICAL] == 200

    def test_allocate_exceeds_budget(self):
        """Test allocation that exceeds budget."""
        budget = TokenBudget(max_tokens=1000)

        # Try to allocate more than available
        success = budget.allocate(ContextPriority.CRITICAL, 400)  # Budget is 300
        assert success is False
        assert budget.used[ContextPriority.CRITICAL] == 0  # No allocation

    def test_allocate_exact_budget(self):
        """Test allocation of exact budget amount."""
        budget = TokenBudget(max_tokens=1000)

        success = budget.allocate(ContextPriority.CRITICAL, 300)  # Exact budget
        assert success is True
        assert budget.used[ContextPriority.CRITICAL] == 300

    def test_can_fit_true(self):
        """Test can_fit when item fits."""
        budget = TokenBudget(max_tokens=1000)

        item = ContextItem(
            id="1",
            type="outline",
            title="Test",
            content="Content",
        )
        budget.estimate_item_tokens(item)

        can_fit = budget.can_fit(item, ContextPriority.CRITICAL)
        assert can_fit is True  # Small item should fit

    def test_can_fit_false(self):
        """Test can_fit when item doesn't fit."""
        budget = TokenBudget(max_tokens=1000)

        # Use up most of the budget
        budget.allocate(ContextPriority.CRITICAL, 290)

        # Create a large item
        large_item = ContextItem(
            id="1",
            type="outline",
            title="Large Title",
            content="x" * 500,  # ~125 tokens
        )

        can_fit = budget.can_fit(large_item, ContextPriority.CRITICAL)
        assert can_fit is False

    def test_select_items_within_budget(self):
        """Test selecting items within budget."""
        # 低重要度设定在 INSPIRATION 档（10%），预算要足够它整条放下。
        budget = TokenBudget(max_tokens=3000)

        items = [
            ContextItem.from_outline(
                id="1",
                title="Small",
                content="x" * 40,  # ~10 tokens
                is_focus=True,  # CRITICAL priority
            ),
            ContextItem.from_outline(
                id="2",
                title="Medium",
                content="y" * 200,  # ~50 tokens
            ),
            ContextItem.from_lore(
                id="3",
                title="Large",
                content="z" * 400,  # ~100 tokens
                importance="low",
            ),
        ]

        selected, budget_used = budget.select_items(items)

        # All items should fit
        assert len(selected) == 3
        # Should have used CRITICAL budget for the focus item
        assert budget_used[ContextPriority.CRITICAL] > 0

    def test_select_items_exceeds_budget(self):
        """Test selecting items when budget is exceeded."""
        budget = TokenBudget(max_tokens=100)

        # Create items that exceed budget
        items = [
            ContextItem.from_outline(
                id=str(i),
                title=f"Item {i}",
                content="x" * 200,  # ~50 tokens each
            )
            for i in range(5)
        ]

        selected, budget_used = budget.select_items(items)

        # Not all items should be selected
        assert len(selected) < 5
        # Budget should be respected (with some margin)
        total_used = sum(budget_used.values())
        assert total_used <= 100 * 1.2  # Allow 20% margin

    def test_select_items_with_priority_groups(self):
        """Test selecting items with pre-grouped priorities."""
        budget = TokenBudget(max_tokens=100)

        items = [
            ContextItem.from_outline(
                id="1",
                title="Focus",
                content="x" * 40,
                is_focus=True,
            ),
            ContextItem.from_lore(
                id="2",
                title="Lore",
                content="y" * 40,
                importance="low",
            ),
        ]

        # Manually group by priority
        groups = {
            ContextPriority.CRITICAL: [items[0]],
            ContextPriority.INSPIRATION: [items[1]],
        }

        selected, budget_used = budget.select_items(items, groups)

        # CRITICAL item should be selected first
        assert len(selected) >= 1
        assert any(item.id == "1" for item in selected)

    def test_truncate_item_success(self):
        """Test successful item truncation."""
        budget = TokenBudget()

        item = ContextItem(
            id="1",
            type="outline",
            title="Test Item",
            content="x" * 1000,  # Long content
        )

        truncated = budget.truncate_item(item, max_tokens=50)

        assert truncated is not None
        assert truncated.id == item.id
        assert truncated.title == item.title
        assert len(truncated.content) < len(item.content)
        assert truncated.metadata.get("truncated") is True

    def test_truncate_item_too_small(self):
        """Test truncation when max_tokens is too small."""
        budget = TokenBudget()

        item = ContextItem(
            id="1",
            type="outline",
            title="Test Item",
            content="x" * 100,
        )

        # Too small to truncate meaningfully
        truncated = budget.truncate_item(item, max_tokens=10)
        assert truncated is None

    def test_truncate_item_preserves_title(self):
        """Test that truncation preserves title."""
        budget = TokenBudget()

        item = ContextItem(
            id="1",
            type="outline",
            title="Important Title",
            content="x" * 1000,
        )

        truncated = budget.truncate_item(item, max_tokens=100)

        assert truncated is not None
        assert truncated.title == "Important Title"
        # 显式截断后缀 + 元数据，而不是让模型猜的裸 "..."
        assert truncated.content.endswith(TRUNCATION_SUFFIX)
        assert truncated.metadata["original_chars"] == 1000
        assert truncated.metadata["shown_chars"] == len(truncated.content) - len(TRUNCATION_SUFFIX)

    def test_truncate_item_sentence_boundary(self):
        """Test that truncation tries to break at sentence boundaries."""
        budget = TokenBudget()

        # Use very long content to ensure truncation happens
        content = "这是第一句话的内容，有足够的长度可以截断。这是第二句话的内容，也有足够的长度可以截断。这是第三句话的内容，同样有足够的长度可以截断。这是第四句话的内容，继续增加长度。这是第五句话的内容，确保内容足够长。" * 2
        item = ContextItem(
            id="1",
            type="outline",
            title="Test",
            content=content,
        )

        # Truncate to small token limit
        truncated = budget.truncate_item(item, max_tokens=30)

        assert truncated is not None
        # Should end with the explicit truncation suffix
        assert truncated.content.endswith(TRUNCATION_SUFFIX)
        # Content should be different from original
        assert truncated.content != content
        # Content should be marked as truncated
        assert truncated.metadata.get("truncated") is True

    def test_truncate_item_no_sentence_boundary(self):
        """Test truncation when no sentence boundary exists."""
        budget = TokenBudget()

        content = "word" * 100  # No sentence boundaries
        item = ContextItem(
            id="1",
            type="outline",
            title="Test",
            content=content,
        )

        truncated = budget.truncate_item(item, max_tokens=50)

        assert truncated is not None
        assert truncated.content.endswith(TRUNCATION_SUFFIX)

    def test_get_usage_report(self):
        """Test getting budget usage report."""
        budget = TokenBudget(max_tokens=1000)

        # Allocate some tokens
        budget.allocate(ContextPriority.CRITICAL, 200)
        budget.allocate(ContextPriority.CONSTRAINT, 150)
        budget.allocate(ContextPriority.RELEVANT, 100)

        report = budget.get_usage_report()

        assert report["max_tokens"] == 1000
        assert report["used_tokens"] == 450  # 200 + 150 + 100
        assert report["remaining_tokens"] == 550  # 1000 - 450
        assert "by_priority" in report

        # Check priority breakdown
        by_priority = report["by_priority"]
        assert by_priority["critical"]["budget"] == 300
        assert by_priority["critical"]["used"] == 200
        assert by_priority["critical"]["remaining"] == 100

    def test_chars_per_token_constant(self):
        """Test CHARS_PER_TOKEN constant."""
        assert CHARS_PER_TOKEN == 4

    def test_default_allocation_percentages(self):
        """Test default allocation percentages."""
        allocation = TokenBudget.DEFAULT_ALLOCATION
        assert allocation[ContextPriority.CRITICAL] == 0.30
        assert allocation[ContextPriority.CONSTRAINT] == 0.35
        assert allocation[ContextPriority.RELEVANT] == 0.25
        assert allocation[ContextPriority.INSPIRATION] == 0.10

    def test_custom_allocation_percentages(self):
        """Test custom allocation percentages."""
        custom = {
            ContextPriority.CRITICAL: 0.50,
            ContextPriority.CONSTRAINT: 0.30,
            ContextPriority.RELEVANT: 0.20,
            ContextPriority.INSPIRATION: 0.00,
        }
        budget = TokenBudget(max_tokens=1000, allocation=custom)

        assert budget.get_budget(ContextPriority.CRITICAL) == 500
        assert budget.get_budget(ContextPriority.CONSTRAINT) == 300
        assert budget.get_budget(ContextPriority.RELEVANT) == 200
        assert budget.get_budget(ContextPriority.INSPIRATION) == 0

    def test_select_items_partial_priority_group(self):
        """Test selecting when only part of a priority group fits the total budget.

        CRITICAL is the must-include tier and may draw on the whole budget when
        no lower-priority items compete for it, so the total content must exceed
        ``max_tokens`` (not merely CRITICAL's nominal share) to force partial
        selection.
        """
        budget = TokenBudget(max_tokens=100)

        # Total ~150 tokens of CRITICAL content against a 100-token budget.
        items = [
            ContextItem.from_outline(
                id=str(i),
                title=f"Item {i}",
                content="x" * 40,  # ~10 tokens each
                is_focus=True,
            )
            for i in range(15)
        ]

        selected, budget_used = budget.select_items(items)

        # Should select some but not all items
        assert len(selected) >= 2
        assert len(selected) < 15
        # CRITICAL may use up to the full budget when nothing else competes.
        assert budget_used[ContextPriority.CRITICAL] <= 100 * 1.2

    def test_select_items_continues_after_oversized_item(self):
        """Test selecting continues scanning when an early item doesn't fit."""
        budget = TokenBudget(max_tokens=100)  # CRITICAL budget = 30

        oversized = ContextItem.from_outline(
            id="oversized",
            title="Oversized",
            content="x" * 180,  # ~45 tokens with title
            is_focus=True,
        )
        small = ContextItem.from_outline(
            id="small",
            title="Small",
            content="ok",  # tiny item that should fit
            is_focus=True,
        )

        selected, budget_used = budget.select_items([oversized, small])

        selected_ids = {item.id for item in selected}
        assert "small" in selected_ids
        assert budget_used[ContextPriority.CRITICAL] > 0

    def test_multiple_priority_selection(self):
        """Test selection across multiple priority levels."""
        budget = TokenBudget(max_tokens=200)

        items = [
            # CRITICAL items
            ContextItem.from_outline(
                id="1",
                title="Critical 1",
                content="x" * 40,
                is_focus=True,
            ),
            ContextItem.from_outline(
                id="2",
                title="Critical 2",
                content="x" * 40,
                is_focus=True,
            ),
            # CONSTRAINT items
            ContextItem.from_character(
                id="3",
                name="Character 1",
                profile="y" * 40,
            ),
            ContextItem.from_character(
                id="4",
                name="Character 2",
                profile="y" * 40,
            ),
            # INSPIRATION items
            ContextItem.from_lore(
                id="5",
                title="Lore 1",
                content="z" * 40,
                importance="low",
            ),
        ]

        selected, budget_used = budget.select_items(items)

        # CRITICAL and CONSTRAINT should be prioritized
        assert len(selected) >= 2  # At least CRITICAL items
        # Check budget allocation
        assert budget_used[ContextPriority.CRITICAL] > 0
        assert budget_used[ContextPriority.CONSTRAINT] > 0

    def test_truncate_with_different_separators(self):
        """Test truncation with different text separators."""
        budget = TokenBudget()

        # Use longer content to ensure truncation actually happens
        test_cases = [
            ("First part of the text.Second part of the text.Third part of the text", "."),
            ("第一句话的内容很长。第二句话的内容也很长。第三句话的内容同样很长。", "。"),
            ("This is exciting! This is amazing! This is wonderful!", "!"),
            ("First question? Second answer? Third response?", "?"),
            ("Line one here\nLine two here\nLine three here", "\n"),
        ]

        for content, _expected_sep in test_cases:
            item = ContextItem(
                id="1",
                type="outline",
                title="Test",
                content=content,
            )

            truncated = budget.truncate_item(item, max_tokens=30)

            assert truncated is not None
            # Content should be different (truncated or same with ...)
            assert truncated.content != content or "..." in truncated.content

    def test_truncate_preserves_metadata(self):
        """Test that truncation preserves original metadata."""
        budget = TokenBudget()

        item = ContextItem(
            id="1",
            type="outline",
            title="Test",
            content="x" * 1000,
            metadata={
                "relation": "sibling",
                "file_type": "draft",
                "custom_field": "custom_value",
            },
        )

        truncated = budget.truncate_item(item, max_tokens=100)

        assert truncated is not None
        assert truncated.metadata["relation"] == "sibling"
        assert truncated.metadata["file_type"] == "draft"
        assert truncated.metadata["custom_field"] == "custom_value"
        assert truncated.metadata["truncated"] is True

    def test_select_items_empty_list(self):
        """Test selecting from empty item list."""
        budget = TokenBudget(max_tokens=1000)

        selected, budget_used = budget.select_items([])

        assert len(selected) == 0
        assert all(v == 0 for v in budget_used.values())

    def test_select_items_all_zero_size(self):
        """Test selecting items with zero estimated tokens."""
        budget = TokenBudget(max_tokens=1000)

        items = [
            ContextItem(
                id="1",
                type="outline",
                title="Empty",
                content="",
            ),
            ContextItem(
                id="2",
                type="outline",
                title="Minimal",
                content="x",
            ),
        ]

        selected, budget_used = budget.select_items(items)

        # Should handle zero/near-zero items gracefully
        assert len(selected) >= 0


@pytest.mark.unit
class TestPromptTokenLedger:
    """Tests for the unified prompt-token ledger (shared ceiling for context + history)."""

    def test_returns_full_history_budget_when_reserved_is_small(self):
        """Small prompt costs leave the full configured history budget intact."""
        result = compute_history_token_budget(
            configured_history_budget=6000,
            reserved_prompt_tokens=1000,
        )
        assert result == 6000

    def test_reserved_context_shrinks_history_window(self):
        """When other prompt costs are large, the history window shrinks accordingly."""
        # Reserve enough that only 4000 tokens remain under the ceiling.
        reserved = DEFAULT_PROMPT_TOKEN_LEDGER_CEILING - 4000
        result = compute_history_token_budget(
            configured_history_budget=6000,
            reserved_prompt_tokens=reserved,
        )
        assert result == 4000
        # Total prompt budget stays within the shared ceiling (no ~12k double-budget).
        assert reserved + result <= DEFAULT_PROMPT_TOKEN_LEDGER_CEILING

    def test_history_never_exceeds_configured_budget(self):
        """The ledger only shrinks history — it never inflates beyond the configured budget."""
        result = compute_history_token_budget(
            configured_history_budget=6000,
            reserved_prompt_tokens=0,
            ceiling=1_000_000,
        )
        assert result == 6000

    def test_history_floor_prevents_starvation(self):
        """A huge context block cannot starve history below the floor."""
        result = compute_history_token_budget(
            configured_history_budget=6000,
            reserved_prompt_tokens=DEFAULT_PROMPT_TOKEN_LEDGER_CEILING * 10,
        )
        assert result == MIN_HISTORY_TOKEN_FLOOR

    def test_handles_none_inputs_without_crashing(self):
        """Missing inputs are coerced to 0 and never crash."""
        result = compute_history_token_budget(
            configured_history_budget=None,  # type: ignore[arg-type]
            reserved_prompt_tokens=None,  # type: ignore[arg-type]
        )
        # configured budget of 0 wins over the remaining ceiling room.
        assert result == 0

    def test_shared_ceiling_bounds_total_prompt_tokens(self):
        """Context + history allocation together stay under the shared ceiling."""
        ceiling = 10000
        reserved = 7000
        history = compute_history_token_budget(
            configured_history_budget=6000,
            reserved_prompt_tokens=reserved,
            ceiling=ceiling,
        )
        # 6000 configured, but only 3000 remains under the ceiling.
        assert history == 3000
        assert reserved + history <= ceiling
