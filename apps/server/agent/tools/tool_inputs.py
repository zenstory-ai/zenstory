"""Pydantic v2 tool input models and compact JSON Schema export.

Single source of truth for:
1. Provider-neutral tool JSON Schemas (``tool_schemas.py`` exports ``TOOL_SCHEMAS``
   directly from these models via ``export_tool_schema`` with zero Pydantic v2
   ``title`` / ``$defs`` / ``anyOf[..., null]`` token bloat).
2. Runtime argument validation, alias resolution (``AliasChoices``), loose-bool
   coercion (``BeforeValidator(coerce_bool)``), strict non-bool integer checks,
   and uniform structured Chinese validation errors for ``mcp_tools.py`` and
   ``openai_agents/tools_adapter.py``.

Important invariants:
- ``strict_json_schema=False`` + ``json_repair`` in ``tools_adapter.py`` remain
  intact for DeepSeek / MiniMax Chat Completions compatibility.
- Open ``metadata: dict[str, Any]`` and ``minimum`` / ``maxLength`` constraints
  are preserved without strict-mode rejection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

from annotated_types import Ge
from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from agent.constants import coerce_bool
from config.project_status import PROJECT_STATUS_MAX_LENGTHS

TOOL_INPUT_VALIDATION_ERROR_TYPE = "invalid_tool_input"

_FILE_TYPE_SCHEMA_VALUES = ("outline", "character", "lore", "snippet", "document", "folder")
_ALL_FILE_TYPE_VALUES = ("outline", "character", "lore", "draft", "script", "snippet", "document", "folder")

AllFileType = Literal["outline", "character", "lore", "draft", "script", "snippet", "document", "folder"]
CreateSchemaFileType = Literal["outline", "character", "lore", "snippet", "document", "folder"]
ChapterFileType = Literal["draft", "script"]
EditOpType = Literal["replace", "insert_after", "insert_before", "append", "prepend", "delete"]
EditMatchMode = Literal["auto", "exact"]
TaskStatusType = Literal["pending", "in_progress", "done"]
TaskKindType = Literal["file", "conversation"]
ArtifactFileType = Literal["outline", "draft", "script", "character", "lore", "snippet", "document"]
UnitType = Literal["章", "集", "回", "幕", "卷", "节", "话"]


# ---------------------------------------------------------------------------
# Shared validators & annotated types
# ---------------------------------------------------------------------------


def _coerce_bool_false(value: Any) -> bool:
    return coerce_bool(value, default=False)


def _coerce_bool_true(value: Any) -> bool:
    if value is None:
        return True
    return coerce_bool(value, default=True)


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return coerce_bool(value, default=False)


def _parse_non_bool_int(value: Any) -> int:
    """Parse an integer while explicitly rejecting Python ``bool`` values."""
    if isinstance(value, bool):
        raise ValueError("必须为整数，不能是布尔值")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError("必须为整数")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(stripped)
            except ValueError:
                pass
        raise ValueError("必须为整数")
    raise ValueError("必须为整数")


def _parse_optional_non_bool_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _parse_non_bool_int(value)


def _strict_str(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("必须为字符串")
    return value


def _strict_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("必须为字符串")
    return value


def _resolve_id_aliases(data: Any) -> Any:
    """Preserve fallback order ``id`` -> ``file_id`` -> ``fileId`` when earlier aliases are empty."""
    if not isinstance(data, Mapping):
        return data
    if "id" in data and data["id"] not in (None, ""):
        return data
    for alias in ("file_id", "fileId"):
        if alias in data and data[alias] not in (None, ""):
            normalized = dict(data)
            normalized["id"] = data[alias]
            return normalized
    return data


CoercedBool = Annotated[bool, BeforeValidator(_coerce_bool_false), WithJsonSchema({"type": "boolean"})]
CoercedBoolDefaultTrue = Annotated[bool, BeforeValidator(_coerce_bool_true), WithJsonSchema({"type": "boolean"})]
CoercedOptionalBool = Annotated[
    bool | None, BeforeValidator(_coerce_optional_bool), WithJsonSchema({"type": "boolean"})
]
NonBoolInt = Annotated[int, BeforeValidator(_parse_non_bool_int)]
OptionalNonBoolInt = Annotated[int | None, BeforeValidator(_parse_optional_non_bool_int)]
StrictStr = Annotated[str, BeforeValidator(_strict_str), WithJsonSchema({"type": "string"})]
StrictOptionalStr = Annotated[str | None, BeforeValidator(_strict_optional_str)]


# ---------------------------------------------------------------------------
# Compact JSON Schema exporter (zero Pydantic v2 token bloat)
# ---------------------------------------------------------------------------


def _normalize_schema_node(node: Any, defs: dict[str, Any]) -> Any:
    """Inline ``$defs``, strip ``title`` & redundant ``default: null``, flatten optional ``anyOf``."""
    if isinstance(node, list):
        return [_normalize_schema_node(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    working = dict(node)

    # Inline $ref from $defs
    ref = working.pop("$ref", None)
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        def_name = ref[len("#/$defs/") :]
        resolved = _normalize_schema_node(defs.get(def_name, {}), defs)
        if isinstance(resolved, dict):
            # Drop model-class docstring description from referenced sub-models;
            # field-level descriptions live on `working` and override below.
            merged = dict(resolved)
            merged.pop("description", None)
            merged.update(working)
            working = merged

    working.pop("title", None)
    working.pop("$defs", None)

    # Flatten Pydantic v2 optional union: {"anyOf": [T, {"type": "null"}]} -> T
    any_of = working.get("anyOf")
    if isinstance(any_of, list):
        non_null = [branch for branch in any_of if branch != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) < len(any_of):
            working.pop("anyOf")
            branch_resolved = _normalize_schema_node(non_null[0], defs)
            if isinstance(branch_resolved, dict):
                merged = dict(branch_resolved)
                merged.update(working)
                working = merged

    # Drop redundant `"default": null` emitted by Pydantic v2 for optional fields
    if "default" in working and working["default"] is None:
        working.pop("default")

    # Drop redundant `"additionalProperties": true` on open dict[str, Any] fields;
    # keep explicit `"additionalProperties": false` where required.
    if working.get("additionalProperties") is True:
        working.pop("additionalProperties")

    result: dict[str, Any] = {}
    for key, value in working.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {prop_name: _normalize_schema_node(prop_schema, defs) for prop_name, prop_schema in value.items()}
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            result[key] = _normalize_schema_node(value, defs)
        elif key == "anyOf" and isinstance(value, list):
            result[key] = [_normalize_schema_node(item, defs) for item in value]
        else:
            result[key] = value
    return result


def export_tool_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Export a clean, prompt-token-efficient JSON Schema from a Pydantic v2 input model."""
    raw = model_cls.model_json_schema(by_alias=False)
    defs = raw.get("$defs") if isinstance(raw.get("$defs"), dict) else {}
    normalized = _normalize_schema_node(raw, defs)
    # Never leak Python class docstrings into LLM input_schema objects.
    normalized.pop("description", None)

    excluded: frozenset[str] = getattr(model_cls, "_schema_excluded_fields", frozenset())
    if excluded and isinstance(normalized.get("properties"), dict):
        for field_name in excluded:
            normalized["properties"].pop(field_name, None)
        if isinstance(normalized.get("required"), list):
            normalized["required"] = [name for name in normalized["required"] if name not in excluded]

    if "required" not in normalized:
        normalized["required"] = []
    return normalized


def build_tool_schema(
    tool_name: str,
    *,
    description: str,
    model_cls: type[BaseModel],
) -> dict[str, Any]:
    """Build a provider-neutral tool schema entry from a Pydantic v2 input model."""
    return {
        "name": tool_name,
        "description": description,
        "input_schema": export_tool_schema(model_cls),
    }


# ---------------------------------------------------------------------------
# Tool input models (single source of truth for schema + validation)
# ---------------------------------------------------------------------------


class _ToolInputModel(BaseModel):
    """Base class for ZenStory agent tool input schemas."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    _schema_excluded_fields: ClassVar[frozenset[str]] = frozenset()


class CreateFileInput(_ToolInputModel):
    """Input model for ``create_file``."""

    _schema_excluded_fields: ClassVar[frozenset[str]] = frozenset({"content", "order"})

    title: StrictStr = Field(
        description="文件标题/名称，最多 255 字；不要把正文放在标题中，工具返回后用 <file> 流式输出正文",
        json_schema_extra={"maxLength": 255},
    )
    # Schema advertises only non-body file types (draft/script go through write_chapter).
    # Runtime accepts draft/script too so _loop_body_create_refusal / _source_copy_content /
    # _write_chapter_create can emit their exact domain error_types or create chapters.
    file_type: Annotated[
        str,
        WithJsonSchema({"type": "string", "enum": list(_FILE_TYPE_SCHEMA_VALUES)}),
    ] = Field(
        description="文件类型：outline(大纲)、character(角色)、lore(设定)、snippet(素材)、document(其他文档)、folder(文件夹)",
    )
    parent_id: StrictOptionalStr = Field(
        default=None,
        description="父文件ID（用于创建子文件/文件夹结构）",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="文件元数据（JSON对象）",
    )
    empty: CoercedOptionalBool = Field(
        default=None,
        description=(
            "仅在作者明确要求只建空文件、这一轮不写内容时（如「只建空档」「先建标题不写内容」）设为 true："
            "文件保持空白，不再用 <file> 写入；需要写入内容时不要设 true"
        ),
    )
    # Kept open as Any at runtime so _source_copy_content preserves exact
    # SOURCE_TEXT_INVALID_ERROR_TYPE / SOURCE_TEXT_CONFLICT_ERROR_TYPE contracts.
    source_text: Annotated[Any, WithJsonSchema({"type": "string"})] = Field(
        default=None,
        description=(
            "仅在录入作者本轮消息里已经写出的资料原文时使用：逐字复制作者消息中的一段完整连续原文，"
            "范围包括原有的标题、小标题和分隔行，即使标题同时用作文件名也不省略；作者明确只要正文时按其限定的范围复制。"
            "服务端会核对来源后直接保存，不要改标点、换行、排版或补字段。成功只证明选中的 source_text 已保存，不证明没有选入的原文也已录入。不能与 empty 或 content 同用；"
            "AI 自己生成的大纲、人物、设定不要传此参数，仍在下一次回复用 <file> 写入。"
        ),
    )
    # Internal-only fields (used by write_chapter & tests; hidden from LLM JSON Schema)
    content: str | None = None
    order: Any = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing_file_type(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and ("file_type" not in data or data["file_type"] is None):
            normalized = dict(data)
            normalized["file_type"] = "draft"
            return normalized
        return data

    @field_validator("file_type", mode="before")
    @classmethod
    def _validate_file_type(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("file_type 必须是字符串")
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("file_type 不能为空")
        return cleaned

    @field_validator("order", mode="before")
    @classmethod
    def _validate_order(cls, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("order 必须为整数，不能是布尔值")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class WriteChapterInput(_ToolInputModel):
    """Input model for ``write_chapter``."""

    _schema_excluded_fields: ClassVar[frozenset[str]] = frozenset({"metadata"})

    chapter: NonBoolInt = Field(
        ge=1,
        description="章号（剧本是集号），从 1 开始；调用前先读完大纲/前一章/角色卡，调用后立即输出 <file> 正文",
    )
    title: StrictStr = Field(
        json_schema_extra={"maxLength": 255},
        description=(
            "章节标题，如「第三章 雨夜」或只写「雨夜」（系统补上章号）；"
            "替换已有章节时传入新章名会在保存时一并更新标题，只传章号（如「第三章」）则沿用原标题"
        ),
    )
    file_type: ChapterFileType = Field(
        description="draft：小说章节；script：剧本分集",
    )
    parent_id: StrictOptionalStr = Field(
        default=None,
        description="正文所在文件夹 ID；分卷时用它指明是哪一卷",
    )
    empty: CoercedOptionalBool = Field(
        default=None,
        description="作者明确只要建空章节、这一轮不写正文时设为 true：这一章不存在就建一个空文件，已存在就不动",
    )
    # Internal pass-through for _write_chapter_create; hidden from LLM schema
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing_fields(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        normalized = dict(data)
        raw_ft = normalized.get("file_type")
        if raw_ft is None or (isinstance(raw_ft, str) and not raw_ft.strip()):
            normalized["file_type"] = "draft"
        if normalized.get("title") is None:
            normalized["title"] = ""
        return normalized

    @field_validator("chapter", mode="before")
    @classmethod
    def _validate_chapter(cls, value: Any) -> int:
        try:
            parsed = _parse_non_bool_int(value)
        except ValueError:
            raise ValueError("write_chapter 需要 chapter（正整数章号；剧本是集号），没有写入。") from None
        if parsed < 1:
            raise ValueError("write_chapter 需要 chapter（正整数章号；剧本是集号），没有写入。")
        return parsed

    @field_validator("file_type", mode="before")
    @classmethod
    def _validate_chapter_file_type(cls, value: Any) -> str:
        cleaned = str(value).strip().lower() if isinstance(value, str) else ""
        if cleaned not in ("draft", "script"):
            raise ValueError(
                "write_chapter 只写正文：file_type 是 draft（小说章节）或 script（剧本分集），没有写入。"
                "大纲、人物、设定等资料文件用 create_file。"
            )
        return cleaned

    @field_validator("title", mode="after")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return value.strip()


class EditOperationInput(_ToolInputModel):
    """Schema model for a single edit item inside ``edit_file.edits``."""

    op: EditOpType = Field(
        description="必填的操作类型；缺失时该项会在 failed_edits 中按 0-based index 明确列出，不会自动补全",
    )
    old: StrictOptionalStr = Field(
        default=None,
        description="要替换/删除的原文（用于replace/delete操作）。跨段时要照抄原文的换行和段首缩进，否则会被拒绝（不会把两段并成一段）",
    )
    new: StrictOptionalStr = Field(
        default=None,
        description="替换后的新文本（replace 必填；想删除原文请用 delete，不要省略 new）",
    )
    anchor: StrictOptionalStr = Field(
        default=None,
        description="锚点文本（用于insert_after/insert_before操作）",
    )
    text: StrictOptionalStr = Field(
        default=None,
        description=(
            "要写入的文本（insert_*/append/prepend 必填，不能为空）。"
            "锚点是一段的结尾（insert_after）或开头（insert_before）时：要另起一段，"
            "insert_after 的 text 以换行开头、insert_before 的以换行结尾，工具会换成本文已有的段落分隔与缩进；"
            "不带换行的 text 接在这一段的末尾/开头（适合补一句）。一整句话不会插在句子中间"
        ),
    )
    occurrence: OptionalNonBoolInt = Field(
        default=None,
        ge=1,
        description=(
            "当 old/anchor 匹配到多处时，指定要操作第几处（1-based，从 1 开始）。"
            "适用于 replace/delete/insert_after/insert_before。"
            "工具报「匹配到多个位置」时，首选加上这个参数（候选片段里已标出每处的序号），而不是 replace_all。"
        ),
    )
    replace_all: CoercedBool = Field(
        default=False,
        description="是否替换所有匹配项（仅用于replace操作）。破坏性选项：会不加次数上限地替换全部匹配处。只想改其中一处请改用 occurrence。",
    )
    match_mode: EditMatchMode = Field(
        default="auto",
        description="匹配模式：auto(默认，exact 失败后依次尝试模糊/近似匹配)；exact(只做逐字精确匹配，双引号写法差异除外，找不到就报错，适合不允许任何偏差的场景)",
    )
    ignore_punct_whitespace: CoercedBoolDefaultTrue = Field(
        default=True,
        description="模糊匹配时是否忽略标点与空白差异（默认 true）。设为 false 可要求标点也逐字一致。",
    )


_EDIT_OPERATION_ITEM_SCHEMA = export_tool_schema(EditOperationInput)


class EditFileInput(_ToolInputModel):
    """Input model for ``edit_file``.

    Individual items inside ``edits`` are intentionally kept as raw entries at the
    top-level boundary so ``FileEditor.edit_file(..., continue_on_error=True)`` can
    report per-item 0-based ``failed_edits`` (e.g. missing ``op``) without aborting
    valid sibling edits. Boolean flags present on dict items are safely coerced.
    """

    id: StrictStr = Field(
        validation_alias=AliasChoices("id", "file_id", "fileId"),
        description="要编辑的文件ID",
    )
    edits: Annotated[
        list[Any],
        WithJsonSchema({"type": "array", "items": _EDIT_OPERATION_ITEM_SCHEMA}),
    ] = Field(
        validation_alias=AliasChoices("edits", "operations"),
        description=(
            "编辑操作列表。每一项都必须显式填写 op，不会根据其他字段猜测操作。"
            "同一文件的多处互不依赖修改优先合并在同一次调用的 edits 里并传 continue_on_error=true（仅超过 15 处或后项依赖前项输出时才拆分）。"
            '最小示例：[{"op": "replace", "old": "当前原文", "new": "替换文本"}]。'
        ),
    )
    continue_on_error: CoercedBool = Field(
        default=False,
        description=(
            "同一文件有多处互不依赖的修改时，合并进一次 edit_file 调用并传 continue_on_error=true"
            "（跳过失败项，其余照常生效，结果里的 failed_edits 列出没改成的几处，后续只需补失败项）；"
            "仅在超过 15 处修改，或后一项依赖前一项改后的新文本时才拆成多次调用。"
            "默认 false：edits 是一个整体，任一处失败则本次调用的全部编辑都不生效（整体回滚），"
            "错误会一次列出所有失败项，整批都仍待修改，不能只补失败项并假定其他项已生效。"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        normalized = dict(_resolve_id_aliases(data))
        if "id" not in normalized:
            normalized["id"] = None
        if "edits" not in normalized and "operations" not in normalized:
            normalized["edits"] = []
        elif normalized.get("edits") is None and "operations" in normalized:
            normalized["edits"] = normalized["operations"]
        return normalized

    @field_validator("id", mode="before")
    @classmethod
    def _validate_id(cls, value: Any) -> str:
        if value is None:
            raise ValueError("edit_file 缺少必填参数 id（别名 file_id），请先用 query_files 获取准确的文件 ID。")
        if not isinstance(value, str):
            raise ValueError("edit_file 参数 id 必须是字符串。")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("edit_file 缺少必填参数 id（别名 file_id），请先用 query_files 获取准确的文件 ID。")
        return cleaned

    @field_validator("edits", mode="before")
    @classmethod
    def _validate_edits(cls, value: Any) -> list[Any]:
        if not isinstance(value, list):
            raise ValueError("edit_file 参数 edits 必须是数组。")
        coerced: list[Any] = []
        for item in value:
            if isinstance(item, dict) and ("replace_all" in item or "ignore_punct_whitespace" in item):
                updated = dict(item)
                if "replace_all" in updated:
                    updated["replace_all"] = _coerce_bool_false(updated["replace_all"])
                if "ignore_punct_whitespace" in updated:
                    updated["ignore_punct_whitespace"] = _coerce_bool_true(updated["ignore_punct_whitespace"])
                coerced.append(updated)
            else:
                coerced.append(item)
        return coerced


class DeleteFileInput(_ToolInputModel):
    """Input model for ``delete_file``."""

    id: StrictStr = Field(
        validation_alias=AliasChoices("id", "file_id", "fileId"),
        description="要删除的文件ID",
    )
    recursive: CoercedBool = Field(
        default=False,
        description="是否递归删除所有子文件",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        normalized = dict(_resolve_id_aliases(data))
        if "id" not in normalized:
            normalized["id"] = None
        return normalized

    @field_validator("id", mode="before")
    @classmethod
    def _validate_id(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("delete_file 需要非空的字符串文件 id")
        return value.strip()


class RenameFileInput(_ToolInputModel):
    """Input model for ``rename_file``."""

    id: StrictStr = Field(
        validation_alias=AliasChoices("id", "file_id", "fileId"),
        description="要改名的文件 ID",
    )
    title: StrictStr = Field(
        description="新标题，最多 255 字；只写标题，不要把正文放进来",
        json_schema_extra={"maxLength": 255},
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        normalized = dict(_resolve_id_aliases(data))
        if "id" not in normalized:
            normalized["id"] = None
        if "title" not in normalized:
            normalized["title"] = None
        return normalized

    @field_validator("id", mode="before")
    @classmethod
    def _validate_id(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("rename_file 需要文件 id")
        return value.strip()

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("rename_file 需要非空的新标题")
        return value.strip()


class QueryFilesInput(_ToolInputModel):
    """Input model for ``query_files``."""

    id: StrictOptionalStr = Field(
        default=None,
        validation_alias=AliasChoices("id", "file_id", "fileId"),
        description="按文件ID精确查询。按 id 读取默认返回全文；只想看预览时显式传 response_mode=summary",
    )
    ids: list[StrictStr] | None = Field(
        default=None,
        json_schema_extra={"maxItems": 5},
        description="按多个文件ID一次批量精确读取（最多 5 个，按传入顺序返回，默认返回全文；读取 2–5 个已知文件时优先使用 ids 代替多次调用）",
    )
    query: StrictOptionalStr = Field(
        default=None,
        description=(
            "搜索关键词（在标题和内容中按原文查找）。结果里每个文件附 match_count（正文命中处数）和 matches"
            "（每处命中前后的原文，逐字，可直接作 edit_file 的 old/anchor）；只改这些位置时不必再读全文"
        ),
    )
    file_type: AllFileType | None = Field(
        default=None,
        description="文件类型过滤（单个类型）",
    )
    file_types: list[AllFileType] | None = Field(
        default=None,
        description="文件类型过滤（多个类型）",
    )
    parent_id: StrictOptionalStr = Field(
        default=None,
        description="父文件ID过滤",
    )
    limit: OptionalNonBoolInt = Field(
        default=None,
        json_schema_extra={"default": 50},
        description="最大返回数量（默认 50；不按 id 且 response_mode=full 时默认 10）",
    )
    offset: OptionalNonBoolInt = Field(
        default=None,
        json_schema_extra={"default": 0},
        description="分页偏移量",
    )
    # Deferred to serialization.py so empty results & full-mode ignore keep exact legacy timing.
    response_mode: Annotated[
        Any,
        WithJsonSchema({"type": "string", "enum": ["summary", "full"]}),
    ] = Field(
        default=None,
        description=(
            "返回模式：full（返回完整 content）或 summary（只返回 content_preview，"
            "并给出 content_length 与 content_truncated，content_truncated=true 表示只是预览，"
            "这时不带 word_count——要长文件的字数按 id 读 full）。"
            "不传时：按 id 读取默认 full，其余默认 summary"
        ),
    )
    content_preview_chars: Annotated[
        Any,
        WithJsonSchema({"type": "integer", "minimum": 0}),
    ] = Field(
        default=None,
        json_schema_extra={"default": 200},
        description="summary 模式下 content_preview 的最大字符数（显式传此参数表示要预览，不返回全文）",
    )
    section: StrictOptionalStr = Field(
        default=None,
        description="按 id 读取时只读这个章节/分集：标题文字，如「第3章」「第十集」「Chapter 3」；读到下一个同级标题为止",
    )
    tail_chars: OptionalNonBoolInt = Field(
        default=None,
        ge=1,
        description="按 id 读取时只读正文最后这么多个字符（上限 20000）",
    )
    start_char: OptionalNonBoolInt = Field(
        default=None,
        ge=0,
        description="仅用于超过 15000 字的超长文件按 id 分段读取：从第几个字符开始读（从 0 起），配合 max_chars；常规长度文件直接按 id 读全文，不要分段",
    )
    max_chars: OptionalNonBoolInt = Field(
        default=None,
        ge=1,
        description="配合 start_char 读取超过 15000 字超长文件的字符数（默认 6000，上限 20000）",
    )
    include_content: CoercedOptionalBool = Field(
        default=None,
        json_schema_extra={"default": False},
        description="兼容参数：true 等同于 response_mode=full",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, data: Any) -> Any:
        return _resolve_id_aliases(data)

    @field_validator("file_type", mode="before")
    @classmethod
    def _normalize_file_type(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip().lower()
            return cleaned or None
        return value

    @field_validator("ids", mode="before")
    @classmethod
    def _validate_ids(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str | bytes) or not isinstance(value, list | tuple):
            raise ValueError("ids 必须是文件 ID 列表（最多 5 个）")
        for item in value:
            if not isinstance(item, str):
                raise ValueError("ids 里的每一项都必须是字符串文件 ID")
        return list(value)


class UnitSpecInput(BaseModel):
    """Schema model for ``update_project.tasks[].unit_spec``."""

    model_config = ConfigDict(extra="forbid")

    unit: UnitType
    numbers: list[Annotated[Annotated[int, Ge(1)], BeforeValidator(_parse_non_bool_int)]] = Field(
        min_length=1,
        max_length=2001,
        json_schema_extra={"uniqueItems": True},
    )


class UpdateProjectTaskInput(BaseModel):
    """Schema model for ``update_project.tasks[]``."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(description="任务描述")
    status: TaskStatusType = Field(
        description="任务状态；同一时刻最多一个任务为 in_progress，其余未开始的任务标 pending",
    )
    task_kind: TaskKindType | None = Field(
        default=None,
        description="file 为文件交付任务，conversation 为明确的对话里程碑。不能把文件任务改成对话任务绕过产物验证。",
    )
    phase_id: str | None = Field(
        default=None,
        description="阶段ID（可选，用于轻量 phase 状态机关联）",
    )
    artifact: str | None = Field(
        default=None,
        description="任务产出物（可选，例如文件名或里程碑）",
    )
    artifact_file_type: ArtifactFileType | None = Field(
        default=None,
        description="文件交付任务的产物类型。缺少类型的旧任务不会自动标完成；大纲不能证明正文交付。",
    )
    artifact_refs: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None,
        json_schema_extra={"uniqueItems": True},
        description="任务对应的实际 file_id 列表，不是文件名或展示路径。",
    )
    unit_spec: UnitSpecInput | None = Field(
        default=None,
        description="明确的交付编号。每章/集正文须有对应类型的实际文件，标题里的编号范围不算多章正文。",
    )
    done_when: str | None = Field(
        default=None,
        description="任务完成判定标准（可选）",
    )


_UPDATE_PROJECT_TASK_ITEM_SCHEMA = export_tool_schema(UpdateProjectTaskInput)


class UpdateProjectInput(_ToolInputModel):
    """Input model for ``update_project``.

    Length bounds on ``title`` / status fields are exported in the JSON Schema while
    runtime enforcement stays in ``ProjectOperations`` so overlong titles yield
    ``title_skipped="invalid_title"`` and notes overflow yields ``notes_note`` without
    discarding valid sibling fields in the same call.
    """

    title: StrictOptionalStr = Field(
        default=None,
        json_schema_extra={"maxLength": 30},
        description=(
            "作品名，30 字以内，不带书名号。项目还叫默认名或之前由 AI 起的名字时生效；"
            "结果里 project_name_updated=true 表示已改名，title_skipped=author_named 表示作者已自己起名、未改，"
            "这时把 title_note 里的手动改名入口原样告诉作者，不要自己编入口"
        ),
    )
    summary: StrictOptionalStr = Field(
        default=None,
        validation_alias=AliasChoices("summary", "projectSummary"),
        json_schema_extra={"maxLength": PROJECT_STATUS_MAX_LENGTHS["summary"]},
        description="项目摘要/背景介绍（整体替换；先合并现有内容；不写你自己选定或建议的内容）",
    )
    current_phase: StrictOptionalStr = Field(
        default=None,
        validation_alias=AliasChoices("current_phase", "currentPhase"),
        json_schema_extra={"maxLength": PROJECT_STATUS_MAX_LENGTHS["current_phase"]},
        description="当前写作阶段描述（作者会在 AI 记忆里看到，用作者使用的语言写）",
    )
    writing_style: StrictOptionalStr = Field(
        default=None,
        validation_alias=AliasChoices("writing_style", "writingStyle"),
        json_schema_extra={"maxLength": PROJECT_STATUS_MAX_LENGTHS["writing_style"]},
        description="作者提过的写作风格要求（整体替换；先合并现有内容；不写你自己定的规则）",
    )
    notes: StrictOptionalStr = Field(
        default=None,
        json_schema_extra={"maxLength": PROJECT_STATUS_MAX_LENGTHS["notes"]},
        description=(
            "作者明确让你记住的内容，用作者原话，一条一件事（「；」或换行分隔），"
            "不加推断、补充、前缀或括号；只传新条目，系统追加到写作约定末尾，已有条目原样保留"
        ),
    )
    # Forwarded to ProjectOperations.execute_update_plan so partial saves (status updated +
    # rejected plan) and legacy title/name task aliases keep exact behavior.
    tasks: Annotated[
        Any,
        WithJsonSchema({"type": "array", "items": _UPDATE_PROJECT_TASK_ITEM_SCHEMA}),
    ] = Field(
        default=None,
        description="完整任务列表；同一时刻最多一个任务为 in_progress，其余未开始的任务标 pending",
    )


class LoadSkillInput(_ToolInputModel):
    """Input model for ``load_skill``."""

    name: StrictStr = Field(
        description="技能名称（与技能目录中的名称一致），也可以传技能 ID；目录里标注了 id 的同名技能必须传 id",
    )

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("load_skill 需要非空的技能名称或 ID（name）")
        return value.strip()


class ReadSkillResourceInput(_ToolInputModel):
    """Input model for ``read_skill_resource``."""

    name: StrictStr = Field(
        description="技能名称（与 load_skill 使用的名称一致），也可以传技能 ID；同名技能必须传 id",
    )
    path: StrictStr = Field(
        description="资源文件路径（以 references/ 或 assets/ 开头），或 SKILL.md 表示技能正文",
    )
    offset: OptionalNonBoolInt = Field(
        default=None,
        ge=0,
        description="从第几个字符开始读取，默认 0；续读时传上次返回的 next_offset",
    )

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("read_skill_resource 需要非空的技能名称或 ID（name）")
        return value.strip()

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("read_skill_resource 需要非空的资源路径（path）")
        return value


TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "create_file": CreateFileInput,
    "write_chapter": WriteChapterInput,
    "edit_file": EditFileInput,
    "delete_file": DeleteFileInput,
    "rename_file": RenameFileInput,
    "query_files": QueryFilesInput,
    "update_project": UpdateProjectInput,
    "load_skill": LoadSkillInput,
    "read_skill_resource": ReadSkillResourceInput,
}


# ---------------------------------------------------------------------------
# Uniform structured Chinese validation error formatting
# ---------------------------------------------------------------------------


def _clean_pydantic_value_error(msg: str) -> str:
    for prefix in ("Value error, ", "Assertion failed, "):
        if msg.startswith(prefix):
            return msg[len(prefix) :]
    return msg


def _format_single_error(err: Mapping[str, Any]) -> tuple[str, bool]:
    """Translate one Pydantic v2 error dict to Chinese.

    Returns ``(text, is_complete_sentence)`` where ``is_complete_sentence=True``
    indicates a domain validator already supplied a self-contained message.
    """
    loc = ".".join(str(part) for part in (err.get("loc") or ()))
    err_type = str(err.get("type") or "")
    ctx = err.get("ctx") if isinstance(err.get("ctx"), Mapping) else {}
    raw_msg = str(err.get("msg") or "")

    if err_type == "value_error":
        cleaned = _clean_pydantic_value_error(raw_msg)
        if loc and not cleaned.startswith((loc, "write_chapter", "edit_file", "delete_file", "rename_file", "load_skill", "read_skill_resource")):
            return f"参数 {loc} {cleaned}", False
        return cleaned, True

    if err_type == "missing":
        return f"缺少必填参数 {loc}" if loc else "缺少必填参数", False
    if err_type in ("int_type", "int_parsing", "int_from_float"):
        return f"参数 {loc} 必须是整数" if loc else "参数必须是整数", False
    if err_type == "bool_type":
        return f"参数 {loc} 必须是布尔值" if loc else "参数必须是布尔值", False
    if err_type == "string_type":
        return f"参数 {loc} 必须是字符串" if loc else "参数必须是字符串", False
    if err_type == "list_type":
        return f"参数 {loc} 必须是数组" if loc else "参数必须是数组", False
    if err_type == "dict_type":
        return f"参数 {loc} 必须是 JSON 对象" if loc else "参数必须是 JSON 对象", False
    if err_type == "greater_than_equal":
        ge = ctx.get("ge")
        return f"参数 {loc} 必须为不小于 {ge} 的整数" if loc else f"参数不能小于 {ge}", False
    if err_type == "less_than_equal":
        le = ctx.get("le")
        return f"参数 {loc} 不能大于 {le}" if loc else f"参数不能大于 {le}", False
    if err_type == "string_too_short":
        min_len = ctx.get("min_length")
        return f"参数 {loc} 长度不能少于 {min_len} 个字符" if loc else f"参数长度不能少于 {min_len} 个字符", False
    if err_type == "string_too_long":
        max_len = ctx.get("max_length")
        return f"参数 {loc} 长度不能超过 {max_len} 个字符" if loc else f"参数长度不能超过 {max_len} 个字符", False
    if err_type == "too_short":
        min_len = ctx.get("min_length")
        return f"参数 {loc} 元素数量不能少于 {min_len} 个" if loc else f"参数元素数量不能少于 {min_len} 个", False
    if err_type == "too_long":
        max_len = ctx.get("max_length")
        return f"参数 {loc} 元素数量不能超过 {max_len} 个" if loc else f"参数元素数量不能超过 {max_len} 个", False
    if err_type == "extra_forbidden":
        return f"参数 {loc} 不被支持（多余字段）" if loc else "包含不支持的多余字段", False
    if err_type == "literal_error":
        expected = ctx.get("expected")
        if expected:
            expected_cn = str(expected).replace(", or ", " 或 ").replace(" or ", " 或 ").replace(", ", "、")
            return f"参数 {loc} 取值无效（允许值：{expected_cn}）" if loc else f"参数取值无效（允许值：{expected_cn}）", False
        return f"参数 {loc} 取值无效" if loc else "参数取值无效", False

    cleaned = _clean_pydantic_value_error(raw_msg)
    return (f"参数 {loc} 无效：{cleaned}" if loc else cleaned), False


def format_validation_error(
    tool_name: str,
    exc: ValidationError,
    *,
    error_type: str = TOOL_INPUT_VALIDATION_ERROR_TYPE,
    extra_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a Pydantic ``ValidationError`` into ZenStory's structured Chinese error dict.

    Returns a payload carrying ``status="error"``, ``error_type``, ``message`` and
    ``error`` (kept identical so ``ToolFailureBreaker``, UI cards, and tests all see
    the same Chinese message), plus ``mutation_applied=False``.
    """
    errors = exc.errors(include_url=False)
    parts: list[str] = []
    all_complete = bool(errors)
    for err in errors:
        text, complete = _format_single_error(err)
        if text and text not in parts:
            parts.append(text)
        if not complete:
            all_complete = False

    if not parts:
        message = f"{tool_name} 参数格式无效。"
    elif len(parts) == 1 and all_complete:
        message = parts[0]
    else:
        message = f"{tool_name} 参数错误：{'；'.join(parts)}"

    payload: dict[str, Any] = {
        "status": "error",
        "error_type": error_type,
        "error": message,
        "message": message,
        "mutation_applied": False,
    }
    if extra_fields:
        payload.update(extra_fields)
    return payload


def validate_tool_input[ModelT: BaseModel](
    model_cls: type[ModelT],
    raw_args: Any,
    *,
    tool_name: str,
    error_type: str = TOOL_INPUT_VALIDATION_ERROR_TYPE,
    extra_fields: Mapping[str, Any] | None = None,
) -> tuple[ModelT | None, dict[str, Any] | None]:
    """Validate raw tool arguments against ``model_cls``.

    Returns ``(validated_model, None)`` on success or ``(None, error_payload)`` on failure.
    """
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, Mapping):
        message = f"{tool_name} 参数错误：输入必须是 JSON 对象"
        payload: dict[str, Any] = {
            "status": "error",
            "error_type": error_type,
            "error": message,
            "message": message,
            "mutation_applied": False,
        }
        if extra_fields:
            payload.update(extra_fields)
        return None, payload

    try:
        return model_cls.model_validate(raw_args), None
    except ValidationError as exc:
        return None, format_validation_error(
            tool_name,
            exc,
            error_type=error_type,
            extra_fields=extra_fields,
        )


__all__ = [
    "TOOL_INPUT_MODELS",
    "TOOL_INPUT_VALIDATION_ERROR_TYPE",
    "CoercedBool",
    "CoercedBoolDefaultTrue",
    "CoercedOptionalBool",
    "CreateFileInput",
    "DeleteFileInput",
    "EditFileInput",
    "EditOperationInput",
    "LoadSkillInput",
    "NonBoolInt",
    "OptionalNonBoolInt",
    "QueryFilesInput",
    "ReadSkillResourceInput",
    "RenameFileInput",
    "StrictOptionalStr",
    "StrictStr",
    "UnitSpecInput",
    "UpdateProjectInput",
    "UpdateProjectTaskInput",
    "WriteChapterInput",
    "build_tool_schema",
    "export_tool_schema",
    "format_validation_error",
    "validate_tool_input",
]
