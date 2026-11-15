"""
agent/constants.py 共享基础设施的单元测试。

覆盖：
- coerce_bool 的全部分支（尤其是朴素 bool() 会判真的 "false"/"0" 等字符串）
- CONTENT_FILE_TYPES / INVENTORY_FILE_TYPES 的内容契约
"""

import pytest

from agent.constants import (
    CONTENT_FILE_TYPES,
    INVENTORY_FILE_TYPES,
    coerce_bool,
    should_offload_session_work,
)
from models.file_model import (
    FILE_TYPE_CHARACTER,
    FILE_TYPE_DOCUMENT,
    FILE_TYPE_DRAFT,
    FILE_TYPE_FOLDER,
    FILE_TYPE_LORE,
    FILE_TYPE_OUTLINE,
    FILE_TYPE_SCRIPT,
    FILE_TYPE_SNIPPET,
)

# ---------------------------------------------------------------------------
# coerce_bool
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        True,
        1,
        2,
        -1,
        1.5,
        "true",
        "True",
        "TRUE",
        "  true  ",
        "1",
        "yes",
        "YES",
        "y",
        "on",
        "t",
    ],
)
def test_coerce_bool_truthy_values(value):
    """真正的真值必须转成 True。"""
    assert coerce_bool(value) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        False,
        0,
        0.0,
        None,
        "",
        "   ",
        "false",
        "False",
        "FALSE",
        "  false  ",
        "0",
        "no",
        "n",
        "off",
        "f",
        "null",
        "none",
        "None",
    ],
)
def test_coerce_bool_falsy_values(value):
    """假值必须转成 False——这些正是朴素 bool() 会判真的坑。"""
    assert coerce_bool(value) is False


@pytest.mark.unit
@pytest.mark.parametrize("value", ["false", "0", "no", "off", "null", "none", "   "])
def test_coerce_bool_beats_naive_bool(value):
    """回归护栏：朴素 bool() 判真，coerce_bool 必须判假。"""
    assert bool(value) is True  # 说明这些值确实会被朴素真值判断坑到
    assert coerce_bool(value) is False
    # 即便调用方给了 default=True，明确的假值字面量也不能被 default 覆盖
    assert coerce_bool(value, default=True) is False


@pytest.mark.unit
@pytest.mark.parametrize("value", ["maybe", "yes please", "２", "trueish", "-"])
def test_coerce_bool_unknown_string_uses_default(value):
    """无法判定的字符串走 default，不做猜测。"""
    assert coerce_bool(value) is False
    assert coerce_bool(value, default=True) is True


@pytest.mark.unit
@pytest.mark.parametrize("value", [[], [1], {}, {"a": 1}, object(), 1 + 2j])
def test_coerce_bool_unsupported_types_use_default(value):
    """list/dict/对象等语义不明的类型走 default。"""
    assert coerce_bool(value) is False
    assert coerce_bool(value, default=True) is True


@pytest.mark.unit
def test_coerce_bool_none_is_always_false():
    """None 表示"未提供"，即便 default=True 也应视为假。"""
    assert coerce_bool(None) is False
    assert coerce_bool(None, default=True) is False


@pytest.mark.unit
def test_coerce_bool_returns_real_bool_type():
    """返回值必须是真正的 bool，而不是原值透传。"""
    for value in (1, "true", 0, "false", "unknown", None):
        assert type(coerce_bool(value)) is bool


# ---------------------------------------------------------------------------
# 文件类型元组
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_content_file_types_contract():
    """CONTENT_FILE_TYPES 必须含正文类型 draft/script，且不含 folder。"""
    assert isinstance(CONTENT_FILE_TYPES, tuple)
    assert FILE_TYPE_DRAFT in CONTENT_FILE_TYPES
    assert FILE_TYPE_SCRIPT in CONTENT_FILE_TYPES
    assert FILE_TYPE_FOLDER not in CONTENT_FILE_TYPES
    # folder 之外的非正文类型也不应混入
    assert FILE_TYPE_OUTLINE not in CONTENT_FILE_TYPES
    assert FILE_TYPE_CHARACTER not in CONTENT_FILE_TYPES
    assert FILE_TYPE_LORE not in CONTENT_FILE_TYPES
    assert FILE_TYPE_SNIPPET not in CONTENT_FILE_TYPES
    assert len(set(CONTENT_FILE_TYPES)) == len(CONTENT_FILE_TYPES)


@pytest.mark.unit
def test_inventory_file_types_contract():
    """INVENTORY_FILE_TYPES 覆盖除 folder 外的全部实体类型，必含 script 与 document。"""
    assert isinstance(INVENTORY_FILE_TYPES, tuple)
    assert FILE_TYPE_SCRIPT in INVENTORY_FILE_TYPES
    assert FILE_TYPE_DOCUMENT in INVENTORY_FILE_TYPES
    assert FILE_TYPE_FOLDER not in INVENTORY_FILE_TYPES
    assert set(INVENTORY_FILE_TYPES) == {
        FILE_TYPE_OUTLINE,
        FILE_TYPE_DRAFT,
        FILE_TYPE_CHARACTER,
        FILE_TYPE_LORE,
        FILE_TYPE_SNIPPET,
        FILE_TYPE_SCRIPT,
        FILE_TYPE_DOCUMENT,
    }
    assert len(set(INVENTORY_FILE_TYPES)) == len(INVENTORY_FILE_TYPES)


@pytest.mark.unit
def test_content_types_are_subset_of_inventory_types():
    """正文类型必然要出现在文件清单里，否则 Agent 看不见自己写的正文。"""
    assert set(CONTENT_FILE_TYPES).issubset(set(INVENTORY_FILE_TYPES))


@pytest.mark.unit
def test_file_type_document_constant_exists():
    """document 是 create_file 的默认类型，必须有常量而非字面量。"""
    assert FILE_TYPE_DOCUMENT == "document"
    assert FILE_TYPE_DOCUMENT in INVENTORY_FILE_TYPES


@pytest.mark.unit
def test_should_offload_session_work_only_for_postgresql_sessions():
    """共享 helper：仅当传入绑定为 postgresql 的 session 时返回 True。"""
    assert should_offload_session_work(None) is False

    class _FakeDialect:
        def __init__(self, name: str):
            self.name = name

    class _FakeBind:
        def __init__(self, dialect_name: str):
            self.dialect = _FakeDialect(dialect_name)

    class _FakeSession:
        def __init__(self, dialect_name: str):
            self._bind = _FakeBind(dialect_name)

        def get_bind(self):
            return self._bind

    assert should_offload_session_work(_FakeSession("sqlite")) is False
    assert should_offload_session_work(_FakeSession("postgresql")) is True
