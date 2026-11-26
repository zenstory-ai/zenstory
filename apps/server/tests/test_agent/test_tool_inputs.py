"""Comprehensive tests for Pydantic v2 tool input models & compact schema export."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.openai_agents.tool_failure_breaker import (
    MAX_IDENTICAL_TOOL_FAILURES,
    TRIP_REASON_IDENTICAL,
    ToolFailureBreaker,
)
from agent.openai_agents.tools_adapter import (
    _cancels_overdue_file,
    build_agent_function_tools,
    invoke_project_tool,
)
from agent.tools import mcp_tools
from agent.tools.registry import LOOP_TOOL_NAMES
from agent.tools.tool_context import AgentRequestContext
from agent.tools.tool_inputs import (
    TOOL_INPUT_MODELS,
    TOOL_INPUT_VALIDATION_ERROR_TYPE,
    CreateFileInput,
    DeleteFileInput,
    EditFileInput,
    EditOperationInput,
    LoadSkillInput,
    QueryFilesInput,
    ReadFilesInput,
    ReadSkillResourceInput,
    RenameFileInput,
    SearchFilesInput,
    UpdateProjectInput,
    WriteChapterInput,
    export_tool_schema,
    validate_tool_input,
)
from agent.tools.tool_schemas import TOOL_SCHEMAS

# Compact JSON char count budget for exposed LOOP_TOOL_NAMES schemas (significantly leaner after B1/B2/B3/Option A).
BASELINE_EXPOSED_TOOL_SCHEMAS_COMPACT_CHARS = 10_500


def _parse_mcp(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["content"][0]["text"])


def _walk_schema_objects(node: Any):
    """Yield every JSON Schema object node (skipping property-name mapping dicts)."""
    if not isinstance(node, dict):
        return
    yield node
    props = node.get("properties")
    if isinstance(props, dict):
        for prop_schema in props.values():
            yield from _walk_schema_objects(prop_schema)
    items = node.get("items")
    if isinstance(items, dict):
        yield from _walk_schema_objects(items)
    any_of = node.get("anyOf")
    if isinstance(any_of, list):
        for branch in any_of:
            yield from _walk_schema_objects(branch)


# ---------------------------------------------------------------------------
# 1. Schema parity, open metadata: dict, zero Pydantic bloat, token budget
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_input_models_cover_every_loop_tool_in_order():
    for name in LOOP_TOOL_NAMES:
        assert name in TOOL_INPUT_MODELS
        assert name in TOOL_SCHEMAS
    # Legacy query_files stays registered for backend/replay compatibility, excluded from exposed LOOP_TOOL_NAMES
    assert "query_files" in TOOL_INPUT_MODELS
    assert "query_files" in TOOL_SCHEMAS
    assert "query_files" not in LOOP_TOOL_NAMES
    assert "read_files" in LOOP_TOOL_NAMES
    assert "search_files" in LOOP_TOOL_NAMES


@pytest.mark.unit
def test_exported_schemas_have_zero_pydantic_bloat_and_stay_within_baseline_budget():
    exposed = [TOOL_SCHEMAS[name] for name in LOOP_TOOL_NAMES]
    compact = json.dumps(exposed, ensure_ascii=False, separators=(",", ":"))
    assert len(compact) <= BASELINE_EXPOSED_TOOL_SCHEMAS_COMPACT_CHARS

    for tool_name, entry in TOOL_SCHEMAS.items():
        schema = entry["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema
        assert "description" not in schema, f"{tool_name} leaked class docstring into input_schema"

        for node in _walk_schema_objects(schema):
            assert "title" not in node, f"{tool_name} leaked Pydantic title: {node}"
            assert "$defs" not in node, f"{tool_name} leaked $defs"
            assert "$ref" not in node, f"{tool_name} leaked $ref"
            assert "anyOf" not in node, f"{tool_name} leaked verbose anyOf union: {node}"
            if "default" in node:
                assert node["default"] is not None, f"{tool_name} leaked redundant default: null"
            if "additionalProperties" in node:
                assert node["additionalProperties"] is False, f"{tool_name} leaked redundant additionalProperties: true"


@pytest.mark.unit
def test_exported_schemas_preserve_required_properties_constraints_and_open_metadata():
    # create_file: open metadata object, non-body file_type enum, hidden internal content/order
    create_schema = TOOL_SCHEMAS["create_file"]["input_schema"]
    assert create_schema["required"] == ["title", "file_type"]
    create_props = create_schema["properties"]
    assert list(create_props.keys()) == ["title", "file_type", "parent_id", "metadata", "empty", "source_text"]
    assert create_props["title"]["maxLength"] == 255
    assert create_props["file_type"]["enum"] == ["outline", "character", "lore", "snippet", "document", "folder"]
    assert create_props["metadata"] == {"type": "object", "description": "文件元数据（JSON对象）"}
    assert "content" not in create_props and "order" not in create_props

    # write_chapter: chapter minimum=1, file_type enum draft/script, hidden metadata, pre-read & immediate <file> guidance
    wc_schema = TOOL_SCHEMAS["write_chapter"]["input_schema"]
    assert wc_schema["required"] == ["chapter", "title", "file_type"]
    assert wc_schema["properties"]["chapter"]["minimum"] == 1
    assert "<file>" in wc_schema["properties"]["chapter"]["description"]
    assert "大纲" in wc_schema["properties"]["chapter"]["description"]
    assert "read_files" in TOOL_SCHEMAS["write_chapter"]["description"]
    assert "<file>" in TOOL_SCHEMAS["write_chapter"]["description"]
    assert wc_schema["properties"]["title"]["maxLength"] == 255
    assert wc_schema["properties"]["file_type"]["enum"] == ["draft", "script"]
    assert "metadata" not in wc_schema["properties"]

    # edit_file (B3): 4 core ops, hidden match_mode / ignore_punct_whitespace / replace_all
    edit_schema = TOOL_SCHEMAS["edit_file"]["input_schema"]
    assert edit_schema["required"] == ["id", "edits"]
    assert edit_schema["properties"]["continue_on_error"]["type"] == "boolean"
    assert "default" not in edit_schema["properties"]["continue_on_error"]
    edit_item = edit_schema["properties"]["edits"]["items"]
    assert edit_item["required"] == ["op"]
    assert edit_item["properties"]["op"]["enum"] == ["replace", "append", "insert_after", "insert_before"]
    assert edit_item["properties"]["occurrence"]["minimum"] == 1
    for hidden_key in ("replace_all", "match_mode", "ignore_punct_whitespace"):
        assert hidden_key not in edit_item["properties"]

    # delete_file & rename_file
    assert TOOL_SCHEMAS["delete_file"]["input_schema"]["required"] == ["id"]
    assert TOOL_SCHEMAS["delete_file"]["input_schema"]["properties"]["recursive"]["default"] is False
    assert TOOL_SCHEMAS["rename_file"]["input_schema"]["required"] == ["id", "title"]
    assert TOOL_SCHEMAS["rename_file"]["input_schema"]["properties"]["title"]["maxLength"] == 255

    # read_files (B2): required ids maxItems=5, range constraints, show_paragraph_stats
    rf_schema = TOOL_SCHEMAS["read_files"]["input_schema"]
    assert rf_schema["required"] == ["ids"]
    rf_props = rf_schema["properties"]
    assert rf_props["ids"]["maxItems"] == 5
    assert rf_props["tail_chars"]["minimum"] == 1
    assert rf_props["start_char"]["minimum"] == 0
    assert rf_props["max_chars"]["minimum"] == 1
    assert rf_props["show_paragraph_stats"]["default"] is False

    # search_files (B2): query + filters, no response_mode/include_content
    sf_schema = TOOL_SCHEMAS["search_files"]["input_schema"]
    assert sf_schema["required"] == []
    sf_props = sf_schema["properties"]
    assert sf_props["limit"]["default"] == 30
    assert "query" in sf_props and "file_types" in sf_props and "parent_id" in sf_props and "ids" in sf_props
    assert "response_mode" not in sf_props and "include_content" not in sf_props

    # query_files (legacy compat): still valid schema with hidden include_content/content_preview_chars
    qf_schema = TOOL_SCHEMAS["query_files"]["input_schema"]
    assert qf_schema["required"] == []
    qf_props = qf_schema["properties"]
    assert qf_props["ids"]["maxItems"] == 5
    assert qf_props["limit"]["default"] == 50
    assert qf_props["offset"]["default"] == 0
    assert qf_props["tail_chars"]["minimum"] == 1
    assert qf_props["start_char"]["minimum"] == 0
    assert qf_props["max_chars"]["minimum"] == 1
    assert qf_props["response_mode"]["enum"] == ["summary", "full", "matches"]
    assert qf_props["show_paragraph_stats"]["default"] is False

    # update_project (B1): tasks removed, memory fields kept
    up_schema = TOOL_SCHEMAS["update_project"]["input_schema"]
    assert up_schema["required"] == []
    assert up_schema["properties"]["title"]["maxLength"] == 30
    assert "tasks" not in up_schema["properties"]
    for key in ("title", "summary", "writing_style", "current_phase", "notes"):
        assert key in up_schema["properties"]

    # load_skill & read_skill_resource
    assert TOOL_SCHEMAS["load_skill"]["input_schema"]["required"] == ["name"]
    rsr_schema = TOOL_SCHEMAS["read_skill_resource"]["input_schema"]
    assert rsr_schema["required"] == ["name", "path"]
    assert rsr_schema["properties"]["offset"]["minimum"] == 0

    # batch_replace_files: ultra-compact 5 fields
    br_schema = TOOL_SCHEMAS["batch_replace_files"]["input_schema"]
    assert br_schema["required"] == ["old_text", "new_text"]
    assert list(br_schema["properties"].keys()) == ["old_text", "new_text", "file_ids", "file_type", "dry_run"]
    assert br_schema["properties"]["dry_run"]["default"] is False


# ---------------------------------------------------------------------------
# 2. Coercion: CoercedBool, strict non-bool int, parameter aliases
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_bool", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("fals", False),
        ("", False),
        (1, True),
        (0, False),
        (None, False),
    ],
)
def test_coerce_bool_across_tool_models(raw_bool, expected):
    delete_in, err = validate_tool_input(DeleteFileInput, {"id": "f-1", "recursive": raw_bool}, tool_name="delete_file")
    assert err is None and delete_in is not None and delete_in.recursive is expected

    edit_in, err = validate_tool_input(
        EditFileInput,
        {"id": "f-1", "edits": [], "continue_on_error": raw_bool},
        tool_name="edit_file",
    )
    assert err is None and edit_in is not None and edit_in.continue_on_error is expected


@pytest.mark.unit
def test_query_files_include_content_preserves_three_valued_semantics():
    omitted, err = validate_tool_input(QueryFilesInput, {}, tool_name="query_files")
    assert err is None and omitted is not None
    assert omitted.include_content is None
    assert "include_content" not in omitted.model_fields_set

    explicit_none, err = validate_tool_input(QueryFilesInput, {"include_content": None}, tool_name="query_files")
    assert err is None and explicit_none is not None
    assert explicit_none.include_content is None
    assert "include_content" in explicit_none.model_fields_set

    str_true, err = validate_tool_input(QueryFilesInput, {"include_content": "true"}, tool_name="query_files")
    assert err is None and str_true is not None and str_true.include_content is True

    str_false, err = validate_tool_input(QueryFilesInput, {"include_content": "false"}, tool_name="query_files")
    assert err is None and str_false is not None and str_false.include_content is False


@pytest.mark.unit
@pytest.mark.parametrize("bad_bool", [True, False])
def test_bool_is_strictly_rejected_for_integer_fields(bad_bool):
    # query_files integer fields
    for field in ("start_char", "max_chars", "tail_chars", "limit", "offset"):
        parsed, err = validate_tool_input(QueryFilesInput, {field: bad_bool}, tool_name="query_files")
        assert parsed is None and err is not None
        assert err["status"] == "error"
        assert err["error_type"] == TOOL_INPUT_VALIDATION_ERROR_TYPE
        assert "布尔值" in err["message"]
        assert err["error"] == err["message"]

    # write_chapter.chapter
    parsed, err = validate_tool_input(
        WriteChapterInput,
        {"chapter": bad_bool, "title": "第一章", "file_type": "draft"},
        tool_name="write_chapter",
        error_type="chapter_invalid",
    )
    assert parsed is None and err is not None
    assert err["error_type"] == "chapter_invalid"

    # read_skill_resource.offset
    parsed, err = validate_tool_input(
        ReadSkillResourceInput,
        {"name": "s", "path": "SKILL.md", "offset": bad_bool},
        tool_name="read_skill_resource",
    )
    assert parsed is None and err is not None and "布尔值" in err["message"]

    # create_file.order
    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": "outline", "order": bad_bool},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "布尔值" in err["message"]

    # EditOperationInput.occurrence
    parsed, err = validate_tool_input(
        EditOperationInput,
        {"op": "replace", "old": "a", "new": "b", "occurrence": bad_bool},
        tool_name="edit_file",
    )
    assert parsed is None and err is not None and "布尔值" in err["message"]

    # ReadFilesInput & SearchFilesInput integer fields
    for field in ("start_char", "max_chars", "tail_chars"):
        parsed, err = validate_tool_input(ReadFilesInput, {"ids": ["f-1"], field: bad_bool}, tool_name="read_files")
        assert parsed is None and err is not None and "布尔值" in err["message"]
    parsed, err = validate_tool_input(SearchFilesInput, {"limit": bad_bool}, tool_name="search_files")
    assert parsed is None and err is not None and "布尔值" in err["message"]


@pytest.mark.unit
@pytest.mark.parametrize("id_key", ["id", "file_id", "fileId"])
def test_file_id_aliases_work_across_file_tools(id_key):
    edit_in, err = validate_tool_input(
        EditFileInput,
        {id_key: "  f-42  ", "operations": [{"op": "append", "text": "hi", "replace_all": "false"}]},
        tool_name="edit_file",
    )
    assert err is None and edit_in is not None
    assert edit_in.id == "f-42"
    assert edit_in.edits == [{"op": "append", "text": "hi", "replace_all": False}]

    del_in, err = validate_tool_input(DeleteFileInput, {id_key: "f-42"}, tool_name="delete_file")
    assert err is None and del_in is not None and del_in.id == "f-42"

    ren_in, err = validate_tool_input(RenameFileInput, {id_key: "f-42", "title": "新标题"}, tool_name="rename_file")
    assert err is None and ren_in is not None and ren_in.id == "f-42" and ren_in.title == "新标题"

    qf_in, err = validate_tool_input(QueryFilesInput, {id_key: "f-42"}, tool_name="query_files")
    assert err is None and qf_in is not None and qf_in.id == "f-42"


@pytest.mark.unit
def test_empty_id_falls_back_to_file_id_alias_and_update_project_aliases_work():
    edit_in, err = validate_tool_input(
        EditFileInput,
        {"id": "", "file_id": "f-fallback", "edits": []},
        tool_name="edit_file",
    )
    assert err is None and edit_in is not None and edit_in.id == "f-fallback"

    up_in, err = validate_tool_input(
        UpdateProjectInput,
        {
            "projectSummary": "简介",
            "currentPhase": "第二章",
            "writingStyle": "冷峻克制",
        },
        tool_name="update_project",
    )
    assert err is None and up_in is not None
    assert up_in.summary == "简介"
    assert up_in.current_phase == "第二章"
    assert up_in.writing_style == "冷峻克制"
    assert up_in.model_fields_set == {"summary", "current_phase", "writing_style"}


# ---------------------------------------------------------------------------
# 3. Structured Chinese error contract & ToolFailureBreaker compatibility
# ---------------------------------------------------------------------------


def _mock_req_ctx(mock_executor: MagicMock, *, project_id: str = "proj-1") -> AgentRequestContext:
    req_ctx = AgentRequestContext(session=MagicMock(), project_id=project_id, session_id="sess-1")
    req_ctx.executor = MagicMock(return_value=mock_executor)
    return req_ctx


@pytest.mark.asyncio
@pytest.mark.unit
async def test_direct_mcp_tools_return_structured_chinese_validation_errors():
    mock_executor = MagicMock()
    req_ctx = _mock_req_ctx(mock_executor)

    # edit_file bad id type -> invalid_edit + edits_applied=0 + Chinese error & message
    bad_edit = _parse_mcp(await mcp_tools.edit_file({"id": 123, "edits": []}, request_context=req_ctx))
    assert bad_edit["status"] == "error"
    assert bad_edit["error_type"] == "invalid_edit"
    assert bad_edit["mutation_applied"] is False
    assert bad_edit["edits_applied"] == 0
    assert bad_edit["message"] == bad_edit["error"]
    assert "字符串" in bad_edit["message"]

    # query_files bool passed for start_char -> invalid_tool_input
    bad_query = _parse_mcp(
        await mcp_tools.query_files({"id": "f-1", "start_char": True}, request_context=req_ctx)
    )
    assert bad_query["status"] == "error"
    assert bad_query["error_type"] == TOOL_INPUT_VALIDATION_ERROR_TYPE
    assert bad_query["mutation_applied"] is False
    assert bad_query["message"] == bad_query["error"]
    assert "布尔值" in bad_query["message"]

    # delete_file missing id -> invalid_tool_input
    bad_del = _parse_mcp(await mcp_tools.delete_file({}, request_context=req_ctx))
    assert bad_del["status"] == "error"
    assert bad_del["error_type"] == TOOL_INPUT_VALIDATION_ERROR_TYPE
    assert bad_del["message"] == bad_del["error"]

    # write_chapter bool chapter -> WRITE_CHAPTER_INVALID_ERROR_TYPE
    bad_wc = _parse_mcp(
        await mcp_tools.write_chapter(
            {"chapter": True, "title": "第一章", "file_type": "draft"},
            request_context=req_ctx,
        )
    )
    assert bad_wc["status"] == "error"
    assert bad_wc["error_type"] == mcp_tools.WRITE_CHAPTER_INVALID_ERROR_TYPE
    assert bad_wc["message"] == bad_wc["error"]

    mock_executor.edit_file.assert_not_called()
    mock_executor.query_files.assert_not_called()
    mock_executor.delete_file_with_meta.assert_not_called()


@pytest.mark.unit
def test_validation_error_payload_is_compatible_with_tool_failure_breaker():
    _, err_payload = validate_tool_input(
        QueryFilesInput,
        {"id": "f-1", "start_char": True},
        tool_name="query_files",
    )
    assert err_payload is not None
    raw_output = json.dumps(err_payload, ensure_ascii=False)
    raw_args = json.dumps({"id": "f-1", "start_char": True})

    breaker = ToolFailureBreaker()
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe("query_files", raw_args, raw_output)

    assert breaker.is_open is True
    assert breaker.trip is not None
    assert breaker.trip.reason == TRIP_REASON_IDENTICAL
    assert "布尔值" in breaker.trip.last_error


# ---------------------------------------------------------------------------
# 4. strict_json_schema=False + json_repair + overdue-file cancellation guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sdk_function_tools_keep_strict_json_schema_false_and_clean_params():
    tools = build_agent_function_tools()
    assert [tool.name for tool in tools] == LOOP_TOOL_NAMES
    for tool in tools:
        assert tool.strict_json_schema is False
        assert tool.params_json_schema == export_tool_schema(TOOL_INPUT_MODELS[tool.name])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_invoke_project_tool_repairs_json_before_pydantic_validation():
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {"id": "f-1", "edits_applied": 1, "mutation_applied": True}
    req_ctx = _mock_req_ctx(mock_executor)

    # Trailing comma & single quotes (repaired by json_repair) + alias file_id + string bool "false"
    malformed_json = "{'file_id': 'f-1', 'operations': [{'op': 'append', 'text': '你好'}], 'continue_on_error': 'false',}"
    output_text = await invoke_project_tool("edit_file", malformed_json, request_context=req_ctx)

    payload = json.loads(output_text)
    assert payload["status"] == "success"
    mock_executor.edit_file.assert_called_once()
    call_kwargs = mock_executor.edit_file.call_args.kwargs
    assert call_kwargs["id"] == "f-1"
    assert call_kwargs["edits"] == [{"op": "append", "text": "你好"}]
    assert call_kwargs["continue_on_error"] is False


@pytest.mark.unit
def test_cancels_overdue_file_uses_delete_file_input_aliases_and_bool_coercion():
    overdue = {"pending-1"}
    assert _cancels_overdue_file('{"id": "pending-1", "recursive": "false"}', overdue) == "pending-1"
    assert _cancels_overdue_file('{"file_id": "pending-1", "recursive": "0"}', overdue) == "pending-1"
    assert _cancels_overdue_file('{"fileId": "pending-1"}', overdue) == "pending-1"
    assert _cancels_overdue_file('{"id": "pending-1", "recursive": "true"}', overdue) is None
    assert _cancels_overdue_file('{"id": "other-file"}', overdue) is None
    assert _cancels_overdue_file('{"id": 123}', overdue) is None


@pytest.mark.unit
def test_load_skill_and_read_skill_resource_validation():
    parsed, err = validate_tool_input(LoadSkillInput, {"name": "  悬念大师  "}, tool_name="load_skill")
    assert err is None and parsed is not None and parsed.name == "悬念大师"

    parsed, err = validate_tool_input(LoadSkillInput, {"name": "   "}, tool_name="load_skill")
    assert parsed is None and err is not None and err["error_type"] == TOOL_INPUT_VALIDATION_ERROR_TYPE

    parsed, err = validate_tool_input(
        ReadSkillResourceInput,
        {"name": "s1", "path": "SKILL.md", "offset": "120"},
        tool_name="read_skill_resource",
    )
    assert err is None and parsed is not None and parsed.offset == 120

    parsed, err = validate_tool_input(
        ReadSkillResourceInput,
        {"name": "s1", "path": "SKILL.md", "offset": -5},
        tool_name="read_skill_resource",
    )
    assert parsed is None and err is not None and "不小于 0" in err["message"]


# ---------------------------------------------------------------------------
# 5. Verifier regression tests (null-bool, blank file_type, fallback defaults, Chinese errors)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ignore_punct_whitespace_none_preserves_default_true():
    """Passing null for ignore_punct_whitespace must stay True (matching FileEditor default)."""
    op_in, err = validate_tool_input(
        EditOperationInput,
        {"op": "replace", "old": "a", "new": "b", "ignore_punct_whitespace": None},
        tool_name="edit_file",
    )
    assert err is None and op_in is not None and op_in.ignore_punct_whitespace is True

    edit_in, err = validate_tool_input(
        EditFileInput,
        {
            "id": "f-1",
            "edits": [
                {"op": "replace", "old": "a", "new": "b", "ignore_punct_whitespace": None},
                {"op": "replace", "old": "c", "new": "d", "ignore_punct_whitespace": "false"},
                {"op": "replace", "old": "e", "new": "f", "ignore_punct_whitespace": "true"},
            ],
        },
        tool_name="edit_file",
    )
    assert err is None and edit_in is not None
    assert [item["ignore_punct_whitespace"] for item in edit_in.edits] == [True, False, True]


@pytest.mark.unit
def test_query_files_blank_file_type_normalizes_to_none_and_whitespace_case_folded():
    for blank in ("", "   ", None):
        parsed, err = validate_tool_input(QueryFilesInput, {"file_type": blank}, tool_name="query_files")
        assert err is None and parsed is not None and parsed.file_type is None

    parsed, err = validate_tool_input(QueryFilesInput, {"file_type": "  DRAFT  "}, tool_name="query_files")
    assert err is None and parsed is not None and parsed.file_type == "draft"

    parsed, err = validate_tool_input(QueryFilesInput, {"file_type": 123}, tool_name="query_files")
    assert parsed is None and err is not None and "允许值" in err["message"]
    assert " or " not in err["message"]


@pytest.mark.unit
def test_write_chapter_restores_fallback_defaults_for_omitted_file_type_and_title():
    parsed, err = validate_tool_input(WriteChapterInput, {"chapter": 3}, tool_name="write_chapter")
    assert err is None and parsed is not None
    assert parsed.chapter == 3
    assert parsed.file_type == "draft"
    assert parsed.title == ""

    parsed, err = validate_tool_input(
        WriteChapterInput,
        {"chapter": "2", "title": None, "file_type": ""},
        tool_name="write_chapter",
    )
    assert err is None and parsed is not None
    assert parsed.chapter == 2 and parsed.file_type == "draft" and parsed.title == ""

    # Whole float accepted; fractional float / <=0 / non-body file_type rejected
    parsed, err = validate_tool_input(WriteChapterInput, {"chapter": 4.0}, tool_name="write_chapter")
    assert err is None and parsed is not None and parsed.chapter == 4

    for bad_ch in (0, -2, 1.5, "abc", "", []):
        parsed, err = validate_tool_input(WriteChapterInput, {"chapter": bad_ch}, tool_name="write_chapter")
        assert parsed is None and err is not None and "正整数" in err["message"]

    parsed, err = validate_tool_input(
        WriteChapterInput,
        {"chapter": 1, "title": "大纲", "file_type": "outline"},
        tool_name="write_chapter",
    )
    assert parsed is None and err is not None and "draft" in err["message"]


@pytest.mark.unit
def test_chinese_error_formatting_covers_all_pydantic_error_types_without_english_leakage():
    from pydantic import BaseModel, ConfigDict, Field

    from agent.tools.tool_inputs import _format_single_error

    class _StrictSample(BaseModel):
        model_config = ConfigDict(extra="forbid")
        items: list[str] = Field(min_length=1, max_length=2)
        tag: str = Field(min_length=1)

    # Non-mapping raw_args & None raw_args
    parsed, err = validate_tool_input(QueryFilesInput, ["not-a-dict"], tool_name="query_files")
    assert parsed is None and err is not None and "JSON 对象" in err["message"]
    parsed, err = validate_tool_input(QueryFilesInput, None, tool_name="query_files")
    assert err is None and parsed is not None

    # literal_error: no English " or "
    parsed, err = validate_tool_input(QueryFilesInput, {"file_type": "bogus"}, tool_name="query_files")
    assert parsed is None and err is not None
    assert " 或 " in err["message"] and " or " not in err["message"]

    # extra_forbidden on strict sub-model
    parsed, err = validate_tool_input(
        _StrictSample,
        {"items": ["a"], "tag": "x", "unknown_field": 1},
        tool_name="sample",
    )
    assert parsed is None and err is not None and "多余字段" in err["message"]

    # too_short / too_long / string_too_short
    parsed, err = validate_tool_input(_StrictSample, {"items": [], "tag": "x"}, tool_name="sample")
    assert parsed is None and err is not None and "不能少于 1 个" in err["message"]

    parsed, err = validate_tool_input(_StrictSample, {"items": ["a", "b", "c"], "tag": "x"}, tool_name="sample")
    assert parsed is None and err is not None and "不能超过 2 个" in err["message"]

    parsed, err = validate_tool_input(_StrictSample, {"items": ["a"], "tag": ""}, tool_name="sample")
    assert parsed is None and err is not None and "不能少于 1 个字符" in err["message"]

    # dict_type, list_type, string_type, non-int string/empty, non-string ids items, non-list edits
    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": "outline", "metadata": "not-a-dict", "order": "bad"},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "JSON 对象" in err["message"]

    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": 123},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "字符串" in err["message"]

    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": "   "},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "不能为空" in err["message"]

    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": "outline", "parent_id": 123, "order": "abc"},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "字符串" in err["message"]

    parsed, err = validate_tool_input(
        EditFileInput,
        {"id": "f-1", "edits": "not-a-list"},
        tool_name="edit_file",
    )
    assert parsed is None and err is not None and "数组" in err["message"]

    parsed, err = validate_tool_input(QueryFilesInput, {"ids": "not-a-list"}, tool_name="query_files")
    assert parsed is None and err is not None and "ID 列表" in err["message"]

    parsed, err = validate_tool_input(QueryFilesInput, {"ids": [123]}, tool_name="query_files")
    assert parsed is None and err is not None and "字符串文件 ID" in err["message"]

    parsed, err = validate_tool_input(QueryFilesInput, {"start_char": ""}, tool_name="query_files")
    assert err is None and parsed is not None and parsed.start_char is None

    parsed, err = validate_tool_input(ReadSkillResourceInput, {"name": "", "path": ""}, tool_name="read_skill_resource")
    assert parsed is None and err is not None and "name" in err["message"] and "path" in err["message"]

    # Direct unit checks for remaining _format_single_error mappings
    assert "整数" in _format_single_error({"loc": ("x",), "type": "int_type", "msg": ""})[0]
    assert "布尔值" in _format_single_error({"loc": ("x",), "type": "bool_type", "msg": ""})[0]
    assert "字符串" in _format_single_error({"loc": ("x",), "type": "string_type", "msg": ""})[0]
    assert "数组" in _format_single_error({"loc": ("x",), "type": "list_type", "msg": ""})[0]
    assert "不能大于 10" in _format_single_error({"loc": ("x",), "type": "less_than_equal", "ctx": {"le": 10}, "msg": ""})[0]
    assert "超过 30" in _format_single_error({"loc": ("x",), "type": "string_too_long", "ctx": {"max_length": 30}, "msg": ""})[0]
    assert "取值无效" in _format_single_error({"loc": ("x",), "type": "literal_error", "msg": ""})[0]
    assert "无效" in _format_single_error({"loc": ("x",), "type": "custom_unknown", "msg": "Assertion failed, bad"})[0]

