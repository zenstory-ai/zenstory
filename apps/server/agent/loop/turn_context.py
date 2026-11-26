"""单循环 agent 每轮附在作者消息前的结构化上下文。

只放这一轮请求本身带来的东西：项目信息与写作约定、紧凑的项目文件索引（`<project_files>`，按
类型与章节顺序确定性排序、有界封顶，避免模型首步空跑 `query_files(list)`）、作者正在看的文件、
附加的文件、附加的素材库条目、引用的文字、作者选的技能；另附 <author_edits>：焦点、附加文件和
本会话 AI 写过的正文文件里，作者在 AI 写过之后亲手改了哪几处（与图同一套 SessionLoader.author_edits_for_files，
有文件数上限）。解析沿用 ContextAssembler 的既有解析器（归属校验、素材库格式化、渲染标注都一样），
不做优先级裁剪和检索片段：文件正文由模型按索引 ID 用 query_files 读取。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from agent.core.message_manager import TURN_CONTEXT_CLOSE, TURN_CONTEXT_OPEN, MessageManager
from agent.loop.ui_actions import UIAction, build_ui_action_turn_section
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

logger = get_logger(__name__)

PROJECT_FILES_OPEN = "<project_files>"
PROJECT_FILES_CLOSE = "</project_files>"
PROJECT_FILES_UNCHANGED_MARKER = '<project_files unchanged="true"/>'
AUTHOR_EDITS_UNCHANGED_MARKER = '<author_edits unchanged="true"/>'
PROJECT_FILES_MANIFEST_MAX_FILES = 150
# 仅对达到一定长度的全文文件做温缓存跨轮去重，避免替几十字短条目标注占位符反而增加冗余。
_WARM_DEDUP_FILE_MIN_CHARS = 500
_WARM_DEDUP_MAX_TRACKED_FILES = 20
_UNCHANGED_FILE_POINTER_ZH = "（与本会话近期上下文中已提供的全文一致，未变更；沿用前文全文，无需重复读取）"
_UNCHANGED_FILE_POINTER_EN = (
    "(Unchanged since the full text provided earlier in this session; reuse that text without re-reading)"
)
# 参考资料优先于大批量正文分章，确保超大长篇封顶时大纲/角色/设定不被数百章正文挤出索引。
_MANIFEST_TYPE_ORDER: dict[str, int] = {
    "outline": 0,
    "character": 1,
    "lore": 2,
    "snippet": 3,
    "document": 4,
    "draft": 5,
    "script": 6,
}

_PREAMBLE = (
    "这一段由系统在本轮开始时附上（不是作者写的）：作者这一轮附带的文件、素材和引用，以及项目信息。"
    "标注 [全文] 的是截至本轮开始的完整内容；与之前的对话不一致时，以这一段为准。"
)
_PREAMBLE_EN = (
    "The system attached this block at the start of this turn (the author did not write it): the files, "
    "materials and quotes the author attached, and the project information. Items marked [全文] are complete "
    "as of the start of this turn; where they differ from earlier conversation, follow this block."
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class LoopTurnContext:
    text: str = ""
    # 当本轮基于温缓存指纹做了去重省略时，保留未省略的完整 <turn_context>（供 Stage 2 压缩或历史截断回退）。
    full_text: str = ""
    # 以全文给模型看的项目文件正文（file_id → 正文）：edit_file 的陈旧检查以它为起点。
    read_snapshots: dict[str, str] = field(default_factory=dict)
    # 仅从本会话此前轮次结转、未在当前 <turn_context> 全文给出的文件 id（轮间压缩成功时撤销）。
    session_carried_snapshot_ids: set[str] = field(default_factory=set)
    # 前端 context 事件的条目（与图同一个形状）。
    items: list[dict[str, Any]] = field(default_factory=list)
    # 本轮上下文轻量指纹（写入 wire_prefix_cache 供下一温缓存轮次去重，0 KV 前缀破坏）。
    context_fingerprints: dict[str, Any] = field(default_factory=dict)


def build_loop_turn_context(
    session: Session,
    *,
    project_id: str,
    user_id: str | None,
    metadata: dict[str, Any] | None,
    language: str,
    author_message: str = "",  # noqa: ARG001 - kept for caller compatibility; reply language is decided by the model
    selected_skills: list[dict[str, Any]] | None = None,
    context_assembler: Any = None,
    chat_session_id: str | None = None,
    carried_compaction: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
    ui_action: UIAction | None = None,
    selected_text: str | None = None,
    prior_context_fingerprints: dict[str, Any] | None = None,
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
    focus_file = None
    focus_file_id = metadata.get("current_file_id")
    if isinstance(focus_file_id, str) and focus_file_id:
        focus = session.get(File, focus_file_id)
        if focus is not None and not focus.is_deleted and focus.project_id == project_id and focus.file_type != "folder":
            focus_file = focus
            items.append(assembler.file_to_context_item(focus, is_focus=True))
    items = assembler.deduplicate_items(items)
    items = _bound_turn_context_items(items, assembler)

    project_status = assembler.get_project_status(session, project_id)

    prior_fp = prior_context_fingerprints if isinstance(prior_context_fingerprints, dict) else None
    prior_file_hashes: dict[str, str] = (
        {
            str(k): str(v)
            for k, v in prior_fp.get("full_file_hashes", {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        if prior_fp and isinstance(prior_fp.get("full_file_hashes"), dict)
        else {}
    )

    loaded_word_counts: dict[str, int] = {}
    current_full_file_hashes: dict[str, str] = {}
    in_context_full_ids: set[str] = set()
    render_items: list[Any] = []
    deduped_any = False
    pointer_text = _UNCHANGED_FILE_POINTER_EN if force_en else _UNCHANGED_FILE_POINTER_ZH

    for item in items:
        file_id = assembler.item_file_id(item)
        if file_id and not item.is_truncated and isinstance(item.content, str):
            loaded_word_counts[file_id] = count_words(item.content)
            in_context_full_ids.add(file_id)
            if len(item.content) >= _WARM_DEDUP_FILE_MIN_CHARS:
                f_hash = _sha256_text(item.content)
                current_full_file_hashes[file_id] = f_hash
                if prior_file_hashes.get(file_id) == f_hash:
                    deduped_any = True
                    if hasattr(item, "model_copy"):
                        render_items.append(item.model_copy(update={"content": pointer_text}))
                    else:
                        render_items.append(item)
                    continue
        render_items.append(item)

    rendered = assembler.format_context(render_items, None, project_status)

    manager = MessageManager(project_id=project_id, user_id=user_id)
    project_files = _project_files_block(
        session,
        project_id=project_id,
        force_en=force_en,
        loaded_word_counts=loaded_word_counts,
        cancel_event=cancel_event,
    )
    project_files_sha = _sha256_text(project_files) if project_files else ""
    emitted_project_files = project_files
    if project_files and prior_fp and prior_fp.get("project_files_sha256") == project_files_sha:
        emitted_project_files = PROJECT_FILES_UNCHANGED_MARKER
        deduped_any = True

    author_edits = _author_edits_block(
        session,
        project_id=project_id,
        user_id=user_id,
        chat_session_id=chat_session_id,
        in_context_full_ids=in_context_full_ids,
        force_en=force_en,
        cancel_event=cancel_event,
    )
    author_edits_sha = _sha256_text(author_edits) if author_edits else ""
    emitted_author_edits = author_edits
    if author_edits and prior_fp and prior_fp.get("author_edits_sha256") == author_edits_sha:
        emitted_author_edits = AUTHOR_EDITS_UNCHANGED_MARKER
        deduped_any = True

    metadata_section = manager._build_metadata_section(metadata, force_en)
    selected_skill_section = (
        manager._build_selected_skill_section(selected_skills, force_en) if selected_skills else []
    )
    ui_action_section = (
        build_ui_action_turn_section(
            session,
            ui_action=ui_action,
            force_en=force_en,
            focus_file_id=focus_file.id,
            focus_file_title=focus_file.title,
            focus_file_type=focus_file.file_type,
            has_selected_text=bool((selected_text or "").strip()),
        )
        if ui_action is not None and focus_file is not None
        else []
    )

    def _assemble_turn_text(rendered_ctx: str, pf_block: str, ae_block: str) -> str:
        assembled: list[str] = [TURN_CONTEXT_OPEN, _PREAMBLE_EN if force_en else _PREAMBLE, "", rendered_ctx.strip()]
        if pf_block:
            assembled.extend(["", pf_block])
        if ae_block:
            assembled.extend(["", ae_block])
        assembled.extend(metadata_section)
        assembled.extend(selected_skill_section)
        assembled.extend(ui_action_section)
        assembled.append(TURN_CONTEXT_CLOSE)
        return "\n".join(assembled)

    result.text = _assemble_turn_text(rendered, emitted_project_files, emitted_author_edits)
    if deduped_any:
        full_rendered = assembler.format_context(items, None, project_status) if render_items != items else rendered
        result.full_text = _assemble_turn_text(full_rendered, project_files, author_edits)

    # Carry forward recent full-file hashes (bounded) + overwrite with this turn's hashes.
    merged_file_hashes: dict[str, str] = {}
    if prior_file_hashes:
        merged_file_hashes.update(prior_file_hashes)
    merged_file_hashes.update(current_full_file_hashes)
    if len(merged_file_hashes) > _WARM_DEDUP_MAX_TRACKED_FILES:
        trimmed_keys = list(merged_file_hashes)[-_WARM_DEDUP_MAX_TRACKED_FILES:]
        merged_file_hashes = {k: merged_file_hashes[k] for k in trimmed_keys}
    result.context_fingerprints = {
        "project_files_sha256": project_files_sha,
        "full_file_hashes": merged_file_hashes,
        "author_edits_sha256": author_edits_sha,
        # 记录本轮实际按完整正文发出的指纹（供 Stage 2 压缩回退到 full_text 时重置指纹使用）。
        "_current_full_file_hashes": current_full_file_hashes,
    }

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


def _decode_file_metadata(raw_metadata: str | None) -> dict[str, Any]:
    if not raw_metadata:
        return {}
    try:
        parsed = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _project_files_block(
    session: Session,
    *,
    project_id: str,
    force_en: bool,
    loaded_word_counts: dict[str, int] | None = None,
    max_files: int = PROJECT_FILES_MANIFEST_MAX_FILES,
    cancel_event: threading.Event | None = None,
) -> str:
    """构建紧凑有界的 `<project_files>` 索引块（仅查元数据列，不触发 PostgreSQL TOAST 解压）。

    放在每轮动态 `<turn_context>` 内（静态系统提示与已缓存历史之后），保持前缀缓存 100% 稳定，
    同时消除模型首步为找文件 ID 空跑 `query_files(list)` 的额外往返。
    """
    from models import File
    from models.file_model import cached_word_count
    from utils.title_sequence import build_sequence_sort_key

    if max_files <= 0 or (cancel_event is not None and cancel_event.is_set()):
        return ""
    try:
        rows = session.exec(
            select(
                File.id,
                File.title,
                File.file_type,
                File.order,
                File.file_metadata,
                File.created_at,
            ).where(
                File.project_id == project_id,
                File.file_type != "folder",
                File.is_deleted.is_(False),
            )
        ).all()
    except Exception as exc:  # noqa: BLE001 - 增益信息，失败不影响本轮
        with contextlib.suppress(Exception):
            session.rollback()
        log_with_context(
            logger,
            30,
            "Loop project files manifest build failed",
            project_id=project_id,
            error_type=type(exc).__name__,
        )
        return ""

    if not rows:
        return ""

    entries: list[tuple[tuple[int, str, int, int, str, str], str]] = []
    for file_id, title, file_type, file_order, raw_metadata, created_at in rows:
        meta = _decode_file_metadata(raw_metadata)
        ftype = str(file_type or "document")
        clean_title = " ".join(str(title or "").split()) or "(untitled)"
        effective_order, seq_num = build_sequence_sort_key(
            file_order,
            title=clean_title,
            metadata=meta,
            file_type=ftype,
        )
        created_key = created_at.isoformat() if created_at is not None else ""
        sort_key = (
            _MANIFEST_TYPE_ORDER.get(ftype, len(_MANIFEST_TYPE_ORDER)),
            ftype,
            effective_order,
            seq_num,
            created_key,
            str(file_id),
        )
        word_count = cached_word_count(raw_metadata)
        if word_count is None and loaded_word_counts and file_id in loaded_word_counts:
            word_count = loaded_word_counts[file_id]
        wc_part = (
            (f", {word_count} words" if force_en else f", {word_count}字")
            if isinstance(word_count, int)
            else ""
        )
        line = f"- [{ftype}] {clean_title} (id={file_id}{wc_part})"
        entries.append((sort_key, line))

    entries.sort(key=lambda item: item[0])
    shown = entries[:max_files]
    remaining = len(entries) - len(shown)

    lines = [PROJECT_FILES_OPEN, *(line for _, line in shown)]
    if remaining > 0:
        footer = (
            f"... (+{remaining} more; use query_files to list or search remaining files)"
            if force_en
            else f"... (+{remaining} more；其余文件可用 query_files 查阅)"
        )
        lines.append(footer)
    lines.append(PROJECT_FILES_CLOSE)
    return "\n".join(lines)


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
    in_context_full_ids: set[str],
    force_en: bool = False,
    cancel_event: threading.Event | None = None,
) -> str:
    """本会话 AI 写过但不在本轮全文上下文中的正文文件：若在最近 AI 写入后被作者手改，给 1 行提醒。

    焦点与附加文件已在本轮 ``<turn_context>`` 里给出最新全文，无需重复提醒；
    增益信息：算不出来时不影响本轮。
    """
    from agent.core.session_loader import AUTHOR_EDITS_MAX_FILES, SessionLoader

    if not user_id or not chat_session_id or (cancel_event is not None and cancel_event.is_set()):
        return ""
    loader = SessionLoader(project_id=project_id, user_id=user_id)
    try:
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
        non_context_written = [fid for fid in written if fid not in in_context_full_ids]
        if not non_context_written:
            return ""
        block, _edited, _files = loader.author_edits_for_files(
            session, non_context_written, {}, force_en=force_en, cancel_event=cancel_event
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
