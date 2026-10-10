"""on_llm_end 在执行本轮工具前标记"待写文件的正文已在本条回复里写完"。

回归：同一条回复里 "<file>上一集</file> + create_file(下一集)" 时，SDK 先执行
create_file(下一集)，StreamAdapter 却还没处理到 </file>、没落库清守卫，下一集
被空文件守卫误拒（"创建文件失败"），模型随后把已保存的正文原样重写进对话。
"""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openai.types.responses import ResponseOutputMessage, ResponseOutputText, ResponseReasoningItem
from openai.types.responses.response_reasoning_item import Summary

from agent.tools.mcp_tools import ToolContext


def _message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg-1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def _reasoning(text: str) -> ResponseReasoningItem:
    return ResponseReasoningItem(
        id="rs-1", type="reasoning", summary=[Summary(type="summary_text", text=text)]
    )


def _response(*items) -> SimpleNamespace:
    return SimpleNamespace(output=list(items), usage=SimpleNamespace(total_tokens=1))


@pytest.fixture
def pending_ep1():
    ToolContext.set_context(session=None, user_id="u-1", project_id="p-1", session_id="s-1")
    ToolContext.set_pending_empty_file("ep1", "第1集")
    yield
    ToolContext.clear_context()


def _next_create_allowed() -> bool:
    ok, _blocking = ToolContext.try_reserve_pending_empty_file("probe", "第2集")
    if ok:
        ToolContext.release_pending_empty_file("probe")
    return ok


def _hooks():
    from agent.openai_agents.usage_hooks import build_agent_run_hooks

    return build_agent_run_hooks("deepseek-flash")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_closed_file_block_marks_latest_pending_body(pending_ep1):
    with patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as meter:
        await _hooks().on_llm_end(
            None, None, _response(_reasoning("先写正文"), _message("好的。<file>\n正文\n</file>"))
        )

    meter.assert_called_once()
    assert _next_create_allowed() is True
    # 条目仍在，等 adapter 落库后清除
    assert ToolContext.get_pending_empty_file() == {"file_id": "ep1", "title": "第1集"}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        pytest.param([_message("<file>\n正文写到一半")], id="unclosed"),
        pytest.param([_message("<file>\n  \n</file>")], id="empty-body"),
        pytest.param([_message("格式如下：\n```\n<file>示例</file>\n```\n")], id="fenced"),
        pytest.param([_message("正文用 `<file>` 开头、`</file>` 结尾。")], id="inline-code"),
        pytest.param([_reasoning("<file>正文</file>"), _message("马上写。")], id="reasoning-only"),
        pytest.param([], id="no-message"),
    ],
)
async def test_reply_without_real_closed_body_does_not_mark(pending_ep1, output):
    with patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as meter:
        await _hooks().on_llm_end(None, None, _response(*output))

    meter.assert_called_once()
    assert _next_create_allowed() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_pending_file_is_a_no_op():
    ToolContext.set_context(session=None, user_id="u-1", project_id="p-1", session_id="s-1")
    try:
        with (
            patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as meter,
            patch.object(ToolContext, "mark_latest_pending_body_streamed") as mark,
        ):
            await _hooks().on_llm_end(None, None, _response(_message("<file>正文</file>")))
        meter.assert_called_once()
        mark.assert_not_called()
    finally:
        ToolContext.clear_context()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_failure_is_swallowed_and_usage_still_metered(pending_ep1):
    with (
        patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as meter,
        patch(
            "agent.openai_agents.usage_hooks._reply_has_closed_file_block",
            side_effect=RuntimeError("boom"),
        ),
    ):
        # on_llm_end 抛出会让整个 SDK run 失败：检查失败只能记日志
        await _hooks().on_llm_end(None, None, _response(_message("<file>正文</file>")))

    meter.assert_called_once()
    assert _next_create_allowed() is False


# ---------------------------------------------------------------------------
# 真实 openai-agents run loop：钩子拿到的是 chat-completions 转换后的 ModelResponse
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk

        return gen()

    async def close(self):
        return None


class _FakeCompletions:
    def __init__(self, text: str):
        self._text = text

    async def create(self, **_kwargs):
        from openai.types.chat import ChatCompletionChunk
        from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta
        from openai.types.completion_usage import CompletionUsage

        common = {
            "id": "c1",
            "created": int(time.time()),
            "model": "deepseek-flash",
            "object": "chat.completion.chunk",
        }
        return _FakeStream(
            [
                ChatCompletionChunk(
                    choices=[Choice(index=0, delta=ChoiceDelta(content=self._text), finish_reason=None)],
                    **common,
                ),
                ChatCompletionChunk(
                    choices=[Choice(index=0, delta=ChoiceDelta(), finish_reason="stop")],
                    usage=CompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                    **common,
                ),
            ]
        )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "allowed"),
    [("第1集如下。\n<file>\n雨夜，她推开门。\n</file>", True), ("第1集如下。\n<file>\n雨夜，", False)],
)
async def test_real_sdk_run_marks_body_from_model_response(pending_ep1, text, allowed):
    from agents import Agent, OpenAIChatCompletionsModel, RunConfig, Runner

    client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(text)), base_url="http://fake")
    agent = Agent(
        name="writer",
        instructions="x",
        model=OpenAIChatCompletionsModel(model="deepseek-flash", openai_client=client),
    )
    with patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as meter:
        result = Runner.run_streamed(
            agent, input="写第1集", hooks=_hooks(), run_config=RunConfig(tracing_disabled=True)
        )
        async for _event in result.stream_events():
            pass

    meter.assert_called_once()
    assert _next_create_allowed() is allowed
