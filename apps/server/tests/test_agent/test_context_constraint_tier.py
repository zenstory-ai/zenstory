"""长篇续写时，角色卡与高重要度设定不能被章节挤出上下文（报告 F2）。

合成场景照搬评审时的模拟：打开第 60 章续写，前一章第 59 章，3 个最近修改的
兄弟章节，章节各 3000 字，10 张 500 字角色卡，3 条高 / 5 条中 / 2 条低重要度设定，
条目预算 28k。修复前兄弟章节与前一章一起占满 CONSTRAINT 档，角色卡大半被丢弃
或只剩残片。
"""

from __future__ import annotations

from unittest.mock import patch

from agent.context.assembler import QUERY_NAME_MATCH_BOOST, ContextAssembler
from agent.context.budget import MIN_TRUNCATED_ITEM_CHARS, TokenBudget
from agent.context.prioritizer import ContextPrioritizer
from agent.schemas.context import ContextItem, ContextPriority

CHARACTER_NAMES = ["林凡", "苏瑶", "陈默", "赵青", "王虎", "李婉", "周岩", "吴越", "郑霜", "孙策"]
ITEM_BUDGET = 28000


def _prose(chars: int, seed: str) -> str:
    """不重复单字的中文正文，避免估算器对重复字符的偏差。"""
    base = f"{seed}站在山门前望着远处的云海，心中想起师父临别时说过的话，剑意在胸中翻涌。"
    return (base * (chars // len(base) + 1))[:chars]


def _chapter(id: str, title: str, chars: int, *, relation: str | None, is_focus: bool = False) -> ContextItem:
    item = ContextItem.from_outline(
        id=id,
        title=title,
        content=_prose(chars, title),
        is_focus=is_focus,
        relation=relation,
    )
    item.metadata["file_type"] = "draft"
    return item


def _character(name: str) -> ContextItem:
    item = ContextItem.from_character(id=f"char-{name}", name=name, profile=_prose(500, name))
    item.metadata["file_type"] = "character"
    return item


def _lore(id: str, title: str, importance: str, chars: int) -> ContextItem:
    item = ContextItem.from_lore(
        id=id,
        title=title,
        content=_prose(chars, title),
        category="宗门",
        importance=importance,
    )
    item.metadata["file_type"] = "lore"
    return item


def _scenario() -> list[ContextItem]:
    items = [
        _chapter("ch60", "第60章", 3000, relation=None, is_focus=True),
        _chapter("ch59", "第59章", 3000, relation="previous"),
        *[_chapter(f"ch{n}", f"第{n}章", 3000, relation="sibling") for n in (57, 56, 55)],
        *[_character(name) for name in CHARACTER_NAMES],
        *[_lore(f"lore-high-{i}", f"核心规则{i}", "high", 800) for i in range(3)],
        *[_lore(f"lore-medium-{i}", f"地理{i}", "medium", 600) for i in range(5)],
        *[_lore(f"lore-low-{i}", f"传闻{i}", "low", 400) for i in range(2)],
    ]
    return items


def _select(items: list[ContextItem]) -> list[ContextItem]:
    prioritizer = ContextPrioritizer()
    budget = TokenBudget(max_tokens=ITEM_BUDGET)
    prioritized = prioritizer.prioritize(items)
    groups = prioritizer.group_by_priority(prioritized)
    selected, _ = budget.select_items(prioritized, groups)
    return selected


def _full_ids(selected: list[ContextItem]) -> set[str]:
    return {item.id for item in selected if not item.is_truncated}


def test_continuation_keeps_all_high_lore_and_characters_full():
    selected = _select(_scenario())
    full = _full_ids(selected)

    assert {"ch60", "ch59"} <= full, "焦点章与前一章必须是全文"
    assert {f"lore-high-{i}" for i in range(3)} <= full, "高重要度设定必须全部以全文保留"
    full_characters = [name for name in CHARACTER_NAMES if f"char-{name}" in full]
    assert len(full_characters) >= 8, f"角色卡只保留了 {full_characters}"


def test_siblings_drop_to_relevant_while_previous_and_focus_stay_constraint_or_higher():
    prioritizer = ContextPrioritizer()
    by_id = {item.id: item for item in prioritizer.prioritize(_scenario())}

    assert by_id["ch60"].priority == ContextPriority.CRITICAL
    assert by_id["ch59"].priority == ContextPriority.CONSTRAINT
    for n in (57, 56, 55):
        assert by_id[f"ch{n}"].priority == ContextPriority.RELEVANT


def test_constraint_tier_orders_characters_and_high_lore_before_previous_chapter():
    prioritizer = ContextPrioritizer()
    groups = prioritizer.group_by_priority(_scenario())
    constraint_ids = [item.id for item in groups[ContextPriority.CONSTRAINT]]

    assert constraint_ids[-1] == "ch59"
    assert set(constraint_ids[:-1]) == {
        *(f"char-{name}" for name in CHARACTER_NAMES),
        *(f"lore-high-{i}" for i in range(3)),
    }


def test_query_naming_characters_boosts_their_cards_to_the_front():
    assembler = ContextAssembler()
    items = assembler._apply_query_recall_ranking(_scenario(), "继续写第61章，林凡和苏瑶在山门重逢")
    by_id = {item.id: item for item in items}

    assert by_id["char-林凡"].relevance_score == 0.7 + QUERY_NAME_MATCH_BOOST
    assert by_id["char-苏瑶"].relevance_score == 0.7 + QUERY_NAME_MATCH_BOOST
    assert by_id["char-陈默"].relevance_score == 0.7

    groups = ContextPrioritizer().group_by_priority(items)
    constraint_ids = [item.id for item in groups[ContextPriority.CONSTRAINT]]
    assert set(constraint_ids[:2]) == {"char-林凡", "char-苏瑶"}


def test_query_naming_lore_matches_title_without_category_prefix():
    assembler = ContextAssembler()
    lore = _lore("lore-qy", "青云宗", "medium", 300)
    assert lore.title == "宗门 - 青云宗"

    assembler._apply_query_recall_ranking([lore], "让林凡回青云宗复命")

    assert lore.relevance_score == 0.6 + QUERY_NAME_MATCH_BOOST


def test_single_character_names_do_not_get_substring_boost():
    assembler = ContextAssembler()
    card = _character("凡")

    assembler._apply_query_recall_ranking([card], "平凡的一天")

    assert card.relevance_score == 0.7


def test_retrieval_excludes_only_selected_items():
    """被预算挤掉的角色卡不能出现在检索排除名单里，否则检索也捞不回来。"""
    assembler = ContextAssembler()
    characters = [_character(name) for name in CHARACTER_NAMES]
    for card in characters:
        card.content = _prose(3000, card.title)
    captured: dict[str, set[str]] = {}

    def fake_snippets(*, project_id, query, exclude_entity_ids=None, top_k=6):
        captured["exclude"] = set(exclude_entity_ids or set())
        return []

    with (
        patch.object(assembler, "_get_project_status", return_value={}),
        patch.object(assembler, "_get_file_inventory", return_value={}),
        patch.object(assembler, "_get_files_by_types", return_value=characters),
        patch.object(assembler, "_get_retrieved_snippets", side_effect=fake_snippets),
    ):
        data = assembler.assemble(
            session=None,  # type: ignore[arg-type]
            project_id="p1",
            query="林凡",
            max_tokens=6000,
        )

    selected_ids = set(data.refs)
    all_ids = {card.id for card in characters}
    assert selected_ids, "至少应选中一张角色卡"
    assert selected_ids < all_ids, "场景需要有被挤掉的角色卡"
    assert captured["exclude"] == selected_ids


def test_truncated_stub_below_threshold_is_dropped_outside_critical():
    budget = TokenBudget(max_tokens=4000)
    filler = _character("填充")
    filler.content = _prose(2400, "填充")
    long_lore = _lore("lore-long", "长设定", "high", 3000)

    selected, _ = budget.select_items([filler, long_lore])
    selected_ids = {item.id for item in selected}

    assert "lore-long" not in selected_ids
    assert [item.id for item in budget.dropped_stubs] == ["lore-long"]
    for item in selected:
        assert not item.is_truncated or item.metadata["shown_chars"] >= MIN_TRUNCATED_ITEM_CHARS


def test_critical_items_are_truncated_not_dropped():
    budget = TokenBudget(max_tokens=600)
    focus = _chapter("focus", "第1章", 6000, relation=None, is_focus=True)

    selected, _ = budget.select_items([focus])

    assert [item.id for item in selected] == ["focus"]
    assert selected[0].is_truncated
    assert budget.dropped_stubs == []
