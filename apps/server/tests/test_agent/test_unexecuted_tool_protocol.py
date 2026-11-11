"""回复文字里完整、已注册、却没有执行的工具调用协议（A2）：只认结构，不猜意图或完成度。

正例是合成的、与匿名样本同形的骨架：成对的 <tool_call> 包着 <invoke name="已注册工具">…</invoke>，
其间可能夹着模型专用分隔符。反例覆盖文件正文、代码、引用、残片、未知工具和普通叙述。
"""

import pytest

from agent.graph.completion_evidence import unexecuted_tool_protocol

pytestmark = pytest.mark.unit

TOOLS = {"create_file", "edit_file", "query_files"}
SEP = "]<]minimax[>["


def _call(name: str = "create_file", sep: str = "\n") -> str:
    return (
        f"<tool_call>{sep}<invoke name=\"{name}\">{sep}<title>第七章</title>{sep}"
        f"<parent_id>folder-1</parent_id>{sep}</invoke>{sep}</tool_call>"
    )


@pytest.mark.parametrize("sep", ["\n", SEP, f"\n{SEP}\n", ""])
def test_complete_registered_call_in_the_tail_is_reported(sep):
    assert unexecuted_tool_protocol(f"好的，我来建第七章。\n{_call(sep=sep)}", TOOLS) == ["create_file"]


def test_each_complete_call_is_reported_once_in_order():
    text = _call("create_file") + "\n" + _call("edit_file")
    assert unexecuted_tool_protocol(text, TOOLS) == ["create_file", "edit_file"]


@pytest.mark.parametrize(
    "text",
    [
        # 文件正文里的完整调用（例如作者要的“工具说明”文档）：是正文，不是调用。
        f"<file>\n{_call()}\n</file>",
        f"<file>\n正文开头\n{_call()}",  # 未闭合的正文直到结尾
        # 代码围栏、行内代码、引用：示例或转述。
        f"格式如下：\n```xml\n{_call()}\n```",
        f"格式如下：\n~~~\n{_call()}\n~~~",
        f"写成 `{_call(sep='')}` 就行。",
        "\n".join(f"> {line}" for line in _call().splitlines()),
        # 未注册（或不属于当前 agent）的工具名。
        _call("delete_everything"),
        _call("handoff_to_agent"),
        # 残片：只有开头、只有结尾、没有 invoke、invoke 未闭合。
        "<tool_call>\n好的，下面是第七章的计划：",
        f"{SEP}</invoke>{SEP}</tool_call>",
        "<tool_call>\n<title>第七章</title>\n</tool_call>",
        "<tool_call>\n<invoke name=\"create_file\">\n<title>第七章</title>\n</tool_call>",
        "<invoke name=\"create_file\"><title>第七章</title></invoke>",  # 没有外层 <tool_call>
        # 文件尾的字面 </content>、面包屑标头、普通叙述里提到工具名。
        "第七章写完了。</content>",
        "[此前的工具操作]\n- 新建「第七章」(id=folder-1)",
        "我会用 create_file 新建第七章，再用 edit_file 改第六章的结尾。",
        "",
    ],
)
def test_examples_fragments_and_unknown_tools_are_not_reported(text):
    assert unexecuted_tool_protocol(text, TOOLS) == []


def test_only_the_registered_set_passed_in_counts():
    assert unexecuted_tool_protocol(_call("query_files"), {"create_file"}) == []
