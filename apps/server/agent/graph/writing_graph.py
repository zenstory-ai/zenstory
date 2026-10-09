"""
Writing workflow for zenstory.

Provides streaming multi-agent orchestration with router, planner, writer, and quality reviewer.
"""

import asyncio
import contextlib
import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any

from agent.constants import CONTENT_FILE_TYPES
from agent.core.events import READ_ONLY_HANDOFF_BLOCKED_REASON
from agent.core.run_meter import AgentRunMeter
from agent.core.stream_errors import classify_stream_exception, log_stream_exception
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.nodes import (
    detect_task_complete,
    ends_with_question_to_user,
    evaluate_agent_output,
    run_streaming_agent,
)
from agent.graph.review_evidence import find_numeric_fact_pairs, find_repetition_evidence
from agent.graph.router import (
    get_next_node,
    inherit_routing_after_clarification,
    resume_route_after_exhaustion,
    router_node,
)
from agent.graph.state import WritingState
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.tools.mcp_tools import ToolContext, update_project
from config.agent_runtime import (
    AGENT_AUTO_REVIEW_THRESHOLD_CHARS,
    AGENT_COLLABORATION_MAX_ITERATIONS,
    AGENT_ENABLE_GRAPH_AUTO_REVIEW,
    AGENT_ROUTER_STRATEGY,
    AGENT_TOOL_CALL_MAX_ITERATIONS,
)
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


def _build_scope_directive(routing_metadata: dict[str, Any] | None) -> str:
    """把路由对用户范围的判断（read_only / write_content / scope）转成系统提示约束。

    交接包和 handoff 文本只在交接时出现，而本轮第一个 agent 也需要知道范围，
    因此统一走系统提示，由 nodes.run_streaming_agent 追加到每个 agent 的提示末尾。
    """
    if not isinstance(routing_metadata, dict):
        return ""
    parts: list[str] = []
    if routing_metadata.get("read_only") is True:
        parts.append(SCOPE_DIRECTIVE_READ_ONLY)
    elif routing_metadata.get("write_content") is False:
        parts.append(SCOPE_DIRECTIVE_NO_CONTENT)
    scope = str(routing_metadata.get("scope") or "").strip()
    if scope:
        parts.append(
            f"用户要求的交付范围：{scope}。只交付这个范围内的内容，"
            "不要额外续写后续章节或创建范围外的文件；范围内的工作完成后即结束。"
        )
    return "\n".join(parts)


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


def _unfinished_file_notice(rolled_back: list[str], unresolved: list[str]) -> str:
    """工具调用轮数耗尽时留下空文件：回滚 / 无法回滚的说明（随 assistant 正文落库）。"""
    lines: list[str] = []
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
) -> AsyncIterator[StreamEvent]:
    """工具调用轮数耗尽且还有 pending 空文件：核验 → 回滚空文件 → 文字说明。

    与空文件纠偏分支「配额用尽」时同一套核验与回滚，只是不再安排补写轮。
    """
    unfinished: list[dict[str, str]] = []
    for entry in ToolContext.get_pending_empty_files():
        file_id = str(entry.get("file_id") or entry.get("id") or "")
        title = str(entry.get("title") or "未命名")
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
    notice = _unfinished_file_notice(rolled_back_titles, unresolved_titles)
    if notice:
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": notice})


def _review_limit_text(notes: str) -> str:
    """达到审查轮数上限、不再自动返修时追加给用户的说明（随 assistant 正文落库）。"""
    body = f"\n{notes}" if notes else ""
    return (
        f"\n\n——\n已完成 {MAX_REVIEW_ROUNDS} 轮质量审查（本次请求的上限），不再自动返修。"
        f"审稿人仍建议修改：{body}\n"
        "如需按这些意见继续修改，请直接回复「按审稿意见修改」。"
    )


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
        effective_order, seq_num = build_sequence_sort_key(row.order, title=row.title, file_type=row.file_type)
        return (effective_order, seq_num, row.created_at, row.id)

    with ToolContext.short_lived_session() as session:
        rows = session.exec(
            select(
                File.id, File.title, File.file_type, File.parent_id, File.order, File.created_at, File.content
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
                select(File.id, File.title, File.file_type, File.order, File.created_at).where(
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


def _collect_review_payload_text(file_ids: list[str]) -> str | None:
    """读本请求写过的正文（draft / script）落库后的内容，按写入顺序拼成送审内容。

    流式落库和 edit_file 会把半角引号规范成稿件风格，库里这份才是作者看到的正文；
    模型原始输出里那些已经修好的半角引号不能再送审。没有可用正文时返回 None。
    """
    project_id = ToolContext.get_project_id()
    if not project_id or not file_ids:
        return None

    from sqlmodel import select

    from models import File

    with ToolContext.short_lived_session() as session:
        rows = session.exec(
            select(File.id, File.content).where(
                File.project_id == project_id,
                File.id.in_(file_ids),
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
            )
        ).all()
    contents = {row.id: (row.content or "").strip() for row in rows}
    return "\n\n".join(contents[file_id] for file_id in file_ids if contents.get(file_id)) or None


async def _review_payload_text(file_ids: list[str]) -> str | None:
    """在送审预算内读落库正文；读取失败或超时只记 WARNING，返回 None（退回模型输出）。"""
    if not file_ids:
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_collect_review_payload_text, list(file_ids)),
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


def _format_review_payload(text: str, *, max_chars: int = 9000) -> str:
    """Trim extremely long draft content while keeping head+tail for reviewer context."""
    normalized = (text or "").strip()
    if not normalized:
        return ""
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized

    head_chars = int(max_chars * 0.7)
    tail_chars = max_chars - head_chars
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


def _build_completion_task_payload(tasks: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """将任务板中的 in_progress 任务标记为 done。"""
    updated_tasks: list[dict[str, Any]] = []
    has_updates = False

    for task in tasks:
        if not isinstance(task, dict):
            continue

        normalized_task = dict(task)
        if normalized_task.get("status") == "in_progress":
            normalized_task["status"] = "done"
            has_updates = True
        updated_tasks.append(normalized_task)

    return updated_tasks if has_updates else None


async def _auto_finalize_task_board_on_completion() -> list[StreamEvent]:
    """
    在 workflow 完成时自动补一次 update_project(tasks=[...])。

    仅将 in_progress 任务改为 done，避免误改 pending 任务。
    """
    session_id = ToolContext.get_session_id()
    if not session_id:
        return []

    user_id = ToolContext.get_user_id()
    project_id = ToolContext.get_project_id()

    try:
        from services.infra.task_board_service import task_board_service

        current_tasks = task_board_service.get_tasks(
            session_id,
            user_id=user_id,
            project_id=project_id,
        ) or []
        completion_tasks = _build_completion_task_payload(current_tasks)
        if not completion_tasks:
            return []

        tool_result = await update_project({"tasks": completion_tasks})
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

    iteration = 0
    current_agent_type: str | None = None
    handoff_context: str = ""
    workflow_agents: list[str] = []  # Planned agents to execute after initial
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
    # 纠偏轮是被打断那一轮 writer 的延续：把上一轮的正文与写工具信号接续过来，
    # 否则自动质检门（按本轮字数 + 是否动过写工具判定）会因为纠偏轮本身只补了
    # 一小段而永远不触发。
    carried_agent_content: str = ""
    carried_writer_used_write_tools: bool = False
    carried_writer_emitted_file_markers: bool = False
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
        yield StreamEvent(
            type=StreamEventType.ROUTER_THINKING,
            data={
                "message": (
                    "快速模式：直接进入生成..."
                    if generation_mode == "fast"
                    else "高质量模式：正在规划工作流..."
                    if generation_mode == "quality"
                    else "Router 正在选择处理方式..."
                ),
            },
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
        if generation_mode == "fast":
            router_strategy = "off"
            enable_graph_auto_review = False
        elif generation_mode == "quality":
            router_strategy = "llm"
            enable_graph_auto_review = True

        # 上一轮以工具调用轮数耗尽 / 无进展停止收尾，用户只回了一句「继续」：直接交给
        # 上一轮的 agent 走 quick 工作流，不再重新做多 agent 规划（规划会从头再读一遍）。
        resume_result = resume_route_after_exhaustion(state)
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
                        "reason": "generation_mode_fast" if generation_mode == "fast" else "router_off",
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
        except (ValueError, KeyError) as validation_error:
            # 可恢复的验证错误 - 使用 fallback
            log_with_context(
                logger,
                30,  # WARNING
                "Router validation failed, using fallback",
                error=str(validation_error),
                error_type=type(validation_error).__name__,
            )
            current_agent_type = "writer"
            workflow_agents = []
            workflow_plan = "quick"
            routing_metadata = {
                "agent_type": "writer",
                "workflow_type": "quick",
                "reason": "router_validation_fallback",
                "confidence": 0.0,
            }
            # fallback 路径没有成功的路由调用，用量为空
            routing_usage = None
        except Exception as router_error:
            # 其他错误 - 记录并使用 fallback
            log_with_context(
                logger,
                40,  # ERROR
                "Router failed, using fallback",
                error=str(router_error),
                error_type=type(router_error).__name__,
            )
            current_agent_type = "writer"
            workflow_agents = []
            workflow_plan = "quick"
            routing_metadata = {
                "agent_type": "writer",
                "workflow_type": "quick",
                "reason": "router_exception_fallback",
                "confidence": 0.0,
            }
            # 路由调用抛异常：可能已经烧了 token，但拿不到 usage，只能记空
            routing_usage = None

        # 用户范围：只读请求不交接给有写权限的 agent；范围约束注入每个 agent 的系统提示。
        read_only_request = (
            isinstance(routing_metadata, dict) and routing_metadata.get("read_only") is True
        )
        user_scope = (
            str(routing_metadata.get("scope") or "").strip()
            if isinstance(routing_metadata, dict)
            else ""
        )
        scope_directive = _build_scope_directive(routing_metadata)
        if scope_directive:
            state["scope_directive"] = scope_directive
        if read_only_request:
            # 工具层强制：写文件工具的调用一律拒绝执行（见 tools_adapter），不只靠系统提示约束。
            state["read_only"] = True

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

        while current_agent_type and iteration < max_iterations:
            iteration += 1
            ToolContext.set_current_agent(current_agent_type)

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
                        f"[质量检查任务]\n\n请审查上一个 Agent 完成的内容。\n\n"
                        f"交接信息: {handoff_context}{packet_items_text}{work_log_text}"
                        f"{inventory_text}{round_hint}{last_iteration_hint}"
                    )
                else:
                    # The original user request is already replayed as the first user turn
                    # in history; re-embedding it on every handoff duplicates it and lets
                    # prior handoff contexts pile up across iterations. Pass only the fresh
                    # handoff context as this turn's user message.
                    modified_state["user_message"] = (
                        f"[来自上一个Agent的交接信息]: "
                        f"{handoff_context}{packet_items_text}{work_log_text}{inventory_text}"
                        f"{last_iteration_hint}"
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
            handoff_packet: dict[str, Any] | None = None
            explicit_handoff_event_data: dict[str, Any] | None = None
            clarification_stopped = False
            tool_call_exhausted = False
            invalid_handoff_stopped = False
            writer_used_write_tools = False
            # writer 本轮写工具调用数 / 其中失败数：全部失败（例如数据库被锁）时
            # 不算「动过写工具」，否则自动质检会把一篇没落库的稿子送审，审稿人再
            # 把 writer 叫回来重试同一个失败的写入。
            writer_write_calls = 0
            writer_write_failures = 0
            writer_write_call_ids: set[str] = set()
            writer_emitted_file_markers = False
            agent_message_started = False
            mid_run_steering_seen = False
            agent_run_errored = False

            async for event in run_streaming_agent(
                modified_state, current_agent_type, get_steering_messages=get_steering_messages
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
                elif event.type in (StreamEventType.TOOL_USE, StreamEventType.TOOL_RESULT):
                    agent_tail_text = ""

                if (
                    current_agent_type == "writer"
                    and event.type == StreamEventType.TOOL_USE
                    and event.data.get("status") == "complete"
                ):
                    tool_name = str(event.data.get("name") or "").strip()
                    if tool_name in {"create_file", "edit_file"}:
                        writer_write_calls += 1
                        writer_write_call_ids.add(str(event.data.get("id") or ""))
                elif (
                    current_agent_type == "writer"
                    and event.type == StreamEventType.TOOL_RESULT
                    and str(event.data.get("tool_use_id") or "") in writer_write_call_ids
                    and _tool_result_failed(event.data.get("result"))
                ):
                    writer_write_failures += 1

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

            writer_used_write_tools = writer_write_calls > writer_write_failures
            ended_with_question = ends_with_question_to_user(agent_tail_text)

            # Carry forward conversation evolution from this agent turn so that
            # downstream agents can see full assistant/tool history.
            updated_messages = modified_state.get("messages")
            if isinstance(updated_messages, list):
                state["messages"] = updated_messages

            # 接续上一轮被纠偏打断的 writer 产出（正文 + 写工具信号），让后面的
            # 自动质检门、待审查内容提取看到完整的一轮写作，而不是只看到补写片段。
            if (
                carried_agent_content
                or carried_writer_used_write_tools
                or carried_writer_emitted_file_markers
            ):
                agent_content = f"{carried_agent_content}{agent_content}"
                writer_used_write_tools = (
                    writer_used_write_tools or carried_writer_used_write_tools
                )
                writer_emitted_file_markers = (
                    writer_emitted_file_markers or carried_writer_emitted_file_markers
                )
                carried_agent_content = ""
                carried_writer_used_write_tools = False
                carried_writer_emitted_file_markers = False
            if carried_ended_with_question:
                ended_with_question = True
                carried_ended_with_question = False

            if current_agent_type == "writer" and agent_content:
                lowered = agent_content.lower()
                if "<file" in lowered or "</file" in lowered:
                    writer_emitted_file_markers = True

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
                    carried_writer_used_write_tools = writer_used_write_tools
                    carried_writer_emitted_file_markers = writer_emitted_file_markers
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
                        current_agent_type
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

            # 快速模式：用户选的是「更快出结果」，writer 写完直接结束。nodes 已在系统提示
            # 里说明不要送审；模型仍然交接给审稿人时，这里丢弃。
            if (
                generation_mode == "fast"
                and current_agent_type == "writer"
                and next_agent == "quality_reviewer"
            ):
                log_with_context(
                    logger,
                    20,  # INFO
                    "Fast mode: dropping writer handoff to quality_reviewer",
                )
                next_agent = None
                explicit_handoff_event_data = None
                handoff_packet = None

            # 审查轮数上限：writer 不能再显式送审；审稿人要求返修时不再交回 writer，
            # 而是正常收尾并把审稿意见展示给用户。
            review_limit_notes: str | None = None
            if next_agent and review_round >= MAX_REVIEW_ROUNDS:
                if current_agent_type == "quality_reviewer" and _agent_can_write_files(next_agent):
                    review_limit_notes = _review_notes_from_packet(handoff_packet, handoff_context)
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
                    data={"text": _review_limit_text(review_limit_notes)},
                )
                for auto_task_update_event in await _auto_finalize_task_board_on_completion():
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

            # agent 以向用户提问收尾：它在等用户回答，计划交接不能越过用户继续跑
            # （否则 planner 刚问完“这个方向可以吗？”，writer 已经按没确认的方向写完
            # 了正文）。显式 handoff 是 agent 自己的明确决定，不受影响。
            # 自动质检门不看这个信号：它只审已经写完的稿子（审稿人没有写权限），
            # 而 writer 收尾时习惯性地问一句“需要我继续写第二章吗？”——按提问拦下
            # 会让用户选了高质量模式（generation_mode=quality）的请求实际不送审。
            awaiting_user_reply = not next_agent and ended_with_question
            if awaiting_user_reply and workflow_agents:
                log_with_context(
                    logger,
                    20,  # INFO
                    "Agent ended with a question to the user; skipping planned handoff",
                    agent_type=current_agent_type,
                    skipped_workflow_agents=list(workflow_agents),
                )
                workflow_agents.clear()

            # Determine upcoming handoff after stop checks.
            # Explicit handoff requests still take precedence over completion checks.
            has_pending_handoff = False
            has_explicit_handoff = False
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
                and len(agent_content) >= auto_review_threshold
                and (writer_emitted_file_markers or writer_used_write_tools)
            ):
                # Auto-trigger quality_reviewer for long content
                has_pending_handoff = True
                log_with_context(
                    logger,
                    20,
                    "Auto-triggering quality_reviewer due to content length",
                    content_length=len(agent_content),
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
                and agent_content.strip()
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
                # 没写文件或读不到时退回模型输出里的 <file> 块 / 全文。
                payload_guard = _guard_from_state(state)
                persisted_payload = (
                    await _review_payload_text(payload_guard.written_file_ids()) if payload_guard else None
                )
                review_payload = _format_review_payload(
                    persisted_payload or _extract_review_payload(agent_content)
                )
                handoff_context = (
                    f"{base_context}\n\n"
                    f"[待审查内容]\n{review_payload}"
                ).strip()

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
            # - planned/auto handoff 仅在显式完成标记时允许打断。
            # - 无待交接时允许启发式完成。
            if agent_content:
                evaluation = evaluate_agent_output(agent_content, current_agent_type)
                complete_result = detect_task_complete(agent_content, current_agent_type)

                explicit_complete = complete_result.reason == "explicit_complete_marker"

                if has_explicit_handoff:
                    can_stop_for_completion = False
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
                    auto_task_update_events = await _auto_finalize_task_board_on_completion()
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

            if pending_handoff_event_data is not None and will_run_next_agent:
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data=pending_handoff_event_data,
                )

            # 保存当前 agent 类型，用于下一轮循环检测
            previous_agent = current_agent_type

            # Apply next agent decision
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
