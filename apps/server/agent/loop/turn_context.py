"""单循环 agent 每轮附在作者消息前的结构化上下文。

只放这一轮请求本身带来的东西：项目信息与写作约定、作者正在看的文件、附加的文件、附加的素材库条目、
引用的文字、作者选的技能、本轮回复语言；另附 <author_edits>：焦点、附加文件和本会话 AI 写过的正文文件里，
作者在 AI 写过之后亲手改了哪几处（与图同一套 SessionLoader.author_edits_for_files，有文件数上限）。解析沿用 ContextAssembler 的既有解析器（归属校验、素材库
格式化、渲染标注都一样），但不做优先级裁剪、检索片段和文件清单：项目里其他文件由模型用 query_files 查。
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from agent.core.message_manager import TURN_CONTEXT_CLOSE, TURN_CONTEXT_OPEN, MessageManager
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

_PREAMBLE = (
    "这一段由系统在本轮开始时附上（不是作者写的）：作者这一轮附带的文件、素材和引用，以及项目信息。"
    "标注 [全文] 的是截至本轮开始的完整内容；与之前的对话不一致时，以这一段为准。"
)
_PREAMBLE_EN = (
    "The system attached this block at the start of this turn (the author did not write it): the files, "
    "materials and quotes the author attached, and the project information. Items marked [全文] are complete "
    "as of the start of this turn; where they differ from earlier conversation, follow this block."
)


@dataclass
class LoopTurnContext:
    text: str = ""
    # 以全文给模型看的项目文件正文（file_id → 正文）：edit_file 的陈旧检查以它为起点。
    read_snapshots: dict[str, str] = field(default_factory=dict)
    # 仅从本会话此前轮次结转、未在当前 <turn_context> 全文给出的文件 id（轮间压缩成功时撤销）。
    session_carried_snapshot_ids: set[str] = field(default_factory=set)
    # 前端 context 事件的条目（与图同一个形状）。
    items: list[dict[str, Any]] = field(default_factory=list)


def build_loop_turn_context(
    session: Session,
    *,
    project_id: str,
    user_id: str | None,
    metadata: dict[str, Any] | None,
    language: str,
    author_message: str,
    selected_skills: list[dict[str, Any]] | None = None,
    context_assembler: Any = None,
    chat_session_id: str | None = None,
    carried_compaction: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> LoopTurnContext:
    from agent.context import get_context_assembler
    from agent.tools.permissions import ForbiddenError, NotFoundError, check_project_ownership
    from models import File

    assembler = context_assembler or get_context_assembler()
    force_en = language.startswith("en")
    metadata = metadata if isinstance(metadata, dict) else {}
    result = LoopTurnContext()

    if cancel_event is not None and cancel_event.is_set():
        return result

    if user_id is not None:
        try:
            check_project_ownership(session, project_id, user_id)
        except (ForbiddenError, NotFoundError):
            # 与 assembler 同口径：没有权限就不附任何项目内容。
            log_with_context(logger, 40, "Loop turn context blocked: permission denied", project_id=project_id)
            return result

    items: list[Any] = []
    attached_file_ids = metadata.get("attached_file_ids")
    if isinstance(attached_file_ids, list) and attached_file_ids:
        items.extend(assembler.get_attached_files(session, project_id, [str(i) for i in attached_file_ids]))
    materials = metadata.get("attached_library_materials")
    if isinstance(materials, list) and materials:
        items.extend(assembler.get_attached_library_materials(session, user_id, materials))
    quotes = metadata.get("text_quotes")
    if isinstance(quotes, list) and quotes:
        items.extend(assembler.get_text_quotes([q for q in quotes if isinstance(q, dict)]))
    focus_file_id = metadata.get("current_file_id")
    if isinstance(focus_file_id, str) and focus_file_id:
        focus = session.get(File, focus_file_id)
        if focus is not None and not focus.is_deleted and focus.project_id == project_id and focus.file_type != "folder":
            items.append(assembler.file_to_context_item(focus, is_focus=True))
    items = assembler.deduplicate_items(items)
    items = _bound_turn_context_items(items, assembler)

    project_status = assembler.get_project_status(session, project_id)
    rendered = assembler.format_context(items, None, project_status)

    manager = MessageManager(project_id=project_id, user_id=user_id)
    parts: list[str] = [TURN_CONTEXT_OPEN, _PREAMBLE_EN if force_en else _PREAMBLE, "", rendered.strip()]
    author_edits = _author_edits_block(
        session,
        project_id=project_id,
        user_id=user_id,
        chat_session_id=chat_session_id,
        primary_ids=[fid for fid in (assembler.item_file_id(item) for item in items) if fid],
        cancel_event=cancel_event,
    )
    if author_edits:
        parts.extend(["", author_edits])
    parts.extend(manager._build_metadata_section(metadata, force_en))
    if selected_skills:
        parts.extend(manager._build_selected_skill_section(selected_skills, force_en))
    parts.extend(manager._build_author_language_line(author_message))
    parts.append(TURN_CONTEXT_CLOSE)
    result.text = "\n".join(parts)

    from agent.loop.history import valid_compaction

    for item in items:
        file_id = assembler.item_file_id(item)
        if file_id and not item.is_truncated and isinstance(item.content, str):
            result.read_snapshots[file_id] = item.content
    has_carried_compaction = isinstance(carried_compaction, dict) and valid_compaction(carried_compaction) is not None
    if chat_session_id and not has_carried_compaction and not (cancel_event is not None and cancel_event.is_set()):
        carried = _session_read_snapshots(
            session,
            project_id=project_id,
            user_id=user_id,
            chat_session_id=chat_session_id,
            exclude_ids=set(result.read_snapshots),
            cancel_event=cancel_event,
        )
        for file_id, content in carried.items():
            if file_id not in result.read_snapshots:
                result.read_snapshots[file_id] = content
                result.session_carried_snapshot_ids.add(file_id)
    result.items = [item.to_dict() for item in items]
    return result


# 本会话 AI 写过、会一起核对作者手改的正文类文件。
_BODY_FILE_TYPES = ("draft", "script")


def _bound_turn_context_items(
    items: list[Any],
    assembler: Any,
    max_provider_tokens: int | None = None,
) -> list[Any]:
    """限制单轮焦点/附加文件与素材的总预算，常态多章附加原样给全文，数十万字长卷自动截断并引导 query_files。"""
    if not items:
        return items
    from agent.context.budget import TokenBudget
    from agent.loop.history import estimate_provider_text_tokens
    from config.agent_runtime import AGENT_LOOP_TURN_CONTEXT_MAX_TOKENS

    limit = AGENT_LOOP_TURN_CONTEXT_MAX_TOKENS if max_provider_tokens is None else max_provider_tokens
    budget = TokenBudget(
        max_tokens=limit,
        item_overhead=assembler.item_render_overhead_tokens,
        token_estimator=estimate_provider_text_tokens,
    )
    if sum(budget.estimate_item_tokens(item) for item in items) <= limit:
        return items

    # 优先保住引用与当前打开的焦点文件全文（使 edit_file 快照可用），超大附加参考稿在剩余额度内截断。
    orig_order = {(str(item.type), str(item.id)): idx for idx, item in enumerate(items)}
    prioritized = sorted(
        items,
        key=lambda item: (
            0 if item.type == "quote" else 1 if item.is_focus else 2,
            orig_order[(str(item.type), str(item.id))],
        ),
    )
    selected, _ = budget.select_items(prioritized)
    # TokenBudget 对 CRITICAL 条目只截断不静默丢弃；按原进入顺序排回以便渲染稳定。
    selected.sort(key=lambda item: orig_order.get((str(item.type), str(item.id)), len(items)))
    return selected


def _author_edits_block(
    session: Session,
    *,
    project_id: str,
    user_id: str | None,
    chat_session_id: str | None,
    primary_ids: list[str],
    cancel_event: threading.Event | None = None,
) -> str:
    """焦点 / 附加文件在前，再是本会话 AI 写过的正文文件：作者在 AI 写过之后亲手改了哪几处。

    增益信息：算不出来时不影响本轮（块里明说，或整块不附）。
    """
    from agent.core.session_loader import AUTHOR_EDITS_MAX_FILES, SessionLoader

    if not user_id or (cancel_event is not None and cancel_event.is_set()):
        return ""
    loader = SessionLoader(project_id=project_id, user_id=user_id)
    try:
        written: list[str] = []
        if chat_session_id:
            # 先按本项目、未删除的正文文件筛，再计名额：大纲、设定等文件不挤掉 AI 写过的章节。
            written = loader.session_written_file_ids(
                session,
                chat_session_id=chat_session_id,
                limit=AUTHOR_EDITS_MAX_FILES,
                file_types=_BODY_FILE_TYPES,
                cancel_event=cancel_event,
            )
        if cancel_event is not None and cancel_event.is_set():
            return ""
        ordered = list(dict.fromkeys([*primary_ids, *written]))
        if not ordered:
            return ""
        block, _edited, _files = loader.author_edits_for_files(
            session, ordered, {}, cancel_event=cancel_event
        )
        return block
    except Exception as exc:  # noqa: BLE001 - 增益信息，失败不影响本轮
        with contextlib.suppress(Exception):
            session.rollback()
        log_with_context(
            logger, 30, "Loop author edits build failed", project_id=project_id, error_type=type(exc).__name__
        )
        return ""


def _session_read_snapshots(
    session: Session,
    *,
    project_id: str,
    user_id: str | None,
    chat_session_id: str,
    exclude_ids: set[str],
    cancel_event: threading.Event | None = None,
) -> dict[str, str]:
    """结转本会话此前轮次读/写过且未变更的文件快照（增益信息，失败不影响本轮）。"""
    from agent.core.session_loader import SESSION_SNAPSHOT_MAX_FILES, SessionLoader

    if cancel_event is not None and cancel_event.is_set():
        return {}
    loader = SessionLoader(project_id=project_id, user_id=user_id)
    try:
        return loader.session_read_snapshots(
            session,
            chat_session_id=chat_session_id,
            exclude_ids=exclude_ids,
            limit=SESSION_SNAPSHOT_MAX_FILES,
            cancel_event=cancel_event,
        )
    except Exception as exc:  # noqa: BLE001 - 增益信息，失败不影响本轮
        with contextlib.suppress(Exception):
            session.rollback()
        log_with_context(
            logger,
            30,
            "Loop session read snapshots carryover failed",
            project_id=project_id,
            error_type=type(exc).__name__,
        )
        return {}
