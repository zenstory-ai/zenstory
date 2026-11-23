"""单循环 agent：一个模型带写作工具集跑完整轮，没有路由、交接、计划或自动审稿。

它接在 ``AgentService.process_stream`` 的同一个生成器接口上（产出 ``core.workflow_events.StreamEvent``），
SSE 外壳、计费 / 退还、停止 / 插话、残稿补存、文件版本并发保护都沿用。一轮里同一个 agent 可能再跑几次
（都从上一次完整的 SDK 输入项 ``state["run_items"]`` 接着来，见 ``runner.build_run_input``），只因为结构化事实：

- 空文件纠偏：create_file / write_chapter 建了文件、正文没用 <file> 写成（落库核验），为同一个文件恢复正文写入后
  再跑一次；补不成就回滚能确认的空文件并发 ``empty_file_correction`` 卡。write_chapter 定位到已有非空章节的
  整章替换不算空文件：没收到正文时原文不动，在纠偏次数内为这一章恢复正文捕获再跑一次（核对定位时的版本），
  补不成就作废定位。
- 文件流续写：这一次 run 建文件或定位章节成功、回复停在写完的 </file> 上（SDK 把它当最终输出），接着跑一次。
- 运行中插话：工具边界或 run 边界消费到作者的插话，接着跑一次让它在本轮生效。

请求墙钟、模型调用预算、工具轮数上限与失控退还都由 runner / run_meter / stream_billing 原样执行；
这里额外的上限是本轮最多跑 ``AGENT_COLLABORATION_MAX_ITERATIONS`` 次。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from agent.core import empty_files, task_board_completion
from agent.core.author_facing_text import (
    AgentTextShaper,
    body_cut_at_output_cap_notice,
    rewrite_not_saved_notice,
    text_contains_cjk,
)
from agent.core.events import UNEXECUTED_TOOL_TEXT_LAYER, WALL_CLOCK_LIMIT
from agent.core.message_manager import AGENT_DIRECTIVE_HEADER
from agent.core.output_truncation import OUTPUT_TRUNCATED_RESUME
from agent.core.run_meter import AgentRunMeter
from agent.core.stream_errors import classify_stream_exception, log_stream_exception
from agent.core.stream_processor import LEAKED_TOOL_CALL_STOP_REASON
from agent.core.workflow_events import FileBodyResume, StreamEvent, StreamEventType
from agent.core.writing_state import WritingState
from agent.openai_agents.events import parse_mcp_tool_payload
from agent.openai_agents.repeat_edit_guard import RepeatEditGuard
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.runner import (
    OUTPUT_CAPPED_KEY,
    RunExecutionPolicy,
    append_pending_steering,
    run_openai_agents_streaming_agent,
)
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.tools.registry import LOOP_AGENT_TYPE, LOOP_TOOL_NAMES
from agent.tools.tool_context import AgentRequestContext
from config.agent_runtime import AGENT_COLLABORATION_MAX_ITERATIONS
from utils.logger import get_logger, log_with_context

from . import run_support
from .completion_evidence import (
    ends_on_file_block,
    ends_on_unclosed_file_block,
    unexecuted_tool_protocol,
)

logger = get_logger(__name__)

_EMPTY_FILE_CORRECTION_CONTEXT = (
    "[系统提醒] 你创建的文件《{title}》(id={file_id}) 正文仍为空：上一次没有用 <file>…</file> 写完它的正文。"
    "系统已为这个文件重新打开正文写入。这一次不要在思考里重新推演全盘方案、逐条排表或预写全文（思考只留两三行提纲，否则会挤占单次输出上限导致保存失败）；"
    "回复第一行直接输出 <file>，把完整内容直接写进 <file>…</file> 中，最后用 </file> 结束；"
    "写完之前不要调用任何工具（write_chapter / create_file / edit_file 都会被拒绝），也不要只在对话里复述正文。"
)
_EMPTY_FILE_CUT_CORRECTION_CONTEXT = (
    "[系统提醒] 你创建的文件《{title}》(id={file_id}) 正文仍为空：上一次回复在写出完整 <file>…</file> 之前就达到了单次输出上限"
    "（思考或前置内容过长），文件内容没有保存。系统已为这个文件重新打开正文写入：这一次思考尽量简短（只留两三行要点，绝不在思考里逐条排表或起草全文），"
    "回复第一行直接输出 <file>，把完整内容直接写进 <file>…</file> 中，最后用 </file> 结束；"
    "写完之前不要调用任何工具（write_chapter / create_file / edit_file 都会被拒绝），也不要只在对话里复述正文。"
)
_CHAPTER_REPLACE_RETRY_CONTEXT = (
    "[系统提醒] 你用 write_chapter 定下了整章替换《{title}》(id={file_id})，但上一次回复没有用 <file>…</file> 写出新正文，"
    "原文保持不变。系统已为这一章重新打开正文写入：这一次思考尽量简短（不在思考里起草全文），"
    "回复第一行直接输出 <file>，接着写这一章完整的新正文，最后用 </file> 结束；"
    "写完之前不要调用任何工具（write_chapter 也不要再调用），也不要只在对话里复述正文。"
)
# 整章替换的新正文写到单次回复的输出上限被截断（runner 的 output_capped）时的重试说明。
_CHAPTER_REPLACE_CUT_RETRY_CONTEXT = (
    "[系统提醒] 你用 write_chapter 定下了整章替换《{title}》(id={file_id})，但上一次回复的新正文写到单次回复的输出上限，"
    "在中途被截断（没有写到 </file>），没有保存，原文保持不变。系统已为这一章重新打开正文写入：这一次思考尽量简短，"
    "回复第一行直接输出 <file>，接着写这一章完整的新正文，最后用 </file> 结束；"
    "写完之前不要调用任何工具（write_chapter 也不要再调用），也不要只在对话里复述正文。"
)
# 新正文写到单次回复的输出上限被截断、截断处之前已保存时的续跑说明：从断点 append 续写，不整章重写。
_BODY_CUT_TAIL_CHARS = 200
_BODY_CUT_CONTEXT = (
    "[系统提醒] 上一次回复写《{title}》(id={file_id}) 的正文时达到了单次回复的输出上限，正文在中途被截断"
    "（没有写到 </file>，结束是系统补上的）。截断处之前的正文已保存，这一章还没有写完。"
    "这是系统的续跑说明，不是作者的新消息。已保存正文的末尾：\n「{tail}」\n"
    "现在用 edit_file 的 op=append（id={file_id}）从截断处接着写完这一章：text 直接接在上面最后一个字之后"
    "（截断在句子中间就先把这句话写完），不要重复已保存的内容；不要用 write_chapter 或 <file> 把这一章整章重写。"
    "剩下的篇幅较长时分几次 append，每次思考简短。写完后按「工作方式」自查这一章；作者这一轮要求的其他章节还没写完就接着写，"
    "全部完成后用一到三句话回复作者，这一章的字数报续写后最后一次 edit_file 结果里的 new_word_count。"
)
_BODY_CUT_CONTEXT_EN = (
    "[System note] While writing the body of \"{title}\" (id={file_id}), the previous response reached the single-reply "
    "output limit and was cut off mid-text (it never reached </file>; the system closed it). The text up to the cut is "
    "saved, but the chapter is not finished. This is a system continuation, not a new author message. The end of the "
    "saved text:\n\"{tail}\"\nNow continue the chapter from the cut with edit_file op=append (id={file_id}): the text "
    "follows straight on from the last character above (finish the sentence first if it was cut mid-sentence) and does "
    "not repeat saved text; do not rewrite the chapter with write_chapter or <file>. If much is left, use several "
    "appends and keep thinking short. When it is finished, self-check the chapter; if chapters the author asked for "
    "this turn are still unwritten, continue with them; when everything is done, reply to the author in one to three "
    "sentences; report this chapter's word count from new_word_count in the last edit_file result after continuing. "
    "Keep the author's language."
)
# 正文写完、回复停在 </file> 上时的续跑说明（相当于工具结果）：保存的文件与真实字数由调用方附在后面。
_BODY_SAVED_CONTEXT = (
    "[系统提醒] 上一次回复写完的正文已保存：{titles}。这是系统的续跑说明，不是作者的新消息。"
    "现在先按「工作方式」直接对照上方刚写完的正文做一次快速自查（只写了大纲、人物等资料文件时不用自查，直接继续；不要为自查再调用 query_files 重读刚写完的章节）："
    "逐字核对主角及关键人物姓名字形（防同音错字），对照大纲里这一章和前后章的条目、前一章结尾和角色卡，核对称谓、身份与设定、"
    "时间线、数值和物品状态是否连续（含同章内时间/场合是否自洽），大纲要求这一章发生的关键情节有没有写到，有没有提前写出后面章节才有的情节或揭示，以及有无混入外文词或错别字。"
    "只有发现确凿的事实、连贯、错字或外文词硬伤时，才对这一章调用至多一次 edit_file（多处独立修正合并进同一 edits 列表并传 continue_on_error=true）集中修正；"
    "这一次 edit_file 完成后立刻停止修改（除非定位报错可补改一次），绝不再调 query_files 或再次调用 edit_file 反复挑刺；没有硬伤时不要调用 edit_file"
    "（本轮已保存的这一章不能再调用 write_chapter 整章重写，系统会直接拒绝；也绝不要为了凑近近似字数、卡字数、润色措辞或顺手改大纲去调用 edit_file）。"
    "作者这一轮要求的章节还没写完就接着写下一章（按「工作方式」需要先给它补大纲条目时先补，再调用 write_chapter）；"
    "全部完成后用一到三句话回复作者，字数只报下面的真实字数。"
)
_BODY_SAVED_CONTEXT_EN = (
    "[System note] The body written in the previous response is saved: {titles}. This is a system continuation, "
    "not a new author message. First do one quick self-check directly against the newly written chapter text above (skip this when only notes files were written; do not call query_files to re-read the chapter you just wrote) against the outline entries for this and the "
    "adjacent chapters, the end of the previous chapter and the character files: names (including exact spelling), forms of address, identities "
    "and settings, timeline, numbers and object states; whether the key events the outline sets for this chapter "
    "happen; whether anything the outline places in later chapters was revealed early; and any stray foreign-language words or typos. "
    "Only if you find a concrete factual, continuity, typo, or stray-foreign-word error, call edit_file at most ONCE on this chapter (combine independent fixes in one edits list with continue_on_error=true) to fix those spots, then stop editing immediately (do not re-read or make follow-up micro-edits); if none are found, do NOT call edit_file (do not call write_chapter again on a chapter already saved this turn—the system will reject it; "
    "never call edit_file to pad, trim, polish prose toward an approximate word count, or tweak the outline). "
    "If chapters the author asked for this turn "
    "are still unwritten, continue with the next one (if the working rules call for its outline entry first, add it, then call "
    "write_chapter); when everything is done, reply to the author in one to "
    "three sentences, reporting only the real word counts below. Keep the author's language."
)
# 正文之后已经给作者写过总结时的续跑说明（这次运行没有停在 </file> 上）：照样自查，但不重复那段总结。
_BODY_SAVED_REPLIED_CONTEXT = (
    "[系统提醒] 上一次回复写完的正文已保存：{titles}，你也已经回复过作者。这是系统的续跑说明，不是作者的新消息。"
    "现在先按「工作方式」直接对照上方刚写完的正文做一次快速自查（不要为自查再调用 query_files 重读刚写完的章节）：对照大纲里这一章和前后章的条目、前一章结尾和角色卡，核对人名（含字形）、称谓、身份与设定、"
    "时间线、数值和物品状态是否连续，大纲要求这一章发生的关键情节有没有写到，有没有提前写出后面章节才有的情节或揭示，以及有无混入外文词或错别字。"
    "只有发现确凿的事实、连贯、错字或外文词硬伤时，才对这一章调用至多一次 edit_file（多处独立修正合并进同一 edits 列表并传 continue_on_error=true）集中修正；"
    "这一次 edit_file 完成后立刻停止修改，绝不再调 query_files 或反复调用 edit_file；没有硬伤时不要调用 edit_file"
    "（本轮已保存的这一章不能再调用 write_chapter 整章重写，系统会直接拒绝；也绝不要为了凑近近似字数、润色措辞或顺手改大纲调用 edit_file）。"
    "作者这一轮要求的章节还没写完就接着写下一章（按「工作方式」需要先给它补大纲条目时先补，再调用 write_chapter）。"
    "不要重复刚才给作者的总结：改了正文或又写了新章节时，最后用一两句话只说改了哪里、新写了哪一章，字数只报下面的真实字数；"
    "什么都没改、也没有要接着写的，只回复一句「自查过了，没有需要改的地方。」"
)
_BODY_SAVED_REPLIED_CONTEXT_EN = (
    "[System note] The body written in the previous response is saved: {titles}, and you have already replied to the "
    "author. This is a system continuation, not a new author message. Do one quick self-check directly against the newly written chapter text above (do not call query_files to re-read it) against "
    "the outline entries for this and the adjacent chapters, the end of the previous chapter and the character files: "
    "names, forms of address, identities and settings, timeline, numbers and object states; whether the key events the "
    "outline sets for this chapter happen; whether anything the outline places in later chapters was revealed early; and any stray foreign-language words or typos. "
    "Only if you find a concrete factual, continuity, typo, or stray-foreign-word error, call edit_file at most ONCE on this chapter (combine independent fixes in one edits list with continue_on_error=true) to fix those spots, then stop editing immediately (do not re-read or make follow-up micro-edits; do not "
    "call write_chapter again on a chapter already saved this turn—the system will reject it; and never call edit_file to pad, trim, or polish prose toward an approximate word count). If chapters the author "
    "asked for this turn are still unwritten, continue with the next one (if the working rules call for its outline "
    "entry first, add it, then call write_chapter). Do not repeat your earlier summary: if you changed text or wrote "
    "another chapter, end with one or two sentences saying only what changed or which chapter is new, reporting only the "
    "real word counts below; if nothing changed and nothing is left to write, reply with one sentence saying the "
    "self-check found nothing to fix. Keep the author's language."
)
# 自查过的文件又写了一遍之后的续跑说明：不再自查，接着做剩下的事或回复作者。
_BODY_RESAVED_CONTEXT = (
    "[系统提醒] 上一次回复写完的正文已保存：{titles}。这是系统的续跑说明，不是作者的新消息。"
    "这一章已经自查过，不要再改它；作者这一轮要求的章节还没写完就接着写下一章（按「工作方式」需要先给它补大纲条目时先补，再调用 write_chapter），"
    "全部完成后用一到三句话回复作者，字数只报下面的真实字数。"
)
_BODY_RESAVED_CONTEXT_EN = (
    "[System note] The body written in the previous response is saved: {titles}. This is a system continuation, "
    "not a new author message. This chapter was already self-checked; do not change it again. If chapters the author "
    "asked for this turn are still unwritten, continue with the next one (if the working rules call for its outline entry "
    "first, add it, then call write_chapter); otherwise reply to the author in "
    "one to three sentences, reporting only the real word counts below. Keep the author's language."
)
# 整章替换的新正文没写完（没有 </file>）、也没能在本轮重试时的续跑说明：如实说明原文没动，不说已保存。
_REPLACE_UNSAVED_CONTEXT = (
    "[系统提醒] {titles}的整章替换没有保存：上一次回复的新正文没有写完（没有写到 </file>），原文保持不变。"
    "这是系统的续跑说明，不是作者的新消息。不要对作者说这一章已经替换，也不要报它的字数；"
    "作者这一轮要求的其他章节还没写完就接着写，最后用一到三句话如实告诉作者这一章没有换成、原文没动，需要时可以再写一次。"
)
_REPLACE_UNSAVED_CONTEXT_EN = (
    "[System note] The whole-chapter replacement of {titles} was not saved: the new text in the previous response "
    "was not finished (it never reached </file>), so the original text is unchanged. This is a system continuation, "
    "not a new author message. Do not tell the author the chapter was replaced and do not report a word count for it; "
    "if other chapters the author asked for this turn are still unwritten, continue with them, then tell the author in "
    "one to three sentences that this chapter was not replaced and its original text is unchanged. Keep the author's language."
)
# 调用后下一次回复用 <file> 写正文的工具（新建文件 / 按章号写一章）。
_BODY_WRITE_TOOLS = frozenset({"create_file", "write_chapter"})
_UNEXECUTED_TOOL_TEXT_REASON = "回复里有一步工具调用只写成了文字，没有执行。"
_UNEXECUTED_TOOL_TEXT_REASON_EN = "A tool call in the reply was written out as text and was not executed."


def _english(state: WritingState) -> bool:
    return str(state.get("language") or "").strip().lower().startswith("en")


def _armed_replacement_id(result: Any) -> str:
    """write_chapter 成功定位整章替换（结构化结果 action=replaced）时返回章节 id，否则返回空串。"""
    payload = parse_mcp_tool_payload(result)
    data = payload.get("data") if isinstance(payload, dict) and payload.get("status") == "success" else None
    if isinstance(data, dict) and data.get("action") == "replaced" and data.get("awaiting_body") is True:
        file_id = data.get("id")
        return file_id if isinstance(file_id, str) else ""
    return ""


def _word_count_note(file_ids: list[str], req_ctx: AgentRequestContext) -> str:
    """续跑说明里附上本请求写过的正文落库后的字数，模型给作者报真实字数。"""
    try:
        counts = run_support.collect_body_word_counts(file_ids, request_context=req_ctx)
    except Exception as exc:  # noqa: BLE001 - 读不到字数不影响续跑
        logger.debug(f"Word counts for the continuation note failed: {type(exc).__name__}")
        return ""
    return run_support.format_body_word_counts(counts)


def _cut_word_count_note(file_ids: list[str], req_ctx: AgentRequestContext) -> str:
    """截断续写说明里附上截断处的落库字数，明确标为截断时的数（续写后会变）。"""
    try:
        counts = run_support.collect_body_word_counts(file_ids, request_context=req_ctx)
    except Exception as exc:  # noqa: BLE001 - 读不到字数不影响续跑
        logger.debug(f"Word counts for the cut continuation note failed: {type(exc).__name__}")
        return ""
    return run_support.format_cut_body_word_counts(counts)


async def _drain_boundary_steering(get_steering_messages: Any) -> list[dict[str, str]]:
    if get_steering_messages is None:
        return []
    try:
        raw = await get_steering_messages()
    except Exception as exc:  # noqa: BLE001
        log_with_context(
            logger,
            40,  # ERROR
            "Failed to retrieve steering messages at loop run boundary",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []
    drained: list[dict[str, str]] = []
    for msg in raw or []:
        if isinstance(msg, dict) and str(msg.get("content") or ""):
            drained.append({"id": str(msg.get("id") or ""), "content": str(msg["content"])})
    return drained


async def _settle_pending_empty_files(
    req_ctx: AgentRequestContext,
    *,
    can_correct: bool,
    english: bool,
    body_cut: bool = False,
    output_capped: bool = False,
) -> tuple[str | None, StreamEvent | None, list[StreamEvent], list[StreamEvent]]:
    """run 边界核验仍在等正文的空文件。

    返回 (纠偏说明, FILE_BODY_RESUME 事件, 说明事件, 收尾事件)。纠偏说明非空时调用方先把 FILE_BODY_RESUME
    交给 StreamAdapter（它决定能否为同一个文件恢复捕获），再跑纠偏；说明事件是作废整章替换时给作者的
    「未保存」文字，照常发出；补不成时收尾事件里是回滚后的 ``empty_file_correction`` 卡，调用方以它结束这一轮。
    """
    unfinished: list[dict[str, str]] = []
    replacements: list[dict[str, str]] = []
    for entry in req_ctx.get_pending_empty_files():
        file_id = str(entry.get("file_id") or entry.get("id") or "")
        title = str(entry.get("title") or ("Untitled" if english else "未命名"))
        armed = req_ctx.pending_chapter_replacement(file_id)
        if armed is not None:
            # write_chapter 定下的整章替换没收到正文：原文没动、不是空文件，不回滚、不发空文件卡。
            replacements.append({"file_id": file_id, "title": title, "updated_at": str(armed.get("updated_at") or "")})
            continue
        state = await asyncio.to_thread(
            empty_files.probe_pending_file_body, file_id, request_context=req_ctx
        )
        if state in (empty_files.PENDING_BODY_WRITTEN, empty_files.PENDING_BODY_GONE):
            req_ctx.clear_pending_empty_file(file_id)
            continue
        unfinished.append({"file_id": file_id, "title": title})
    if replacements and not unfinished and can_correct and replacements[-1]["updated_at"]:
        # 能纠偏时为这一章恢复正文捕获（StreamAdapter 核对定位时的版本），模型只需用 <file> 写正文。
        target = replacements[-1]
        for other in replacements[:-1]:
            req_ctx.clear_pending_empty_file(other["file_id"])
        resume = FileBodyResume(file_id=target["file_id"], expected_updated_at=target["updated_at"])
        # 这次 run 停在没闭合的 <file> 上、又撞上了输出上限（runner 的结构化标记）：如实说新正文被截断，而不是没写。
        template = _CHAPTER_REPLACE_CUT_RETRY_CONTEXT if body_cut else _CHAPTER_REPLACE_RETRY_CONTEXT
        context = template.format(title=target["title"], file_id=target["file_id"])
        notices = [_rewrite_not_saved_event(english)] if replacements[:-1] else []
        return context, StreamEvent(type=StreamEventType.FILE_BODY_RESUME, data={"request": resume}), notices, []
    for other in replacements:
        # 补不成：作废这次定位，原文保持不变。
        req_ctx.clear_pending_empty_file(other["file_id"])
    notices = [_rewrite_not_saved_event(english)] if replacements else []
    if not unfinished:
        return None, None, notices, []
    if can_correct:
        target = unfinished[-1]
        facts = await asyncio.to_thread(
            empty_files.resume_target_version, target["file_id"], request_context=req_ctx
        )
        if facts is not None:
            resume = FileBodyResume(file_id=target["file_id"], expected_updated_at=facts[0])
            template = _EMPTY_FILE_CUT_CORRECTION_CONTEXT if output_capped else _EMPTY_FILE_CORRECTION_CONTEXT
            context = template.format(title=target["title"], file_id=target["file_id"])
            return context, StreamEvent(type=StreamEventType.FILE_BODY_RESUME, data={"request": resume}), notices, []
    return None, None, notices, [await _empty_file_card(unfinished, None, req_ctx)]


async def _body_cut_followup(
    cut_bodies: dict[str, str],
    read_guard: RepeatReadGuard,
    english: bool,
    req_ctx: AgentRequestContext,
) -> tuple[str, StreamEvent]:
    """新正文被输出上限截断、已保存到截断处：返回 (续跑说明, 给作者的固定说明)。

    续跑说明给出截断处附近已保存的原文，要求从断点 append 续写；给作者的说明不说这一章已经写完。
    """
    file_id = list(cut_bodies)[-1]
    tail = await asyncio.to_thread(
        run_support.read_body_tail, file_id, _BODY_CUT_TAIL_CHARS, request_context=req_ctx
    )
    titles = {
        fid: title or read_guard.files_written.get(fid) or read_guard.files_targeted.get(fid) or ""
        for fid, title in cut_bodies.items()
    }
    if tail is not None and tail[0]:
        titles[file_id] = tail[0]
    title = titles[file_id] or ("this chapter" if english else "这一章")
    template = _BODY_CUT_CONTEXT_EN if english else _BODY_CUT_CONTEXT
    followup = template.format(title=title, file_id=file_id, tail=tail[1] if tail else "") + _cut_word_count_note(
        list(cut_bodies), req_ctx
    )
    joiner = ", " if english else "、"
    named = joiner.join((f'"{t}"' if english else f"《{t}》") for t in titles.values() if t) or (
        "This chapter" if english else "这一章"
    )
    log_with_context(
        logger,
        30,  # WARNING
        "Loop body cut at the output cap; continuing from the break",
        file_count=len(cut_bodies),
        tail_chars=len(tail[1]) if tail else 0,
    )
    notice = StreamEvent(
        type=StreamEventType.TEXT, data={"text": "\n\n" + body_cut_at_output_cap_notice(english, named)}
    )
    return followup, notice


def _abandon_armed_replacements(english: bool, req_ctx: AgentRequestContext) -> list[StreamEvent]:
    """作废所有还在等正文的整章替换定位（原文不动）；有作废时返回一条「未保存」说明。"""
    abandoned = False
    for entry in req_ctx.get_pending_empty_files():
        file_id = str(entry.get("file_id") or entry.get("id") or "")
        if req_ctx.pending_chapter_replacement(file_id) is not None:
            req_ctx.clear_pending_empty_file(file_id)
            abandoned = True
    return [_rewrite_not_saved_event(english)] if abandoned else []


def _rewrite_not_saved_event(english: bool) -> StreamEvent:
    """放弃整章替换时给作者的固定说明（与 StreamAdapter 拒绝截断覆盖时同一份文字）。"""
    return StreamEvent(type=StreamEventType.TEXT, data={"text": "\n\n" + rewrite_not_saved_notice(english)})


async def _empty_file_card(
    unfinished: list[dict[str, str]],
    resume: FileBodyResume | None,
    req_ctx: AgentRequestContext,
) -> StreamEvent:
    """补不成时回滚能确认的空文件，发既有的 empty_file_correction 卡（同图的口径）。"""
    from agent.core.workflow_events import FILE_BODY_RESUME_VERSION_CHANGED

    kept_ids = (
        {resume.file_id}
        if resume is not None and resume.refusal == FILE_BODY_RESUME_VERSION_CHANGED
        else set()
    )
    rollback_entries = empty_files.with_creation_versions(
        [entry for entry in unfinished if entry["file_id"] not in kept_ids],
        request_context=req_ctx,
    )
    rolled_back = (
        await asyncio.to_thread(
            empty_files.rollback_unfinished_empty_files,
            rollback_entries,
            request_context=req_ctx,
        )
        if rollback_entries
        else set()
    )
    for entry in unfinished:
        if entry["file_id"] in rolled_back:
            req_ctx.clear_pending_empty_file(entry["file_id"])
    unresolved = [entry for entry in unfinished if entry["file_id"] not in rolled_back]
    log_with_context(
        logger,
        30,  # WARNING
        "Loop empty-file correction not completed",
        unfinished_count=len(unfinished),
        rolled_back_count=len(rolled_back),
        unresolved_count=len(unresolved),
    )
    return StreamEvent(
        type=StreamEventType.ITERATION_EXHAUSTED,
        data={
            "layer": "empty_file_correction",
            "iterations_used": empty_files.MAX_FILE_CORRECTION_ATTEMPTS,
            "max_iterations": empty_files.MAX_FILE_CORRECTION_ATTEMPTS,
            "reason": (
                "文件正文补写未完成，且无法安全回滚空文件。请继续对话完成这些文件。"
                if unresolved
                else "文件正文没有写成，已撤销这些空文件。"
            ),
            "unfinished_files": unresolved,
            "rolled_back_files": [entry for entry in unfinished if entry["file_id"] in rolled_back],
            "last_agent": None,
        },
    )


def _can_soft_land_self_check(
    req_ctx: AgentRequestContext,
    *,
    output_capped: bool,
    created_files: bool,
    run_resumed: bool,
    self_checked_ids: set[str],
    cut_bodies: Sequence[Any],
    textual_calls: Sequence[Any],
    has_steering: bool,
) -> bool:
    """自查 run 撞上墙钟软着陆时，是否已无任何未完结构化待办。"""
    return not (
        output_capped
        or created_files
        or run_resumed
        or (set(req_ctx.saved_content_body_ids()) - self_checked_ids)
        or req_ctx.has_pending_empty_file()
        or req_ctx.unsaved_chapter_replacement_ids()
        or cut_bodies
        or textual_calls
        or has_steering
    )


async def run_loop_agent_streaming(
    state: WritingState,
    *,
    get_steering_messages: Any | None = None,
    max_runs: int = AGENT_COLLABORATION_MAX_ITERATIONS,
    request_context: AgentRequestContext | None = None,
) -> AsyncIterator[StreamEvent]:
    """跑完单循环 agent 的一轮，产出与图相同类型的工作流事件（不含路由 / 角色 / 交接事件）。"""
    english = _english(state)
    req_ctx = (
        request_context
        if isinstance(request_context, AgentRequestContext)
        else state.get("request_context")
        if isinstance(state.get("request_context"), AgentRequestContext)
        else AgentRequestContext(
            project_id=state.get("project_id") or None,
            user_id=state.get("user_id") or None,
            session_id=state.get("session_id"),
        )
    )
    state["request_context"] = req_ctx
    state["tool_failure_breaker"] = ToolFailureBreaker()
    state["repeat_read_guard"] = RepeatReadGuard(request_context=req_ctx)
    state["repeat_edit_guard"] = RepeatEditGuard(request_context=req_ctx)
    if not isinstance(state.get("run_meter"), AgentRunMeter):
        state["run_meter"] = AgentRunMeter()
    run_meter: AgentRunMeter = state["run_meter"]
    read_guard: RepeatReadGuard = state["repeat_read_guard"]
    system_prompt = str(state.get("system_prompt") or "")
    initial_user_message = str(state.get("user_message") or "")
    drop_english_narration = text_contains_cjk(state.get("router_message") or state.get("user_message"))

    # 上一轮停在思考阶段的输出上限（历史行的 stop_reason 数出的结构化事实）：本轮第一次 run 的输入末尾
    # 加一条运行指令，要求简短思考、先交付第一部分。只进这次 SDK 输入，不写回对话历史。
    truncation_directive = (
        f"{AGENT_DIRECTIVE_HEADER}\n\n## 截断后的本轮交付\n\n{OUTPUT_TRUNCATED_RESUME}"
        if int(state.get("previous_output_truncations") or 0) > 0
        else ""
    )

    req_ctx.current_agent = LOOP_AGENT_TYPE
    # 写作约定由模型按提示判断是否追加（只追加、不删改、expected_notes 冲突保护照旧）。
    req_ctx.notes_append_allowed = True
    req_ctx.begin_agent_run()

    runs = 0
    correction_attempts = 0
    resumed_correction = False
    unexecuted_reported = False
    # 已经要求过自查的文件：同一章自查后再写一遍不再要求自查（否则「自查 → 整章重写 → 再自查」会一直转下去）。
    self_checked_ids: set[str] = set()
    # 已经在续跑说明里如实说过「没有保存」的整章替换（同一章不重复说）。
    reported_unsaved_ids: set[str] = set()
    accumulated_text = ""
    terminated = False
    pending_self_check = False
    followup_ended_on_file = False
    followup_saved_titles: list[str] = []
    try:
        while runs < max_runs:
            if runs > 0 and run_meter.past_wrap_up:
                if pending_self_check:
                    task_events, has_incomplete = await task_board_completion.auto_finalize_task_board_on_completion(
                        read_guard, request_context=req_ctx
                    )
                    if not has_incomplete:
                        # 正文已确认落库、任务板无未完待办、无待写空文件/截断残稿/未保存替换/未执行工具与插话：
                        # 待办仅剩常规写后自查，直接按正常完成收尾，不发「可能未完成 / 继续」暂停卡。
                        log_with_context(
                            logger,
                            20,  # INFO
                            "Loop agent finishing cleanly instead of starting post-save self-check near wrap-up",
                            runs=runs,
                            ended_on_file=followup_ended_on_file,
                            saved_count=len(followup_saved_titles),
                        )
                        if followup_ended_on_file:
                            yield StreamEvent(
                                type=StreamEventType.TEXT,
                                data={
                                    "text": run_support.wrap_up_self_check_skipped_text(
                                        english, followup_saved_titles
                                    )
                                },
                            )
                        for task_event in task_events:
                            yield task_event
                        terminated = True
                        break
                    for task_event in task_events:
                        yield task_event
                # 请求时间快用完：不再开新的 run，停在已经完成的部分（同图层收尾的暂停卡）。
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": await run_support.wrap_up_text(english, request_context=req_ctx)},
                )
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={
                        "layer": "collaboration",
                        "iterations_used": runs,
                        "max_iterations": max_runs,
                        "reason": run_support.WRAP_UP_CARD_REASON_EN if english else run_support.WRAP_UP_CARD_REASON,
                        "last_agent": None,
                        "limit": WALL_CLOCK_LIMIT,
                    },
                )
                terminated = True
                break
            runs += 1
            in_self_check = pending_self_check
            run_saved_titles = list(followup_saved_titles)
            pending_self_check = False
            followup_ended_on_file = False
            followup_saved_titles = []
            unexecuted_this_run = False
            run_outcome = state.get("run_outcome")
            if run_outcome is not None and hasattr(run_outcome, "runs_started"):
                run_outcome.runs_started = runs
            run_resumed = resumed_correction
            resumed_correction = False

            tail_text = ""
            run_started = False
            mid_run_steering = False
            errored = False
            tool_call_exhausted = False
            deferred_exhausted: StreamEvent | None = None
            run_policy = RunExecutionPolicy(defer_wall_clock_soft_landing=in_self_check)
            create_ids: set[str] = set()
            failed_ids: set[str] = set()
            # 这次 run 最后一次模型响应撞上了输出上限（runner 在 MESSAGE_END 上的结构化标记）。
            output_capped = False
            # 等待 <file> 正文时模型开头直接吐出泄漏 tool_call 协议头被早停，需经 FILE_BODY_RESUME 立即纠偏。
            leaked_tool_call_stopped = False
            shaper = AgentTextShaper(previous_text=accumulated_text, drop_english_narration=drop_english_narration)

            async for event in run_support.shape_agent_text(
                run_openai_agents_streaming_agent(
                    state=state,
                    agent_type=LOOP_AGENT_TYPE,
                    system_prompt=system_prompt,
                    get_steering_messages=get_steering_messages,
                    agent_directive=truncation_directive if runs == 1 else "",
                    run_policy=run_policy,
                    request_context=req_ctx,
                ),
                shaper,
            ):
                if event.type == StreamEventType.MESSAGE_START:
                    run_started = True
                elif event.type == StreamEventType.MESSAGE_END:
                    output_capped = output_capped or event.data.get(OUTPUT_CAPPED_KEY) is True
                    if event.data.get("stop_reason") == LEAKED_TOOL_CALL_STOP_REASON:
                        leaked_tool_call_stopped = True
                elif event.type == StreamEventType.ERROR:
                    errored = True
                elif getattr(event.type, "value", "") == "steering_received" and run_started:
                    # MESSAGE_START 之后的插话是工具边界消费的，这次 run 已经用不上。
                    mid_run_steering = True
                if event.type == StreamEventType.TEXT:
                    text = str(event.data.get("text") or "")
                    tail_text += text
                    accumulated_text += text
                elif event.type in (StreamEventType.TOOL_USE, StreamEventType.TOOL_RESULT):
                    tail_text = ""
                    if event.type == StreamEventType.TOOL_USE and event.data.get("name") in _BODY_WRITE_TOOLS:
                        create_ids.add(str(event.data.get("id") or ""))
                    elif event.type == StreamEventType.TOOL_RESULT and run_support.tool_result_failed(
                        event.data.get("result")
                    ):
                        failed_ids.add(str(event.data.get("tool_use_id") or ""))
                    elif event.type == StreamEventType.TOOL_RESULT and (
                        rearmed_id := _armed_replacement_id(event.data.get("result"))
                    ):
                        # 同一章重新定位了整章替换：这是新的一次替换，没保存时要重新如实说明。
                        reported_unsaved_ids.discard(rearmed_id)
                if event.type == StreamEventType.ITERATION_EXHAUSTED and event.data.get("layer") == "tool_call":
                    tool_call_exhausted = True
                    if req_ctx.has_pending_empty_file():
                        # 等 run 收尾（StreamAdapter 跑完 MESSAGE_END 补存尾部正文）后再按结构化事实附空文件状态。
                        deferred_exhausted = event
                        continue
                yield event

            if run_policy.deferred_wall_clock_exhausted is not None and not errored:
                cut_after_self_check = req_ctx.take_cut_bodies()
                boundary_steering = await _drain_boundary_steering(get_steering_messages)
                if boundary_steering:
                    append_pending_steering(state, [msg["content"] for msg in boundary_steering])
                    from agent.core.events import steering_received_event

                    for msg in boundary_steering:
                        yield steering_received_event(message_id=msg["id"], preview=msg["content"][:50])
                textual_calls_after_self_check = (
                    unexecuted_tool_protocol(tail_text, frozenset(LOOP_TOOL_NAMES))
                    if not unexecuted_reported
                    else []
                )
                if _can_soft_land_self_check(
                    req_ctx,
                    output_capped=output_capped,
                    created_files=bool(create_ids - failed_ids),
                    run_resumed=run_resumed,
                    self_checked_ids=self_checked_ids,
                    cut_bodies=cut_after_self_check,
                    textual_calls=textual_calls_after_self_check,
                    has_steering=bool(mid_run_steering or boundary_steering),
                ):
                    task_events, has_incomplete = await task_board_completion.auto_finalize_task_board_on_completion(
                        read_guard, request_context=req_ctx
                    )
                    if not has_incomplete:
                        log_with_context(
                            logger,
                            20,  # INFO
                            "Loop agent finishing cleanly after self-check soft-landed near wrap-up",
                            runs=runs,
                            saved_count=len(run_saved_titles),
                        )
                        for task_event in task_events:
                            yield task_event
                        terminated = True
                        break
                    for task_event in task_events:
                        yield task_event
                tool_call_exhausted = True
                deferred_exhausted = StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data=run_policy.deferred_wall_clock_exhausted,
                )

            if deferred_exhausted is not None:
                # 耗尽后不再重试：作废还在等正文的整章替换并如实说明（原文没动，不是空文件，不交给空文件核验——
                # 那里按「文件已有正文」把它静默清掉）。
                for notice in _abandon_armed_replacements(english, req_ctx):
                    yield notice
                file_facts = await empty_files.settle_empty_files_after_exhaustion(
                    LOOP_AGENT_TYPE, english, request_context=req_ctx
                )
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={**deferred_exhausted.data, **file_facts},
                )
            if errored or tool_call_exhausted:
                terminated = True
                break

            if req_ctx.has_pending_empty_file():
                can_correct = (
                    runs < max_runs
                    and correction_attempts < empty_files.MAX_FILE_CORRECTION_ATTEMPTS
                    and not run_meter.exhausted
                    and not run_meter.past_wrap_up
                )
                context, resume_event, notices, settle_events = await _settle_pending_empty_files(
                    req_ctx,
                    can_correct=can_correct,
                    english=english,
                    body_cut=(not leaked_tool_call_stopped) and output_capped and ends_on_unclosed_file_block(tail_text),
                    output_capped=(not leaked_tool_call_stopped) and output_capped,
                )
                for notice in notices:
                    yield notice
                if resume_event is not None:
                    yield resume_event
                    resume = resume_event.data["request"]
                    if resume.armed:
                        if leaked_tool_call_stopped:
                            log_with_context(
                                logger,
                                20,  # INFO
                                "Loop agent routing leaked tool_call early stop into FILE_BODY_RESUME",
                                run=runs,
                                file_id=resume.file_id,
                                correction_attempt=correction_attempts + 1,
                            )
                        correction_attempts += 1
                        resumed_correction = True
                        if not state.get("run_items") and initial_user_message:
                            state["run_items"] = [
                                *list(state.get("history_items") or []),
                                {"role": "user", "content": initial_user_message},
                            ]
                        state["user_message"] = context or ""
                        continue
                    if req_ctx.pending_chapter_replacement(resume.file_id) is not None:
                        # 整章替换的捕获没能恢复（版本变了等）：作废定位，原文不动，不发空文件卡。
                        req_ctx.clear_pending_empty_file(resume.file_id)
                        yield _rewrite_not_saved_event(english)
                    pending = [
                        {"file_id": str(e.get("file_id") or ""), "title": str(e.get("title") or "")}
                        for e in req_ctx.get_pending_empty_files()
                    ]
                    settle_events = [await _empty_file_card(pending, resume, req_ctx)] if pending else []
                for settle_event in settle_events:
                    yield settle_event
                if settle_events:
                    terminated = True
                    break

            if not unexecuted_reported and not leaked_tool_call_stopped and (
                textual_calls := unexecuted_tool_protocol(tail_text, frozenset(LOOP_TOOL_NAMES))
            ):
                # 回复里写出了完整的工具调用协议却没有执行：只按协议结构识别，如实记为未完成，不重试。
                unexecuted_reported = True
                unexecuted_this_run = True
                log_with_context(
                    logger, 30, "textual_tool_call", agent_type=LOOP_AGENT_TYPE, call_count=len(textual_calls)
                )
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={
                        "layer": UNEXECUTED_TOOL_TEXT_LAYER,
                        "iterations_used": runs,
                        "max_iterations": max_runs,
                        "reason": _UNEXECUTED_TOOL_TEXT_REASON_EN if english else _UNEXECUTED_TOOL_TEXT_REASON,
                        "last_agent": None,
                    },
                )

            followup: str | None = None
            # 这次 run 里没等到 </file>、按流结束补全保存的新正文（StreamAdapter 保存点的结构化事实）。
            # 每个 run 边界都取走，只有这次 run 撞上输出上限时才按截断续写处理。
            cut_bodies = req_ctx.take_cut_bodies()
            ended_on_file = ends_on_file_block(tail_text) or (
                (bool(create_ids - failed_ids) or run_resumed) and ends_on_unclosed_file_block(tail_text)
            )
            # 本请求确认落库的完整章节 / 分集正文（StreamAdapter 保存点记下的结构化事实）里还没自查过的：
            # 不管 </file> 之后模型有没有再写一句总结，都按保存事实续跑一次自查。大纲、人物等资料文件不在内，
            # 它们照旧只在停在 </file> 上时续跑。
            saved_bodies = req_ctx.saved_bodies_this_request()
            new_saved = set(req_ctx.saved_content_body_ids()) - self_checked_ids
            if (
                output_capped
                and cut_bodies
                and ends_on_unclosed_file_block(tail_text)
                and not req_ctx.has_pending_empty_file()
            ):
                # 这次 run 停在没闭合的 <file> 正文上、最后一次响应撞上输出上限、正文按截断补全保存了：
                # 是被截断，不是写完，不说「已保存，请自查」。
                followup, cut_notice = await _body_cut_followup(cut_bodies, read_guard, english, req_ctx)
                yield cut_notice
            elif not req_ctx.has_pending_empty_file() and (
                new_saved or ((bool(create_ids - failed_ids) or run_resumed) and ended_on_file)
            ):
                # 定位过整章替换、新正文却没落库的章节（AgentRequestContext 的结构化保存事实）：不算已保存，不列它的原稿字数。
                unsaved_ids = req_ctx.unsaved_chapter_replacement_ids()
                # 写入事实：工具确认的改动 + 还没保存的整章替换目标（定位结果） + 落库确认的正文。
                written = {
                    **read_guard.files_written,
                    **{fid: t for fid, t in read_guard.files_targeted.items() if fid in unsaved_ids},
                    **{fid: read_guard.files_written.get(fid) or t for fid, t in saved_bodies.items()},
                }
                saved = {fid: t for fid, t in written.items() if fid not in unsaved_ids}
                unsaved_written = [(fid, t) for fid, t in written.items() if fid in unsaved_ids]
                newly_unsaved = [(fid, t) for fid, t in unsaved_written if fid not in reported_unsaved_ids]
                if not newly_unsaved and not saved:
                    # 没有任何保存过的正文：不能退回「已保存」的说明，照旧如实说替换没保存。
                    newly_unsaved = unsaved_written
                if newly_unsaved:
                    reported_unsaved_ids |= {fid for fid, _ in newly_unsaved}
                    unsaved_titles = "、".join(f"《{t}》" for _, t in newly_unsaved if t) or (
                        "this chapter" if english else "这一章"
                    )
                    template = _REPLACE_UNSAVED_CONTEXT_EN if english else _REPLACE_UNSAVED_CONTEXT
                    followup = template.format(titles=unsaved_titles) + _word_count_note(list(saved), req_ctx)
                else:
                    titles = "、".join(f"《{t}》" for t in saved.values() if t) or "（见下）"
                    written_ids = set(saved)
                    if (written_ids - self_checked_ids or not written_ids) and new_saved and not ended_on_file:
                        # 正文之后作者已经看到一段回复（这次运行没停在 </file> 上）：自查，但不再重复总结。
                        template = _BODY_SAVED_REPLIED_CONTEXT_EN if english else _BODY_SAVED_REPLIED_CONTEXT
                    elif written_ids - self_checked_ids or not written_ids:
                        template = _BODY_SAVED_CONTEXT_EN if english else _BODY_SAVED_CONTEXT
                    else:
                        template = _BODY_RESAVED_CONTEXT_EN if english else _BODY_RESAVED_CONTEXT
                    self_checked_ids |= written_ids
                    followup = template.format(titles=titles) + _word_count_note(list(saved), req_ctx)
                    if (
                        new_saved
                        and not unsaved_ids
                        and not cut_bodies
                        and not unexecuted_this_run
                    ):
                        pending_self_check = True
                        followup_ended_on_file = bool(ended_on_file)
                        content_ids = set(req_ctx.saved_content_body_ids()) - unsaved_ids
                        followup_saved_titles = [
                            t for fid, t in saved.items() if fid in content_ids and t
                        ] or [t for fid, t in saved.items() if fid in new_saved and t]
            boundary_steering = await _drain_boundary_steering(get_steering_messages)
            if boundary_steering:
                append_pending_steering(state, [msg["content"] for msg in boundary_steering])
                from agent.core.events import steering_received_event

                for msg in boundary_steering:
                    yield steering_received_event(message_id=msg["id"], preview=msg["content"][:50])
            if mid_run_steering or boundary_steering:
                pending_self_check = False
                followup = (
                    f"{followup}\n{run_support.STEERING_FOLLOWUP_CONTEXT}"
                    if followup
                    else run_support.STEERING_FOLLOWUP_CONTEXT
                )
            if followup is None:
                # 正常结束：与图相同，按本请求实际写出的文件把任务板上对应的任务收尾（什么都没写时不补）。
                task_events, _ = await task_board_completion.auto_finalize_task_board_on_completion(
                    read_guard, request_context=req_ctx
                )
                for task_event in task_events:
                    yield task_event
                terminated = True
                break
            log_with_context(
                logger,
                20,  # INFO
                "Loop agent continues within the turn",
                run=runs,
                self_check=pending_self_check,
                ended_on_file=bool(ended_on_file),
                new_saved=len(new_saved),
                body_cut_at_output_cap=bool(output_capped and cut_bodies and ends_on_unclosed_file_block(tail_text)),
                steering=bool(mid_run_steering or boundary_steering),
            )
            if not state.get("run_items") and initial_user_message:
                state["run_items"] = [
                    *list(state.get("history_items") or []),
                    {"role": "user", "content": initial_user_message},
                ]
            state["user_message"] = followup

        if not terminated and runs >= max_runs:
            yield StreamEvent(
                type=StreamEventType.ITERATION_EXHAUSTED,
                data={
                    "layer": "collaboration",
                    "iterations_used": runs,
                    "max_iterations": max_runs,
                    "reason": (
                        f"这一轮已连续运行 {max_runs} 次，先停在这里。"
                        "任务可能未完全完成，您可以继续对话让 AI 完成剩余工作。"
                    ),
                    "last_agent": None,
                },
            )
    except Exception as exc:
        info = classify_stream_exception(exc)
        log_stream_exception(logger, "Loop agent stream error", exc, info)
        yield StreamEvent(type=StreamEventType.ERROR, data=info.as_event_data(error_type=type(exc).__name__))
    finally:
        req_ctx.current_agent = None
