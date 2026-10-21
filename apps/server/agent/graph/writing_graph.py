"""
Writing workflow for zenstory.

Provides streaming multi-agent orchestration with router, planner, writer, and quality reviewer.
"""

import asyncio
import contextlib
import functools
import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any

from agent.constants import CONTENT_FILE_TYPES
from agent.core.author_facing_text import (
    AgentTextShaper,
    author_facing_handoff_reason,
    author_facing_text,
    text_contains_cjk,
    trailing_question_text,
)
from agent.core.events import READ_ONLY_HANDOFF_BLOCKED_REASON
from agent.core.run_meter import AgentRunMeter
from agent.core.stream_errors import StreamErrorInfo, classify_stream_exception, log_stream_exception
from agent.core.workflow_events import (
    CHAT_HOLD_BEGIN,
    CHAT_HOLD_DISCARD,
    StreamEvent,
    StreamEventType,
)
from agent.graph.author_scope import (
    PLANNED_HANDOFF_SCOPE_NOTE,
    PLANNING_AGENTS,
    SCOPE_DIRECTIVE_CLARIFY_FIRST,
    SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING,
    chat_only_plan_units,
    chat_plan_correction_context,
    clarify_first_route,
    has_selected_text,
    is_vague_edit_request,
    outline_covers_units,
)
from agent.graph.body_writes import (
    BodyWrite,
    BodyWriteTracker,
    body_words_written,
    latest_file_word_counts,
    measured_body_words,
)
from agent.graph.completion_evidence import ends_on_file_block
from agent.graph.nodes import (
    detect_task_complete,
    ends_with_question_to_user,
    evaluate_agent_output,
    run_streaming_agent,
)
from agent.graph.review_evidence import (
    find_length_requirements,
    find_numeric_fact_pairs,
    find_repetition_evidence,
)
from agent.graph.router import (
    RouterFailure,
    _last_assistant_message,
    _previous_routing,
    get_next_node,
    inherit_routing_after_clarification,
    resume_route_after_exhaustion,
    router_node,
)
from agent.graph.state import WritingState
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.runner import extract_text_from_message_content
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.tools.mcp_tools import ToolContext, update_project
from agent.tools.task_progress import (
    ArtifactEvidence,
    artifact_units,
    format_units,
    task_artifact_type,
    task_evidence_spec,
    validate_task_artifact_fields,
)
from config.agent_runtime import (
    AGENT_AUTO_REVIEW_THRESHOLD_CHARS,
    AGENT_COLLABORATION_MAX_ITERATIONS,
    AGENT_ENABLE_GRAPH_AUTO_REVIEW,
    AGENT_ROUTER_STRATEGY,
    AGENT_TOOL_CALL_MAX_ITERATIONS,
)
from core.error_codes import ErrorCode
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

logger = get_logger(__name__)

# Max times the workflow will re-run the creating agent to finish a file it created but
# left empty (created via create_file but never completed the <file>…</file>
# write). Bounded so a model that keeps failing cannot loop indefinitely.
MAX_FILE_CORRECTION_ATTEMPTS = 2

# 一次请求内 writer → quality_reviewer 的审查轮数上限。达到后不再自动送审（自动质检门
# 与 writer 的显式送审都不再生效），审稿人再要求返修时不交回 writer，而是以正常完成
# 收尾并把审稿意见展示给用户——之前没有硬上限，只靠第 3 轮起的「尽量放行」提示，
# writer ↔ 审稿人能一直来回到协作轮数耗尽。
MAX_REVIEW_ROUNDS = 2

# 交接给审稿人时本轮 user 消息的开头标记。这条消息（含 [待审查内容] 旧稿）只写给审稿人，
# 交接给其他 agent 时要从历史里去掉，见 _drop_reviewer_task_messages。
REVIEWER_TASK_MARKER = "[质量检查任务]"

# pending-empty-file 标记的落库核验结果
_PENDING_BODY_EMPTY = "empty"
_PENDING_BODY_WRITTEN = "written"
_PENDING_BODY_GONE = "gone"
_PENDING_BODY_UNVERIFIABLE = "unverifiable"


def _probe_pending_file_body(file_id: str) -> str:
    """核验 pending-empty-file 标记指向的文件正文当前是否真的为空。

    标记由 create_file(不带 content) 置上，只有 <file>…</file> 流式写入完成
    （或流结束）时才会清除；edit_file 把正文写进去并不会清除它。所以"标记仍在"
    只说明模型没走流式写入协议，不代表正文为空——必须落库核验，否则
    create_file(空) + edit_file(op=append) 这条常走路径会被误判成空文件。

    核验不了时（无 file_id / 无可用 session / 查询失败）返回 unverifiable，
    由调用方保留原来的纠偏兜底。这里用列级查询，绕开 ORM 身份映射里可能陈旧的
    实例，直接读数据库当前值。
    """
    if not file_id:
        return _PENDING_BODY_UNVERIFIABLE

    try:
        from sqlmodel import select

        from models import File

        with ToolContext.short_lived_session() as session:
            row = session.exec(
                select(File.content, File.is_deleted).where(File.id == file_id)
            ).first()
    except Exception as e:
        logger.debug(f"Pending empty-file verification failed: {e}")
        return _PENDING_BODY_UNVERIFIABLE

    if row is None:
        return _PENDING_BODY_GONE
    content, is_deleted = row[0], row[1]
    if is_deleted:
        return _PENDING_BODY_GONE
    return _PENDING_BODY_EMPTY if not str(content or "").strip() else _PENDING_BODY_WRITTEN


def _rollback_unfinished_empty_files(
    entries: list[dict[str, str]],
) -> set[str]:
    """Soft-delete verified empty artifacts when correction cannot continue."""
    if not entries:
        return set()

    ids = [entry["file_id"] for entry in entries if entry.get("file_id")]
    if not ids:
        return set()

    try:
        with ToolContext.short_lived_session() as session:
            return _soft_delete_verified_empty_files(session, ids)
    except Exception as exc:
        logger.warning(
            "Failed to roll back unfinished empty files",
            exc_info=True,
            extra={"error": str(exc)},
        )
        return set()


def _soft_delete_verified_empty_files(session: Any, ids: list[str]) -> set[str]:
    """在给定 session 上软删除仍为空的文件；失败时回滚并抛出。"""
    from sqlalchemy import update
    from sqlmodel import select

    from config.datetime_utils import utcnow
    from models import File

    project_id = ToolContext.get_project_id()
    try:
        rolled_back: set[str] = set()
        with session.begin_nested():
            for file_id in ids:
                # Read scalar columns rather than an ORM identity-map object,
                # then include the observed content in the UPDATE predicate.
                # If a user/other run writes content between verification and
                # rollback, rowcount becomes zero and their write is preserved.
                row = session.exec(
                    select(File.content, File.is_deleted, File.project_id).where(
                        File.id == file_id
                    )
                ).first()
                if row is None:
                    continue
                content, is_deleted, row_project_id = row
                if (
                    row_project_id != project_id
                    or is_deleted
                    or str(content or "").strip()
                ):
                    continue

                result = session.exec(
                    update(File)
                    .where(
                        File.id == file_id,
                        File.project_id == project_id,
                        File.is_deleted.is_(False),
                        File.content == content,
                    )
                    .values(is_deleted=True, deleted_at=utcnow())
                )
                if result.rowcount == 1:
                    rolled_back.add(file_id)
        if rolled_back:
            session.commit()
        return rolled_back
    except Exception:
        with contextlib.suppress(Exception):
            session.rollback()
        raise


# 追加轮提示：openai-agents 不支持向进行中的 run 注入消息，运行期间到达的
# steering 只能在 run 结束后补一轮才能在本次请求内生效；引导内容本身已作为
# 用户消息追加进会话历史，这里只引导模型去看它。
STEERING_FOLLOWUP_CONTEXT = (
    "[系统提醒] 用户在生成过程中发来了新的引导消息（见对话历史末尾的用户消息）。"
    "请在已完成工作的基础上按用户最新引导继续调整或补充；"
    "如果引导无需改动已完成内容，请简要回应说明。"
)

# 只读 agent（工具集里没有任何写文件工具，如 quality_reviewer）的追加轮提示。
# 不能对它们说"继续调整或补充"——那是一句明确的"去改"指令，而 review_only
# 这类工作流本就承诺全程不动文件；要改只能显式交接给有写权限的 agent。
STEERING_FOLLOWUP_CONTEXT_READONLY = (
    "[系统提醒] 用户在生成过程中发来了新的引导消息（见对话历史末尾的用户消息）。"
    "请在已完成工作的基础上按用户最新引导继续你的审查；"
    "如果引导要求改动正文，请用 handoff_to_agent 交接给 writer，不要自行改写文件；"
    "如果引导无需额外工作，请简要回应说明。"
)



def _agent_can_write_files(agent_type: str | None) -> bool:
    """该 agent 类型的工具集里是否包含写文件工具。

    工具集由 registry 按 agent 类型分配（quality_reviewer 被刻意剥夺了
    create_file/edit_file/delete_file）。这里按 registry 的实际映射判断，
    而不是硬编码 agent 名字，新增只读 agent 时无需再改这里。
    """
    if not agent_type:
        return False
    try:
        # 写文件工具集合与 runner 的只读拒绝共用 registry.FILE_WRITE_TOOL_NAMES，
        # 「谁算有写权限」与「只读请求下拒绝哪些工具」不会各说各话。
        from agent.tools.registry import AGENT_TOOL_NAME_MAP, FILE_WRITE_TOOL_NAMES

        tool_names = AGENT_TOOL_NAME_MAP.get(agent_type)
    except Exception as e:  # pragma: no cover - registry 导入失败属异常路径
        logger.debug(f"Failed to resolve agent toolset for {agent_type}: {e}")
        return True
    if tool_names is None:
        # 未知 agent 类型走 registry 的 writer 兜底，视为有写权限。
        return True
    return bool(FILE_WRITE_TOOL_NAMES.intersection(tool_names))


def _steering_followup_context(agent_type: str | None, *, read_only: bool = False) -> str:
    """按 agent 是否有写权限（以及用户是否要求只读）选择追加轮提示文案。"""
    return (
        STEERING_FOLLOWUP_CONTEXT
        if _agent_can_write_files(agent_type) and not read_only
        else STEERING_FOLLOWUP_CONTEXT_READONLY
    )


# 用户明确要求不改文件时注入的范围约束。
SCOPE_DIRECTIVE_READ_ONLY = (
    "用户明确要求本轮不修改任何文件（只回答/只分析/只讨论）。"
    "禁止调用 create_file / edit_file / delete_file，也不要交接给其他 Agent 去改写；"
    "直接在对话里给出回答或分析即可。"
)
# 路由判定用户没要正文（只要大纲/人设/设定/爽点思路）时注入的范围约束。
SCOPE_DIRECTIVE_NO_CONTENT = (
    "用户本轮没有要求写正文：只做用户要求的事（规划/设定/设计/回答等），完成后直接结束；"
    "不要撰写或续写章节正文，也不要交接给 writer 去写。"
)


def _build_scope_directive(
    routing_metadata: dict[str, Any] | None, *, agent_type: str | None = None
) -> str:
    """把路由对用户范围的判断（read_only / write_content / scope）转成本次运行的系统指令约束。

    交接包和 handoff 文本只在交接时出现，而本轮第一个 agent 也需要知道范围，
    因此统一由 nodes.build_agent_directive 放进每个 agent 运行的系统指令（输入最后一条消息）。

    agent_type 是这次要跑的 agent。「规划完成后直接交接 writer 写正文」只是写给规划类
    角色的：计划交接后的 writer、审稿人也读到它时，会把它当成「作者要正文」的事实，
    和交接说明「作者要的已经全部交付时直接结束」、自己的「只为客观缺陷交接」互相打架
    （10-10 U070：writer 在两条之间来回权衡 20 万字后撞上输出上限）。
    """
    if not isinstance(routing_metadata, dict):
        return ""
    if routing_metadata.get("clarify_first") is True:
        return SCOPE_DIRECTIVE_CLARIFY_FIRST
    parts: list[str] = []
    if routing_metadata.get("read_only") is True:
        parts.append(SCOPE_DIRECTIVE_READ_ONLY)
    elif routing_metadata.get("write_content") is False:
        parts.append(SCOPE_DIRECTIVE_NO_CONTENT)
    elif (
        routing_metadata.get("write_content") is True
        and routing_metadata.get("agent_type") in PLANNING_AGENTS
        and (agent_type is None or agent_type in PLANNING_AGENTS)
    ):
        # 作者已经要正文：规划师不要按「首轮问一句要不要开始写」的规则截停（P2-21）。
        parts.append(SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING)
    scope = str(routing_metadata.get("scope") or "").strip()
    if scope:
        parts.append(
            f"用户要求的交付范围：{scope}。只交付这个范围内的内容，"
            "不要额外续写后续章节或创建范围外的文件；范围内的工作完成后即结束。"
        )
    return "\n".join(parts)


def _should_clarify_first(state: WritingState) -> bool:
    """Ask about a vague edit unless selected context or a recorded clarification exists."""
    message = str(state.get("router_message") or state.get("user_message") or "")
    if not is_vague_edit_request(message):
        return False
    if has_selected_text(str(state.get("user_message") or "")):
        return False
    scope = ToolContext.get_author_scope()
    if scope is not None and scope.referenced_file_ids:
        return False
    previous = _last_assistant_message(list(state.get("messages") or []))
    if previous is not None:
        if _previous_routing(previous).get("clarify_first") is True:
            return False
        if previous.get("clarification_pending") is True:
            return False
    return True


def _written_outline_contents(file_ids: list[str]) -> list[str]:
    """本请求写过的大纲文件的正文（判断对话里的规划是否已经落进文件）。"""
    if not file_ids:
        return []
    from sqlmodel import select

    from models import File

    with ToolContext.short_lived_session() as session:
        rows = session.exec(
            select(File).where(
                File.id.in_(file_ids),  # type: ignore[attr-defined]
                File.file_type == "outline",
                File.is_deleted.is_(False),  # type: ignore[attr-defined]
            )
        ).all()
        return [str(row.content or "") for row in rows]


def _review_notes_from_packet(packet: dict[str, Any] | None, context: str) -> str:
    """审稿人要求返修时的交接内容（context + todo），用于达到审查上限后展示给用户。"""
    parts: list[str] = []
    if isinstance(packet, dict):
        packet_context = str(packet.get("context") or "").strip()
        if packet_context:
            parts.append(packet_context)
        todo = [str(item).strip() for item in packet.get("todo") or [] if str(item).strip()]
        if todo:
            parts.append("\n".join(f"- {item}" for item in todo))
    if not parts and context.strip():
        parts.append(context.strip())
    return "\n".join(parts)


# 图自己写进 evidence 的内部标记（workflow_plan=standard、auto_checks=repeat:2,facts:1）：
# 对下一个 agent 没有信息量，不渲染。
_INTERNAL_EVIDENCE_RE = re.compile(r"^[a-z_]+=\S*$")


def _format_handoff_packet_items(packet: dict[str, Any] | None) -> str:
    """把交接包的 todo / evidence 渲染成下一个 agent 交接信息里的列表。"""
    if not isinstance(packet, dict):
        return ""

    def _items(key: str) -> list[str]:
        raw = packet.get(key)
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    todo = _items("todo")
    evidence = [item for item in _items("evidence") if not _INTERNAL_EVIDENCE_RE.match(item)]
    sections: list[str] = []
    if todo:
        sections.append("[待办]\n" + "\n".join(f"- {item}" for item in todo))
    if evidence:
        sections.append("[依据]\n" + "\n".join(f"- {item}" for item in evidence))
    return "".join(f"\n\n{section}" for section in sections)


def _unfinished_file_notice(
    rolled_back: list[str], unresolved: list[str], english: bool = False
) -> str:
    """工具调用轮数耗尽时留下空文件：回滚 / 无法回滚的说明（随 assistant 正文落库）。"""
    lines: list[str] = []
    if english:
        if rolled_back:
            titles = ", ".join(f"\"{title}\"" for title in rolled_back)
            lines.append(f"{titles} had no text yet, so the empty file was removed. Reply \"continue\" to write it again.")
        if unresolved:
            titles = ", ".join(f"\"{title}\"" for title in unresolved)
            lines.append(
                f"{titles} has no text yet and could not be removed automatically. "
                "Reply \"continue\" to finish it, or delete it yourself."
            )
    else:
        if rolled_back:
            titles = "、".join(f"《{title}》" for title in rolled_back)
            lines.append(f"{titles}还没写入正文，已撤销这个空文件。回复「继续」会重新写。")
        if unresolved:
            titles = "、".join(f"《{title}》" for title in unresolved)
            lines.append(f"{titles}还没写入正文，也没能自动撤销。回复「继续」补完，或手动删除。")
    if not lines:
        return ""
    return "\n\n——\n" + "\n".join(lines)


async def _roll_back_empty_files_after_exhaustion(
    agent_type: str | None,
    english: bool = False,
) -> AsyncIterator[StreamEvent]:
    """工具调用轮数耗尽且还有 pending 空文件：核验 → 回滚空文件 → 文字说明。

    与空文件纠偏分支「配额用尽」时同一套核验与回滚，只是不再安排补写轮。
    """
    unfinished: list[dict[str, str]] = []
    for entry in ToolContext.get_pending_empty_files():
        file_id = str(entry.get("file_id") or entry.get("id") or "")
        title = str(entry.get("title") or ("Untitled" if english else "未命名"))
        body_state = await asyncio.to_thread(_probe_pending_file_body, file_id)
        if body_state in (_PENDING_BODY_WRITTEN, _PENDING_BODY_GONE):
            ToolContext.clear_pending_empty_file(file_id)
            continue
        unfinished.append({"file_id": file_id, "title": title})
    if not unfinished:
        return

    rolled_back = await asyncio.to_thread(_rollback_unfinished_empty_files, unfinished)
    for entry in unfinished:
        if entry["file_id"] in rolled_back:
            ToolContext.clear_pending_empty_file(entry["file_id"])
    rolled_back_titles = [e["title"] for e in unfinished if e["file_id"] in rolled_back]
    unresolved_titles = [e["title"] for e in unfinished if e["file_id"] not in rolled_back]
    log_with_context(
        logger,
        30,  # WARNING
        "Tool-call exhaustion left empty files; rolled back what could be verified",
        agent_type=agent_type,
        unfinished_count=len(unfinished),
        rolled_back_count=len(rolled_back_titles),
        unresolved_count=len(unresolved_titles),
    )
    notice = _unfinished_file_notice(rolled_back_titles, unresolved_titles, english)
    if notice:
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": notice})


# 写完 </file> 就结束了 run（openai-agents 把不带工具调用的回复当作最终输出）：同一个 agent
# 接着跑一轮，见 completion_evidence.ends_on_file_block 与主循环里的续写分支。
FILE_STREAM_CONTINUATION_MARKER = "[系统提醒] 你上一次回复写完 </file> 就结束了"

# 作品的第一条消息这一轮不送审：writer 想交给审稿人时丢掉交接，让它自己给作者写总结（交接前按
# 回复协议它不写总结）。见 first-turn-skips-auto-review。
FIRST_ROUND_SUMMARY_CONTEXT = (
    "[系统提醒] 这是作者在这部作品里的第一条消息，这一轮不送审，作者在对话里还什么都没看到。"
    "不要再调用任何工具，也不要重写文件，直接给作者写这一轮的总结：先用一两句说写了哪些文件、"
    "各多少字（按工具返回的字数），再用三五句话交代主角、背景和核心冲突，"
    "最后列两个可以换的方向，每个一句话、用「」括起来，作者可以照着直接回复。"
)


WRAP_UP_TEXT = "\n\n这一轮的时间快用完了，先停在这里；回复「继续」我接着做。"
WRAP_UP_TEXT_EN = "\n\nThis round is almost out of time, so I'm stopping here. Reply \"continue\" and I'll pick it up."
FAST_MODE_SUMMARY_CONTEXT = (
    "[系统提醒] 本轮是快速模式，不送审，刚才的交接没有执行，作者在对话里只看到了你那句进度说明。"
    "不要再调用任何工具，也不要重写文件，直接给作者写这一轮的总结：写了哪些文件、各多少字"
    "（按工具返回的字数），简要说内容；不要说已经通读核对、送审或审稿。"
)
AUTHOR_NOTES_RELAY_HEADER = "下面是你原本写在交接里、要转告作者的话，照实写在总结最后："
AUTHOR_NOTES_MARKER = "[要告诉作者]"
# 交接前只写一句进度（如「第三章扩写好了，我再通读核对一遍」）时，这句话不算给作者的总结。
PROGRESS_ONLY_MAX_CHARS = 80


def _author_notes_from_handoff(context: Any) -> str:
    """writer 交接 context 里「[要告诉作者]」那一段；没有或写的是「无」时为空。"""
    text = str(context or "")
    start = text.find(AUTHOR_NOTES_MARKER)
    if start < 0:
        return ""
    notes = text[start + len(AUTHOR_NOTES_MARKER):]
    next_header = re.search(r"\n\s*\[[^\]\n]{1,30}\]", notes)
    if next_header:
        notes = notes[: next_header.start()]
    notes = notes.strip().lstrip("：:").strip()
    return "" if notes in {"", "无", "无。", "没有"} else notes


def _open_board_tasks() -> list[dict[str, Any]]:
    """任务计划板上还没完成的任务（读不到时为空，不影响续写）。"""
    session_id = ToolContext.get_session_id()
    if not session_id:
        return []
    try:
        from services.infra.task_board_service import task_board_service

        tasks = task_board_service.get_tasks(
            session_id,
            user_id=ToolContext.get_user_id(),
            project_id=ToolContext.get_project_id(),
        ) or []
    except Exception as e:
        logger.debug(f"Failed to read task board for continuation: {e}")
        return []
    return [
        task
        for task in tasks
        if isinstance(task, dict) and task.get("status") in {"pending", "in_progress"}
    ]


def _file_stream_continuation_context(
    written_titles: list[str],
    open_tasks: list[dict[str, Any]],
    user_scope: str,
) -> str:
    """写完 </file> 就结束 run 之后，同一个 agent 接着那一轮的交接说明。"""
    saved = "、".join(f"《{title}》" for title in written_titles if title)
    lines = [
        f"{FILE_STREAM_CONTINUATION_MARKER}，没有接着建下一个文件，也没有给作者写总结，"
        "作者在对话里还什么都没看到。"
    ]
    if saved:
        lines.append(f"本请求已保存：{saved}。这些文件不要重写。")
    if user_scope:
        lines.append(f"作者这一轮要求的交付范围：{user_scope}。")
    if open_tasks:
        status_names = {"pending": "未开始", "in_progress": "进行中"}
        task_lines = []
        for task in open_tasks:
            line = f"- {task.get('task')}（{status_names.get(str(task.get('status')), '')}）"
            progress = str(task.get("progress") or "").strip()
            if progress:
                line += f"：{progress}"
            task_lines.append(line)
        lines.append("任务计划板上还没完成的任务：\n" + "\n".join(task_lines))
    lines.append(
        "对照作者的要求：还有没写的文件，就调用 create_file 写下一个（工具返回后，下一次回复以 <file> 开头"
        "写正文），写完 </file> 后在同一次回复里接着建下一个，或者写总结；"
        "全部写完了，就不再调用写文件的工具，直接用 1–3 行告诉作者实际写了哪些文件、各多少字"
        "（按工具返回的字数），没写完的如实说明。"
    )
    return "\n".join(lines)


def _author_facing_handoff_event_data(
    event_data: dict[str, Any], english: bool = False
) -> dict[str, Any]:
    """发给前端的 HANDOFF 事件：reason / context 换成作者能看懂的话。

    前端把 reason 拼进「接下来由{{agent}}继续：{{reason}}」，事件也会存进展示记录。
    交接包（handoff_packet）和交给下一个 agent 的交接信息不在这里改，返回的是新字典。
    """
    display = dict(event_data)
    display["reason"] = author_facing_handoff_reason(event_data.get("reason"), english)
    if "context" in event_data:
        display["context"] = author_facing_text(event_data.get("context"))
    return display


def _review_limit_text(notes: str, english: bool = False) -> str:
    """达到审查轮数上限、不再自动返修时追加给用户的说明（随 assistant 正文落库）。

    notes 应先经过 author_facing_text：它来自审稿人写给 writer 的交接说明。
    """
    body = f"\n{notes}" if notes else ""
    if english:
        return (
            f"\n\n——\nFinished {MAX_REVIEW_ROUNDS} rounds of quality review (the limit for one request), "
            f"so no more automatic revisions. The reviewer still suggests changes:{body}\n"
            "To revise along these notes, reply \"revise per the review\"."
        )
    return (
        f"\n\n——\n已完成 {MAX_REVIEW_ROUNDS} 轮质量审查（本次请求的上限），不再自动返修。"
        f"审稿人仍建议修改：{body}\n"
        "如需按这些意见继续修改，请直接回复「按审稿意见修改」。"
    )


def _is_reviewer_task_message(message: Any) -> bool:
    """是不是交接给审稿人时那条「[质量检查任务]…」user 消息（content 可能是字符串或 block 列表）。"""
    if not isinstance(message, dict):
        return False
    if str(message.get("role") or "").strip().lower() != "user":
        return False
    text = extract_text_from_message_content(message.get("content"))
    return text.lstrip().startswith(REVIEWER_TASK_MARKER)


def _drop_reviewer_task_messages(messages: Any) -> list[Any]:
    """去掉只写给审稿人的审稿任务消息，其余消息（作者原话、各 agent 输出、交接信息）原样保留。

    审稿人那次 run 的 user 消息会随 state["messages"] 传给下一个 agent。返修交回 writer 时，
    writer 的输入里就同时有「[质量检查任务] 请审查…[待审查内容] <旧稿>」和系统提示里的
    「当前角色：writer」，模型在思考里把自己当成审稿人反复纠结
    （见 bug-fix/2026-10-10-writer-handoff-reviewer-task-and-length-stop.md）。
    """
    if not isinstance(messages, list):
        return []
    return [message for message in messages if not _is_reviewer_task_message(message)]


def _author_is_english(state: WritingState) -> bool:
    """作者界面语言是不是英文（service 按 Accept-Language 写入 state["language"]）；
    决定状态条、交接原因和追加说明这些作者看得到的固定文案用哪种语言。"""
    return str(state.get("language") or "").strip().lower().startswith("en")


def _guard_from_state(state: WritingState) -> RepeatReadGuard | None:
    guard = state.get("repeat_read_guard")
    return guard if isinstance(guard, RepeatReadGuard) else None


# writer → quality_reviewer 交接时附在交接信息末尾的程序检测证据。标题是和审稿人
# 提示词约定好的固定写法，不能改。
REVIEW_AUTO_CHECK_HEADER = "[自动检测：需核对]"
# 读正文 + 计算的总预算：超时就跳过，不阻断送审。
REVIEW_AUTO_CHECK_TIMEOUT_SECONDS = 0.5
_REVIEW_AUTO_CHECK_MAX_TARGETS = 2
_REVIEW_AUTO_CHECK_PREVIOUS_SIBLINGS = 2
_REVIEW_AUTO_CHECK_MAX_CHARACTER_CARDS = 10
_REVIEW_AUTO_CHECK_MAX_REPEATS = 8
_REVIEW_AUTO_CHECK_MAX_FACTS = 6


def _collect_review_auto_checks(file_ids: list[str]) -> tuple[list[str], list[str]] | None:
    """读本请求写过的正文（draft / script，最多 2 个）和参照文件，跑复读与数字事实对照。

    参照文件：同一父目录、同类型、按 order（与文件清单同一排序键）排在它前面的 2 个文件，
    以及项目里最多 10 张角色卡。同步执行（调用方放进工作线程），用短生命周期 session。
    没有可检测的正文时返回 None；返回 (复读证据, 事实对照)。
    """
    project_id = ToolContext.get_project_id()
    if not project_id or not file_ids:
        return None

    from sqlmodel import select

    from models import File
    from utils.title_sequence import build_sequence_sort_key

    def _sort_key(row: Any) -> tuple:
        try:
            metadata = json.loads(row.file_metadata or "{}")
        except (TypeError, ValueError):
            metadata = {}
        effective_order, seq_num = build_sequence_sort_key(
            row.order, title=row.title, metadata=metadata if isinstance(metadata, dict) else {},
            file_type=row.file_type,
        )
        return (effective_order, seq_num, row.created_at, row.id)

    with ToolContext.short_lived_session() as session:
        rows = session.exec(
            select(
                File.id, File.title, File.file_type, File.parent_id, File.order, File.created_at,
                File.content, File.file_metadata,
            ).where(
                File.project_id == project_id,
                File.id.in_(file_ids),
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
            )
        ).all()
        by_id = {row.id: row for row in rows}
        targets = [by_id[file_id] for file_id in file_ids if file_id in by_id][:_REVIEW_AUTO_CHECK_MAX_TARGETS]
        if not targets:
            return None

        cards = session.exec(
            select(File.title, File.content)
            .where(
                File.project_id == project_id,
                File.file_type == "character",
                File.is_deleted.is_(False),
            )
            .order_by(File.order, File.created_at)
            .limit(_REVIEW_AUTO_CHECK_MAX_CHARACTER_CARDS)
        ).all()
        card_refs = [(f"角色：{title}", content or "") for title, content in cards if (content or "").strip()]

        repeats: list[str] = []
        facts: list[str] = []
        for target in targets:
            parent_clause = (
                File.parent_id.is_(None) if target.parent_id is None else File.parent_id == target.parent_id
            )
            siblings = session.exec(
                select(File.id, File.title, File.file_type, File.order, File.created_at, File.file_metadata).where(
                    File.project_id == project_id,
                    parent_clause,
                    File.file_type == target.file_type,
                    File.is_deleted.is_(False),
                )
            ).all()
            ordered = sorted(siblings, key=_sort_key)
            position = next((i for i, row in enumerate(ordered) if row.id == target.id), None)
            previous_ids = (
                [row.id for row in ordered[max(0, position - _REVIEW_AUTO_CHECK_PREVIOUS_SIBLINGS) : position]]
                if position
                else []
            )
            neighbors: list[tuple[str, str]] = []
            if previous_ids:
                contents = {
                    row.id: (row.title, row.content or "")
                    for row in session.exec(
                        select(File.id, File.title, File.content).where(File.id.in_(previous_ids))
                    ).all()
                }
                neighbors = [contents[file_id] for file_id in previous_ids if file_id in contents]

            target_text = target.content or ""
            repeats.extend(find_repetition_evidence(target.title, target_text, neighbors))
            facts.extend(find_numeric_fact_pairs((target.title, target_text), neighbors + card_refs))

    return repeats[:_REVIEW_AUTO_CHECK_MAX_REPEATS], facts[:_REVIEW_AUTO_CHECK_MAX_FACTS]


async def _review_auto_checks(file_ids: list[str]) -> tuple[list[str], list[str]] | None:
    """在预算内跑送审前的程序检测；读取失败或超时只记 WARNING，返回 None（照常送审）。"""
    if not file_ids:
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_collect_review_auto_checks, list(file_ids)),
            timeout=REVIEW_AUTO_CHECK_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        log_with_context(
            logger,
            30,  # WARNING
            "Review auto-checks timed out; handing off without them",
            timeout_seconds=REVIEW_AUTO_CHECK_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        log_with_context(
            logger,
            30,  # WARNING
            "Review auto-checks failed; handing off without them",
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return None


# 送审内容的总预算；先逐文件分配，避免拼接后截掉中间整章。
REVIEW_PAYLOAD_MAX_CHARS = 9000
# 只附改动段时，改动前后各带多少字符的原文（按整行对齐）。
_REVIEW_CHANGED_CONTEXT_CHARS = 600


def _changed_span(before: str, after: str) -> tuple[int, int] | None:
    """after 里和 before 不一样的那一段 [start, end)；两者相同时为 None。"""
    if before == after:
        return None
    limit = min(len(before), len(after))
    start = 0
    while start < limit and before[start] == after[start]:
        start += 1
    end_after, end_before = len(after), len(before)
    while end_after > start and end_before > start and before[end_before - 1] == after[end_after - 1]:
        end_after -= 1
        end_before -= 1
    return start, end_after


def _section_start_before(content: str, start: int, count: int = 1) -> int | None:
    """``start`` 之前倒数第 ``count`` 个章节/分集标题的位置；不够 ``count`` 个时为 None。"""
    from agent.tools.file_ops.content_range import heading_offsets

    offsets = [offset for offset in heading_offsets(content) if offset < start]
    return offsets[-count] if len(offsets) >= count else None


def _review_payload_block(
    title: str, content: str, before: str | None, *, max_chars: int = REVIEW_PAYLOAD_MAX_CHARS
) -> str:
    """一个文件的送审块：系统标注（文件名 + 编辑器字数）+ 正文或本请求改动的那一段。

    - 字数由系统按编辑器口径算好：审稿人「第一行报文件名和字数」「篇幅是否达标」不必为了
      一个数字再把整份文件读一遍（10-10 U072 / U074、10-11 U094：summary 读取不带
      word_count，模型反复权衡要不要全文重读）。
    - 本请求之前就有正文、而且整份放不进送审预算的文件，只附改动的那一段和前后少量原文，
      并写明其余部分没改、没附上。以前取的是整份文件的开头和结尾：在 14 万字的单文件里
      追加第 101—105 章，审稿人收到的「待审查内容」是第 1—4 章（10-10 U074）。
    """
    words = count_words(content)
    body = content.strip()
    header = f"[《{title}》编辑器字数 {words}]\n"
    span = _changed_span(before, content) if before and before.strip() else None
    if span is None or len(header) + len(body) <= max_chars:
        return f"{header}{body}"
    start, end = span

    def excerpt_note(leading_words: int, trailing_words: int, sections: int = 0) -> str:
        attached = (
            f"本请求改动的部分，以及改动之前的 {sections} 个章节（从标题起的完整原文）和后面少量原文"
            if sections else "本请求改动的部分和前后少量原文"
        )
        return (
            f"[《{title}》编辑器字数 {words}。下面是{attached}；"
            f"再往前的 {leading_words} 字、后面的 {trailing_words} 字"
            "本请求没有改动，没有附上；核对更早的事实时按章节读取那一段，不要整份重读]\n"
        )

    # 先为标注和改动本身预留空间；邻接旧文不能挤掉真正的新改句。
    note_budget = len(excerpt_note(words, words, 2))
    compact_note = "[本请求改动片段；其余原文没有附上，请读取文件核对]\n"
    if note_budget >= max_chars:
        note_budget = len(compact_note)
    body_budget = max(0, max_chars - note_budget)
    if not body_budget:
        return compact_note[:max_chars]
    changed = content[start:end]
    context_chars = min(_REVIEW_CHANGED_CONTEXT_CHARS, max(0, body_budget - len(changed)) // 2)
    context_start = max(0, start - context_chars)
    line_break = content.find("\n", context_start, start)
    if context_start > 0 and line_break != -1:
        context_start = line_break + 1
    # 审稿人按提示要对照「同一文件或前两章」核对重复和事实：只给前后 600 字时它会再把文件结尾
    # 整段读一遍（#275）。放得下时从改动之前的两个（不够就一个）章节标题起附完整原文。
    sections_attached = 0
    for count in (2, 1):
        section_start = _section_start_before(content, start, count)
        if (
            section_start is not None
            and section_start < context_start
            and (start - section_start) + len(changed) + context_chars <= body_budget - 40
        ):
            context_start, sections_attached = section_start, count
            break
    context_end = min(len(content), end + context_chars)
    line_break = content.rfind("\n", end, context_end)
    if context_end < len(content) and line_break != -1:
        context_end = line_break
    excerpt = _format_review_payload(content[context_start:context_end], max_chars=body_budget)
    note = excerpt_note(
        count_words(content[:context_start]), count_words(content[context_end:]), sections_attached
    )
    if len(note) > note_budget:
        note = compact_note
    return f"{note}{excerpt}"


def _collect_review_payload_text(file_ids: list[str], *, focus: str | None = None) -> str | None:
    """读本请求写过的正文（draft / script）落库后的内容，按写入顺序拼成送审内容。

    流式落库和 edit_file 会把半角引号规范成稿件风格，库里这份才是作者看到的正文；
    模型原始输出里那些已经修好的半角引号不能再送审。没有可用正文时返回 None。
    每个文件带系统标注的编辑器字数；长文件只附本请求改动的那一段（见 _review_payload_block）。

    focus 是审稿人交回返修的意见：只附意见里点到（标题或 id）的文件，把预算留给要改的正文，
    其余文件只列名。一个都没点到时照旧全附。
    """
    project_id = ToolContext.get_project_id()
    if not project_id or not file_ids:
        return None

    from sqlmodel import select

    from models import File

    with ToolContext.short_lived_session() as session:
        rows = session.exec(
            select(File.id, File.title, File.content, File.updated_at).where(
                File.project_id == project_id,
                File.id.in_(file_ids),
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
            )
        ).all()
    files = {
        row.id: (row.title or "", row.content or "", row.updated_at)
        for row in rows
        if (row.content or "").strip()
    }
    ordered = list(dict.fromkeys(file_id for file_id in file_ids if file_id in files))
    if not ordered:
        return None
    prefix = ""
    if focus:
        # 批量写的多章平分 9000 字预算时，要改的那章只剩片段，返修 writer 只能再读一遍。
        named = [
            file_id for file_id in ordered
            if file_id in focus or (files[file_id][0].strip() and files[file_id][0].strip() in focus)
        ]
        if named and len(named) < len(ordered):
            skipped = "、".join(f"《{files[file_id][0]}》" for file_id in ordered if file_id not in named)
            # 只是标题匹配：意见换了叫法（「第3章」对「第三章」）时也会落在这里，不能说「不用改」。
            prefix = f"[审稿意见没有按标题提到 {skipped}，没有附上；意见要改其中的内容时按章节读那一段]\n\n"
            ordered = named
    if len(ordered) > 1:
        counts = "、".join(f"《{files[file_id][0]}》{count_words(files[file_id][1])}" for file_id in ordered)
        prefix += f"[本请求写过的正文（编辑器字数）：{counts}]\n\n"
    def source_line(file_id: str, whole: bool) -> str:
        # 每份都写「省略内容用 query_files 读取」时，审稿人对已经附了全文的文件也会再读一遍。
        tail = "以下是全文，不用再读取" if whole else f"省略内容用 query_files(id={file_id}) 按章节读取后再核对"
        return f"[来源 id={file_id} updated_at={files[file_id][2].isoformat()}；{tail}]"

    # 预算按较长的那种来源行预留，算完分配后再按实际是否附了全文写。
    sources = [source_line(file_id, False) for file_id in ordered]
    available = REVIEW_PAYLOAD_MAX_CHARS - len(prefix) - sum(len(source) + 1 for source in sources)
    available -= 2 * (len(ordered) - 1)
    if available <= 0:
        # 极大批次连清单也放不下时，不把部分片段冒充完整送审正文。
        return _format_review_payload(
            "[本轮正文未附：文件清单超出预算，须按交接读写记录和文件清单读取正文后核对]\n"
            + "\n".join(sources)
        )
    share = max(1, available // len(ordered))
    blocks = [
        _review_payload_block(
            files[file_id][0], files[file_id][1], ToolContext.pre_write_content(file_id), max_chars=share
        )
        for file_id in ordered
    ]
    # 短文件先完整容纳，其余正文平分剩余预算，省略标记也在预算内。
    allocations = [0] * len(blocks)
    for rank, index in enumerate(sorted(range(len(blocks)), key=lambda index: len(blocks[index]))):
        allocation = min(len(blocks[index]), available // (len(blocks) - rank))
        allocations[index] = allocation
        available -= allocation
    parts = []
    for file_id, block, allocation in zip(ordered, blocks, allocations, strict=True):
        attached = _format_review_payload(block, max_chars=allocation) if allocation else ""
        whole = attached.endswith(files[file_id][1].strip())
        parts.append(source_line(file_id, whole) + "\n" + attached)
    return prefix + "\n\n".join(parts)


async def _review_payload_text(file_ids: list[str], *, focus: str | None = None) -> str | None:
    """在送审预算内读落库正文；读取失败或超时只记 WARNING，返回 None（退回模型输出）。"""
    if not file_ids:
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(functools.partial(_collect_review_payload_text, list(file_ids), focus=focus)),
            timeout=REVIEW_AUTO_CHECK_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        log_with_context(
            logger,
            30,  # WARNING
            "Review payload read timed out; using writer output",
            timeout_seconds=REVIEW_AUTO_CHECK_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        log_with_context(
            logger,
            30,  # WARNING
            "Review payload read failed; using writer output",
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return None


def _format_review_auto_checks(repeats: list[str], facts: list[str]) -> str:
    """有检测结果时渲染成交接信息末尾的「[自动检测：需核对]」段，没有时返回空串。"""
    items = [*repeats, *facts]
    if not items:
        return ""
    return f"\n\n{REVIEW_AUTO_CHECK_HEADER}\n" + "\n".join(f"- {item}" for item in items)


# 送审时附给审稿人的两段：本轮正文的编辑器字数、作者说过的篇幅要求（I05）。
BODY_WORD_COUNT_HEADER = "[本轮正文字数]"
LENGTH_REQUIREMENT_HEADER = "[作者的篇幅要求]"
_BODY_WORD_COUNT_NOTE = (
    "以上是编辑器统计的实际字数；交接说明和对话里写的字数是写作时的估计，"
    "判断篇幅、回复作者都只用这里的数。"
)
_LENGTH_REQUIREMENT_NOTE = (
    "以上是作者在本会话或项目设定里说过的篇幅要求（后说的为准），不只是作者本轮原话："
    "逐章对照上面的实际字数判断够不够；说的是全篇还是单章看上下文。"
    "实际字数在标出的达标区间内就算达标，不为篇幅返工。"
)
# writer 送审前写给作者的说明被丢掉了：审稿人的回复是作者看到的唯一总结。
WRITER_CHAT_SUPERSEDED_NOTE = (
    "[作者看到的回复] 内容创作者写完后给作者的说明没有显示给作者（避免出现两份总结）。"
    "不需要再改时，你的回复就是作者看到的唯一一份总结：说清写好了什么、实际字数，"
    "其中作者需要知道的取舍和可选的下一步一并写进去。需要再改时照常不写文字，"
    "交给内容创作者修改，由它修完后回复作者。"
)
# writer 以问作者的问题收尾、它的说明又被丢掉了：问题原样交给审稿人 / 返修 writer。
WRITER_QUESTION_HEADER = "[内容创作者要问作者的问题]"
_WRITER_QUESTION_FOR_REVIEWER_NOTE = (
    "作者还没看到这个问题。不需要再改时，把它原样放在你回复的最后（不算进可选建议）；"
    "需要再改时照常交给内容创作者，由它修完后问。"
)
_WRITER_QUESTION_FOR_REWORK_NOTE = "作者还没看到这个问题：改完后把它原样放在你回复的最后问作者。"
# 返修时附上审稿对象的当前落库正文（与送审片段同一口径）。
REWORK_CURRENT_TEXT_HEADER = (
    "[返修对象当前正文] 以下是审稿意见涉及的正文的当前落库版本（长文件只附改动所在的一段），"
    "每个文件标了编辑器字数；改这里面的内容时直接逐字引用作为 edit_file 的 old/anchor，"
    "不要再用 query_files 或 parallel_execute 读这些正文，改完的字数看 edit_file 返回的 new_word_count；"
    "要改的地方不在下面时只按章节读那一段。"
)
WRITER_REWORK_AFTER_SUPERSEDED_NOTE = (
    "[作者看到的回复] 你送检前写给作者的说明作者没有看到。这次改完后的回复就是作者看到的"
    "唯一一份总结：说清这一轮写了什么，字数只用 edit_file 返回的 new_word_count，"
    "之前说明里作者需要知道的取舍一并写进去。"
)


def _collect_body_word_counts(file_ids: list[str]) -> list[tuple[str, int]] | None:
    """本请求写过的正文类文件（draft / script）落库后的编辑器字数，按写入顺序。"""
    project_id = ToolContext.get_project_id()
    if not project_id or not file_ids:
        return None

    from sqlmodel import select

    from models import File

    with ToolContext.short_lived_session() as session:
        rows = session.exec(
            select(File.id, File.title, File.content).where(
                File.project_id == project_id,
                File.id.in_(file_ids),
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
            )
        ).all()
    by_id = {row.id: (row.title or "", count_words(row.content or "")) for row in rows}
    return [by_id[file_id] for file_id in file_ids if file_id in by_id] or None


def _collect_length_requirement_sources() -> list[tuple[str, str]]:
    """篇幅要求的来源：项目设定、本会话作者说过的话、作者本轮原话（按先后）。"""
    sources: list[tuple[str, str]] = []
    project_id = ToolContext.get_project_id()
    session_id = ToolContext.get_session_id()
    if project_id or session_id:
        from sqlmodel import desc, select

        from models import ChatMessage, Project

        with ToolContext.short_lived_session() as session:
            if project_id:
                project = session.get(Project, project_id)
                if project is not None:
                    for label, value in (
                        ("项目简介", project.summary),
                        ("项目写作风格", project.writing_style),
                        ("项目备注", project.notes),
                    ):
                        if isinstance(value, str) and value.strip():
                            sources.append((label, value))
            if session_id:
                rows = session.exec(
                    select(ChatMessage.content)
                    .where(ChatMessage.session_id == session_id, ChatMessage.role == "user")
                    .order_by(desc(ChatMessage.created_at))
                    .limit(_LENGTH_REQUIREMENT_HISTORY_LIMIT)
                ).all()
                sources.extend(("作者在本会话说过", content) for content in reversed(rows) if content)
    sources.extend(("作者本轮原话", message) for message in ToolContext.get_author_messages())
    return sources


_LENGTH_REQUIREMENT_HISTORY_LIMIT = 40


async def _bounded_review_reads(reads: dict[str, Any]) -> dict[str, Any]:
    """送审前的几次一次性读取，共用一份送审预算（REVIEW_AUTO_CHECK_TIMEOUT_SECONDS）。

    在同一个工作线程里按顺序读（测试 / 非 offload 路径共用一个 session，不能并发），超时时
    返回已经读完的那些，没读完的是 None；失败或超时只记 WARNING。超时后线程不能被取消，
    会把正在做的那次读取做完（用的是用完即还的短 session），结果不再采用。
    """
    results: dict[str, Any] = dict.fromkeys(reads)

    def _run() -> None:
        for label, func in reads.items():
            try:
                results[label] = func()
            except Exception as exc:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Review handoff read failed; handing off without it",
                    read=label,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    try:
        await asyncio.wait_for(asyncio.to_thread(_run), timeout=REVIEW_AUTO_CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        log_with_context(
            logger,
            30,  # WARNING
            "Review handoff reads timed out; handing off with what was read",
            timeout_seconds=REVIEW_AUTO_CHECK_TIMEOUT_SECONDS,
            missing=[label for label, value in results.items() if value is None],
        )
    return dict(results)


def _format_body_word_counts(
    counts: list[tuple[str, int]] | None,
    sections: list[tuple[str, str, int]] | None = None,
) -> str:
    if not counts:
        return ""
    lines = "\n".join(f"- 《{title}》{words} 字" if title else f"- {words} 字" for title, words in counts)
    if sections:
        # 一个文件里写了好几章/集时逐章给数（#277）：审稿人对照「每章/每集」要求不必自己数。
        lines += "\n" + _SECTION_WORD_COUNT_LEAD + "\n" + "\n".join(
            f"  - 《{title}》{heading} {words} 字" for title, heading, words in sections
        )
    return f"\n\n{BODY_WORD_COUNT_HEADER}\n{lines}\n{_BODY_WORD_COUNT_NOTE}"


_SECTION_WORD_COUNT_LEAD = "其中本请求改动所在的各章/集（按章节标题切分，从标题算到下一个同级标题）："
_MAX_SECTION_WORD_COUNTS = 20


def _collect_section_word_counts(file_ids: list[str]) -> list[tuple[str, str, int]] | None:
    """本请求改动所在的章节/分集的编辑器字数，只看落库版本里改动覆盖到的章节。"""
    project_id = ToolContext.get_project_id()
    if not project_id or not file_ids:
        return None

    from sqlmodel import select

    from agent.tools.file_ops.content_range import section_word_counts
    from models import File

    with ToolContext.short_lived_session() as session:
        rows = session.exec(
            select(File.id, File.title, File.content).where(
                File.project_id == project_id,
                File.id.in_(file_ids),
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
            )
        ).all()
    by_id = {row.id: (row.title or "", row.content or "") for row in rows}
    found: list[tuple[str, str, int]] = []
    for file_id in file_ids:
        if file_id not in by_id:
            continue
        title, content = by_id[file_id]
        before = ToolContext.pre_write_content(file_id)
        span = _changed_span(before, content) if before and before.strip() else (0, len(content))
        if span is None:
            continue
        counts = section_word_counts(content, *span)
        if len(counts) < 2 and not (before and before.strip()):
            # 新建的单章文件：上面的文件字数就是这一章的字数，不重复列。
            continue
        found.extend((title, heading, words) for heading, words in counts)
    return found[:_MAX_SECTION_WORD_COUNTS] or None


def _format_length_requirements(items: list[str] | None, *, before_writing: bool = False) -> str:
    if not items:
        return ""
    lines = "\n".join(f"- {item}" for item in items)
    note = (
        "以上是作者原话与来源（后说的为准）；先区分全篇目标、单章目标和本轮范围再开写。"
        "本轮未明确改变的历史要求仍适用；只写指定范围，逐章写到达标区间内，用实际工具字数核对；"
        "差得不多时一次改写到位，不逐字增删。"
        if before_writing else _LENGTH_REQUIREMENT_NOTE
    )
    return f"\n\n{LENGTH_REQUIREMENT_HEADER}\n{lines}\n{note}"


def _tool_result_failed(result: Any) -> bool:
    """TOOL_RESULT 事件的 result（MCP 文本载荷）是否是 status=error 的失败结果。"""
    if not isinstance(result, dict):
        return False
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        try:
            payload = json.loads(str(block.get("text") or ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("status") == "error":
            return True
    return False


def _extract_review_payload(agent_content: str) -> str:
    """
    Prefer reviewing the concrete draft payload.

    When the writer follows the "<file>...</file>" streaming protocol, extract file blocks.
    Otherwise, fall back to the full agent text.
    """
    raw = (agent_content or "").strip()
    if not raw:
        return ""

    if "<file" not in raw.lower():
        return raw

    # Normalize file marker variants (best-effort). We reuse the same normalization
    # logic as StreamProcessor so reviewer extraction is consistent with file writes.
    normalized = raw
    try:
        from agent.core.stream_processor import normalize_file_markers

        normalized = normalize_file_markers(raw)
    except Exception:
        normalized = raw

    start_tag = "<file>"
    end_tag = "</file>"
    if start_tag not in normalized or end_tag not in normalized:
        return raw

    blocks: list[str] = []
    cursor = 0
    while True:
        start = normalized.find(start_tag, cursor)
        if start == -1:
            break
        start += len(start_tag)
        end = normalized.find(end_tag, start)
        if end == -1:
            break
        block = normalized[start:end].strip()
        if block:
            blocks.append(block)
        cursor = end + len(end_tag)

    return "\n\n".join(blocks).strip() if blocks else raw


def _format_review_payload(text: str, *, max_chars: int = REVIEW_PAYLOAD_MAX_CHARS) -> str:
    """Trim extremely long draft content while keeping head+tail for reviewer context."""
    normalized = (text or "").strip()
    if not normalized:
        return ""
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized

    # 省略标记也计入预算；先以全文字数的位数预留上界，避免外层再截断拼接结果。
    hint_budget = len(f"\n\n...[中间省略 {count_words(normalized)} 字]...\n\n")
    if max_chars <= hint_budget:
        return "[正文未附，请读取文件]"[:max_chars]
    body_budget = max_chars - hint_budget
    head_chars = int(body_budget * 0.7)
    tail_chars = body_budget - head_chars
    head = normalized[:head_chars].rstrip()
    tail = normalized[-tail_chars:].lstrip() if tail_chars > 0 else ""
    omitted = len(normalized) - len(head) - len(tail)
    # 省略量按编辑器口径（count_words）报，不用字符数：审稿人会把这里的数字当字数。
    omitted_words = count_words(normalized[len(head) : len(normalized) - len(tail)]) if omitted > 0 else 0
    omitted_hint = f"\n\n...[中间省略 {omitted_words} 字]...\n\n" if omitted > 0 else "\n\n"
    return f"{head}{omitted_hint}{tail}".strip()


def _format_file_inventory(inventory: dict[str, list[dict[str, Any]]]) -> str:
    """格式化文件清单为可读字符串（handoff 时注入给下一个 agent）。

    分桶规则与 ContextAssembler._build_inventory_sections 保持一致，二者是同一份
    清单的两个渲染端，任何一端漏类型都会让 Agent「失明」：

    - 大纲：outline
    - 正文：CONTENT_FILE_TYPES（draft + script）合并输出——短剧项目的分集正文是
      script，历史实现只认 draft，交接后的 writer/quality_reviewer 看不到已写好的
      分集，于是从头重复创建。
    - 角色 / 设定：character / lore
    - 其他文件：document / snippet 以及任何未预期的新类型，统一进兜底桶，
      保证没有文件会从清单里凭空消失（这正是数据源改了、渲染端没改的历史顽疾）。

    条目带类型标注（正文桶与兜底桶各类型混排，不标注读不出是哪一类）。
    """
    type_names = {
        "outline": "大纲",
        "draft": "正文",
        "script": "剧本",
        "character": "角色",
        "lore": "设定",
        "document": "文档",
        "snippet": "片段",
    }

    def _render(files: list[dict[str, Any]], *, show_type: bool) -> list[str]:
        rendered: list[str] = []
        for f in files:
            if not isinstance(f, dict):
                continue
            title = f.get("title") or ""
            file_id = f.get("id") or ""
            entry = f"{title}(id={file_id})"
            if show_type:
                raw_type = str(f.get("file_type") or "").strip()
                if raw_type:
                    entry = f"{entry}[{type_names.get(raw_type, raw_type)}]"
            rendered.append(entry)
        return rendered

    def _bucket(file_type: str) -> list[dict[str, Any]]:
        rows = inventory.get(file_type) or []
        # 行字典里可能没带 file_type（旧数据源），用桶名兜底，保证类型标注不丢。
        return [
            {**row, "file_type": row.get("file_type") or file_type}
            for row in rows
            if isinstance(row, dict)
        ]

    known_types = {"outline", "character", "lore", *CONTENT_FILE_TYPES}

    content_rows: list[dict[str, Any]] = []
    for file_type in CONTENT_FILE_TYPES:
        content_rows.extend(_bucket(file_type))

    other_rows: list[dict[str, Any]] = []
    for file_type in inventory:
        if file_type in known_types:
            continue
        other_rows.extend(_bucket(file_type))

    sections: list[tuple[str, list[dict[str, Any]], bool]] = [
        ("大纲", _bucket("outline"), False),
        ("正文", content_rows, True),
        ("角色", _bucket("character"), False),
        ("设定", _bucket("lore"), False),
        ("其他文件", other_rows, True),
    ]

    parts: list[str] = []
    for label, rows, show_type in sections:
        items = _render(rows, show_type=show_type)
        if items:
            parts.append(f"{label}: {', '.join(items)}")

    return "\n".join(parts) if parts else ""


def _artifact_written(artifact: str, written: list[ArtifactEvidence]) -> bool:
    """A legacy label requires exact title identity, never a substring."""
    return bool(artifact.strip()) and any(artifact.strip() == item.title.strip() for item in written)


def _build_completion_task_payload(
    tasks: list[dict[str, Any]],
    written_artifacts: list[ArtifactEvidence],
    live_artifacts: list[ArtifactEvidence] | None = None,
) -> list[dict[str, Any]] | None:
    """Complete file tasks only from live, typed evidence written in this request."""
    written = [item for item in written_artifacts if isinstance(item, ArtifactEvidence)]
    if not written:
        return None
    updated_tasks: list[dict[str, Any]] = []
    has_updates = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        updated = dict(task)
        if updated.get("status") not in {"in_progress", "pending"}:
            updated_tasks.append(updated)
            continue
        try:
            validate_task_artifact_fields(updated)
        except ValueError:
            updated_tasks.append(updated)
            continue
        file_type = task_artifact_type(updated)
        if file_type is None:
            updated_tasks.append(updated)
            continue
        refs = set(updated.get("artifact_refs") or [])
        matching = [item for item in written if item.file_type == file_type and (not refs or item.id in refs)]
        if not matching:
            updated_tasks.append(updated)
            continue
        spec = task_evidence_spec(updated)
        label = str(updated.get("artifact") or "").strip()
        if spec is not None:
            prior_ids = set(updated.get("completed_artifact_refs") or [])
            live_prior = [
                item for item in live_artifacts or []
                if item.id in prior_ids and item.file_type == file_type and (not refs or item.id in refs)
            ]
            relevant = [
                item for item in matching + live_prior
                if artifact_units([item], spec.unit, file_type) & spec.numbers
            ]
            if not relevant:
                updated_tasks.append(updated)
                continue
            updated["completed_artifact_refs"] = sorted({item.id for item in relevant})
            covered = artifact_units(relevant, spec.unit, file_type) & spec.numbers
            missing = set(spec.numbers) - covered
            if not missing:
                updated["status"] = "done"
                updated.pop("progress", None)
                updated.pop("done_units", None)
            else:
                updated["status"] = "in_progress"
                updated["done_units"] = sorted(covered)
                updated["progress"] = f"已写：{format_units(covered, spec.unit)}；待写：{format_units(missing, spec.unit)}"
        elif refs:
            prior_ids = set(updated.get("completed_artifact_refs") or [])
            live_prior = [item for item in live_artifacts or [] if item.id in prior_ids and item.file_type == file_type and item.id in refs]
            covered_ids = {item.id for item in matching + live_prior}
            updated["completed_artifact_refs"] = sorted(covered_ids)
            if refs <= covered_ids:
                updated["status"] = "done"
        elif label and _artifact_written(label, matching):
            updated["status"] = "done"
            updated["completed_artifact_refs"] = sorted(item.id for item in matching if item.title.strip() == label)
        has_updates |= updated != task
        updated_tasks.append(updated)
    return updated_tasks if has_updates else None


def _existing_written_artifacts(file_ids: list[str]) -> list[ArtifactEvidence]:
    """Read nonempty live files scoped to this request's project."""
    if not file_ids:
        return []
    from sqlmodel import select

    from models import File
    with ToolContext.short_lived_session() as session:
        query = select(File).where(File.id.in_(file_ids), File.is_deleted.is_(False))
        project_id = ToolContext.get_project_id()
        if project_id:
            query = query.where(File.project_id == project_id)
        rows = session.exec(query).all()
        return [ArtifactEvidence(row.id, row.title, row.file_type, row.get_metadata()) for row in rows if row.content.strip()]


def _live_project_artifacts(project_id: str) -> list[ArtifactEvidence] | None:
    """Current typed file evidence for revalidating persisted progress."""
    from sqlmodel import select

    from models import File
    try:
        with ToolContext.short_lived_session() as session:
            rows = session.exec(select(File).where(File.project_id == project_id, File.is_deleted.is_(False))).all()
            return [ArtifactEvidence(row.id, row.title, row.file_type, row.get_metadata()) for row in rows if row.content.strip()]
    except Exception as exc:
        logger.warning("Unable to verify live task artifacts", extra={"error_type": type(exc).__name__})
        return None


async def _auto_finalize_task_board_on_completion(
    guard: RepeatReadGuard | None = None,
) -> list[StreamEvent]:
    """
    在 workflow 完成时按实际产物补一次 update_project(tasks=[...])。

    只有本请求确实写出对应文件的任务改为 done（规则见 _build_completion_task_payload；
    pending 任务只认编号或产出物对上的文件），什么都没写时不补。
    """
    session_id = ToolContext.get_session_id()
    if not session_id:
        return []

    user_id = ToolContext.get_user_id()
    project_id = ToolContext.get_project_id()

    try:
        written_ids = guard.written_file_ids() if guard is not None else []
        if not written_ids:
            return []

        from services.infra.task_board_service import task_board_service

        current_tasks = task_board_service.get_tasks(
            session_id,
            user_id=user_id,
            project_id=project_id,
        ) or []
        if not any(
            isinstance(task, dict) and task.get("status") in ("in_progress", "pending") for task in current_tasks
        ):
            return []
        written_artifacts = await asyncio.to_thread(_existing_written_artifacts, written_ids)
        live_artifacts: list[ArtifactEvidence] | None = None
        if project_id and any(
            isinstance(task, dict) and task.get("completed_artifact_refs")
            for task in current_tasks
        ):
            live_artifacts = await asyncio.to_thread(_live_project_artifacts, project_id)
        completion_tasks = _build_completion_task_payload(
            current_tasks, written_artifacts, live_artifacts
        )
        if not completion_tasks:
            return []

        tool_result = await update_project({"tasks": completion_tasks}, written_artifact_ids=written_ids)
        tool_use_id = "workflow_auto_completion"
        return [
            StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "id": tool_use_id,
                    "name": "update_project",
                    "status": "complete",
                    "input": {"tasks": completion_tasks},
                },
            ),
            StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": tool_use_id,
                    "name": "update_project",
                    "result": tool_result,
                },
            ),
        ]
    except Exception as e:
        log_with_context(
            logger,
            30,  # WARNING
            "Auto finalize task board failed",
            error=str(e),
            error_type=type(e).__name__,
            session_id=session_id,
            user_id=user_id,
            project_id=project_id,
        )
        return []


# =============================================================================
# Streaming Workflow Execution
# =============================================================================


async def _shape_agent_text(
    events: AsyncIterator[StreamEvent],
    shaper: AgentTextShaper,
) -> AsyncIterator[StreamEvent]:
    """一个 agent run 的事件流：TEXT 经 AgentTextShaper 整理后再交给图（P3-16）。

    非文字事件（思考过程除外）结束当前这段文字：先把攒着的文字发出去（紧接着是工具调用
    且攒着的是英文过程句时丢掉），再发这个事件。
    """
    async for event in events:
        if event.type == StreamEventType.TEXT:
            text = shaper.feed(str(event.data.get("text") or ""))
            if text:
                yield StreamEvent(type=StreamEventType.TEXT, data={**event.data, "text": text})
            continue
        if event.type != StreamEventType.THINKING:
            rest = shaper.end_segment(before_tool_call=event.type == StreamEventType.TOOL_USE)
            if rest:
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": rest})
        yield event
    rest = shaper.end_segment(before_tool_call=False)
    if rest:
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": rest})


async def run_writing_workflow_streaming(
    state: WritingState,
    thread_id: str | None = None,
    max_iterations: int = AGENT_COLLABORATION_MAX_ITERATIONS,
    auto_review_threshold: int = AGENT_AUTO_REVIEW_THRESHOLD_CHARS,
    get_steering_messages: Any | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    Execute the writing workflow with true streaming output and agent collaboration.

    Uses router to determine initial agent and workflow plan, then streams from agents.
    Supports both planned workflows and dynamic agent handoffs.

    Args:
        state: Initial workflow state
        thread_id: Optional thread ID for logging
        max_iterations: Maximum number of agent handoffs (default: AGENT_COLLABORATION_MAX_ITERATIONS from config/agent_runtime.py)
        auto_review_threshold: Character count threshold for auto-triggering quality reviewer
        get_steering_messages: Optional async callback to retrieve steering messages

    Yields:
        StreamEvent objects for real-time streaming
    """
    log_with_context(
        logger,
        20,  # INFO
        "Starting streaming writing workflow with collaboration",
        user_message_preview=state.get("user_message", "")[:50],
        thread_id=thread_id,
    )

    agent_names = {
        "planner": "大纲规划师",
        "hook_designer": "爽点设计师",
        "writer": "内容创作者",
        "quality_reviewer": "质量审稿人",
    }

    async def _drain_boundary_steering() -> list[dict[str, str]]:
        """Agent 边界消费 steering 队列。

        覆盖 run 内没有工具边界可消费（纯文本生成）、或落在最后一个 agent
        运行期间的消息；不消费则它们只会在流结束时被持久化，无法影响本次
        请求的生成。
        """
        if get_steering_messages is None:
            return []
        try:
            raw_msgs = await get_steering_messages()
        except Exception as exc:
            log_with_context(
                logger,
                40,  # ERROR
                "Failed to retrieve steering messages at agent boundary",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        drained: list[dict[str, str]] = []
        for msg in raw_msgs or []:
            if not isinstance(msg, dict):
                continue
            content = str(msg.get("content") or "")
            if content:
                drained.append({"id": str(msg.get("id") or ""), "content": content})
        return drained

    async def _absorb_boundary_steering(
        boundary_msgs: list[dict[str, str]],
    ) -> AsyncIterator[StreamEvent]:
        """把边界消费的 steering 追加进会话消息，并向前端发确认事件。"""
        if not boundary_msgs:
            return
        state["messages"] = list(state.get("messages") or []) + [
            {"role": "user", "content": msg["content"]} for msg in boundary_msgs
        ]
        from agent.core.events import steering_received_event

        for msg in boundary_msgs:
            yield steering_received_event(
                message_id=msg["id"],
                preview=msg["content"][:50],
            )

    length_requirement_inputs: tuple[str, ...] | None = None
    length_requirements: list[str] | None = None

    async def _current_length_requirements() -> list[str] | None:
        # Collect once before the first writer, reuse for review/rework. New author
        # steering invalidates the snapshot; the source helper keeps chronological quotes.
        nonlocal length_requirement_inputs, length_requirements
        author_inputs = tuple(ToolContext.get_author_messages())
        if author_inputs != length_requirement_inputs:
            length_requirement_inputs = author_inputs
            reads = await _bounded_review_reads({
                "length_requirements": lambda: find_length_requirements(
                    _collect_length_requirement_sources()
                ),
            })
            length_requirements = reads.get("length_requirements")
        return length_requirements

    iteration = 0
    current_agent_type: str | None = None
    handoff_context: str = ""
    workflow_agents: list[str] = []  # Planned agents to execute after initial
    # 只接续同一次原计划的正文交付；计划外跳转不能借残留计划重新加入累计。
    planned_delivery_active = True
    accumulated_content: str = ""  # Track content for auto-review threshold
    review_round: int = 0  # 跟踪 writer-quality_reviewer 循环次数
    previous_agent: str | None = None  # 跟踪上一个 agent
    # 当前 agent 是不是审稿人交回来返工的：返工稿不再被自动质检门送审。
    # 不能只看 previous_agent——空文件纠偏轮、steering 追加轮会把它改成当前 agent。
    rework_from_reviewer = False
    # 本轮交接包（todo / evidence 要渲染进下一个 agent 的交接信息）。
    incoming_handoff_packet: dict[str, Any] | None = None
    file_correction_attempts: int = 0  # 跟踪「创建了空文件但未写入正文」的纠正次数
    # 被空文件纠偏轮暂存的显式 handoff：纠偏分支会用 continue 跳过本轮末尾的
    # 交接决策，若不暂存，模型这一轮请求的目标 agent（例如 WRITER_PROMPT 强制
    # 要求的 quality_reviewer 送审）会连同 handoff_packet 一起被静默吞掉。
    deferred_handoff: dict[str, Any] | None = None
    # 纠偏/续写轮接续上一轮的正文和写入记录；计划内正文交付只接续正文
    # 写入记录。否则前一程已经写完正文、writer 只补几字时会绕过自动质检门。
    carried_agent_content: str = ""
    carried_writer_body_writes: list[BodyWrite] = []
    # writer 送审前写给作者的文字被图丢掉了（审稿人的回复会取代它）：审稿人交回 writer
    # 返修时据此告诉 writer，作者没看到它之前的说明。
    writer_chat_discarded = False
    # 被丢掉的 writer 说明以问作者的问题收尾时，那几句问题（返修 writer 也要原样问）。
    writer_superseded_question: str = ""
    # 被纠偏打断那一轮是否以向用户提问收尾：纠偏轮只补正文，它的收尾文本
    # （「已补齐」）不能把原 agent 还在等用户回答的问题冲掉。
    carried_ended_with_question: bool = False

    # 工具失败熔断器按请求共享：每个 agent run 都从 state 取同一个（见 runner），
    # writer → 审稿人 → writer 的往返不会让「同一调用连续失败」的计数归零。
    state["tool_failure_breaker"] = ToolFailureBreaker()
    # 重复读取守卫 + 本请求读写台账：同样按请求共享（见 runner / repeat_read_guard）。
    # 交接时把「已读过 / 已改过哪些文件」写进下一个 agent 的交接信息，避免从头再读。
    state["repeat_read_guard"] = RepeatReadGuard()
    # 请求级模型调用预算：service 可能已放入一个（以便请求结束时读计数写摘要），
    # 否则这里建一个；每个 agent run 都从 state 取同一个。
    if not isinstance(state.get("run_meter"), AgentRunMeter):
        state["run_meter"] = AgentRunMeter()

    # 只读请求拦下写交接时的提示卡片：拦下时只记录（每个请求只记第一次），
    # 等工作流收尾、紧挨着终止事件（WORKFLOW_COMPLETE / 澄清 / 无效交接 /
    # 轮数耗尽）或自然结束之前才发。WORKFLOW_STOPPED 在前端会结束流式状态
    # （重新启用发送、隐藏取消、关闭 steering），运行中途发出会让用户以为
    # 本轮已经结束，而后面的 steering 追加轮等其实还在跑。
    pending_read_only_notice: StreamEvent | None = None
    read_only_notice_recorded = False

    def _take_read_only_notice() -> StreamEvent | None:
        nonlocal pending_read_only_notice
        notice = pending_read_only_notice
        pending_read_only_notice = None
        return notice

    try:
        generation_mode = str(state.get("generation_mode") or "").strip().lower()
        if generation_mode not in {"fast", "quality"}:
            generation_mode = ""

        # Step 1: Run router to determine initial agent and workflow plan
        english = _author_is_english(state)
        if english:
            router_message = (
                "Fast mode: reading your request..."
                if generation_mode == "fast"
                else "Quality mode: planning the steps..."
                if generation_mode == "quality"
                else "Choosing how to handle this..."
            )
        else:
            router_message = (
                "快速模式：正在理解你的要求..."
                if generation_mode == "fast"
                else "高质量模式：正在规划工作流..."
                if generation_mode == "quality"
                else "Router 正在选择处理方式..."
            )
        yield StreamEvent(
            type=StreamEventType.ROUTER_THINKING,
            data={"message": router_message},
        )

        router_strategy = (os.getenv("AGENT_ROUTER_STRATEGY") or AGENT_ROUTER_STRATEGY).strip().lower()
        if router_strategy not in {"llm", "off"}:
            router_strategy = AGENT_ROUTER_STRATEGY

        enable_graph_auto_review = (
            os.getenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW")
            if os.getenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW") is not None
            else str(AGENT_ENABLE_GRAPH_AUTO_REVIEW)
        ).strip().lower() in {"1", "true", "yes", "y", "on"}

        # Per-request override (driven by frontend generation_mode UI).
        # 快速模式只关自动质检：路由照常跑，只读 / 范围 / 作品第一条消息这些判断都来自路由，
        # 关掉路由会让作者的提问也落到有写权限的 writer 上。质检和返修才是这档模式省下的成本。
        if generation_mode == "fast":
            router_strategy = "llm"
            enable_graph_auto_review = False
        elif generation_mode == "quality":
            router_strategy = "llm"
            enable_graph_auto_review = True
        # 作品的第一条消息：作者还没看过任何产出，最后读到的应是这一轮写了什么。送审返修后
        # 唯一的总结出自返修稿，只剩修改记录，所以这一轮不自动送审；作者要审稿时路由照常安排。
        first_message_round = bool(state.get("first_message_project_label"))
        if first_message_round:
            enable_graph_auto_review = False
        first_round_summary_rerun_used = False

        # 上一轮以工具调用轮数耗尽 / 无进展停止收尾，用户只回了一句「继续」：直接交给
        # 上一轮的 agent 走 quick 工作流，不再重新做多 agent 规划（规划会从头再读一遍）。
        resume_result = resume_route_after_exhaustion(state)
        # 「帮我优化一下」这类笼统要求：不走路由，writer 单独一轮先问清楚改哪里（P2-16）。
        # 放在「沿用上一轮路由」之前：上一轮按回复协议以选项收尾时，这句话也会被当成
        # 简短作答沿用路由，直接去改好几个文件。
        if resume_result is None and _should_clarify_first(state):
            resume_result = clarify_first_route()
            log_with_context(
                logger,
                20,  # INFO
                "Vague edit request; asking the author what to improve before editing",
            )
        # 上一轮以提问收尾、用户简短作答（「可以」「B」「主角叫陈默」）：沿用上一轮落库的
        # 路由，不让只看本条原话的 LLM 路由把回答重新分类。只替代 LLM 路由那一步，
        # 快速模式（路由关闭）照旧固定从 writer 开始。
        if resume_result is None and router_strategy == "llm":
            resume_result = inherit_routing_after_clarification(state)

        try:
            if resume_result is not None:
                router_result = resume_result
            elif router_strategy == "off":
                router_result = {
                    "current_agent": "writer",
                    "workflow_plan": "quick",
                    "workflow_agents": [],
                    "routing_metadata": {
                        "agent_type": "writer",
                        "workflow_type": "quick",
                        "reason": "router_off",
                        "confidence": 0.0,
                    },
                }
            else:
                router_result = await router_node(state)

            current_agent_type = get_next_node(router_result)
            workflow_agents = list(router_result.get("workflow_agents", []))
            # Drop any leading planned agents equal to the initial agent. A router
            # output where initial == workflow_agents[0] would otherwise pop itself
            # on the next boundary and trip the invalid-self-handoff stop AFTER
            # content was already produced; treat it as a normal single-agent start.
            while workflow_agents and workflow_agents[0] == current_agent_type:
                workflow_agents.pop(0)
            workflow_plan = router_result.get("workflow_plan", "quick")
            routing_metadata = router_result.get("routing_metadata", {})
            # 路由自己那次 LLM 调用的用量：随事件下发，由 stream_adapter 汇入
            # 整轮 usage 累加器，否则这部分 token 永远不进任何统计。
            routing_usage = router_result.get("routing_usage")
        except Exception as router_error:
            failure = router_error if isinstance(router_error, RouterFailure) else RouterFailure(
                "schema" if isinstance(router_error, ValueError | KeyError) else "provider_failure",
                error_info=classify_stream_exception(router_error),
            )
            log_with_context(
                logger,
                30,
                "Invalid routing stopped before agent execution",
                stage="routing",
                category=failure.category,
                has_usage=bool(failure.usage),
            )
            if failure.usage:
                # Internal-only usage carrier; no fabricated successful decision
                # and no author-facing reply or extra terminal frame.
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_END,
                    data={"usage": failure.usage, "stop_reason": "router_failure"},
                )
            error_info = failure.error_info
            if error_info.code == ErrorCode.AGENT_RUN_FAILED:
                # This boundary precedes agents/tools: the retry UI can accurately
                # say the work is unchanged. Quota/upstream-specific codes stay intact.
                error_info = StreamErrorInfo(ErrorCode.AGENT_ROUTING_FAILED, True, True)
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=error_info.as_event_data(reason=f"router_{failure.category}"),
            )
            return

        # 用户范围：只读请求不交接给有写权限的 agent；范围约束注入每个 agent 的系统提示。
        read_only_request = (
            isinstance(routing_metadata, dict) and routing_metadata.get("read_only") is True
        )
        user_scope = (
            str(routing_metadata.get("scope") or "").strip()
            if isinstance(routing_metadata, dict)
            else ""
        )
        state["resume_from_output_truncation"] = (
            routing_metadata.get("previous_stop_reason") == "output_truncated"
            and routing_metadata.get("reason") == "resume_after_exhaustion"
        )
        scope_directive = _build_scope_directive(routing_metadata)
        if scope_directive:
            state["scope_directive"] = scope_directive
        if read_only_request:
            # 工具层强制：写文件工具的调用一律拒绝执行（见 tools_adapter），不只靠系统提示约束。
            state["read_only"] = True
        clarify_first_request = (
            isinstance(routing_metadata, dict) and routing_metadata.get("clarify_first") is True
        )
        if clarify_first_request:
            # 笼统要求先问清楚：同样在工具层拒绝写文件（拒绝说明不同，见 tools_adapter）。
            state["clarify_first"] = True
        # 作者写中文时，调用工具前那句英文过程说明不发给作者（AgentTextShaper）。
        drop_english_narration = text_contains_cjk(
            state.get("router_message") or state.get("user_message")
        )
        # 本请求是否已经为「规划只贴在对话里」补写过一次大纲文件（只补一次）。
        chat_plan_corrected = False
        # 规划师以提问收尾、但作者已经要正文时，计划交接给 writer 时附上的说明。

        yield StreamEvent(
            type=StreamEventType.ROUTER_DECIDED,
            data={
                "initial_agent": current_agent_type,
                "workflow_plan": workflow_plan,
                "workflow_agents": workflow_agents.copy(),
                "routing_metadata": routing_metadata,
                "routing_usage": routing_usage,
            },
        )

        log_with_context(
            logger,
            20,
            "Router determined workflow",
            initial_agent=current_agent_type,
            workflow_plan=workflow_plan,
            workflow_agents=workflow_agents,
            router_strategy=router_strategy,
            enable_graph_auto_review=enable_graph_auto_review,
            read_only_request=read_only_request,
            user_scope=user_scope,
        )

        # Set when the loop exits via a terminal break (WORKFLOW_COMPLETE /
        # WORKFLOW_STOPPED / clarification / invalid handoff / tool-call
        # exhaustion). Used to suppress the collaboration ITERATION_EXHAUSTED
        # emit when the turn already ended on the final iteration, which would
        # otherwise produce two contradictory terminal events.
        terminated_via_break = False

        run_meter: AgentRunMeter = state["run_meter"]
        # 上一个正常结束的 agent run 完整的 SDK 输入（runner 写入）：同一 agent 紧接着自己再跑时
        # 从它接着来，请求只在末尾追加、整段命中前缀缓存，读过的文件不用重读。
        native_runs: dict[str, Any] = state.setdefault("native_run_holder", {})
        while current_agent_type and iteration < max_iterations:
            # 请求时间快用完：不再开新的 agent run（交接、自动送审、续写、纠偏、插话追加轮），
            # 这一轮停在已经完成的部分，作者回复「继续」接着做（不等墙钟时限把它从中间截断）。
            if iteration > 0 and run_meter.past_wrap_up:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Request near its wall-clock limit: not starting another agent run",
                    next_agent=current_agent_type,
                    elapsed_s=round(run_meter.elapsed_s(), 1),
                    iterations=iteration,
                )
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": WRAP_UP_TEXT_EN if english else WRAP_UP_TEXT},
                )
                terminated_via_break = True
                break
            iteration += 1
            ToolContext.set_current_agent(current_agent_type)
            continue_native_run = (
                previous_agent == current_agent_type and native_runs.get("agent_type") == current_agent_type
            )
            if not continue_native_run:
                # 换了 agent：上一程读到的新正文这一程看不到（工具结果不跨 agent 回放），
                # edit_file 的陈旧检查不再认它。同一 agent 接着跑时工具结果都还在，照常认。
                native_runs.clear()
                ToolContext.begin_agent_run()

            log_with_context(
                logger,
                20,
                f"Agent collaboration iteration {iteration}",
                agent_type=current_agent_type,
                remaining_workflow_agents=workflow_agents,
            )

            # Emit agent_selected event with iteration status
            yield StreamEvent(
                type=StreamEventType.AGENT_SELECTED,
                data={
                    "agent_type": current_agent_type,
                    "agent_name": agent_names.get(current_agent_type, current_agent_type),
                    "iteration": iteration,
                    "max_iterations": max_iterations,
                    "remaining": max_iterations - iteration,
                },
            )

            # 构建最后一轮提示（如果是最后一轮）
            is_last_iteration = iteration == max_iterations
            last_iteration_hint = ""
            if is_last_iteration:
                last_iteration_hint = (
                    f"\n\n[重要提示] 这是最后一轮协作（第 {iteration}/{max_iterations} 轮），"
                    "请直接完成当前任务并输出最终结果，不要交接给其他 Agent。"
                )

            # If there's handoff context, add it to the state
            if handoff_context:
                modified_state = dict(state)

                # 检测 writer-quality_reviewer 循环
                is_reviewer = current_agent_type == "quality_reviewer"
                is_from_writer = previous_agent == "writer"
                if is_reviewer and is_from_writer:
                    review_round += 1
                    log_with_context(
                        logger, 20, "Writer-quality_reviewer cycle detected",
                        review_round=review_round,
                    )

                # 交接包里的待办与依据：handoff_to_agent 的 todo / evidence 只在交接包里，
                # 不渲染出来下一个 agent 就看不到审稿人列的具体修改项。
                packet_items_text = _format_handoff_packet_items(incoming_handoff_packet)

                # 本请求已读 / 已改的文件（只有标题与 id）：交接后 SDK 的输入只回放文字，
                # 工具结果不进下一个 agent 的历史，不告诉它就会从头再读一遍。
                work_log_text = ""
                read_guard = _guard_from_state(state)
                if read_guard is not None:
                    work_summary = read_guard.handoff_summary()
                    if work_summary:
                        work_log_text = f"\n\n[本请求的读写记录]: {work_summary}"
                # 被「作者手改保护」拒绝的删除 / 整份重写只出现在上一个 agent 的工具结果里，不跨
                # agent 回放：不说出来，审稿人会把「这个文件没动」当成缺陷要求返工（10-11 U094）。
                # 只列拒绝后本请求里始终没写成的文件，也只说发生了什么。修改（edit_file）不再被拦。
                written_ids = set(read_guard.written_file_ids()) if read_guard is not None else set()
                refused_ids = [
                    file_id
                    for file_id in ToolContext.author_edit_refused_file_ids()
                    if file_id not in written_ids
                ]
                if refused_ids:
                    refused_labels = "、".join(
                        read_guard.file_label(file_id) if read_guard is not None else f"(id={file_id})"
                        for file_id in refused_ids
                    )
                    work_log_text += (
                        f"\n\n[本请求没有删除或重写的文件]: 对 {refused_labels} 的删除或整份重写被作者手改保护拒绝了"
                        "（作者手动改过它们），文件保持原样；不要因此要求返工。"
                        "删除请引导作者使用现有文件菜单；不要以再次聊天确认重试被保护的删除或同名覆写；"
                        "常规文本编辑仍按现有读写保护执行。"
                    )

                # 刷新文件清单
                inventory_text = ""
                try:
                    # 同步查库放进工作线程，并用短生命周期 session（查完即还
                    # 连接）：在事件循环上直接查会阻塞所有并发 SSE，且懒建的
                    # session 会以 idle in transaction 挂到整轮结束。
                    refreshed = await asyncio.to_thread(
                        ToolContext.refresh_file_inventory
                    )
                    if refreshed:
                        inventory_text = _format_file_inventory(refreshed)
                        if inventory_text:
                            inventory_text = f"\n\n[当前项目文件清单]:\n{inventory_text}"
                except Exception as e:
                    log_with_context(
                        logger, 30, "Failed to refresh file inventory", error=str(e)
                    )

                # 对 Reviewer 使用专门的消息格式，不传递原始用户请求
                # 避免 Reviewer 误以为自己需要创作
                if is_reviewer:
                    # 构建审查轮次提示：review_round=1 是第一次审查，不需要提示；
                    # 最后一轮（MAX_REVIEW_ROUNDS）告诉审稿人之后不会再自动返修。
                    # 通过标准只由审稿人提示词决定，这里不另给分数线。
                    round_hint = ""
                    if review_round >= MAX_REVIEW_ROUNDS:
                        round_hint = (
                            f"\n\n[提示] 这是第 {review_round} 轮审查，也是本次请求的最后一轮"
                            "（之后不会再自动返修）。请重点核对之前提出的问题是否已修复，"
                            "仍有问题时把剩余修改意见写清楚，交给用户决定。"
                        )

                    modified_state["user_message"] = (
                        f"{REVIEWER_TASK_MARKER}\n\n请审查上一个 Agent 完成的内容。\n\n"
                        f"交接信息: {handoff_context}{packet_items_text}{work_log_text}"
                        f"{inventory_text}{round_hint}{last_iteration_hint}"
                    )
                else:
                    # The original user request is already replayed as the first user turn
                    # in history; re-embedding it on every handoff duplicates it and lets
                    # prior handoff contexts pile up across iterations. Pass only the fresh
                    # handoff context as this turn's user message.
                    # 审稿人那次 run 的任务消息（含待审查的旧稿）只写给审稿人：交给其他
                    # agent 前从历史里去掉。下一个 agent 跑完后会把这份历史写回
                    # state["messages"]，之后的 agent 也不会再看到它。
                    modified_state["messages"] = _drop_reviewer_task_messages(
                        state.get("messages")
                    )
                    superseded_note = (
                        f"\n\n{WRITER_REWORK_AFTER_SUPERSEDED_NOTE}"
                        if current_agent_type == "writer"
                        and previous_agent == "quality_reviewer"
                        and writer_chat_discarded
                        else ""
                    )
                    if superseded_note and writer_superseded_question:
                        superseded_note += (
                            f"\n\n{WRITER_QUESTION_HEADER}\n{writer_superseded_question}\n"
                            f"{_WRITER_QUESTION_FOR_REWORK_NOTE}"
                        )
                    rework_text = ""
                    if current_agent_type == "writer" and previous_agent == "quality_reviewer":
                        # 审稿人那份 [待审查内容] 已从历史里去掉，返修 writer 看不到正文，只能再读一遍。
                        # 按当前落库版本重新附上同一份送审片段：逐字引用它做 edit_file 的 old/anchor（#275）。
                        rework_guard = _guard_from_state(state)
                        rework_ids = rework_guard.written_file_ids() if rework_guard else []
                        rework_payload = (
                            await _review_payload_text(rework_ids, focus=handoff_context) if rework_ids else None
                        )
                        if rework_payload:
                            rework_text = f"\n\n{REWORK_CURRENT_TEXT_HEADER}\n{rework_payload}"
                    modified_state["user_message"] = (
                        f"[来自上一个Agent的交接信息]: "
                        f"{handoff_context}{packet_items_text}{work_log_text}{inventory_text}"
                        f"{rework_text}{superseded_note}{last_iteration_hint}"
                    )
            else:
                # 即使没有 handoff_context，也需要注入最后一轮提示
                if last_iteration_hint:
                    modified_state = dict(state)
                    original_msg = modified_state.get("user_message", "")
                    modified_state["user_message"] = f"{original_msg}{last_iteration_hint}"
                else:
                    modified_state = state

            # Stream from the current agent
            next_agent: str | None = None
            agent_content: str = ""  # Track this agent's output
            # 最后一次工具调用之后的文本：agent 收尾时对用户说的话，用于识别
            # 「以向用户提问结束」（此时不能替用户做决定继续自动交接）。
            agent_tail_text: str = ""
            # 交接调用前 writer 对作者说的话。一次回复里的多个工具调用先全部发出、再出结果，tail 在
            # 每个工具事件处清空，所以按「这一批工具调用之前」记，而不是按交接调用那一刻。
            prose_before_handoff = ""
            prose_before_tool_turn = ""
            tool_turn_open = False
            handoff_packet: dict[str, Any] | None = None
            explicit_handoff_event_data: dict[str, Any] | None = None
            clarification_stopped = False
            tool_call_exhausted = False
            invalid_handoff_stopped = False
            # 本 run 写进正文类文件（draft / script）的内容，自动质检门只看它
            # （body_writes.py）。只认成功的写入：写工具全部失败（例如数据库被锁）时
            # 不送审，否则审稿人会把 writer 叫回来重试同一个失败的写入。
            body_tracker = BodyWriteTracker()
            # 这一 run 写完正文以后、可能还会送审：之后给作者的文字先扣在适配器里，
            # 真的送审时丢掉（审稿人或返修后的 writer 写唯一一份总结），不送审时原样放出。
            # 审稿只可能来自自动质检门或计划好的 quality_reviewer：两者都没有时不扣。
            reviewer_planned_next = bool(workflow_agents) and workflow_agents[0] == "quality_reviewer"
            # 快照保留到 writer 为止的可写计划前缀；交接决策会弹出/裁剪原列表。
            planned_body_targets = (
                workflow_agents[: workflow_agents.index("writer") + 1]
                if "writer" in workflow_agents else []
            )
            planned_body_delivery = (
                planned_delivery_active
                and current_agent_type != "writer"
                and _agent_can_write_files(current_agent_type)
                and bool(planned_body_targets)
                and all(_agent_can_write_files(agent) for agent in planned_body_targets)
                and routing_metadata.get("write_content") is True
                and enable_graph_auto_review
            )
            chat_hold_eligible = (
                (current_agent_type == "writer" or planned_body_delivery)
                and generation_mode != "fast"
                and not read_only_request
                and not rework_from_reviewer
                and review_round < MAX_REVIEW_ROUNDS
                and (enable_graph_auto_review or reviewer_planned_next)
            )
            chat_hold_started = False
            # 正文可能已由计划内的上游写完：后续角色只读稿或小修，也要扣住
            # 将被审稿取代的总结，不依赖它再次调用写工具才开始扣留。
            if (
                chat_hold_eligible
                and measured_body_words(carried_writer_body_writes) >= auto_review_threshold
            ):
                chat_hold_started = True
                yield StreamEvent(type=StreamEventType.CHAT_HOLD, data={"action": CHAT_HOLD_BEGIN})
            # 范围约束按这次要跑的 agent 生成：规划类角色专用的那句不给 writer / 审稿人。
            agent_scope_directive = _build_scope_directive(
                routing_metadata, agent_type=current_agent_type
            )
            if agent_scope_directive:
                modified_state["scope_directive"] = agent_scope_directive
            else:
                modified_state.pop("scope_directive", None)

            if current_agent_type == "writer":
                modified_state["length_requirements_directive"] = _format_length_requirements(
                    await _current_length_requirements(), before_writing=True
                )
            else:
                modified_state.pop("length_requirements_directive", None)

            agent_message_started = False
            mid_run_steering_seen = False
            agent_run_errored = False
            # 本次 run 里的 create_file 调用与其中失败的：runner 给每次调用都发 TOOL_RESULT，
            # 没有失败的就是成功的。run 停在写完的 <file> 上时据此确认刚建过文件。
            run_create_call_ids: set[str] = set()
            run_failed_call_ids: set[str] = set()

            text_shaper = AgentTextShaper(
                previous_text=accumulated_content,
                drop_english_narration=drop_english_narration,
            )
            modified_state["continue_native_run"] = continue_native_run
            async for event in _shape_agent_text(
                run_streaming_agent(
                    modified_state, current_agent_type, get_steering_messages=get_steering_messages
                ),
                text_shaper,
            ):
                if event.type == StreamEventType.MESSAGE_START:
                    agent_message_started = True
                elif event.type == StreamEventType.ERROR:
                    agent_run_errored = True
                elif (
                    getattr(event.type, "value", "") == "steering_received"
                    and agent_message_started
                ):
                    # MESSAGE_START 之前的 steering 已注入本次 run 的输入；
                    # 之后的是 runner 在工具边界消费、本次 run 无法生效的干预。
                    mid_run_steering_seen = True

                # Track text content for auto-review threshold
                if event.type == StreamEventType.TEXT:
                    text = event.data.get("text", "")
                    agent_content += text
                    accumulated_content += text
                    agent_tail_text += text
                    tool_turn_open = False
                elif event.type in (StreamEventType.TOOL_USE, StreamEventType.TOOL_RESULT):
                    if event.type == StreamEventType.TOOL_USE:
                        if not tool_turn_open:
                            prose_before_tool_turn = agent_tail_text
                            tool_turn_open = True
                        if event.data.get("name") == "handoff_to_agent":
                            prose_before_handoff = prose_before_tool_turn
                    else:
                        tool_turn_open = False
                    agent_tail_text = ""

                if (
                    event.type == StreamEventType.TOOL_USE
                    and event.data.get("status") == "complete"
                ):
                    tool_name = str(event.data.get("name") or "").strip()
                    call_id = str(event.data.get("id") or "")
                    if tool_name == "create_file":
                        run_create_call_ids.add(call_id)
                elif event.type == StreamEventType.TOOL_RESULT and _tool_result_failed(
                    event.data.get("result")
                ):
                    run_failed_call_ids.add(str(event.data.get("tool_use_id") or ""))

                # Check for handoff event
                if event.type == StreamEventType.HANDOFF:
                    next_agent = event.data.get("target_agent")
                    if next_agent == current_agent_type:
                        invalid_handoff_stopped = True
                        if (notice := _take_read_only_notice()) is not None:
                            yield notice
                        yield StreamEvent(
                            type=StreamEventType.WORKFLOW_STOPPED,
                            data={
                                "reason": "invalid_handoff",
                                "agent_type": current_agent_type,
                                "message": (
                                    f"无效交接：{current_agent_type} 不能交接给自己。"
                                    "请直接完成当前任务或交接给其他 Agent。"
                                ),
                                "target_agent": next_agent,
                            },
                        )
                        continue

                    handoff_context = event.data.get("context", "")
                    handoff_packet = event.data.get("handoff_packet")
                    if not isinstance(handoff_packet, dict):
                        handoff_packet = {
                            "target_agent": next_agent or "",
                            "reason": event.data.get("reason", ""),
                            "context": handoff_context,
                            "completed": [],
                            "todo": [],
                            "evidence": [],
                        }
                    reason = event.data.get("reason", "")

                    log_with_context(
                        logger,
                        20,
                        "Agent requested handoff",
                        from_agent=current_agent_type,
                        to_agent=next_agent,
                        reason=reason,
                    )

                    explicit_handoff_event_data = {
                        "target_agent": next_agent,
                        "reason": reason,
                        "context": handoff_context,
                        "handoff_packet": handoff_packet,
                    }
                elif (
                    event.type == StreamEventType.WORKFLOW_STOPPED
                    and event.data.get("reason") == "clarification_needed"
                ):
                    clarification_stopped = True
                    if (notice := _take_read_only_notice()) is not None:
                        yield notice
                    yield event
                elif (
                    event.type == StreamEventType.ITERATION_EXHAUSTED
                    and event.data.get("layer") == "tool_call"
                ):
                    tool_call_exhausted = True
                    if (notice := _take_read_only_notice()) is not None:
                        yield notice
                    yield event
                else:
                    if event.type == StreamEventType.ERROR and (
                        notice := _take_read_only_notice()
                    ) is not None:
                        # error 帧在前端同样结束流，提示卡片要赶在它前面。
                        yield notice
                    yield event

                if (current_agent_type == "writer" or planned_body_delivery) and event.type in (
                    StreamEventType.TOOL_USE,
                    StreamEventType.TOOL_RESULT,
                ):
                    body_write = body_tracker.observe(event, text_length=len(agent_content))
                    if (
                        body_write is not None
                        and chat_hold_eligible
                        and not chat_hold_started
                        and (
                            reviewer_planned_next
                            # 计划里的后续角色将接手同一范围；是否丢掉这份说明仍等到
                            # 真正交接时判断，提问或交接未执行时适配器原样释放。
                            or planned_body_delivery
                            # create_file 的正文还没写出来，字数要等 <file> 块，可能送审。
                            or body_write.text_offset is not None
                            # edit_file / write_chapter 的字数已知：到了送审阈值才可能送审，
                            # 只改几个字时说明照常流式显示。
                            or measured_body_words([*carried_writer_body_writes, *body_tracker.writes])
                            >= auto_review_threshold
                        )
                    ):
                        # 正文写好了：之后的文字（总结、选项）在送审时会被审稿人的回复取代。
                        chat_hold_started = True
                        yield StreamEvent(
                            type=StreamEventType.CHAT_HOLD,
                            data={"action": CHAT_HOLD_BEGIN},
                        )

            writer_body_writes = body_tracker.finish(agent_content)
            run_create_succeeded = bool(run_create_call_ids - run_failed_call_ids)
            ended_with_question = ends_with_question_to_user(agent_tail_text)

            # Carry forward conversation evolution from this agent turn so that
            # downstream agents can see full assistant/tool history.
            updated_messages = modified_state.get("messages")
            if isinstance(updated_messages, list):
                state["messages"] = updated_messages

            # 接续纠偏/续写或计划内交付的正文记录，让自动质检门看到完整的一轮
            # 写作；整段文字只在纠偏/续写时接续，不拼接上游角色的聊天。
            if carried_agent_content or carried_writer_body_writes:
                agent_content = f"{carried_agent_content}{agent_content}"
                writer_body_writes = [*carried_writer_body_writes, *writer_body_writes]
                carried_agent_content = ""
                carried_writer_body_writes = []
            if carried_ended_with_question:
                ended_with_question = True
                carried_ended_with_question = False

            # Corrective feedback for an abandoned file write.
            #
            # create_file makes an EMPTY file and sets a pending-empty-file guard
            # that is cleared only when the model completes the <file>…</file>
            # streaming write. If the guard is still set at this agent boundary,
            # the model created a file but never wrote its body — it narrated
            # instead, or dropped the closing </file>. Rather than ending the turn
            # with an empty file (the StreamProcessor guard already prevents that
            # narration from being persisted as the file's content), re-run the
            # writer and explicitly tell it to finish the file. Bounded by
            # MAX_FILE_CORRECTION_ATTEMPTS to avoid loops.
            #
            # 触发前必须落库核验（_probe_pending_file_body）：标记只表示"没走
            # <file>…</file> 流式写入"，模型改用 edit_file(op=append) 写完正文时
            # 标记依然留着，此时按"正文仍为空"重跑会让正文被追加两遍。
            #
            # 纠偏配额（attempts / iteration）**不能**参与本分支的进入条件。
            # 配额耗尽时必须显式回滚空产物或报告未完成；绝不能只清除标记后继续，
            # 否则文件树会永久留下一个没有任何失败证据的空文件。
            if (
                not clarification_stopped
                and not invalid_handoff_stopped
                and not tool_call_exhausted
                and ToolContext.has_pending_empty_file()
            ):
                # 标记现在是集合：同一轮里可能有多个空文件在等补写（例如模型连建
                # 三集只写完最后一集）。只救 get_pending_empty_file() 返回的"最近
                # 一个"会让先建的那几份永远停在空正文，因此这里取全量逐个核验。
                pendings = ToolContext.get_pending_empty_files()
                unfinished: list[dict[str, str]] = []
                probed_states: list[str] = []
                for entry in pendings:
                    entry_file_id = str(entry.get("file_id") or entry.get("id") or "")
                    entry_title = str(entry.get("title") or "未命名")
                    entry_state = await asyncio.to_thread(
                        _probe_pending_file_body, entry_file_id
                    )
                    probed_states.append(entry_state)
                    if entry_state in (_PENDING_BODY_WRITTEN, _PENDING_BODY_GONE):
                        ToolContext.clear_pending_empty_file(entry_file_id)
                        continue
                    unfinished.append({"file_id": entry_file_id, "title": entry_title})

                # 兼容原有单文件日志/提示语：取第一份未完成的作为主对象。
                pending_title = unfinished[0]["title"] if unfinished else (
                    str(pendings[0].get("title") or "未命名") if pendings else "未命名"
                )
                pending_file_id = unfinished[0]["file_id"] if unfinished else (
                    str(pendings[0].get("file_id") or "") if pendings else ""
                )
                body_state = probed_states[0] if probed_states else _PENDING_BODY_UNVERIFIABLE

                # 只读请求（用户明确说了别改文件）不安排补写：写工具调用本就会被拒绝，
                # 万一仍出现空文件，直接走下面的回滚分支，而不是让 agent 去写正文。
                can_schedule_correction = (
                    not read_only_request
                    and iteration < max_iterations
                    and file_correction_attempts < MAX_FILE_CORRECTION_ATTEMPTS
                )
                if not unfinished:
                    # 正文已经写入（或文件已被删除）：对应标记已逐一清除。
                    log_with_context(
                        logger,
                        20,  # INFO
                        "Pending empty-file guard cleared without correction",
                        file_id=pending_file_id,
                        title=pending_title,
                        body_state=body_state,
                        pending_count=len(pendings),
                    )
                elif not can_schedule_correction:
                    # 配额用尽或已是最后一轮：回滚能确认的空产物。无法确认/
                    # 回滚的条目继续保留守卫，并向客户端报告明确的未完成状态。
                    rolled_back = await asyncio.to_thread(
                        _rollback_unfinished_empty_files, unfinished
                    )
                    for entry in unfinished:
                        if entry["file_id"] in rolled_back:
                            ToolContext.clear_pending_empty_file(entry["file_id"])
                    unresolved = [
                        entry
                        for entry in unfinished
                        if entry["file_id"] not in rolled_back
                    ]
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Empty-file correction exhausted",
                        file_id=pending_file_id,
                        title=pending_title,
                        attempts=file_correction_attempts,
                        iteration=iteration,
                        body_state=body_state,
                        unfinished_count=len(unfinished),
                        rolled_back_count=len(rolled_back),
                        unresolved_count=len(unresolved),
                    )
                    if unresolved:
                        yield StreamEvent(
                            type=StreamEventType.ITERATION_EXHAUSTED,
                            data={
                                "layer": "empty_file_correction",
                                "iterations_used": file_correction_attempts,
                                "max_iterations": MAX_FILE_CORRECTION_ATTEMPTS,
                                "reason": (
                                    "文件正文补写未完成，且无法安全回滚空文件。"
                                    "请继续对话完成这些文件。"
                                ),
                                "unfinished_files": unresolved,
                                "last_agent": current_agent_type,
                            },
                        )
                else:
                    file_correction_attempts += 1
                    # 纠偏轮由「建了空文件的那个 agent」自己补写：空文件是它建的
                    # （它必然有写权限），交给 writer 会让 writer 去补 planner 的大纲/
                    # 人设——只要大纲的请求因此凭空多出一轮 writer，还会顺带触发自动
                    # 质检；计划序列里排着的 writer 也会在纠偏后撞上自交接被判无效。
                    correction_agent = (
                        current_agent_type
                        if _agent_can_write_files(current_agent_type)
                        else "writer"
                    )
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Empty file detected after agent turn; re-running the creating agent to complete it",
                        correction_agent=correction_agent,
                        file_id=pending_file_id,
                        title=pending_title,
                        attempt=file_correction_attempts,
                        body_state=body_state,
                        unfinished_count=len(unfinished),
                    )
                    id_hint = f"(id={pending_file_id})" if pending_file_id else ""
                    # 暂存本轮的显式 handoff：纠偏轮的 continue 会跳过本轮末尾的
                    # 交接决策，不暂存就等于把模型请求的送审/交接静默丢弃。
                    if next_agent:
                        deferred_handoff = {
                            "next_agent": next_agent,
                            "event_data": explicit_handoff_event_data,
                            "packet": handoff_packet,
                            "context": handoff_context,
                        }
                    # 把本轮写作产出接续给纠偏轮，供纠偏轮结束后的质检门判定使用。
                    carried_agent_content = agent_content
                    carried_writer_body_writes = writer_body_writes
                    carried_ended_with_question = ended_with_question
                    handoff_context = (
                        f"[系统提醒] 你创建的文件《{pending_title}》{id_hint} 正文仍为空——"
                        "上一轮没有用 <file>…</file> 完成流式写入（很可能漏了结尾的 </file>）。"
                        "请立即调用 edit_file（id="
                        f"{pending_file_id or '<该文件id>'}，op=append）把完整正文写入该文件；"
                        "不要重复创建文件，也不要只在对话里复述正文。"
                        "若该文件已有部分正文，只补齐缺失的部分，不要重复写入已有段落。"
                    )
                    if len(unfinished) > 1:
                        # 还有别的空文件同样在等补写，必须一次性全部点名，
                        # 否则模型只补第一份，其余的下一轮就没人再提醒了。
                        others = "；".join(
                            f"《{item['title']}》(id={item['file_id'] or '未知'})"
                            for item in unfinished[1:]
                        )
                        handoff_context += (
                            f"\n[系统提醒] 另有 {len(unfinished) - 1} 个文件同样正文为空，"
                            f"请在同一轮内一并补齐：{others}。"
                        )
                    previous_agent = current_agent_type
                    current_agent_type = correction_agent
                    incoming_handoff_packet = None
                    continue

            # 规划师把作者要的分章 / 分集规划只贴在对话里、没写进文件（P2-13：「前十章大纲」
            # 只留在聊天里，大纲目录里找不到）：同一个规划师补一轮，把这份规划写进大纲文件。
            # 只补一次；只读 / 先问清楚的请求、已经停下的轮次、显式交接都不补。
            if (
                current_agent_type == "planner"
                and not chat_plan_corrected
                and not next_agent
                and not clarification_stopped
                and not invalid_handoff_stopped
                and not tool_call_exhausted
                and not agent_run_errored
                and not read_only_request
                and not clarify_first_request
                and iteration < max_iterations
                # 作者要的是一份规划（「前十章大纲」）；「先聊聊前十章怎么安排」这类讨论不补写。
                and "outline" in routing_metadata.get("requested_file_types", [])
            ):
                plan_units = chat_only_plan_units(agent_tail_text)
                if plan_units:
                    plan_guard = _guard_from_state(state)
                    written_ids = plan_guard.written_file_ids() if plan_guard else []
                    try:
                        outline_contents = await asyncio.to_thread(
                            _written_outline_contents, written_ids
                        )
                    except Exception as e:
                        log_with_context(
                            logger, 30, "Failed to read outline files written this request", error=str(e)
                        )
                        outline_contents = None
                    if outline_contents is not None and not outline_covers_units(
                        outline_contents, plan_units
                    ):
                        chat_plan_corrected = True
                        log_with_context(
                            logger,
                            20,  # INFO
                            "Planner left a chapter plan only in chat; re-running it to save the plan to an outline file",
                            plan_units=len(plan_units),
                        )
                        # 补写轮只把规划落进文件，它的收尾（「已写进《前十章大纲》」）不能冲掉
                        # 规划师原本在等作者回答的问题。
                        carried_ended_with_question = ended_with_question
                        handoff_context = chat_plan_correction_context(plan_units)
                        previous_agent = current_agent_type
                        incoming_handoff_packet = None
                        continue

            # 本轮能不能为同一个 agent 再跑一轮（续写 / 更正）：已经停下的轮次、显式交接、
            # 只读或先问清楚的请求、最后一轮都不行。
            run_ended_normally = (
                not next_agent
                and deferred_handoff is None
                and not clarification_stopped
                and not invalid_handoff_stopped
                and not tool_call_exhausted
                and not agent_run_errored
            )
            can_rerun_same_agent = (
                run_ended_normally
                and _agent_can_write_files(current_agent_type)
                and not read_only_request
                and not clarify_first_request
                and iteration < max_iterations
            )

            # 写完 </file> 就结束了 run（I08）：流式写文件协议把正文放在回复文字里，
            # openai-agents 把不带工具调用的回复当作最终输出，模型写完一个文件没在同一次
            # 回复里接着调用 create_file 时，run 就在这里停了——计划里没写的文件、给作者的
            # 总结都还没写，这不是模型决定收尾。同一个 agent 接着跑一轮，带上已保存的文件、
            # 作者的范围和任务板上没完成的任务：还有没写的接着写，写完了就给作者总结。
            # 每一轮都必须真的新建并写完一个文件才会再续，受 max_iterations 约束；放在计划交接 /
            # 自动质检之前，规划师写完再交给 writer、writer 写完再送审。
            if (
                can_rerun_same_agent
                and run_create_succeeded
                and not ToolContext.has_pending_empty_file()
                and ends_on_file_block(agent_tail_text)
            ):
                continuation_guard = _guard_from_state(state)
                written_titles = (
                    list(continuation_guard.files_written.values()) if continuation_guard else []
                )
                handoff_context = _file_stream_continuation_context(
                    written_titles, _open_board_tasks(), user_scope
                )
                if mid_run_steering_seen:
                    handoff_context += "\n" + _steering_followup_context(
                        current_agent_type, read_only=read_only_request
                    )
                log_with_context(
                    logger,
                    20,  # INFO
                    "Agent run ended on a closed <file> block; continuing the same agent",
                    agent_type=current_agent_type,
                    iteration=iteration,
                    written_files=len(written_titles),
                )
                # 续写轮是这一轮的延续：正文与写工具信号接续过去，自动质检门按整轮判断。
                carried_agent_content = agent_content
                carried_writer_body_writes = writer_body_writes
                previous_agent = current_agent_type
                incoming_handoff_packet = None
                continue

            # Structured clarification stop is canonical and must block planned/auto handoff.
            if clarification_stopped:
                terminated_via_break = True
                break
            # Invalid explicit handoff should stop collaboration to prevent self-loop.
            if invalid_handoff_stopped:
                terminated_via_break = True
                break
            # Tool-call exhaustion should stop workflow; never continue with planned/auto handoff.
            if tool_call_exhausted:
                # 轮数耗尽时不再安排补写轮（本轮已经停了），但 create_file 留下的空文件
                # 不能就这么留在文件树里：落库核验后回滚确认为空的，回滚不了的明确告诉用户。
                if ToolContext.has_pending_empty_file():
                    async for notice_event in _roll_back_empty_files_after_exhaustion(
                        current_agent_type, english
                    ):
                        yield notice_event
                terminated_via_break = True
                break

            # 恢复被纠偏轮暂存的显式 handoff。本轮若自己产生了新的显式 handoff，
            # 以新的为准（并丢弃旧的，避免交接意图堆积）。
            if deferred_handoff is not None:
                if next_agent:
                    deferred_handoff = None
                elif deferred_handoff.get("next_agent") == current_agent_type:
                    # 防御：纠偏轮恰好就是交接目标时，目标 agent 已经跑过，再交接
                    # 一次就是自交接，丢弃并留痕。纠偏轮现在由建空文件的 agent 自己
                    # 跑，而自交接在流内就被判无效、不会被暂存，正常不会走到这里。
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Deferred handoff target already ran as the correction agent; dropping",
                        target_agent=deferred_handoff.get("next_agent"),
                        agent_type=current_agent_type,
                    )
                    deferred_handoff = None
                else:
                    next_agent = deferred_handoff.get("next_agent")
                    explicit_handoff_event_data = deferred_handoff.get("event_data")
                    handoff_packet = deferred_handoff.get("packet")
                    handoff_context = str(deferred_handoff.get("context") or "")
                    log_with_context(
                        logger,
                        20,  # INFO
                        "Restored handoff deferred by empty-file correction",
                        target_agent=next_agent,
                        agent_type=current_agent_type,
                    )
                    deferred_handoff = None

            # 只读请求（用户明确说了别改文件）：交接给有写权限的 agent 就等于
            # 替用户改文件，显式交接也一并拦下；交接给只读 agent（审稿人）不受影响。
            if read_only_request and next_agent and _agent_can_write_files(next_agent):
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Dropping handoff to a write-capable agent on a read-only request",
                    from_agent=current_agent_type,
                    to_agent=next_agent,
                )
                if not read_only_notice_recorded:
                    # 前端已经看到了 handoff_to_agent 的工具卡片，交接却没有发生：
                    # 用已有的 WORKFLOW_STOPPED（非澄清原因渲染为一条提示卡片、随消息
                    # 落库）告诉用户为什么没去改。这里只记录、不发：前端收到任何
                    # workflow_stopped 都会结束流式状态，所以推迟到收尾时发（见
                    # _take_read_only_notice 的调用点）；不 break，同一轮的
                    # steering 追加轮等后续逻辑照常。
                    read_only_notice_recorded = True
                    pending_read_only_notice = StreamEvent(
                        type=StreamEventType.WORKFLOW_STOPPED,
                        data={
                            "reason": READ_ONLY_HANDOFF_BLOCKED_REASON,
                            "agent_type": current_agent_type,
                            "message": (
                                f"你要求本轮只回答、不改文件，已跳过交接给 {next_agent} 去修改。"
                                "需要改动时，直接告诉我改哪里即可。"
                            ),
                            "target_agent": next_agent,
                        },
                    )
                next_agent = None
                explicit_handoff_event_data = None
                handoff_packet = None

            # 笼统要求先问清楚：这一轮只问、不交接（交给别的角色就会去改文件）。
            if clarify_first_request and next_agent:
                log_with_context(
                    logger,
                    20,  # INFO
                    "Clarify-first round: dropping handoff",
                    from_agent=current_agent_type,
                    to_agent=next_agent,
                )
                next_agent = None
                explicit_handoff_event_data = None
                handoff_packet = None

            # 快速模式 / 作品第一条消息：这一轮不送审，writer 自己交给审稿人时丢弃交接。
            # 交接前 writer 按协议只写一句进度、把要告诉作者的话放进交接 context；丢掉交接后
            # 作者只剩那一句进度，所以没写总结时让它再跑一轮、只给作者写总结（带上那些话），
            # 已经写了总结时把那些话直接补给作者。
            review_skipped_reason = (
                "first_message"
                if first_message_round
                else "fast_mode"
                if generation_mode == "fast"
                else None
            )
            if (
                review_skipped_reason is not None
                and current_agent_type == "writer"
                and next_agent == "quality_reviewer"
            ):
                author_notes = _author_notes_from_handoff(handoff_context)
                # 作者在生成中途发来的话由下面的 steering 追加轮处理（它也会给作者写总结），
                # 只写总结的一轮会把它丢掉。
                # 快速模式已在指令里要求 writer 自己写总结；它仍按送审协议交接时，交接前那句
                # 话就是送审前的进度。第一条消息沿用原规则：写了话就不重跑。
                prose = prose_before_handoff.strip()
                wrote_no_summary = (
                    len(prose) < PROGRESS_ONLY_MAX_CHARS
                    if review_skipped_reason == "fast_mode"
                    else not prose
                )
                needs_summary = (
                    not first_round_summary_rerun_used
                    and wrote_no_summary
                    and not mid_run_steering_seen
                    and iteration < max_iterations
                )
                log_with_context(
                    logger,
                    20,  # INFO
                    "Review skipped this round: dropping writer handoff to quality_reviewer",
                    reason=review_skipped_reason,
                    rerun_for_summary=needs_summary,
                    has_author_notes=bool(author_notes),
                )
                next_agent = None
                explicit_handoff_event_data = None
                handoff_packet = None
                if needs_summary:
                    first_round_summary_rerun_used = True
                    handoff_context = (
                        FIRST_ROUND_SUMMARY_CONTEXT
                        if review_skipped_reason == "first_message"
                        else FAST_MODE_SUMMARY_CONTEXT
                    )
                    if author_notes:
                        handoff_context += f"\n\n{AUTHOR_NOTES_RELAY_HEADER}\n{author_notes}"
                    carried_agent_content = agent_content
                    carried_writer_body_writes = writer_body_writes
                    previous_agent = current_agent_type
                    incoming_handoff_packet = None
                    continue
                if author_notes:
                    yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"\n\n{author_notes}"})

            # 审查轮数上限：writer 不能再显式送审；审稿人要求返修时不再交回 writer，
            # 而是正常收尾并把审稿意见展示给用户。
            review_limit_notes: str | None = None
            if next_agent and review_round >= MAX_REVIEW_ROUNDS:
                if current_agent_type == "quality_reviewer" and _agent_can_write_files(next_agent):
                    # 审稿意见要直接显示给作者：去掉 id、字段名和流程用语。
                    review_limit_notes = author_facing_text(
                        _review_notes_from_packet(handoff_packet, handoff_context)
                    )
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Review round limit reached; finishing instead of handing back for rework",
                        review_round=review_round,
                        max_review_rounds=MAX_REVIEW_ROUNDS,
                        to_agent=next_agent,
                    )
                    next_agent = None
                    explicit_handoff_event_data = None
                    handoff_packet = None
                elif current_agent_type == "writer" and next_agent == "quality_reviewer":
                    log_with_context(
                        logger,
                        20,  # INFO
                        "Review round limit reached; dropping writer handoff to reviewer",
                        review_round=review_round,
                        max_review_rounds=MAX_REVIEW_ROUNDS,
                    )
                    next_agent = None
                    explicit_handoff_event_data = None
                    handoff_packet = None

            if review_limit_notes is not None:
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": _review_limit_text(review_limit_notes, english)},
                )
                for auto_task_update_event in await _auto_finalize_task_board_on_completion(
                    _guard_from_state(state)
                ):
                    yield auto_task_update_event
                if (notice := _take_read_only_notice()) is not None:
                    yield notice
                yield StreamEvent(
                    type=StreamEventType.WORKFLOW_COMPLETE,
                    data={
                        "reason": "review_round_limit",
                        "agent_type": current_agent_type,
                        "message": "已达到审查轮数上限，审稿意见已列出",
                        "review_rounds": review_round,
                        "max_review_rounds": MAX_REVIEW_ROUNDS,
                        "review_notes": review_limit_notes,
                    },
                )
                terminated_via_break = True
                break

            # Waiting is controlled by the structured clarification event handled above.

            # Determine upcoming handoff after stop checks.
            # Explicit handoff requests still take precedence over completion checks.
            has_pending_handoff = False
            has_explicit_handoff = False
            has_planned_handoff = False
            pending_next_agent: str | None = None
            pending_handoff_event_data: dict[str, Any] | None = None

            if next_agent:
                # Agent explicitly requested handoff
                has_pending_handoff = True
                has_explicit_handoff = True
                pending_next_agent = next_agent
                pending_handoff_event_data = explicit_handoff_event_data
                # An explicit handoff JUMPS to the target: any earlier-planned
                # stages are skipped, not deferred to run after it. Truncate up
                # to AND including the target so leftover earlier-stage agents
                # (e.g. hook_designer when planner hands straight to writer)
                # don't later run out of order via the planned-handoff branch.
                if next_agent in workflow_agents:
                    idx = workflow_agents.index(next_agent)
                    del workflow_agents[: idx + 1]
                if handoff_packet and handoff_packet.get("context"):
                    handoff_context = str(handoff_packet.get("context", ""))
            elif workflow_agents:
                # Follow planned workflow
                has_pending_handoff = True
                has_planned_handoff = True
                next_planned = workflow_agents.pop(0)
                if next_planned == current_agent_type:
                    if (notice := _take_read_only_notice()) is not None:
                        yield notice
                    yield StreamEvent(
                        type=StreamEventType.WORKFLOW_STOPPED,
                        data={
                            "reason": "invalid_handoff",
                            "agent_type": current_agent_type,
                            "message": (
                                f"无效自动交接：{current_agent_type} 不能交接给自己。"
                            ),
                            "target_agent": next_planned,
                        },
                    )
                    terminated_via_break = True
                    break
                handoff_context = f"按照工作流计划，从 {current_agent_type} 自动交接"
                planned_todo: list[str] = []
                if user_scope:
                    # 计划交接没有 agent 写的交接说明，必须把用户的范围带过去，
                    # 否则下游 writer 只看到「自动交接」，会按大纲把整本书往下写。
                    handoff_context += f"。用户要求的交付范围：{user_scope}，只完成该范围内的内容"
                    planned_todo.append(f"按用户要求的范围完成：{user_scope}")
                # 不重做、不扩大交付（P2-16：首轮规划师已经交付了大纲和前三集剧本，
                # 计划交接后 writer 又给三集追加了作者没要的「拍摄执行版」）。
                handoff_context += f"。{PLANNED_HANDOFF_SCOPE_NOTE}"
                planned_guard = _guard_from_state(state)
                handoff_packet = {
                    "target_agent": next_planned,
                    "reason": "工作流自动交接",
                    "context": handoff_context,
                    "completed": planned_guard.completed_items() if planned_guard else [],
                    "todo": planned_todo,
                    "evidence": [f"workflow_plan={workflow_plan}"],
                    "artifact_refs": planned_guard.written_file_ids() if planned_guard else [],
                }

                log_with_context(
                    logger,
                    20,
                    "Following planned workflow",
                    from_agent=current_agent_type,
                    to_agent=next_planned,
                )

                pending_handoff_event_data = {
                    "target_agent": next_planned,
                    "reason": "工作流自动交接",
                    "context": handoff_context,
                    "handoff_packet": handoff_packet,
                }

                pending_next_agent = next_planned
            elif (
                enable_graph_auto_review
                and not read_only_request
                and current_agent_type == "writer"
                # 审稿人交回来的返工稿不再自动送审（WRITER_PROMPT：返工后直接结束）。
                and not rework_from_reviewer
                and review_round < MAX_REVIEW_ROUNDS
                # 只看本轮写进正文类文件（draft / script）的字数，不看 writer 的聊天文字：
                # 改大纲、设定、角色卡，或者回答问题时说得再长也不送审（I03）。
                and body_words_written(writer_body_writes) >= auto_review_threshold
            ):
                # Auto-trigger quality_reviewer for long content
                has_pending_handoff = True
                log_with_context(
                    logger,
                    20,
                    "Auto-triggering quality_reviewer due to body words written",
                    body_words=body_words_written(writer_body_writes),
                    body_files=len(writer_body_writes),
                    threshold=auto_review_threshold,
                )

                # 不带数字：len() 是字符数，不是编辑器口径的字数，写进交接文案会被
                # 审稿人当成字数转述给作者。触发阈值仍按字符长度判断。
                handoff_event_context = "正文已写完，自动进入质量检查"
                handoff_context = handoff_event_context
                review_guard = _guard_from_state(state)
                handoff_packet = {
                    "target_agent": "quality_reviewer",
                    "reason": "自动质量门控",
                    "context": handoff_event_context,
                    "completed": review_guard.completed_items() if review_guard else [],
                    "todo": ["执行质量审查并返回问题清单"],
                    "evidence": [],
                    "artifact_refs": review_guard.written_file_ids() if review_guard else [],
                }

                pending_handoff_event_data = {
                    "target_agent": "quality_reviewer",
                    "reason": "自动质量门控",
                    "context": handoff_event_context,
                    "handoff_packet": handoff_packet,
                }

                pending_next_agent = "quality_reviewer"

            if (
                pending_next_agent == "quality_reviewer"
                and current_agent_type == "writer"
                and (agent_content.strip() or writer_body_writes)
            ):
                event_context = ""
                if pending_handoff_event_data is not None:
                    event_context = str(pending_handoff_event_data.get("context") or "").strip()
                base_context = (handoff_context or event_context).strip()
                # The original user request is already the first turn in history (same as
                # the non-reviewer branch); re-embedding it here would duplicate it.
                # Point the reviewer at the draft via the file inventory (already appended
                # as inventory_text which includes file ids) instead of inlining the full
                # draft body, which can reach ~9k chars and pile up across review rounds.
                # 送审内容优先取本请求写过的正文落库后的样子（引号已规范），
                # 读不到时原输出只作片段，不能冒充最新落库正文；明确补读当前文件。
                payload_guard = _guard_from_state(state)
                written_ids = payload_guard.written_file_ids() if payload_guard else []
                persisted_payload = (
                    await _review_payload_text(written_ids) if payload_guard else None
                )
                review_payload = _format_review_payload(
                    persisted_payload or _extract_review_payload(agent_content)
                )
                source_note = ""
                if written_ids and not persisted_payload:
                    source_note = (
                        "[当前落库正文未读取]\n"
                        "下面仅为创作输出片段，不代表当前落库正文。"
                        "先用 query_files 按这些文件 ID 读取当前正文后再判断事实或总结："
                        f"{', '.join(written_ids)}。文件标题和类型见当前项目文件清单。\n\n"
                    )
                handoff_context = (
                    f"{base_context}\n\n"
                    f"{source_note}"
                    f"[待审查内容]\n{review_payload}"
                ).strip()

                # 字数和篇幅要求给审稿人确定的数（I05）：writer 在 <file> 正文后面同一次回复里
                # 写的「约N字」是还没有工具结果时的估计；作者早先说过、写进项目设定的篇幅
                # 要求，审稿人原来只看本轮原话时看不到。
                review_reads = await _bounded_review_reads(
                    {
                        "body_word_counts": functools.partial(_collect_body_word_counts, written_ids),
                        "section_word_counts": functools.partial(_collect_section_word_counts, written_ids),
                    }
                )
                body_counts = review_reads["body_word_counts"]
                if not body_counts:
                    # 落库读取超时：按本 run 写入记下的最新全文字数（建文件后又扩写过的取改后字数）。
                    body_counts = latest_file_word_counts(writer_body_writes)
                handoff_context += _format_body_word_counts(body_counts, review_reads["section_word_counts"])
                handoff_context += _format_length_requirements(await _current_length_requirements())
                if chat_hold_started:
                    handoff_context += f"\n\n{WRITER_CHAT_SUPERSEDED_NOTE}"
                    # writer 最后在问作者：图手里有这几句，原样交给审稿人，不靠它从历史里转述。
                    question = trailing_question_text(agent_tail_text) if ended_with_question else ""
                    if question:
                        handoff_context += (
                            f"\n\n{WRITER_QUESTION_HEADER}\n{question}\n{_WRITER_QUESTION_FOR_REVIEWER_NOTE}"
                        )
                if body_counts and pending_handoff_event_data is not None and isinstance(
                    pending_handoff_event_data.get("handoff_packet"), dict
                ):
                    counted_packet = dict(pending_handoff_event_data["handoff_packet"])
                    prior_evidence = counted_packet.get("evidence")
                    counted_packet["evidence"] = [
                        *(prior_evidence if isinstance(prior_evidence, list) else []),
                        *(f"《{title}》编辑器字数 {words}" for title, words in body_counts),
                    ]
                    pending_handoff_event_data = {
                        **pending_handoff_event_data,
                        "handoff_packet": counted_packet,
                    }
                    handoff_packet = counted_packet

                # 程序检测证据（逐字复读、数字事实对照）：只在 writer 写完交给审稿人时做，
                # 用户手动审查 / review_only 不做；读不到或超时就照常送审。
                review_guard_for_checks = _guard_from_state(state)
                if (
                    workflow_plan != "review_only"
                    and iteration < max_iterations
                    and review_guard_for_checks is not None
                ):
                    auto_checks = await _review_auto_checks(review_guard_for_checks.written_file_ids())
                    if auto_checks is not None:
                        repeat_items, fact_items = auto_checks
                        handoff_context += _format_review_auto_checks(repeat_items, fact_items)
                        if pending_handoff_event_data is not None and isinstance(
                            pending_handoff_event_data.get("handoff_packet"), dict
                        ):
                            checked_packet = dict(pending_handoff_event_data["handoff_packet"])
                            prior_evidence = checked_packet.get("evidence")
                            checked_packet["evidence"] = [
                                *(prior_evidence if isinstance(prior_evidence, list) else []),
                                f"auto_checks=repeat:{len(repeat_items)},facts:{len(fact_items)}",
                            ]
                            pending_handoff_event_data = {
                                **pending_handoff_event_data,
                                "handoff_packet": checked_packet,
                            }
                            handoff_packet = checked_packet

            # 本轮 run 期间到达的 steering：SDK run 中途无法注入，只有当没有
            # 已计划的下一个 agent（否则该 agent 的起始注入/会话历史会带上
            # 这些消息）时，追加一轮让干预在本次请求内生效。消费过的消息不会
            # 重复触发；追加轮同样计入 max_iterations，不会无限循环。
            #
            # 追加轮必须沿用当前 agent，不能硬编码成 writer：review_only 这类
            # 工作流的 workflow_agents 为空，一旦硬编码，用户只要在审查过程中发
            # 一条引导，就会被凭空升级成一轮持有 create_file/edit_file/delete_file
            # 的 writer，并被系统提示引导去"调整或补充"——用户明确说了先别改。
            if (
                not has_pending_handoff
                and not agent_run_errored
                and iteration < max_iterations
            ):
                boundary_steering = await _drain_boundary_steering()
                if mid_run_steering_seen or boundary_steering:
                    async for steering_ack in _absorb_boundary_steering(boundary_steering):
                        yield steering_ack
                    log_with_context(
                        logger,
                        20,
                        "Steering arrived during agent run; scheduling follow-up round",
                        agent_type=current_agent_type,
                        mid_run_consumed=mid_run_steering_seen,
                        boundary_consumed=len(boundary_steering),
                    )
                    handoff_context = _steering_followup_context(
                        current_agent_type, read_only=read_only_request
                    )
                    previous_agent = current_agent_type
                    incoming_handoff_packet = None
                    continue

            # 检测任务完成。
            # - 显式 handoff 优先级最高（继续协作，不触发 stop/complete）。
            # - 计划交接（作者范围内还没跑的阶段）不被完成标记打断。
            # - 自动送审仅在显式完成标记时允许打断。
            # - 无待交接时允许启发式完成。
            if agent_content:
                evaluation = evaluate_agent_output(agent_content, current_agent_type)
                complete_result = detect_task_complete(agent_content, current_agent_type)

                explicit_complete = complete_result.reason == "explicit_complete_marker"

                if has_explicit_handoff:
                    can_stop_for_completion = False
                elif has_planned_handoff:
                    # 计划里还有作者要的阶段（已按 write_content / read_only 裁剪）：一个阶段写了
                    # 完成标记只说明这一段做完了，不能替整条计划宣布完成（#269）。
                    can_stop_for_completion = False
                    if complete_result.is_complete:
                        log_with_context(
                            logger,
                            20,
                            "Completion marker kept the remaining planned stages",
                            agent_type=current_agent_type,
                            next_planned=pending_next_agent,
                        )
                else:
                    can_stop_for_completion = complete_result.is_complete and (
                        explicit_complete or not has_pending_handoff
                    )

                if can_stop_for_completion:
                    log_with_context(
                        logger,
                        20,
                        "Agent marked task as complete, stopping workflow",
                        agent_type=current_agent_type,
                        confidence=complete_result.confidence,
                    )

                    # Best-effort: 自动补发一次 update_project(tasks) 把 in_progress 任务收尾。
                    auto_task_update_events = await _auto_finalize_task_board_on_completion(
                        _guard_from_state(state)
                    )
                    for auto_task_update_event in auto_task_update_events:
                        yield auto_task_update_event

                    if (notice := _take_read_only_notice()) is not None:
                        yield notice

                    # 发送工作流完成事件
                    yield StreamEvent(
                        type=StreamEventType.WORKFLOW_COMPLETE,
                        data={
                            "reason": "task_complete",
                            "agent_type": current_agent_type,
                            "message": "任务已完成",
                            "confidence": complete_result.confidence,
                            "evaluation": {
                                "complete_score": evaluation.complete_score,
                                "clarification_score": evaluation.clarification_score,
                                "consistency_score": evaluation.consistency_score,
                                "decision_reason": evaluation.reason,
                            },
                        },
                    )

                    # 终止工作流
                    terminated_via_break = True
                    break

            # The target agent only runs if another collaboration iteration remains.
            # Emitting a HANDOFF that can never be acted on leaves the frontend with a
            # dangling handoff immediately followed by ITERATION_EXHAUSTED (the promised
            # agent never speaks). Suppress it on the final iteration and let the loop
            # fall through to the exhaustion branch instead.
            will_run_next_agent = bool(pending_next_agent) and iteration < max_iterations

            if (
                planned_body_delivery
                and chat_hold_eligible
                and will_run_next_agent
                and pending_next_agent in planned_body_targets
                and body_words_written(writer_body_writes) > 0
            ):
                carried_writer_body_writes = writer_body_writes
                if (
                    chat_hold_started
                    and not ended_with_question
                    and body_words_written(writer_body_writes) >= auto_review_threshold
                ):
                    yield StreamEvent(
                        type=StreamEventType.CHAT_HOLD,
                        data={"action": CHAT_HOLD_DISCARD, "reason": "superseded_by_planned_writer"},
                    )

            if (
                chat_hold_started
                and will_run_next_agent
                and current_agent_type == "writer"
                and pending_next_agent == "quality_reviewer"
            ):
                # 送审：writer 写完正文后给作者的文字丢掉，审稿人（或返修后的 writer）
                # 写作者看到的唯一一份总结，字数用编辑器的数（I03 / I05）。
                writer_chat_discarded = True
                writer_superseded_question = (
                    trailing_question_text(agent_tail_text) if ended_with_question else ""
                )
                yield StreamEvent(
                    type=StreamEventType.CHAT_HOLD,
                    data={"action": CHAT_HOLD_DISCARD, "reason": "superseded_by_review"},
                )

            if pending_handoff_event_data is not None and will_run_next_agent:
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data=_author_facing_handoff_event_data(pending_handoff_event_data, english),
                )

            # 保存当前 agent 类型，用于下一轮循环检测
            previous_agent = current_agent_type

            # Apply next agent decision
            planned_delivery_active = (
                planned_body_delivery
                and will_run_next_agent
                and pending_next_agent in planned_body_targets
            )
            current_agent_type = pending_next_agent if will_run_next_agent else None
            rework_from_reviewer = (
                previous_agent == "quality_reviewer" and current_agent_type is not None
            )
            incoming_handoff_packet = (
                pending_handoff_event_data.get("handoff_packet")
                if will_run_next_agent and isinstance(pending_handoff_event_data, dict)
                else None
            )

        ToolContext.set_current_agent(None)

        # 自然结束（没有 WORKFLOW_COMPLETE）或协作轮数耗尽：只读提示卡片在这里、
        # 轮数耗尽卡片之前发出。上面的终止分支已经发过的，这里取到的是 None。
        if (notice := _take_read_only_notice()) is not None:
            yield notice

        if iteration >= max_iterations and not terminated_via_break:
            log_with_context(
                logger,
                30,  # WARNING
                "Max collaboration iterations reached",
                iterations=iteration,
            )
            # Notify frontend that collaboration iterations are exhausted
            yield StreamEvent(
                type=StreamEventType.ITERATION_EXHAUSTED,
                data={
                    "layer": "collaboration",
                    "iterations_used": iteration,
                    "max_iterations": max_iterations,
                    "reason": (
                        f"已达到 Agent 协作轮数上限（{max_iterations} 轮）。"
                        f"这是 Agent 之间交接的次数限制，与单个 Agent 的工具调用次数（{AGENT_TOOL_CALL_MAX_ITERATIONS} 次）独立。"
                        "任务可能未完全完成，您可以继续对话让 AI 完成剩余工作。"
                    ),
                    "last_agent": previous_agent,
                },
            )

    except Exception as e:
        error_info = classify_stream_exception(e)
        log_stream_exception(logger, "Streaming workflow error", e, error_info)
        if (notice := _take_read_only_notice()) is not None:
            yield notice
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=error_info.as_event_data(error_type=type(e).__name__),
        )

    log_with_context(
        logger,
        20,  # INFO
        "Streaming writing workflow completed",
        total_iterations=iteration,
        total_content_length=len(accumulated_content),
    )
