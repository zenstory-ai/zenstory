"""
Agent service - AI writing assistant run as a single loop agent.

Features:
- Open-ended conversation (no fixed intents)
- File operations via Function Calling (create, update, delete, query)
- Chat history with memory
- Streaming SSE output
- Intelligent context assembly with priority-based selection
- Steering message support

Built on the single loop agent (agent/loop) and openai-agents-python.
"""

import asyncio
import contextlib
import json
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlmodel import Session, and_, select

from config import agent_runtime
from config.agent_runtime import AGENT_LOOP_RUN_HISTORY_MAX_TOKENS
from config.datetime_utils import utcnow
from database import create_session
from services.chat_session_service import latest_active_chat_session
from utils.logger import get_logger, log_with_context

from .constants import should_offload_session_work
from .context import ContextAssembler, get_context_assembler
from .core import llm_failover
from .core.account_run_limit import AccountRunSlot
from .core.events import (
    HISTORY_COMPACTION_SKIPPED_STEP,
    HISTORY_COMPACTION_STEP,
    WALL_CLOCK_LIMIT,
    context_event,
    done_event,
    error_event,
    history_compacted_event,
    iteration_exhausted_status_card,
    session_started_event,
    skill_matched_event,
    thinking_event,
)
from .core.llm_providers import primary_model_name
from .core.message_manager import (
    MessageManager,
    has_substantive_display_events,
    normalize_assistant_display_events,
)
from .core.round_outcome import RUN_DEADLINE_STOP_REASON, RunOutcome
from .core.run_meter import AgentRunMeter
from .core.session_loader import SessionLoader
from .core.steering import (
    _RUN_HEARTBEAT_INTERVAL_S,
    cleanup_steering_queue_async,
    create_steering_queue_async,
    drain_and_seal_steering_async,
    heartbeat_steering_run_async,
)
from .core.stream_errors import classify_stream_exception, log_stream_exception
from .core.writing_state import WritingState
from .loop.compaction import (
    CompactionOutcome,
    ToolArchiveResult,
    archive_old_tool_results,
    compact_history,
    should_compact,
    skipped_record,
)
from .loop.engine import run_loop_agent_streaming
from .loop.history import (
    LOOP_ENGINE_METADATA_VALUE,
    LoopHistory,
    bound_history_for_provider,
    effective_history_provider_tokens,
    estimate_provider_items_tokens,
    estimate_provider_text_tokens,
    is_tool_archive_compaction,
    load_loop_history,
    row_metadata,
    valid_compaction,
    valid_tool_archive_compaction,
)
from .loop.turn_context import LoopTurnContext, build_loop_turn_context
from .loop.ui_actions import UIAction
from .loop.wire_prefix_cache import (
    ainvalidate_wire_prefix,
    astore_wire_prefix,
    effective_reasoning_effort,
    invalidate_wire_prefix,
    is_well_formed_wire_prefix,
    load_wire_prefix_entry,
    provider_cache_key,
    tools_schema_sha256,
)
from .openai_agents.runner import MAX_TURNS_STOP_REASON
from .skills import get_skill_context_injector, resolve_selected_skills
from .skills.active_skills import list_active_skill_resources
from .skills.content_budget import split_selected_budget
from .stream_adapter import STREAM_FILE_SAVE_TIMEOUT_S, create_stream_adapter
from .tools.tool_context import AgentRequestContext

logger = get_logger(__name__)

# 取消路径补存部分历史时写进 message_metadata.stop_reason 的值。墙钟时限与发布交接
# 也是以取消的方式到达 process_stream（sse_pump 取消后台 task）：原因由路由层写在
# RunOutcome 上（stop_kind / deadline_stop_reason），都没有时才是这个。
PARTIAL_SAVE_CANCELLED_STOP_REASON = "cancelled"
# 前端「重试 / 重新发送」一条没得到回复的消息时在 metadata 里带上它（见
# _drop_unanswered_retry_message_sync）。
RETRY_UNANSWERED_METADATA_KEY = "retry_unanswered"

# Stop reasons whose clean completion (or soft-landing) produces a reusable wire-prefix cache entry.
_WIRE_PREFIX_CACHEABLE_STOP_REASONS = frozenset(
    {"end_turn", RUN_DEADLINE_STOP_REASON, MAX_TURNS_STOP_REASON}
)
# Clean soft-landing pause cards that do NOT taint wire_prefix_cache (as long as no empty files
# were left unfinished or rolled back).
_WIRE_PREFIX_CLEAN_SOFT_LAND_CARD_LAYERS = frozenset({"tool_call", "collaboration"})
_WIRE_PREFIX_CLEAN_SOFT_LAND_CARD_LIMITS = frozenset({WALL_CLOCK_LIMIT, "max_turns"})


def _has_blocking_wire_prefix_status_cards(status_cards: list[dict[str, Any]]) -> bool:
    """Return True when any status card indicates an unclean/non-cacheable ending."""
    for card in status_cards:
        if not isinstance(card, dict):
            return True
        if card.get("type") != "iteration_exhausted":
            return True
        layer = card.get("layer")
        if layer not in _WIRE_PREFIX_CLEAN_SOFT_LAND_CARD_LAYERS:
            return True
        limit = card.get("limit")
        if limit not in _WIRE_PREFIX_CLEAN_SOFT_LAND_CARD_LIMITS and not (
            layer == "collaboration" and limit is None
        ):
            return True
        if card.get("unfinishedFiles") or card.get("rolledBackFiles"):
            return True
    return False


_FINGERPRINT_MUTATION_TOOLS = frozenset(
    {"edit_file", "write_chapter", "create_file", "delete_file", "rename_file"}
)


def _applied_file_mutations_for_fingerprints(
    tool_calls: list[dict[str, Any]],
) -> tuple[set[str], bool]:
    """Return ``(mutated_file_ids, any_file_mutation)`` for tool calls that mutated files this turn.

    Used before storing ``wire_prefix_cache`` so pre-turn ``context_fingerprints`` for
    mutated files (and the project manifest / author_edits block) are invalidated: if the
    author reverts a file back to its pre-turn text before the next turn, the next turn
    will re-emit full content instead of a stale ``unchanged`` pointer.
    """
    mutated_ids: set[str] = set()
    any_mutation = False
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("name") or "").strip()
        if name not in _FINGERPRINT_MUTATION_TOOLS:
            continue
        status = str(tc.get("status") or "").strip().lower()
        if status not in ("success", "partial"):
            continue
        res = tc.get("result")
        if isinstance(res, str):
            with contextlib.suppress(Exception):
                res = json.loads(res)
        res_data = res.get("data") if isinstance(res, dict) and isinstance(res.get("data"), dict) else res
        if res is False or res_data is False:
            continue
        if isinstance(res_data, dict):
            if res_data.get("mutation_applied") is False or res_data.get("all_failed") is True:
                continue
            if res_data.get("body_saved") is False and res_data.get("action") == "replaced":
                continue
            for key in ("id", "file_id"):
                val = res_data.get(key)
                if isinstance(val, str) and val.strip():
                    mutated_ids.add(val.strip())
            deleted_ids = res_data.get("deleted_ids")
            if isinstance(deleted_ids, list):
                for val in deleted_ids:
                    if isinstance(val, str) and val.strip():
                        mutated_ids.add(val.strip())
        args = tc.get("arguments")
        if isinstance(args, dict):
            for key in ("file_id", "id"):
                val = args.get(key)
                if isinstance(val, str) and val.strip():
                    mutated_ids.add(val.strip())
        any_mutation = True
    return mutated_ids, any_mutation


def _read_file_hashes_for_fingerprints(
    tool_calls: list[dict[str, Any]],
) -> tuple[dict[str, str], set[str]]:
    """Return ``(full_read_hashes, all_read_file_ids)`` for successful ``query_files`` calls this turn.

    Used before storing ``wire_prefix_cache`` so:
    - Path A: any file read via ``query_files`` updates (or evicts, for partial/truncated/short reads)
      ``cached_file_hashes[fid]`` even when no write tool ran, preventing a later author revert
      (V1 -> V2 -> V1) from emitting a stale ``unchanged`` pointer while the model last saw V2.
    - Path B: if any file alerted in ``<author_edits>`` was read this turn, ``author_edits_sha256``
      is cleared so a subsequent manual edit (V2 -> V3) never emits ``<author_edits unchanged="true"/>``.
    """
    from agent.loop.turn_context import _WARM_DEDUP_FILE_MIN_CHARS
    from services.features.file_version_service import content_sha256

    full_read_hashes: dict[str, str] = {}
    all_read_ids: set[str] = set()

    def _visit(node: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(node, list):
            for elem in node:
                _visit(elem, depth + 1)
        elif isinstance(node, dict):
            raw_fid = node.get("id") or node.get("file_id")
            if isinstance(raw_fid, str) and raw_fid.strip() and node.get("file_type") != "folder":
                fid = raw_fid.strip()
                all_read_ids.add(fid)
                is_partial = bool(node.get("content_truncated") or node.get("content_partial"))
                content = node.get("content")
                raw_sha = node.get("content_sha256")
                if not is_partial and isinstance(content, str):
                    if len(content) >= _WARM_DEDUP_FILE_MIN_CHARS:
                        full_read_hashes[fid] = content_sha256(content)
                    else:
                        full_read_hashes.pop(fid, None)
                elif not is_partial and isinstance(raw_sha, str) and raw_sha.strip():
                    full_read_hashes[fid] = raw_sha.strip()
                else:
                    full_read_hashes.pop(fid, None)
            for container_key in ("data", "files", "items", "results"):
                nested = node.get(container_key)
                if isinstance(nested, (list, dict)):
                    _visit(nested, depth + 1)

    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        if str(tc.get("name") or "").strip() != "query_files":
            continue
        if str(tc.get("status") or "").strip().lower() != "success":
            continue
        res = tc.get("result")
        if isinstance(res, str):
            with contextlib.suppress(Exception):
                res = json.loads(res)
        if isinstance(res, (list, dict)):
            _visit(res)

    return full_read_hashes, all_read_ids


def _wire_prefix_slimmed_only_file_ids(items: list[dict[str, Any]]) -> set[str]:
    """Identify files whose latest ``query_files`` entry in cached wire items is persist-slimmed
    (``archived_note`` without ``content``), e.g. from a prior cold replay -> warm cache hop."""
    slimmed_only: set[str] = set()

    def _visit(node: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(node, list):
            for elem in node:
                _visit(elem, depth + 1)
        elif isinstance(node, dict):
            raw_fid = node.get("id") or node.get("file_id")
            if isinstance(raw_fid, str) and raw_fid.strip() and node.get("file_type") != "folder":
                fid = raw_fid.strip()
                content = node.get("content")
                has_content = isinstance(content, str) and bool(content)
                has_note = isinstance(node.get("archived_note"), str) and bool(node["archived_note"].strip())
                if has_note and not has_content:
                    slimmed_only.add(fid)
                elif (
                    has_content
                    and not node.get("content_truncated")
                    and not node.get("content_partial")
                ):
                    slimmed_only.discard(fid)
            for container_key in ("data", "files", "items", "results"):
                nested = node.get(container_key)
                if isinstance(nested, (list, dict)):
                    _visit(nested, depth + 1)

    for item in items:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        output = item.get("output")
        if not isinstance(output, str) or '"archived_note"' not in output:
            if isinstance(output, str) and '"content"' in output and slimmed_only:
                with contextlib.suppress(Exception):
                    parsed = json.loads(output)
                    if isinstance(parsed, (list, dict)):
                        _visit(parsed)
            continue
        with contextlib.suppress(Exception):
            parsed = json.loads(output)
            if isinstance(parsed, (list, dict)):
                _visit(parsed)

    return slimmed_only


# 作者在一部作品里发出的第一条消息带 metadata.entry：工作台想法（dashboard_idea）、聊天空态
# 起手卡的示例（starter_example）、已有作者正文时起手卡的读稿请求（starter_manuscript）、空会话里
# 手打的第一条（first_message）。前端发的是作者原话；
# 服务端只在发给模型的本轮内容里附一句隐藏提示，落库仍是原话。作品的任何会话里已经有 AI 回复时
# 不附：老作品里新开的对话、第一条之后才到的旧标记都不是第一条。
# 见 feature/2026-10-19-first-turn-writes-before-asking.md。
DASHBOARD_IDEA_ENTRY = "dashboard_idea"
STARTER_EXAMPLE_ENTRY = "starter_example"
STARTER_MANUSCRIPT_ENTRY = "starter_manuscript"
FIRST_MESSAGE_ENTRY = "first_message"
FIRST_MESSAGE_ENTRIES = frozenset(
    {DASHBOARD_IDEA_ENTRY, STARTER_EXAMPLE_ENTRY, STARTER_MANUSCRIPT_ENTRY, FIRST_MESSAGE_ENTRY}
)

_PROJECT_TYPE_LABELS_ZH = {"novel": "长篇小说", "short": "短篇小说", "screenplay": "短剧剧本"}
_PROJECT_TYPE_LABELS_EN = {"novel": "Novel", "short": "Short Story", "screenplay": "Short Drama Script"}


# 首轮默认交付：按项目类型给一个完整单元，不写死字数。篇幅只是内部参考，不复述给作者。
# 见 feature/2026-10-31-first-turn-complete-unit.md（取代 10-19 note 里的「约 800 字开头」）。
_FIRST_DELIVERY_EN = {
    "novel": (
        "a complete first chapter into the manuscript, at the chapter length usual for the genre "
        "(web-serial chapters commonly run 1,500–3,000 words)"
    ),
    "short": (
        "a complete opening section, from the first line through the first turning point, into a single "
        "manuscript file; at the end of your reply say you can continue to the end of the story in that same file"
    ),
    "screenplay": (
        "the complete episode 1 script into the script, at the episode length usual for this kind of short drama"
    ),
}
_FIRST_DELIVERY_ZH = {
    "novel": "完整的第一章写进正文，篇幅按这类题材常见的章长（中文网文一般 2000–3000 字）",
    "short": "从开篇写到第一个转折的完整开篇段落写进同一个正文文件，回复末尾告诉作者可以在这个文件里接着写完全篇",
    "screenplay": "完整的第 1 集剧本写进剧本，篇幅按这类短剧常见的单集长度",
}


def first_message_hint(project_type: str | None, force_en: bool) -> str:
    """作者第一条消息的隐藏提示（只进模型输入，不落库、不显示）：先写，再给方向。

    免费版每天 3 条：先问两三个问题的一轮照样计 1 条，作者要到第 2、3 条才看到正文。
    有创作意图但范围模糊时按最常见的理解交付一个完整单元（长篇第一章、短篇开篇到第一个转折、
    短剧第 1 集），再给两个可换的方向；作者明确要了别的照做，单独保存项目资料不附送新文稿。
    """
    unit_type = project_type if project_type in _FIRST_DELIVERY_ZH else "novel"
    if force_en:
        label = _PROJECT_TYPE_LABELS_EN.get(project_type or "", "project")
        delivery = _FIRST_DELIVERY_EN[unit_type]
        return (
            f"This is the author's first message in this \"{label}\". "
            "If the author clearly asked for something else (only an outline or settings, only a question, "
            "only a read-through without changes), do exactly that. "
            "If the author pasted formed material (a synopsis section, an outline, a settings sheet, a character list or a chapter list) without saying what to do with it, "
            "read it and ask one short question about what they want done with it (for example, write chapter one from it or first organize it into an outline); "
            "do not write manuscript text this turn. A story idea or one-line premise the author wrote themselves is a creative intent, not material: deliver as below. "
            "If the sole requested delivery is saving the supplied facts to project memory or another project-information update, "
            "pass only the project-information fields the author requested to update_project; facts about characters or plot do not authorize new writing. "
            "Do not issue additional title, summary, style, phase, or task updates unless the author asked for them. "
            "Do not create an outline, character sheet, setting file, or manuscript for that request. "
            "If the project already has manuscript text, continue from the author's request instead of starting a new opening. "
            "If the author expresses a creative-writing intent but has not limited the delivery scope, do not ask questions first; "
            "deliver in this turn: introduce the protagonist, setting and core "
            f"conflict in three to five sentences, then write {delivery}. "
            "That length is only your internal reference: do not quote a word count to the author, "
            "do not present it as the author's request, and do not cut content to hit a number; "
            "write the unit through to a complete ending. "
            "If the author gave a length or scope (including \"no more than N words\" or a total length for a short story), "
            "follow the author's words. "
            "If the idea is still vague, write it the most common way; at the end of your reply list two alternative "
            "directions, one sentence each, so the author can pick one or say what to change."
        )
    label = _PROJECT_TYPE_LABELS_ZH.get(project_type or "", "作品")
    delivery = _FIRST_DELIVERY_ZH[unit_type]
    return (
        f"这是作者在这部「{label}」里发来的第一条消息。"
        "作者明确要了别的（只要大纲或设定、只问问题、只看稿不改等）时，直接按要求做；"
        "作者贴来成型的材料（成段的故事简介或梗概、设定集、人物表、章节列表）、没说要用它做什么时，"
        "先读懂材料，用一句话问作者想怎么用（比如按它写第一章，还是先整理成大纲），本轮不写正文；"
        "作者自己写的故事点子或一句话梗概是创作意图，不是材料，照下面交付；"
        "如果唯一交付物是把这次提供的事实记进项目记忆或更新其他项目资料，"
        "调用 update_project 时只传作者要求的字段，不额外发起改名、摘要、风格、阶段或任务更新；"
        "资料涉及人物或剧情也不代表作者要求创作，不要创建大纲、人设、设定文件或正文。"
        "作品里已经有正文时，按作者的要求接着做，不要另起一个开头。"
        "作者有创作意图但没有限定交付范围时，不要先提问，本轮就交付："
        "先用三五句话交代主角、背景和核心冲突，"
        f"再把{delivery}。"
        "这个篇幅只是你内部的参考：不向作者提具体字数，不说成是作者的要求，也不为凑字数删减内容，把这一部分写完整。"
        "作者给了字数或范围（包括「不超过N字」、短篇给出全篇字数）时一律按作者原话。"
        "想法还模糊时，按最常见的理解来写，回复末尾再列两个可以换的方向，每个一句话，"
        "让作者挑一个或直接说想怎么改。"
    )


def _stamp_body_receipts(
    tool_calls: list[dict[str, Any]],
    receipts: dict[str, int | None],
    replacement_receipts: dict[str, int | None] | None = None,
    replacement_card_updates: dict[str, dict[str, Any]] | None = None,
) -> None:
    """落库前给等正文的写入卡片写上正文回执：等正文的 create_file / write_chapter 结果带
    ``body_saved``（StreamAdapter 的保存回执里有字数才是 True）。历史回放据此说明正文是否保存，
    不再从「当时在等正文」推断「已保存」。只改落库的记录，不发帧，不影响计费。整章替换的卡片
    正常由 StreamAdapter 补发的结果带回执（已有 ``body_saved`` 时不改）；停止时在途提交或补存的
    替换没有补发结果，这里按这张卡自己的 tool_use_id 回执（``replacement_receipts``）写上
    ``mutation_applied`` 与 ``new_word_count``；同一章更早一次替换的文件级回执不算这一次的证据。"""
    for call in tool_calls:
        result = call.get("result")
        if (
            call.get("name") not in ("create_file", "write_chapter")
            or call.get("status") != "success"
            or not isinstance(result, dict)
            or "body_saved" in result
            or not (result.get("awaiting_body") is True or result.get("body_in_reply") is True)
        ):
            continue
        replaced = result.get("action") == "replaced"
        call_id = str(call.get("id") or "")
        card_extra: dict[str, Any] = {}
        if replaced:
            receipt = (replacement_receipts or {}).get(call_id)
            card_extra = (replacement_card_updates or {}).get(call_id) or {}
        else:
            receipt = receipts.get(str(result.get("id") or ""))
        saved = isinstance(receipt, int)
        stamped = {**result, "body_saved": saved}
        if replaced:
            stamped.pop("new_title", None)
            stamped["mutation_applied"] = saved
            if saved:
                stamped["new_word_count"] = receipt
                committed_title = card_extra.get("title")
                if isinstance(committed_title, str) and committed_title:
                    stamped["title"] = committed_title
                if isinstance(card_extra.get("undo"), dict):
                    stamped["undo"] = card_extra["undo"]
        call["result"] = stamped


async def _settle_stopped_writes(run_outcome: Any, adapter: Any | None) -> None:
    """截停后同时等正文的补存 / 在途提交与线程里的写工具，结果写进 run_outcome（保存回执、工具结果）。"""

    async def _capture() -> None:
        if adapter is not None:
            run_outcome.saved_file_word_counts.update(await adapter.settle_aborted_capture())

    await asyncio.gather(_capture(), run_outcome.settle_inflight_tool_writes(STREAM_FILE_SAVE_TIMEOUT_S))


def _project_has_ai_reply(session: Session, project_id: str) -> bool:
    """这部作品的任何会话里有过 AI 回复：之后再带第一条消息标记的，不按第一条处理。"""
    from models import ChatMessage, ChatSession

    return (
        session.exec(
            select(ChatMessage.id)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(ChatSession.project_id == project_id, ChatMessage.role == "assistant")
            .limit(1)
        ).first()
        is not None
    )


def _project_has_ai_reply_sync(project_id: str) -> bool:
    with create_session() as read_session:
        return _project_has_ai_reply(read_session, project_id)


def _load_project_type_sync(project_id: str) -> str | None:
    """用独立短 session 查作品类型（PG offload 分支，不碰请求级 session）。"""
    from models import Project

    with create_session() as read_session:
        project = read_session.get(Project, project_id)
        return project.project_type if project else None


class AgentService:
    """
    AI writing assistant service powered by the single loop agent + openai-agents-python.

    Features:
    - Open-ended conversation with DeepSeek via openai-agents-python
    - File CRUD via tool calling
    - Chat history persistence
    - Streaming responses
    - Intelligent context assembly
    """

    def __init__(
        self,
        context_assembler: ContextAssembler | None = None,
    ):
        """Initialize agent service."""
        self.context_assembler = context_assembler or get_context_assembler()
        log_with_context(
            logger,
            20,  # INFO
            "AgentService initialized with workflow orchestration",
        )

    # Pending out-of-band cleanups; shutdown waits for them (api.agent.wait_for_stream_cleanup).
    background_cleanup_tasks: set[asyncio.Task] = set()

    @staticmethod
    def _schedule_background_cleanup(
        coro: Any, *, description: str, session_id: str | None
    ) -> asyncio.Task:
        """Run async cleanup out of band when generator cancellation makes awaiting risky."""
        task = asyncio.create_task(coro)
        AgentService.background_cleanup_tasks.add(task)
        task.add_done_callback(AgentService.background_cleanup_tasks.discard)

        def _log_task_result(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except Exception as exc:  # pragma: no cover - depends on runtime cancellation timing
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Background cleanup task failed",
                    description=description,
                    session_id=session_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        task.add_done_callback(_log_task_result)
        return task

    def _resolve_or_create_chat_session_id(
        self,
        session: Session,
        *,
        project_id: str,
        user_id: str,
        requested_session_id: str | None,
    ) -> str:
        """
        Resolve the `session_id` used by:
        - SSE `session_started` event (frontend steering + continuity)
        - AgentRequestContext (task board)

        IMPORTANT: This value must exist in `chat_session.id` because
        `chat_message.session_id` has a FK to `chat_session.id`.
        """
        from models import ChatSession
        from services.chat_session_service import lock_active_chat_session

        lock_active_chat_session(session, user_id=user_id, project_id=project_id)
        normalized_requested = (requested_session_id or "").strip()

        # 1) If caller provided a runtime session id, we REQUIRE it to exist in
        #    `chat_session.id` because chat_message has an FK to it.
        #    - If it exists and belongs to this user+project, reuse it.
        #    - If it doesn't exist, create it with that exact id (so we can echo
        #      it back in `session_started` for steering continuity).
        #    - If it exists but belongs to someone else, ignore it and fall back
        #      to the latest active session (defense-in-depth).
        if normalized_requested:
            candidate = session.get(ChatSession, normalized_requested)

            if candidate is not None:
                if candidate.user_id != user_id or candidate.project_id != project_id:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Requested session_id does not belong to user/project; ignoring",
                        project_id=project_id,
                        user_id=user_id,
                        requested_session_id=normalized_requested,
                        candidate_user_id=candidate.user_id,
                        candidate_project_id=candidate.project_id,
                    )
                else:
                    other_actives = session.exec(
                        select(ChatSession)
                        .where(
                            and_(
                                ChatSession.project_id == project_id,
                                ChatSession.user_id == user_id,
                                ChatSession.is_active,
                                ChatSession.id != candidate.id,
                            )
                        )
                        .order_by(
                            desc(ChatSession.updated_at),
                            desc(ChatSession.created_at),
                            desc(ChatSession.id),
                        )
                    ).all()

                    deactivated = False
                    for stale in other_actives:
                        if stale.is_active:
                            stale.is_active = False
                            session.add(stale)
                            deactivated = True

                    if not candidate.is_active:
                        # 部分唯一索引 (user_id, project_id) WHERE is_active=true
                        # 非延迟约束，而 SQLAlchemy 对同一 mapper 的 UPDATE 按主键
                        # 排序下发；必须先把旧活跃会话的去激活刷到数据库，再激活
                        # candidate，否则 candidate 主键较小时会瞬时出现两行
                        # active 触发 IntegrityError。
                        if deactivated:
                            session.flush()
                        candidate.is_active = True

                    candidate.updated_at = utcnow()
                    session.add(candidate)

                    # 即使没有去激活/重新激活，也必须立即提交 updated_at 的刷新。
                    # SQLite 分支传入的是请求级 session（autoflush=True）：脏
                    # UPDATE 若留在 session 里，会被随后 SessionLoader 的第一条
                    # 读查询顺手 flush，pysqlite 在 DML 前隐式 BEGIN，于是这个
                    # 写事务连同 RESERVED 锁一直挂到整轮 SSE 结束才随历史保存
                    # 提交；期间工具在独立连接上的每次写入都会等满 busy_timeout
                    # 后报 "database is locked"。PostgreSQL 分支用
                    # `with create_session()` 包住本函数，未提交的修改在 close
                    # 时被直接丢弃，刷新从未落库。
                    resolved_id = candidate.id
                    session.commit()
                    return resolved_id

            else:
                # Compatibility note:
                # - Before artifact-ledger FK enforcement, the frontend/session layer
                #   used a runtime UUID that was NOT persisted to `chat_session.id`.
                #   Those legacy clients may still send such a session_id after a
                #   backend deploy while an active chat_session (with history) exists.
                #   In that case, we must NOT create a new chat_session (which would
                #   effectively "lose" the current active history in UI).
                active = latest_active_chat_session(
                    session, project_id=project_id, user_id=user_id
                )
                if active is not None:
                    log_with_context(
                        logger,
                        20,
                        "Requested session_id not found; falling back to active chat session",
                        project_id=project_id,
                        user_id=user_id,
                        requested_session_id=normalized_requested,
                        resolved_session_id=active.id,
                    )
                    return active.id

                chat_session = ChatSession(
                    id=normalized_requested,
                    user_id=user_id,
                    project_id=project_id,
                    title="AI 助手对话",
                    is_active=True,
                    message_count=0,
                )
                session.add(chat_session)
                session.commit()
                session.refresh(chat_session)
                return chat_session.id

        # 2) Otherwise, use the newest active session (if any).
        chat_session = latest_active_chat_session(
            session, project_id=project_id, user_id=user_id
        )
        if chat_session is not None:
            return chat_session.id

        # 3) If no active session exists yet, create one now so the round's
        #    messages and task board have a persisted session to belong to.
        chat_session = ChatSession(
            user_id=user_id,
            project_id=project_id,
            title="AI 助手对话",
            is_active=True,
            message_count=0,
        )
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)
        return chat_session.id

    def _resolve_or_create_chat_session_id_sync(
        self,
        *,
        project_id: str,
        user_id: str,
        requested_session_id: str | None,
    ) -> str:
        """Resolve/create chat session id using a fresh sync DB session."""
        with create_session() as sync_session:
            return self._resolve_or_create_chat_session_id(
                sync_session,
                project_id=project_id,
                user_id=user_id,
                requested_session_id=requested_session_id,
            )

    def _resolve_selected_skills(
        self,
        session: Session,
        *,
        user_id: str | None,
        selected_skill_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """
        Resolve skills the user explicitly selected for this message.

        只接受属于当前用户且启用中的技能，其余 ID 静默忽略。
        """
        if not user_id or not selected_skill_ids:
            return []

        selected: list[dict[str, Any]] = []
        skills = resolve_selected_skills(session, user_id, selected_skill_ids)
        # 显式选择的正文进 system prompt，会被每轮迭代重复发送：合计不超过技能内容预算的
        # 显式选择份额，超出部分截断，并提示模型用 read_skill_resource 分段续读。
        segments = split_selected_budget([skill.instructions for skill in skills])
        for skill, segment in zip(skills, segments, strict=True):
            selected.append({
                "id": skill.id,
                "name": skill.name,
                "instructions": segment.text,
                "instructions_tokens": segment.tokens,
                "instructions_next_offset": segment.next_offset,
                "instructions_total_chars": segment.total_chars,
                "source": skill.source,
                "resources": [
                    resource.path for resource in list_active_skill_resources(session, skill)
                ],
            })
        return selected

    async def _lookup_project_type(self, session: Session, project_id: str) -> str | None:
        """作品类型只用于第一条消息提示的类型名；查不到时提示里用通用说法。"""
        offloaded = should_offload_session_work(session)
        try:
            if offloaded:
                return await asyncio.to_thread(_load_project_type_sync, project_id)
            from models import Project

            project = session.get(Project, project_id)
            return project.project_type if project else None
        except Exception as exc:
            if not offloaded and hasattr(session, "rollback"):
                session.rollback()
            log_with_context(
                logger,
                30,  # WARNING
                "Failed to load project type for the first-message hint",
                project_id=project_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    async def _is_first_ai_conversation(self, session: Session, project_id: str) -> bool:
        """作品从没有过 AI 回复。查不到时按「不是第一条」处理：宁可不加提示，也不误导路由。"""
        offloaded = should_offload_session_work(session)
        try:
            if offloaded:
                return not await asyncio.to_thread(_project_has_ai_reply_sync, project_id)
            return not _project_has_ai_reply(session, project_id)
        except Exception as exc:
            if not offloaded and hasattr(session, "rollback"):
                session.rollback()
            log_with_context(
                logger,
                30,  # WARNING
                "Failed to check prior AI replies for the first-message hint",
                project_id=project_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    @staticmethod
    def _history_from_wire_prefix_meta(
        *,
        items: list[dict[str, Any]],
        last_message_id: str,
        history_meta: dict[str, Any],
    ) -> LoopHistory | None:
        """Reconstruct ``LoopHistory`` from compact metadata stored in ``wire_prefix_cache``."""
        raw_prompt_tokens = history_meta.get("last_prompt_tokens")
        raw_author_messages = history_meta.get("author_messages")
        if not isinstance(raw_prompt_tokens, int) or raw_prompt_tokens < 0:
            return None
        if not isinstance(raw_author_messages, list) or not all(
            isinstance(m, str) for m in raw_author_messages
        ):
            return None
        raw_skipped_tokens = history_meta.get("compaction_skipped_at_tokens", 0)
        raw_skipped_reason = history_meta.get("compaction_skipped_reason")
        raw_truncations = history_meta.get("trailing_output_truncations", 0)
        raw_archived_calls = history_meta.get("archived_call_ids") or []
        raw_archived_files = history_meta.get("archived_file_ids") or []
        if not isinstance(raw_skipped_tokens, int) or raw_skipped_tokens < 0:
            return None
        if not isinstance(raw_truncations, int) or raw_truncations < 0:
            return None
        if not isinstance(raw_archived_calls, list) or not isinstance(raw_archived_files, list):
            return None
        return LoopHistory(
            items=items,
            last_prompt_tokens=raw_prompt_tokens,
            last_message_id=last_message_id,
            compacted=bool(history_meta.get("compacted", False)),
            author_messages=list(raw_author_messages),
            compaction_skipped_at_tokens=raw_skipped_tokens,
            compaction_skipped_reason=(
                raw_skipped_reason
                if isinstance(raw_skipped_reason, str) and raw_skipped_reason
                else None
            ),
            trailing_output_truncations=raw_truncations,
            archived_call_ids={str(cid) for cid in raw_archived_calls if isinstance(cid, str) and cid},
            archived_file_ids={str(fid) for fid in raw_archived_files if isinstance(fid, str) and fid},
        )

    def _prepare_loop_turn(
        self,
        session: Session,
        *,
        project_id: str,
        user_id: str | None,
        chat_session_id: str | None,
        selected_skill_ids: list[str] | None,
        message: str,
        metadata: dict[str, Any] | None,
        language: str,
        carried_compaction: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
        ui_action: UIAction | None = None,
        selected_text: str | None = None,
        expected_provider_key: str | None = None,
        expected_tools_sha256: str | None = None,
        expected_reasoning_effort: str | None = None,
    ) -> tuple[LoopHistory, LoopTurnContext, list[dict[str, Any]], str]:
        """单循环 agent 这一轮的输入：跨轮历史、本轮上下文、作者选的技能、系统提示。

        温缓存优先路径（P0-3）：在触发 ``load_loop_history`` 扫库解压 ``chat_message`` TOAST 之前，
        先用 3 列索引查询（``id, role, created_at``）取最新消息 id 并查询 ``wire_prefix_cache``；
        命中且携带完整 ``history_meta`` 时直接复用缓存历史，完全跳过 ``load_loop_history`` 扫库。
        """
        from models import ChatMessage, ChatSession

        history = LoopHistory()
        owned_session_id: str | None = None
        if cancel_event is not None and cancel_event.is_set():
            return history, LoopTurnContext(), [], ""
        selected_skills = self._resolve_selected_skills(
            session, user_id=user_id, selected_skill_ids=selected_skill_ids
        )
        if cancel_event is not None and cancel_event.is_set():
            return history, LoopTurnContext(), selected_skills, ""
        skill_catalog = get_skill_context_injector().build_skill_catalog(session, user_id)
        system_prompt = MessageManager(project_id=project_id, user_id=user_id).build_system_prompt(
            session=session, language=language, skill_catalog=skill_catalog
        )
        if cancel_event is not None and cancel_event.is_set():
            return history, LoopTurnContext(), selected_skills, system_prompt

        prior_context_fingerprints: dict[str, Any] | None = None
        wire_prefix_hit = False
        if user_id and chat_session_id:
            chat_session = session.get(ChatSession, chat_session_id)
            if chat_session is not None and chat_session.user_id == user_id and chat_session.project_id == project_id:
                owned_session_id = chat_session_id
                cached_entry = None
                latest_message_id: str | None = None
                if not carried_compaction and expected_provider_key:
                    latest_row = session.exec(
                        select(ChatMessage.id, ChatMessage.role, ChatMessage.created_at)
                        .where(
                            and_(
                                ChatMessage.session_id == chat_session_id,
                                ChatMessage.role.in_(("user", "assistant")),
                            )
                        )
                        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
                        .limit(1)
                    ).first()
                    latest_message_id = str(latest_row[0]) if latest_row is not None else None
                    if latest_message_id:
                        cached_entry = load_wire_prefix_entry(
                            session_id=chat_session_id,
                            project_id=project_id,
                            user_id=user_id,
                            last_message_id=latest_message_id,
                            system_prompt=system_prompt,
                            provider_key=expected_provider_key,
                            tools_sha256=expected_tools_sha256,
                            reasoning_effort=expected_reasoning_effort,
                        )

                warm_history: LoopHistory | None = None
                if cached_entry is not None and latest_message_id is not None and cached_entry.history_meta is not None:
                    warm_history = self._history_from_wire_prefix_meta(
                        items=cached_entry.items,
                        last_message_id=latest_message_id,
                        history_meta=cached_entry.history_meta,
                    )

                if warm_history is not None:
                    history = warm_history
                    wire_prefix_hit = True
                    prior_context_fingerprints = cached_entry.context_fingerprints if cached_entry else None
                else:
                    history = load_loop_history(
                        session,
                        project_id=project_id,
                        user_id=user_id,
                        chat_session_id=chat_session_id,
                        carried_metadata=carried_compaction,
                    )
                    if cached_entry is not None and history.last_message_id == latest_message_id:
                        history.items = cached_entry.items
                        wire_prefix_hit = True
                        prior_context_fingerprints = cached_entry.context_fingerprints
                if wire_prefix_hit:
                    # Guard against a prior cold-replay -> warm-cache hop where cached wire items
                    # themselves contain persist-slimmed archived_note entries without full content.
                    slimmed_only_fids = _wire_prefix_slimmed_only_file_ids(history.items)
                    if slimmed_only_fids:
                        history.archived_file_ids.update(slimmed_only_fids)
                    # Defense-in-depth: never allow prior full_file_hashes to emit an "unchanged"
                    # pointer for any file whose history text is already archived/slimmed out.
                    if history.archived_file_ids and isinstance(prior_context_fingerprints, dict):
                        prior_hashes = prior_context_fingerprints.get("full_file_hashes")
                        if isinstance(prior_hashes, dict) and any(
                            fid in prior_hashes for fid in history.archived_file_ids
                        ):
                            prior_context_fingerprints = {
                                **prior_context_fingerprints,
                                "full_file_hashes": {
                                    k: v
                                    for k, v in prior_hashes.items()
                                    if k not in history.archived_file_ids
                                },
                            }
        if cancel_event is not None and cancel_event.is_set():
            return history, LoopTurnContext(), selected_skills, system_prompt
        turn = build_loop_turn_context(
            session,
            project_id=project_id,
            user_id=user_id,
            metadata=metadata,
            language=language,
            author_message=message,
            selected_skills=selected_skills or None,
            context_assembler=self.context_assembler,
            chat_session_id=owned_session_id,
            carried_compaction=carried_compaction,
            cancel_event=cancel_event,
            ui_action=ui_action,
            selected_text=selected_text,
            prior_context_fingerprints=prior_context_fingerprints,
            allow_slimmed_read_snapshots=wire_prefix_hit,
        )
        return history, turn, selected_skills, system_prompt

    def _prepare_loop_turn_sync(self, **kwargs: Any) -> tuple[LoopHistory, LoopTurnContext, list[dict[str, Any]], str]:
        with create_session() as sync_session:
            return self._prepare_loop_turn(sync_session, **kwargs)

    async def process_stream(
        self,
        project_id: str,
        user_id: str | None,
        message: str,
        session: Session,
        session_id: str | None = None,
        selected_text: str | None = None,
        metadata: dict[str, Any] | None = None,
        language: str | None = None,
        selected_skill_ids: list[str] | None = None,
        ui_action: UIAction | None = None,
        run_report: dict[str, Any] | None = None,
        run_outcome: RunOutcome | None = None,
        account_run_slot: AccountRunSlot | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Process user message with streaming response.

        Args:
            project_id: Project ID (UUID)
            user_id: User ID (UUID, for chat history)
            message: User's message
            session: Database session
            selected_text: Optional selected text for context
            metadata: Optional metadata (current_file_id, etc.)
            language: Language preference (zh/en)
            selected_skill_ids: Skills the user explicitly selected for this message
            run_report: Optional dict the caller owns; filled at the end of the run
                with model, token usage, model calls, LLM time and stop reason so the
                API layer can write one summary log line together with billing.
            run_outcome: Optional handle the caller owns (agent.core.round_outcome):
                the caller sets ``stop_kind`` before cancelling a stopped /
                disconnected run; this method resolves the persisted assistant
                message id on it once the round's history is saved.
            account_run_slot: The account's task slot the caller acquired for this run.
                Adopted once this generator's cleanup is guaranteed to run; released when
                the run has really ended (after the cancellation path's partial save too).

        Yields:
            SSE event strings
        """
        start_time = utcnow()
        # 作者发出这条消息的时间：user 行落库在回合结束时，created_at 要用它，刷新后时间才不跳。
        user_sent_at = start_time.replace(tzinfo=None)
        run_started_monotonic = time.monotonic()
        message_preview = message[:100] + "..." if len(message) > 100 else message

        log_with_context(
            logger,
            20,  # INFO
            "Agent process_stream started (workflow)",
            project_id=project_id,
            user_id=user_id,
            message_length=len(message),
            message_preview=message_preview,
            has_selected_text=selected_text is not None,
            ui_action=ui_action,
            language=language,
            current_file_id=metadata.get("current_file_id") if metadata else None,
        )

        # Resolve chat-session id for continuity + FK safety.
        # (Historically this was a standalone runtime UUID; persisted rows
        # require `session_id` to exist in `chat_session.id`.)
        requested_session_id = session_id
        if user_id:
            if should_offload_session_work(session):
                session_id = await asyncio.to_thread(
                    self._resolve_or_create_chat_session_id_sync,
                    project_id=project_id,
                    user_id=user_id,
                    requested_session_id=requested_session_id,
                )
            else:
                session_id = self._resolve_or_create_chat_session_id(
                    session,
                    project_id=project_id,
                    user_id=user_id,
                    requested_session_id=requested_session_id,
                )
        else:
            session_id = requested_session_id or str(uuid.uuid4())

        # Claim the session's single generation slot atomically. Chat history
        # rows have no run/turn sequence and message_count is updated per run,
        # so concurrent writers to one session would interleave history and
        # lose increments. Different chat sessions still run concurrently.
        steering_run_id = uuid.uuid4().hex
        steering_queue = await create_steering_queue_async(
            session_id,
            user_id,
            run_id=steering_run_id,
        )

        # 周期心跳：独立 task 每隔 _RUN_HEARTBEAT_INTERVAL_S 秒续期本 run 的持有，
        # 长时间的模型调用 / 工具执行期间也不会被当成僵尸回收；进程被杀时心跳
        # 随之停止，残留持有在 _RUN_HEARTBEAT_TTL_S 后自动失效。
        async def _run_heartbeat_loop() -> None:
            while True:
                await asyncio.sleep(_RUN_HEARTBEAT_INTERVAL_S)
                try:
                    await heartbeat_steering_run_async(session_id, steering_run_id)
                except Exception as exc:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Steering run heartbeat failed",
                        session_id=session_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

        run_heartbeat_task = asyncio.create_task(_run_heartbeat_loop())

        def _stop_run_heartbeat() -> None:
            if not run_heartbeat_task.done():
                run_heartbeat_task.cancel()

        def _record_live_model(model_name: str) -> None:
            if run_report is not None and model_name:
                run_report["model"] = model_name

        if run_report is not None:
            run_report.setdefault("model", primary_model_name())
            if session_id:
                run_report.setdefault("session_id", session_id)

        run_meter = AgentRunMeter(
            timing_origin=run_started_monotonic,
            on_served_model=_record_live_model if run_report is not None else None,
        )
        context_timing_recorded = False

        # Initialize tracking variables before try block for exception safety
        all_tool_calls: list[dict[str, Any]] = []
        tool_call_index_by_id: dict[str, int] = {}
        consumed_steering: list[str] = []
        assistant_response = ""
        post_file_whitespace_pending = False
        post_file_whitespace_buf = ""
        assistant_stop_reason: str | None = None
        assistant_usage: dict[str, Any] | None = None
        assistant_status_cards: list[dict[str, Any]] = []
        assistant_display_events: list[dict[str, Any]] = []

        # 本轮开始前写下的压缩点，随本轮 assistant 行落库（agent/loop/compaction.py）。
        loop_compaction_record: dict[str, Any] | None = None
        # 本轮 Stage 1 tool_archive 归档的文件 id（写回 wire_prefix_cache history_meta 使用）。
        stage1_archived_file_ids: set[str] = set()
        # 本轮没压缩成时的记录：下一轮据此退避，不每轮重付摘要调用。
        loop_compaction_skipped: dict[str, Any] | None = None
        # 重试时删掉的那条作者消息上的压缩记录：本轮历史照它重建，没有新记录时随本轮落库。
        retry_carried_compaction: dict[str, Any] = {}

        def _loop_extra_metadata() -> dict[str, Any] | None:
            """单循环 agent 的 assistant 行：引擎标记、最后一次模型调用的输入 token、压缩点。"""
            return {
                "engine": LOOP_ENGINE_METADATA_VALUE,
                "prompt_tokens": run_meter.last_prompt_tokens or None,
                "compaction": loop_compaction_record,
                "compaction_skipped": loop_compaction_skipped,
            }

        def _loop_user_row_metadata() -> dict[str, Any] | None:
            """这一轮没有 assistant 行时，压缩点 / 没压缩成的记录写在作者那条消息上（history 两处都读）。"""
            record = {
                key: value
                for key, value in (
                    ("compaction", loop_compaction_record),
                    ("compaction_skipped", loop_compaction_skipped),
                )
                if value is not None
            }
            return record or None

        display_text_run_type: str | None = None
        pending_done_payload: dict[str, Any] | None = None
        had_stream_error = False
        request_failed = False
        stream_cancelled = False
        history_saved = False
        stored_wire_prefix = False
        prepare_cancel_event = threading.Event()
        history_save_task: asyncio.Task | None = None
        cancellation_save_task: asyncio.Task | None = None

        # Steering callback for the agent loop. 定义在 try 之前：取消/失败
        # 路径的兜底 drain 也依赖它，不能等到工作流启动处才可用。
        async def get_steering_messages(*, seal: bool = False):
            """Get pending steering messages for agent loop.

            seal=True 是本 run 释放队列前的最后一次 drain：取出的同时封口，此后
            /agent/steer 对本 run 回 404，不会再确认一条没人消费的引导。
            """
            if seal:
                steering_pending = await drain_and_seal_steering_async(
                    session_id, steering_run_id
                )
            else:
                steering_pending = await steering_queue.get_pending()
            payload = [{"id": m.id, "content": m.content} for m in steering_pending]
            # Record what the run actually consumed so it can be persisted to
            # chat history (otherwise node-boundary steering is lost on the
            # next request).
            consumed_steering.extend(
                str(item["content"]) for item in payload if item.get("content")
            )
            return payload

        def _has_assistant_payload() -> bool:
            """本轮是否产生过值得写成一条 assistant 消息的内容。

            取消/失败若发生在模型吐出第一个 token 之前，assistant_response 仍是
            初始空串：此时落库只会得到一条前端永远不会渲染的空消息，却把
            ChatSession.message_count 多加 2，并占掉「最近消息」窗口的一格，
            把更早的真实消息挤出去。
            """
            return bool(
                assistant_response.strip()
                or all_tool_calls
                or assistant_status_cards
                # 「对话已压缩」标记与纯空白文本块本身都不是有效回复：没有实质产出的一轮只落作者那条消息。
                # 单循环轮次的 thinking_content 已完整记入 assistant_display_events（不再双写 reasoning_content），
                # 仅思考的轮次仍经 has_substantive_display_events 正常落库。
                or has_substantive_display_events(assistant_display_events)
            )

        def _append_display_text(
            event_type: str, content: Any, *, response_chunk: str | None = None
        ) -> None:
            """Append one display text chunk, coalescing only an uninterrupted run."""
            nonlocal assistant_response, display_text_run_type
            if not isinstance(content, str) or not content:
                return
            if "\x00" in content:
                content = content.replace("\x00", "")
                if not content:
                    return
            continuing_run = (
                display_text_run_type == event_type
                and assistant_display_events
                and assistant_display_events[-1].get("type") == event_type
            )
            if continuing_run:
                assistant_display_events[-1]["content"] += content
            else:
                assistant_display_events.append({"type": event_type, "content": content})
            if event_type == "content":
                chunk_for_response = (
                    content if response_chunk is None else response_chunk.replace("\x00", "")
                )
                assistant_response += chunk_for_response
            display_text_run_type = event_type

        def _append_user_messages_sync(
            texts: list[str],
            *,
            first_created_at: datetime | None = None,
            first_metadata: dict[str, Any] | None = None,
        ) -> int:
            """把若干条文本作为 user 行追加进当前会话（不重写整轮历史）。

            两个用途：
            1. 整轮历史已经落库之后才 drain 出来的 steering —— 重走整轮保存会把
               user/assistant 正文写第二遍，只能单独追加；
            2. 完全没有 assistant 内容的取消/失败路径 —— 只保留用户消息与已消费
               的 steering。
            message_count 按实际写入的行数递增，避免计数漂移。
            first_created_at：texts[0] 是本轮作者发的那条消息时传入发出时间；晚到的
            steering 不传。first_metadata 同理只写在 texts[0] 上（本轮的压缩记录）。
            """
            from models import ChatMessage, ChatSession

            cleaned = [text for text in texts if isinstance(text, str) and text.strip()]
            if not cleaned or not session_id:
                return 0

            with create_session() as append_session:
                chat_session = append_session.get(ChatSession, session_id)
                if chat_session is None:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Chat session not found; skip appending user messages",
                        session_id=session_id,
                        project_id=project_id,
                        user_id=user_id,
                    )
                    return 0
                if (
                    chat_session.user_id != user_id
                    or chat_session.project_id != project_id
                ):
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Chat session does not belong to project/user; skip appending",
                        session_id=session_id,
                        project_id=project_id,
                        user_id=user_id,
                    )
                    return 0

                for index, text in enumerate(texts):
                    if not (isinstance(text, str) and text.strip()):
                        continue
                    if "\x00" in text:
                        text = text.replace("\x00", "")
                    sent_at = first_created_at if index == 0 else None
                    row_metadata = first_metadata if index == 0 else None
                    append_session.add(
                        ChatMessage(
                            session_id=chat_session.id,
                            role="user",
                            content=text,
                            **({"created_at": sent_at} if sent_at else {}),
                            **(
                                {"message_metadata": json.dumps(row_metadata, ensure_ascii=False)}
                                if row_metadata
                                else {}
                            ),
                        )
                    )
                chat_session.message_count += len(cleaned)
                chat_session.updated_at = utcnow()
                append_session.commit()

                if isinstance(first_metadata, dict):
                    compaction = valid_compaction(first_metadata)
                    if compaction is not None:
                        from .core.chat_storage import archive_pre_compaction_tool_calls_safe

                        archive_pre_compaction_tool_calls_safe(
                            append_session,
                            chat_session_id=session_id,
                            covers_through_message_id=compaction["covers_through_message_id"],
                        )

            return len(cleaned)

        def _drop_unanswered_retry_message_sync() -> bool:
            """重试上一条没得到回复的消息：先删掉会话末尾那条同样的用户消息。

            上一次请求出错时只落库了用户消息（没有助手回复）；重试会在本轮结束时
            再写一条，刷新后就是几条一样的用户消息、没有回复，模型的历史里也会看到
            同一句话两遍。只删最末一条、内容完全相同、之后没有任何消息的用户行。
            """
            from sqlmodel import desc, select

            from models import ChatMessage, ChatSession

            if not session_id:
                return False
            with create_session() as retry_session:
                chat_session = retry_session.get(ChatSession, session_id)
                if (
                    chat_session is None
                    or chat_session.user_id != user_id
                    or chat_session.project_id != project_id
                ):
                    return False
                last = retry_session.exec(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
                    .limit(1)
                ).first()
                if last is None or last.role != "user" or last.content != message:
                    return False
                # 同一时刻还有别的消息（时间戳相同、排序不确定）就不动，宁可留一条重复。
                tied = retry_session.exec(
                    select(ChatMessage.id)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.created_at >= last.created_at,
                        ChatMessage.id != last.id,
                    )
                    .limit(1)
                ).first()
                if tied is not None:
                    return False
                # 没得到回复的一轮若已压缩（或记下没压缩成），记录在这条消息上：删行前接过来，重试不重付摘要。
                last_metadata = row_metadata(last)
                carried = {
                    key: last_metadata[key]
                    for key in ("compaction", "compaction_skipped")
                    if isinstance(last_metadata.get(key), dict)
                }
                retry_session.delete(last)
                chat_session.message_count = max(0, chat_session.message_count - 1)
                chat_session.updated_at = utcnow()
                retry_session.commit()
                retry_carried_compaction.update(carried)
                if carried:
                    invalidate_wire_prefix(session_id)
                return True

        async def _drain_late_steering(*, failure_message: str) -> list[str]:
            """Drain and seal once before cleanup, returning only newly drained text."""
            already_consumed = len(consumed_steering)
            try:
                await get_steering_messages(seal=True)
            except Exception as exc:
                log_with_context(
                    logger,
                    30,  # WARNING
                    failure_message,
                    session_id=session_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            return list(consumed_steering[already_consumed:])

        async def _finish_history_write(write: Any, *args: Any) -> Any:
            """Keep finalization ownership until an uncancellable SQL worker finishes."""
            write_task = asyncio.create_task(asyncio.to_thread(write, *args))
            try:
                return await asyncio.shield(write_task)
            except asyncio.CancelledError:
                # Cancelling the awaiter cannot stop to_thread. Repeated cancellation
                # must not release ownership while that worker can still commit.
                while not write_task.done():
                    try:
                        await asyncio.wait({write_task})
                    except asyncio.CancelledError:
                        continue
                if not write_task.cancelled():
                    write_task.exception()
                raise

        # 取消/失败路径补存历史时，带上 stream adapter 已累计的用量（已完成的
        # agent run 与路由），否则这部分用量在对话展示里直接丢失。
        # 计费以 llm_usage_event 账本为准，这里只影响对话内的用量展示。
        usage_source: dict[str, Any] = {}

        def _accumulated_usage() -> dict[str, Any] | None:
            adapter = usage_source.get("adapter")
            if adapter is None:
                return None
            usage = adapter.get_last_message_metadata().get("usage")
            return usage if isinstance(usage, dict) and usage else None

        def _current_receipts() -> dict[str, int | None]:
            receipts = dict(run_outcome.saved_file_word_counts) if run_outcome is not None else {}
            adapter = usage_source.get("adapter")
            if adapter is not None:
                receipts.update(adapter.get_saved_file_word_counts())
            return receipts

        def _current_replacement_receipts() -> dict[str, int | None]:
            adapter = usage_source.get("adapter")
            return adapter.get_replacement_receipts() if adapter is not None else {}

        def _current_replacement_card_updates() -> dict[str, dict[str, Any]]:
            adapter = usage_source.get("adapter")
            return adapter.get_replacement_card_updates() if adapter is not None else {}

        def _save_partial_history_sync(
            stop_reason: str | None = None,
            persist_empty_assistant: bool = False,
        ) -> str | None:
            """用独立 session 落库部分历史（取消/失败路径，绕开共享 session）。

            persist_empty_assistant：作者停止 / 断线的一轮即使什么都没产出也写一条
            助手消息（带 stop_reason），刷新后作者才看得到这一轮已停止、是否计入。
            返回助手消息 id（没写助手消息时为 None）。
            """
            if not _has_assistant_payload() and not persist_empty_assistant:
                # 空 assistant 不落库，只保留用户消息与已消费的 steering。
                _append_user_messages_sync(
                    [message, *consumed_steering],
                    first_created_at=user_sent_at,
                    first_metadata=_loop_user_row_metadata(),
                )
                return None

            _stamp_body_receipts(
                all_tool_calls,
                _current_receipts(),
                _current_replacement_receipts(),
                _current_replacement_card_updates(),
            )
            recovery_manager = MessageManager(
                project_id=project_id,
                user_id=user_id,
            )
            with create_session() as recovery_session:
                return recovery_manager._save_messages_with_session(
                    recovery_session,
                    session_id,
                    message,
                    assistant_response,
                    all_tool_calls if all_tool_calls else None,
                    None,
                    assistant_status_cards=assistant_status_cards or None,
                    steering_messages=consumed_steering or None,
                    assistant_display_events=normalize_assistant_display_events(assistant_display_events),
                    assistant_usage=_accumulated_usage(),
                    assistant_stop_reason=stop_reason,
                    persist_empty_assistant=persist_empty_assistant,
                    user_created_at=user_sent_at,
                    assistant_extra_metadata=_loop_extra_metadata(),
                )

        if account_run_slot is not None:
            account_run_slot.adopt()

        try:
            lang = (language or "").strip().lower() or "zh"
            force_en = lang.startswith("en")

            # Emit session_started event FIRST
            yield session_started_event(session_id).to_sse()

            # metadata.generation_mode 是旧客户端仍会发送的字段：照常接受，不再读取。

            # 重试上一条没得到回复的消息时，复用它而不是再多一条一样的用户消息。
            if user_id and metadata and metadata.get(RETRY_UNANSWERED_METADATA_KEY) is True:
                try:
                    await asyncio.to_thread(_drop_unanswered_retry_message_sync)
                except Exception as exc:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Failed to reuse the unanswered user message on retry",
                        session_id=session_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

            # Assemble intelligent context
            yield thinking_event(
                "Assembling context..." if force_en else "正在组装上下文..."
            ).to_sse()
            expected_provider_key: str | None = None
            expected_tools_sha = tools_schema_sha256()
            expected_reasoning_effort = effective_reasoning_effort()
            if user_id and session_id and not retry_carried_compaction:
                try:
                    ordered_providers = await llm_failover.aordered_providers(session_id)
                    if ordered_providers:
                        expected_provider_key = provider_cache_key(ordered_providers[0])
                        expected_reasoning_effort = effective_reasoning_effort(ordered_providers[0])
                        if run_report is not None and not run_meter.last_served_model and ordered_providers[0].model:
                            run_report["model"] = ordered_providers[0].model
                except Exception:  # noqa: BLE001 - fail open to DB history
                    expected_provider_key = None
            loop_kwargs: dict[str, Any] = {
                "project_id": project_id,
                "user_id": user_id,
                "chat_session_id": session_id if user_id else None,
                "selected_skill_ids": selected_skill_ids,
                "message": message,
                "metadata": metadata,
                "language": lang,
                "carried_compaction": retry_carried_compaction or None,
                "cancel_event": prepare_cancel_event,
                "ui_action": ui_action,
                "selected_text": selected_text,
                "expected_provider_key": expected_provider_key,
                "expected_tools_sha256": expected_tools_sha,
                "expected_reasoning_effort": expected_reasoning_effort,
            }
            if should_offload_session_work(session):
                loop_history, loop_turn, selected_skills, system_prompt = await asyncio.to_thread(
                    self._prepare_loop_turn_sync, **loop_kwargs
                )
            else:
                loop_history, loop_turn, selected_skills, system_prompt = self._prepare_loop_turn(
                    session, **loop_kwargs
                )
            if loop_turn.items:
                yield context_event(items=loop_turn.items, token_count=0).to_sse()
            for selected in selected_skills:
                yield skill_matched_event(
                    skill_id=selected["id"],
                    skill_name=selected["name"],
                    matched_trigger="selected",
                ).to_sse()

            first_message_project_type = None
            if (
                isinstance(metadata, dict)
                and metadata.get("entry") in FIRST_MESSAGE_ENTRIES
                and not any(item.get("role") == "assistant" for item in loop_history.items)
                and await self._is_first_ai_conversation(session, project_id)
            ):
                first_message_project_type = await self._lookup_project_type(session, project_id) or ""

            base_user_content = message
            if selected_text:
                base_user_content += f"\n\n{'Selected text' if force_en else '选中的文本'}:\n{selected_text}"
            if first_message_project_type is not None:
                base_user_content += (
                    f"\n\n{'Context' if force_en else '上下文'}:\n"
                    + first_message_hint(first_message_project_type, force_en)
                )
            user_content = (
                SessionLoader.attach_working_set_to_user_content(base_user_content, loop_turn.text)
                if loop_turn.text
                else base_user_content
            )
            effective_context_fingerprints = (
                {
                    "project_files_sha256": loop_turn.context_fingerprints.get("project_files_sha256", ""),
                    "full_file_hashes": dict(loop_turn.context_fingerprints.get("full_file_hashes") or {}),
                    "author_edits_sha256": loop_turn.context_fingerprints.get("author_edits_sha256", ""),
                    "_author_edits_file_ids": list(
                        loop_turn.context_fingerprints.get("_author_edits_file_ids") or ()
                    ),
                }
                if loop_turn.context_fingerprints
                else {}
            )

            def _restore_full_turn_context() -> None:
                """Stage 2 摘要或历史滑窗截断清掉前几轮 <turn_context> 时，恢复本轮完整上下文。"""
                nonlocal user_content, effective_context_fingerprints
                if loop_turn.full_text:
                    user_content = SessionLoader.attach_working_set_to_user_content(
                        base_user_content, loop_turn.full_text
                    )
                    writing_state["user_message"] = user_content
                if loop_turn.context_fingerprints:
                    effective_context_fingerprints = {
                        "project_files_sha256": loop_turn.context_fingerprints.get("project_files_sha256", ""),
                        "full_file_hashes": dict(
                            loop_turn.context_fingerprints.get("_current_full_file_hashes") or {}
                        ),
                        "author_edits_sha256": loop_turn.context_fingerprints.get("author_edits_sha256", ""),
                        "_author_edits_file_ids": list(
                            loop_turn.context_fingerprints.get("_author_edits_file_ids") or ()
                        ),
                    }
            message_manager = MessageManager(project_id=project_id, user_id=user_id)

            yield thinking_event(
                "Thinking..." if force_en else "正在思考..."
            ).to_sse()

            log_with_context(
                logger,
                20,  # INFO
                "Starting workflow streaming",
                project_id=project_id,
                user_id=user_id,
                history_items=len(loop_history.items),
            )

            # 进入工作流前结束请求级 session 上的事务。SQLite 分支前面的会话
            # 解析、历史加载、技能目录都跑在它上面，而工具在独立连接上写库：
            # 只要这里残留一个已 flush 的写事务，SQLite 的库级写锁就会被攥满
            # 整轮 SSE，工具写入全部 "database is locked"。只读时 commit 不向
            # 数据库发任何语句；PostgreSQL 分支此时请求级 session 没有事务
            # （路由层已 rollback，前置步骤都 offload 到独立 session），不进分支。
            if session.in_transaction():
                session.commit()

            # Build strong-typed per-request context shared with StreamAdapter, loop engine,
            # SDK RunContextWrapper[AgentRequestContext], and MCP tool worker threads.
            # 始终传 session=None + create_session_func=create_session，每个工具调用在
            # req_ctx.session_scope() 内自建短生命周期独立 Session、用完即关。
            request_context = AgentRequestContext(
                session=None,
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
                create_session_func=create_session,
                # 显式选择的正文已占用的技能内容预算
                skill_tokens_used=sum(
                    int(selected.get("instructions_tokens") or 0) for selected in selected_skills
                ),
                # 作者原话标识应用内对话并提供上下文，不授予删除、整份覆盖或改名权限。
                author_message=message,
                author_steering=consumed_steering,
                # 当轮经服务端校验的结构化 UI 动作（例如 ui_action="natural_polish" 时允许
                # read_skill_resource 读取官方 PublicSkill('去AI味') 资源）。
                ui_action=ui_action,
                # 本轮开始时模型看到的正文：edit_file 拒绝基于旧正文的修改（作者生成期间又改了）。
                read_snapshots=loop_turn.read_snapshots,
                # 截停后还在线程里的写工具登记在 run_outcome 上，收尾时等它们的真实结果。
                inflight_tool_writes=run_outcome.inflight_tool_writes if run_outcome is not None else None,
            )
            # 此前轮次经 Stage 1 tool_archive 归档且未再重新读/写的文件，撤销跨轮会话快照结转，
            # 防止 edit_file 误以为已归档的旧章节全文仍在模型视野内。
            if loop_history.archived_file_ids and loop_turn.session_carried_snapshot_ids:
                request_context.drop_initial_read_snapshots(
                    loop_history.archived_file_ids & loop_turn.session_carried_snapshot_ids
                )

            # Build WritingState for workflow execution
            writing_state: WritingState = {
                "user_message": user_content,
                # The author's raw message (exclude selected_text / metadata decorations)
                "router_message": message,
                "project_id": project_id,
                "user_id": user_id or "",
                "session_id": session_id,
                # 作者界面语言（zh / en…）：作者可见文案按它选英文或中文。
                "language": lang,
                "system_prompt": system_prompt,
                "run_meter": run_meter,
                "run_outcome": run_outcome,
                "request_context": request_context,
            }
            writing_state["history_items"] = loop_history.items
            # 前几轮连续停在输出上限的次数（来自重建的跨轮历史）。
            writing_state["previous_output_truncations"] = loop_history.trailing_output_truncations

            # Create stream adapter for SSE conversion
            stream_adapter = create_stream_adapter(
                project_id=project_id,
                user_id=user_id,
                process_file_markers=True,
                # 流开头已为显式选择的技能发过 skill_matched，load_skill 它们时不再重复发
                matched_skill_ids=[selected["id"] for selected in selected_skills],
                run_meter=run_meter,
                run_outcome=run_outcome,
                language=lang,
                request_context=request_context,
            )
            usage_source["adapter"] = stream_adapter

            with request_context.bind_cost_budget_user():
                try:
                    # Process through workflow stream
                    run_meter.record_stage("prepare_context", (time.monotonic() - run_started_monotonic) * 1000)
                    context_timing_recorded = True
                    if run_report is not None:
                        run_report.update(run_meter.timing_report())
                    compaction_usage: dict[str, int] | None = None
                    current_turn_tokens = estimate_provider_text_tokens(user_content)
                    if should_compact(loop_history, extra_tokens=current_turn_tokens):
                        # 两阶段轮间压缩（不在工具循环中途）：
                        # Stage 1（零 LLM 确定性 tool_archive）：优先归档早于最近 N 轮的大体积只读工具输出；
                        # 满足节省量与阈值准入时零延迟放行，不触发摘要模型调用也不发阻塞式「正在压缩对话…」卡片。
                        # Stage 2（LLM 交接摘要）：Stage 1 未达准入时兜底，并把 Stage 1 已归档的更小历史送入摘要调用。
                        pre_archive: ToolArchiveResult | None = None
                        outcome: CompactionOutcome | None = None
                        if agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_ENABLED:
                            stage1_started = time.monotonic()
                            pre_archive = archive_old_tool_results(
                                loop_history,
                                turn_input_tokens=current_turn_tokens,
                            )
                            if pre_archive.admitted and pre_archive.record is not None:
                                run_meter.record_stage(
                                    "compaction", (time.monotonic() - stage1_started) * 1000
                                )
                                outcome = CompactionOutcome(
                                    record=pre_archive.record,
                                    items=pre_archive.items,
                                    usage=None,
                                    archived_file_ids=set(pre_archive.archived_file_ids),
                                    archived_unidentified_query_files=pre_archive.archived_unidentified_query_files,
                                )
                                log_with_context(
                                    logger,
                                    20,  # INFO
                                    "Loop history compacted via Stage 1 tool_archive (zero LLM calls)",
                                    session_id=session_id,
                                    archived_count=pre_archive.archived_count,
                                    before_tokens=pre_archive.before_tokens,
                                    after_tokens=pre_archive.after_tokens,
                                    projected_after_tokens=pre_archive.projected_after_tokens,
                                    saved_tokens=pre_archive.saved_tokens,
                                    turn_input_tokens=current_turn_tokens,
                                )

                        emitted_compaction_progress = False
                        if outcome is None:
                            # Stage 2 摘要前发「正在压缩对话…」，成功后变成会话里的「对话已压缩」标记
                            # （记进 display_events，刷新后在同一位置回放，摘要读本轮的 compaction 记录）；
                            # 没压缩成时只发一条收起它的进度提示，不落库。
                            yield thinking_event(
                                "Compacting conversation…" if force_en else "正在压缩对话…",
                                step=HISTORY_COMPACTION_STEP,
                            ).to_sse()
                            emitted_compaction_progress = True
                            from services.usage.cost_budget import on_model_transport_start

                            transport_scope = (
                                on_model_transport_start(run_outcome.mark_provider_attempted)
                                if run_outcome is not None
                                else contextlib.nullcontext()
                            )
                            with run_meter.measure_stage("compaction"), transport_scope:
                                outcome = await compact_history(
                                    loop_history,
                                    system_prompt=system_prompt,
                                    session_id=session_id,
                                    run_meter=run_meter,
                                    request_context=request_context,
                                    turn_input_tokens=current_turn_tokens,
                                    pre_archive=pre_archive,
                                )

                        compaction_usage = outcome.usage
                        if outcome.record is not None and outcome.items is not None:
                            loop_compaction_record = outcome.record
                            writing_state["history_items"] = outcome.items
                            if is_tool_archive_compaction(outcome.record):
                                if outcome.archived_file_ids:
                                    stage1_archived_file_ids.update(outcome.archived_file_ids)
                                # Stage 1 折叠了旧轮次的只读工具结果：若本轮 <turn_context> 对被归档文件做了
                                # 指纹去重省略，立即恢复 full_text 避免模型上下文彻底失去正文；同时从
                                # effective_context_fingerprints["full_file_hashes"] 驱逐被归档文件，
                                # 防止下轮对已归档的 query_files 正文发出陈旧 unchanged 指针。
                                current_full_ids = set(
                                    (loop_turn.context_fingerprints or {}).get("_current_full_file_hashes") or ()
                                )
                                if outcome.archived_unidentified_query_files:
                                    _restore_full_turn_context()
                                elif outcome.archived_file_ids:
                                    restored_for_stage1 = bool(
                                        loop_turn.full_text and (outcome.archived_file_ids & current_full_ids)
                                    )
                                    if restored_for_stage1:
                                        _restore_full_turn_context()
                                    if effective_context_fingerprints and isinstance(
                                        effective_context_fingerprints.get("full_file_hashes"), dict
                                    ):
                                        evict_ids = (
                                            outcome.archived_file_ids - current_full_ids
                                            if restored_for_stage1
                                            else outcome.archived_file_ids
                                        )
                                        for fid in evict_ids:
                                            effective_context_fingerprints["full_file_hashes"].pop(fid, None)
                                # Stage 1 只折叠了旧轮次的只读工具结果：仅撤销被归档文件的跨轮快照结转；
                                # 仅当存在未能提取具体 id 的 query_files 归档时才保守全撤，绝不因归档技能而误撤近期文件快照。
                                if loop_turn.session_carried_snapshot_ids:
                                    if outcome.archived_unidentified_query_files:
                                        to_drop = loop_turn.session_carried_snapshot_ids
                                    else:
                                        archived_ids = outcome.archived_file_ids or set()
                                        to_drop = archived_ids & loop_turn.session_carried_snapshot_ids
                                    if to_drop:
                                        request_context.drop_initial_read_snapshots(to_drop)
                                yield thinking_event(
                                    "Archived earlier read results to free context; continuing…"
                                    if force_en
                                    else "已归档较早的只读上下文以释放空间，继续生成…",
                                    step=HISTORY_COMPACTION_SKIPPED_STEP if emitted_compaction_progress else None,
                                ).to_sse()
                            else:
                                # Stage 2 压缩成功后前几轮的工具结果与 <turn_context> 已折叠进摘要，
                                # 撤销全部会话快照结转并恢复本轮完整上下文。
                                _restore_full_turn_context()
                                if loop_turn.session_carried_snapshot_ids:
                                    request_context.drop_initial_read_snapshots(
                                        loop_turn.session_carried_snapshot_ids
                                    )
                                assistant_display_events.append({"type": "history_compacted", "data": {}})
                                yield history_compacted_event(outcome.record["summary"]).to_sse()
                        else:
                            if outcome.skipped:
                                loop_compaction_skipped = skipped_record(loop_history, outcome.skipped)
                            yield thinking_event(
                                "Couldn't compact the conversation; continuing as is"
                                if force_en
                                else "未能压缩对话，按原样继续",
                                step=HISTORY_COMPACTION_SKIPPED_STEP,
                            ).to_sse()
                    # 重试接过来的记录：本轮历史照它重建，本轮没写新记录时随本轮的行落库；
                    # 若重试轮本身又触发了新的 Stage 1 tool_archive，合并被删作者行结转的 archived_call_ids 防丢失。
                    carried_record = valid_compaction(retry_carried_compaction)
                    carried_archive = valid_tool_archive_compaction(retry_carried_compaction)
                    if loop_compaction_record is None:
                        if carried_record is not None and loop_history.compacted:
                            loop_compaction_record = carried_record
                        elif carried_archive is not None and loop_history.archived_call_ids:
                            loop_compaction_record = carried_archive
                    elif is_tool_archive_compaction(loop_compaction_record) and carried_archive is not None:
                        merged_call_ids = list(
                            dict.fromkeys(
                                [
                                    *carried_archive["archived_call_ids"],
                                    *(loop_compaction_record.get("archived_call_ids") or []),
                                ]
                            )
                        )
                        merged_occ: dict[str, list[int]] = {}
                        for src in (carried_archive, loop_compaction_record):
                            raw_occ = src.get("archived_call_occurrences")
                            if isinstance(raw_occ, dict):
                                for cid, indices in raw_occ.items():
                                    if isinstance(cid, str) and cid and isinstance(indices, list):
                                        merged_occ.setdefault(cid, [])
                                        for idx_val in indices:
                                            if isinstance(idx_val, int) and idx_val not in merged_occ[cid]:
                                                merged_occ[cid].append(idx_val)
                        loop_compaction_record = {
                            **loop_compaction_record,
                            "archived_call_ids": merged_call_ids,
                            "archived_count": len(merged_call_ids),
                        }
                        if merged_occ:
                            loop_compaction_record["archived_call_occurrences"] = merged_occ
                    if (
                        loop_compaction_record is None
                        and loop_compaction_skipped is None
                        and carried_record is None
                        and carried_archive is None
                        and isinstance(retry_carried_compaction.get("compaction_skipped"), dict)
                    ):
                        loop_compaction_skipped = retry_carried_compaction["compaction_skipped"]
                    # 兜底上限：压缩一直不成、实测（或供应商同量纲估算）逼近 1M 窗口时才丢掉最旧项；
                    # 本轮刚完成压缩时 items 已是摘要结果，不再按压缩前读数裁剪。
                    if loop_compaction_record is None:
                        run_history = writing_state.get("history_items") or []
                        turn_user_text = str(writing_state.get("user_message") or "")
                        turn_extra_tokens = max(0, estimate_provider_text_tokens(turn_user_text) - 50_000)
                        history_max_tokens = max(100_000, AGENT_LOOP_RUN_HISTORY_MAX_TOKENS - turn_extra_tokens)
                        if effective_history_provider_tokens(loop_history, run_history) > history_max_tokens:
                            from agent.openai_agents.intra_run_trimmer import IntraRunToolOutputTrimmer

                            folded_history = IntraRunToolOutputTrimmer().trim_items(
                                run_history, instructions_chars=len(system_prompt)
                            )
                            if folded_history is not run_history:
                                writing_state["history_items"] = folded_history
                                run_history = folded_history
                                _restore_full_turn_context()
                            bounded_history = bound_history_for_provider(
                                loop_history, run_history, history_max_tokens
                            )
                            if len(bounded_history) < len(run_history):
                                log_with_context(
                                    logger,
                                    30,  # WARNING
                                    "Loop history exceeded the run bound; oldest items left out",
                                    session_id=session_id,
                                    dropped_items=len(run_history) - len(bounded_history),
                                    kept_tokens=estimate_provider_items_tokens(bounded_history),
                                )
                                writing_state["history_items"] = bounded_history
                                _restore_full_turn_context()
                    workflow_events = self._loop_events(
                        writing_state,
                        get_steering_messages=get_steering_messages,
                        leading_usage=compaction_usage,
                    )
                    async for event in stream_adapter.process_workflow_events(workflow_events):
                        event_type = event.type.value
                        if event_type == "done":
                            pending_done_payload = event.data if isinstance(event.data, dict) else {}
                            continue

                        # Yield SSE event
                        yield event.to_sse()

                        if event_type not in {"content", "thinking_content"}:
                            # Even non-persisted stream markers (notably content_start /
                            # content_end) delimit separately rendered text segments.
                            display_text_run_type = None

                        if event_type == "iteration_exhausted":
                            assistant_display_events.append(
                                {
                                    "type": event_type,
                                    "data": dict(event.data),
                                }
                            )

                        if event_type == "file_content_end":
                            post_file_whitespace_pending = True
                            post_file_whitespace_buf = ""
                        elif event_type in {
                            "tool_call",
                            "thinking_content",
                            "iteration_exhausted",
                            "error",
                        }:
                            post_file_whitespace_pending = False
                            post_file_whitespace_buf = ""

                        # Track content for history
                        if event_type == "content":
                            text_chunk = event.data.get("text", "")
                            if post_file_whitespace_pending:
                                candidate = (post_file_whitespace_buf + text_chunk).replace("\x00", "")
                                if not candidate.strip():
                                    post_file_whitespace_buf = candidate
                                else:
                                    trimmed = candidate.lstrip("\r\n")
                                    _append_display_text(
                                        "content",
                                        trimmed,
                                        response_chunk=trimmed if not assistant_response else candidate,
                                    )
                                    post_file_whitespace_pending = False
                                    post_file_whitespace_buf = ""
                            else:
                                _append_display_text("content", text_chunk)
                        elif event_type == "thinking_content":
                            thinking_chunk = event.data.get("content", "")
                            _append_display_text("thinking_content", thinking_chunk)
                        elif event_type == "tool_call":
                            tool_use_id = str(event.data.get("tool_use_id") or "").strip()
                            tool_call_record = {
                                "id": tool_use_id,
                                "name": event.data.get("tool_name", ""),
                                "arguments": event.data.get("arguments", {}),
                                "status": "pending",
                            }
                            if tool_use_id:
                                existing_index = tool_call_index_by_id.get(tool_use_id)
                                if existing_index is None:
                                    tool_call_index = len(all_tool_calls)
                                    tool_call_index_by_id[tool_use_id] = tool_call_index
                                    all_tool_calls.append(tool_call_record)
                                    assistant_display_events.append(
                                        {"type": "tool_call", "tool_call_index": tool_call_index}
                                    )
                                else:
                                    all_tool_calls[existing_index].update(tool_call_record)
                            else:
                                tool_call_index = len(all_tool_calls)
                                all_tool_calls.append(tool_call_record)
                                assistant_display_events.append(
                                    {"type": "tool_call", "tool_call_index": tool_call_index}
                                )
                        elif event_type == "tool_result":
                            tool_use_id = str(event.data.get("tool_use_id") or "").strip()
                            target_call: dict[str, Any] | None = None

                            if tool_use_id:
                                existing_index = tool_call_index_by_id.get(tool_use_id)
                                if existing_index is None:
                                    target_call = {
                                        "id": tool_use_id,
                                        "name": event.data.get("tool_name", ""),
                                        "arguments": {},
                                        "status": "pending",
                                    }
                                    tool_call_index = len(all_tool_calls)
                                    tool_call_index_by_id[tool_use_id] = tool_call_index
                                    all_tool_calls.append(target_call)
                                    assistant_display_events.append(
                                        {"type": "tool_call", "tool_call_index": tool_call_index}
                                    )
                                else:
                                    target_call = all_tool_calls[existing_index]
                            elif all_tool_calls:
                                target_call = all_tool_calls[-1]

                            if target_call is not None:
                                target_call["status"] = event.data.get("status", "success")
                                target_call["result"] = stream_adapter.pop_persisted_tool_result(
                                    tool_use_id,
                                    event.data.get("data"),
                                )
                                target_call["error"] = event.data.get("error")
                        elif event_type == "iteration_exhausted":
                            assistant_status_cards.append(iteration_exhausted_status_card(event.data))
                        elif event_type == "error":
                            had_stream_error = True

                    model_metadata = stream_adapter.get_last_message_metadata()
                    assistant_stop_reason = model_metadata.get("stop_reason")
                    usage_candidate = model_metadata.get("usage")
                    assistant_usage = usage_candidate if isinstance(usage_candidate, dict) else None
                    if had_stream_error:
                        # Still persist partial history so the user and AI can
                        # recover context on the next turn instead of losing
                        # everything (user message + completed tool calls).
                        log_with_context(
                            logger,
                            30,  # WARNING
                            "Stream emitted an error event — saving partial history for recovery",
                            project_id=project_id,
                            user_id=user_id,
                            response_length=len(assistant_response),
                            tool_calls=len(all_tool_calls) if all_tool_calls else 0,
                        )

                    late_steering_before_save = await _drain_late_steering(
                        failure_message=(
                            "Failed to drain remaining steering messages before history save"
                        )
                    )
                    has_pending_file_state = request_context.has_pending_empty_file() or bool(
                        request_context.unsaved_chapter_replacement_ids()
                    )

                    # Save to history。落库放进独立任务并 shield：PG 下
                    # save_messages 内部走 asyncio.to_thread，工作线程一旦启动就
                    # 无法取消（awaiting 侧抛 CancelledError，线程照样 commit）。
                    # shield 保证取消不会打断这次落库，且 history_saved 只在真正
                    # 写完后置位，取消分支据此跳过补偿保存，避免整轮历史写两遍。
                    async def _save_history_once() -> str | None:
                        nonlocal history_saved
                        if not _has_assistant_payload():
                            # 与补偿保存同一不变量：本轮一个 token 都没产出（典型是
                            # 首次模型调用就报错、只发了 error 事件）时不写空
                            # assistant 行，只保留用户消息与已消费的 steering。
                            await asyncio.to_thread(
                                _append_user_messages_sync,
                                [message, *consumed_steering],
                                first_created_at=user_sent_at,
                                first_metadata=_loop_user_row_metadata(),
                            )
                            history_saved = True
                            return None
                        _stamp_body_receipts(
                            all_tool_calls,
                            stream_adapter.get_saved_file_word_counts(),
                            stream_adapter.get_replacement_receipts(),
                            stream_adapter.get_replacement_card_updates(),
                        )
                        saved_id = await message_manager.save_messages(
                            session,
                            session_id,
                            message,
                            assistant_response,
                            all_tool_calls if all_tool_calls else None,
                            None,
                            assistant_stop_reason=assistant_stop_reason,
                            assistant_usage=assistant_usage,
                            assistant_status_cards=assistant_status_cards or None,
                            steering_messages=consumed_steering or None,
                            assistant_display_events=normalize_assistant_display_events(assistant_display_events),
                            user_created_at=user_sent_at,
                            assistant_extra_metadata=_loop_extra_metadata(),
                        )
                        history_saved = True
                        return saved_id

                    def _consume_history_save_error(done_task: asyncio.Task) -> None:
                        # 取消路径下可能没人再取这个任务的结果，先消费掉异常避免
                        # "exception was never retrieved"；失败仍会由下面的 await
                        # 传播成 error 事件。
                        if not done_task.cancelled():
                            done_task.exception()

                    history_save_task = asyncio.create_task(_save_history_once())
                    history_save_task.add_done_callback(_consume_history_save_error)
                    assistant_message_id = await asyncio.shield(history_save_task)
                    if run_outcome is not None:
                        run_outcome.resolve_message_id(assistant_message_id)
                    completed_wire_items = writing_state.get("run_items")
                    has_unconsumed_steering = bool(writing_state.get("pending_steering"))
                    wire_prefix_skip_reason: str | None = None
                    if (
                        user_id
                        and session_id
                        and history_saved
                        and not had_stream_error
                        and not request_failed
                        and not stream_cancelled
                    ):
                        if not assistant_message_id:
                            wire_prefix_skip_reason = "no_assistant_message"
                        elif run_outcome is not None and run_outcome.stop_kind:
                            wire_prefix_skip_reason = "stop_kind"
                        elif assistant_stop_reason not in _WIRE_PREFIX_CACHEABLE_STOP_REASONS:
                            wire_prefix_skip_reason = "stop_reason"
                        elif _has_blocking_wire_prefix_status_cards(assistant_status_cards):
                            wire_prefix_skip_reason = "blocking_status_card"
                        elif late_steering_before_save:
                            wire_prefix_skip_reason = "late_steering"
                        elif has_unconsumed_steering:
                            wire_prefix_skip_reason = "unconsumed_steering"
                        elif has_pending_file_state:
                            wire_prefix_skip_reason = "pending_file_state"
                        elif not is_well_formed_wire_prefix(completed_wire_items):
                            wire_prefix_skip_reason = "malformed_items"
                        elif not run_meter.last_served_provider_key:
                            wire_prefix_skip_reason = "no_provider_key"

                        if wire_prefix_skip_reason is None:
                            stage2_compacted = bool(
                                loop_compaction_record is not None
                                and not is_tool_archive_compaction(loop_compaction_record)
                            )
                            stage1_archived = bool(
                                loop_compaction_record is not None
                                and is_tool_archive_compaction(loop_compaction_record)
                            )
                            turn_author_msgs = [
                                text
                                for text in (message, *consumed_steering)
                                if isinstance(text, str) and text.strip()
                            ]
                            if stage2_compacted and isinstance(loop_compaction_record, dict):
                                kept_prior = loop_compaction_record.get("kept_author_messages")
                                next_author_messages = [
                                    *(kept_prior if isinstance(kept_prior, list) else []),
                                    *turn_author_msgs,
                                ]
                                next_archived_call_ids: set[str] = set()
                                next_archived_file_ids: set[str] = set()
                            else:
                                next_author_messages = [*loop_history.author_messages, *turn_author_msgs]
                                next_archived_call_ids = set(loop_history.archived_call_ids)
                                next_archived_file_ids = set(loop_history.archived_file_ids)
                                if stage1_archived and isinstance(loop_compaction_record, dict):
                                    next_archived_call_ids.update(
                                        str(cid)
                                        for cid in (loop_compaction_record.get("archived_call_ids") or [])
                                        if isinstance(cid, str) and cid
                                    )
                                    if stage1_archived_file_ids:
                                        next_archived_file_ids.update(stage1_archived_file_ids)
                                # If this turn re-read any archived file in full, discard it from archived_file_ids.
                                from agent.loop.history import query_result_full_file_ids

                                for tc in all_tool_calls:
                                    if tc.get("name") == "query_files" and str(tc.get("status") or "").lower() == "success":
                                        for fid in query_result_full_file_ids(
                                            tc.get("result"), include_slimmed_sha256=True
                                        ):
                                            next_archived_file_ids.discard(fid)
                            if isinstance(loop_compaction_skipped, dict) and isinstance(
                                loop_compaction_skipped.get("at_prompt_tokens"), int
                            ):
                                next_skipped_at = max(0, loop_compaction_skipped["at_prompt_tokens"])
                                raw_skip_reason = loop_compaction_skipped.get("reason")
                                next_skipped_reason = (
                                    raw_skip_reason
                                    if isinstance(raw_skip_reason, str) and raw_skip_reason
                                    else None
                                )
                            elif loop_compaction_record is not None:
                                next_skipped_at = 0
                                next_skipped_reason = None
                            else:
                                next_skipped_at = loop_history.compaction_skipped_at_tokens
                                next_skipped_reason = loop_history.compaction_skipped_reason

                            next_history_meta: dict[str, Any] = {
                                "last_prompt_tokens": int(
                                    run_meter.last_prompt_tokens
                                    or (0 if loop_compaction_record is not None else loop_history.last_prompt_tokens)
                                ),
                                "compacted": stage2_compacted or (loop_history.compacted and not stage2_compacted),
                                "compaction_skipped_at_tokens": next_skipped_at,
                                "compaction_skipped_reason": next_skipped_reason,
                                "trailing_output_truncations": 0,
                                "archived_call_ids": sorted(next_archived_call_ids),
                                "archived_file_ids": sorted(next_archived_file_ids),
                                "author_messages": next_author_messages,
                            }
                            # P1-1 / P0-2: Before storing wire_prefix_cache fingerprints:
                            # 1) Update/evict cached_file_hashes for every file read via successful query_files
                            #    in this turn (even when no write tool ran!) so a subsequent author revert
                            #    (V1 -> V2 -> V1) never emits a stale unchanged pointer while model last saw V2.
                            # 2) Evict mutated files and clear project_files/author_edits on any file mutation.
                            # 3) Clear author_edits_sha256 whenever any alerted file was read this turn so a
                            #    second author edit (V2 -> V3) never emits <author_edits unchanged="true"/>.
                            cached_fingerprints: dict[str, Any] | None = None
                            if effective_context_fingerprints:
                                from agent.loop.turn_context import _WARM_DEDUP_MAX_TRACKED_FILES

                                read_file_hashes, read_file_ids = _read_file_hashes_for_fingerprints(
                                    all_tool_calls
                                )
                                mutated_ids, any_file_mutation = _applied_file_mutations_for_fingerprints(
                                    all_tool_calls
                                )
                                cached_file_hashes = dict(
                                    effective_context_fingerprints.get("full_file_hashes") or {}
                                )
                                for fid in read_file_ids:
                                    if fid in read_file_hashes:
                                        cached_file_hashes[fid] = read_file_hashes[fid]
                                    else:
                                        cached_file_hashes.pop(fid, None)
                                for fid in mutated_ids:
                                    cached_file_hashes.pop(fid, None)
                                if len(cached_file_hashes) > _WARM_DEDUP_MAX_TRACKED_FILES:
                                    trimmed_keys = list(cached_file_hashes)[-_WARM_DEDUP_MAX_TRACKED_FILES:]
                                    cached_file_hashes = {k: cached_file_hashes[k] for k in trimmed_keys}
                                alerted_author_edit_ids = {
                                    str(fid)
                                    for fid in (effective_context_fingerprints.get("_author_edits_file_ids") or ())
                                    if isinstance(fid, str) and fid
                                }
                                any_alerted_file_read = bool(read_file_ids & alerted_author_edit_ids)
                                cached_fingerprints = {
                                    "project_files_sha256": (
                                        ""
                                        if any_file_mutation
                                        else effective_context_fingerprints.get("project_files_sha256", "")
                                    ),
                                    "full_file_hashes": cached_file_hashes,
                                    "author_edits_sha256": (
                                        ""
                                        if (any_file_mutation or any_alerted_file_read)
                                        else effective_context_fingerprints.get("author_edits_sha256", "")
                                    ),
                                }
                            stored_wire_prefix = await astore_wire_prefix(
                                session_id=session_id,
                                project_id=project_id,
                                user_id=user_id,
                                last_assistant_message_id=assistant_message_id,
                                system_prompt=system_prompt,
                                provider_key=run_meter.last_served_provider_key,
                                items=completed_wire_items,
                                tools_sha256=expected_tools_sha,
                                reasoning_effort=run_meter.last_served_reasoning_effort or expected_reasoning_effort,
                                history_meta=next_history_meta,
                                context_fingerprints=cached_fingerprints,
                            )
                        else:
                            log_with_context(
                                logger,
                                20,  # INFO
                                "Agent wire prefix cache store skipped",
                                session_id=session_id,
                                skip_reason=wire_prefix_skip_reason,
                                stop_reason=assistant_stop_reason,
                            )
                    if user_id and session_id and history_saved and not stored_wire_prefix:
                        await ainvalidate_wire_prefix(session_id)
                    if pending_done_payload is not None:
                        yield done_event(
                            assistant_message_id=assistant_message_id,
                            session_id=session_id,
                            file_mutated=pending_done_payload.get("file_mutated") is True,
                        ).to_sse()
                finally:
                    request_context.clear()

        except (asyncio.CancelledError, GeneratorExit):
            stream_cancelled = True
            prepare_cancel_event.set()
            # 客户端断连有两种到达方式：任务被取消（CancelledError）与生成器被
            # aclose（GeneratorExit，Starlette 关闭 StreamingResponse 时触发）。
            # GeneratorExit 期间一旦在 finally 里 await 挂起，解释器会抛
            # RuntimeError("async generator ignored GeneratorExit")，收尾动作
            # 全部丢失；两者必须走同一条「后台任务收尾 + 原样上抛」的路径。
            # 前端取消/断连时事件循环正在退栈，此处任何 await 都会被同一个
            # cancel 再次打断，且共享 session 会随请求关闭；因此与
            # had_stream_error 分支同样保留部分历史（用户消息 + 已生成的
            # assistant 内容 + 已完成 tool_calls + 已消费 steering），但改用
            # 后台任务 + 独立 session 落库，随后原样向上传播取消。取消瞬间
            # 队列里可能还有已确认 queued 但未消费的 steering，落库前先在
            # 后台任务里 drain 进 consumed_steering，避免它们随队列清理丢失。
            # 注意这里不能再用 `not history_saved` 预筛：整轮历史已落库、只是
            # done 事件还没发出去时被取消，队列里仍可能有已确认 queued 的
            # steering，跳过后台任务就等于让随后的 cleanup 把它删掉。是否重写
            # 整轮由任务内部按 history_saved 判定。
            # 时限截停的原因由路由层在取消送达之前写入（发布交接把时限提前到几分钟时，
            # 已用时长远小于墙钟时限，不能按时长推断）。
            cancel_stop_reason = (
                run_outcome.deadline_stop_reason if run_outcome is not None else None
            ) or PARTIAL_SAVE_CANCELLED_STOP_REASON
            if user_id:

                async def _drain_then_save_partial_history() -> None:
                    try:
                        await _drain_then_save_partial_history_inner()
                    finally:
                        # 没写成（或已在上面交回）时交回 None，路由层不必一直等。
                        if run_outcome is not None:
                            run_outcome.resolve_message_id(None)

                async def _drain_then_save_partial_history_inner() -> None:
                    # 残稿补存会把没有作为 SSE 发出的尾部缓冲一并保存。先等本轮
                    # 正文保存回执，再交回消息 id，让 done 与历史使用同一份字数。
                    # 被取消了等待、线程仍在提交的写工具也要等它们的结果，交给路由层计费。两类在途写入
                    # 互不依赖，一起等：最长等一个 STREAM_FILE_SAVE_TIMEOUT_S，不是两个时限相加。
                    if run_outcome is not None:
                        await _settle_stopped_writes(run_outcome, usage_source.get("adapter"))
                    # 取消可能恰好落在 save_messages 期间：它被 shield 保护会
                    # 继续跑完，所以补偿保存前必须等它的真实结果。已落库时
                    # 不能整轮重写（user/steering/assistant 会写两遍），但仍要
                    # 处理这段窗口里新入队的 steering，否则它们会随队列清理
                    # 一起消失。
                    if history_save_task is not None:
                        await asyncio.wait({history_save_task})
                        if (
                            run_outcome is not None
                            and history_saved
                            and not history_save_task.cancelled()
                            and history_save_task.exception() is None
                        ):
                            run_outcome.resolve_message_id(history_save_task.result())

                    late_steering = await _drain_late_steering(
                        failure_message=(
                            "Failed to drain steering queue before cancellation save"
                        )
                    )

                    if history_saved:
                        if late_steering:
                            await _finish_history_write(
                                _append_user_messages_sync, late_steering
                            )
                            await ainvalidate_wire_prefix(session_id)
                        elif not stored_wire_prefix or (
                            run_outcome is not None
                            and (run_outcome.stop_kind or run_outcome.deadline_stop_reason)
                        ):
                            await ainvalidate_wire_prefix(session_id)
                        return
                    # 作者停止 / 断线（路由层在取消前写入 stop_kind）：落库真实的停止
                    # 原因，并且即使没有任何产出也写一条助手消息，刷新后看得到终态。
                    stop_kind = run_outcome.stop_kind if run_outcome is not None else None
                    saved_id = await _finish_history_write(
                        _save_partial_history_sync,
                        stop_kind or cancel_stop_reason,
                        bool(stop_kind),
                    )
                    if run_outcome is not None:
                        run_outcome.resolve_message_id(saved_id)
                    await ainvalidate_wire_prefix(session_id)

                cancellation_save_task = self._schedule_background_cleanup(
                    _drain_then_save_partial_history(),
                    description="save_partial_history_after_cancellation",
                    session_id=session_id,
                )
            raise
        except Exception as e:
            request_failed = True
            error_info = classify_stream_exception(e)
            log_stream_exception(
                logger,
                "Agent process_stream failed",
                e,
                error_info,
                project_id=project_id,
                user_id=user_id,
            )
            adapter = usage_source.get("adapter")
            if run_outcome is not None and adapter is not None:
                # 与取消路径一样：先补存进行中的 <file> 正文并等它的保存回执（受
                # STREAM_FILE_SAVE_TIMEOUT_S 限时），下面 finally 交回消息 id 时字数才是真的。
                try:
                    saved_counts = await adapter.settle_aborted_capture()
                except Exception as settle_exc:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Failed to settle in-flight file capture after stream failure",
                        session_id=session_id,
                        error_type=type(settle_exc).__name__,
                    )
                    saved_counts = adapter.get_saved_file_word_counts()
                run_outcome.saved_file_word_counts.update(saved_counts)
            if run_outcome is not None:
                await run_outcome.settle_inflight_tool_writes(STREAM_FILE_SAVE_TIMEOUT_S)
            yield error_event(
                error_info.message,
                code=error_info.code,
                retryable=error_info.retryable,
                refundable=(
                    run_outcome.terminal_model_failure or not run_outcome.provider_attempted
                    if run_outcome is not None
                    else error_info.refundable
                ),
            ).to_sse()

        finally:
            prepare_cancel_event.set()
            if not context_timing_recorded:
                run_meter.record_stage(
                    "prepare_context", (time.monotonic() - run_started_monotonic) * 1000,
                    outcome="interrupted" if stream_cancelled else "error",
                )
            # Cleanup steering queue（只释放本 run；TTL 后接管的新 run 不受影响）
            if stream_cancelled:

                async def _cleanup_after_cancellation_save() -> None:
                    # 先等部分历史落库（其中包含 drain 出的 steering）再释放
                    # 队列，避免清理任务抢先删掉尚未落库的消息。asyncio.wait
                    # 不会向外抛保存任务的异常/取消，失败已由其 done callback
                    # 记录，这里无论如何都要继续清理。
                    try:
                        if isinstance(cancellation_save_task, asyncio.Task):
                            await asyncio.wait({cancellation_save_task})
                        await cleanup_steering_queue_async(session_id, run_id=steering_run_id)
                    finally:
                        _stop_run_heartbeat()
                        if account_run_slot is not None:
                            await account_run_slot.release()

                self._schedule_background_cleanup(
                    _cleanup_after_cancellation_save(),
                    description="cleanup_steering_queue_async",
                    session_id=session_id,
                )
                log_with_context(
                    logger,
                    20,
                    "Scheduled steering queue cleanup in background after stream cancellation",
                    session_id=session_id,
                )
            else:
                try:
                    # cleanup 会连消息一起删除队列：先兜底取出仍未消费的
                    # steering（正常路径在保存历史前已 drain 过，这里覆盖请求失败
                    # 未走到保存点、以及「历史刚落库到队列被删」这段窗口里新入队
                    # 的消息），保证已确认 queued 的输入不会凭空消失。
                    late_steering = await _drain_late_steering(
                        failure_message="Failed to drain steering queue before cleanup"
                    )

                    if user_id and not history_saved:
                        # 与取消路径对等的补偿保存：本轮只要产生过任何值得保留的
                        # 内容就用独立 session 补存。旧实现额外要求
                        # consumed_steering 非空，而绝大多数请求根本没有 steering，
                        # 于是一次瞬时 DB 冲突就让整轮历史（用户消息 + 已经流式吐
                        # 给用户看的正文 + 工具调用记录）彻底消失。
                        if (
                            message.strip()
                            or consumed_steering
                            or _has_assistant_payload()
                        ):
                            try:
                                saved_id = await _finish_history_write(
                                    _save_partial_history_sync, assistant_stop_reason
                                )
                                if run_outcome is not None:
                                    run_outcome.resolve_message_id(saved_id)
                                await ainvalidate_wire_prefix(session_id)
                            except Exception as exc:
                                log_with_context(
                                    logger,
                                    30,  # WARNING
                                    "Failed to persist partial history after stream failure",
                                    session_id=session_id,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                )
                    elif user_id and late_steering:
                        # 整轮历史已经落库，之后才 drain 出来的 steering 单独追加为
                        # user 行：/agent/steer 已经回过 queued=True，既不能重写整轮，
                        # 更不能随队列一起删掉。
                        try:
                            await _finish_history_write(
                                _append_user_messages_sync, late_steering
                            )
                            await ainvalidate_wire_prefix(session_id)
                        except Exception as exc:
                            log_with_context(
                                logger,
                                30,  # WARNING
                                "Failed to persist late steering messages after history save",
                                session_id=session_id,
                                error=str(exc),
                                error_type=type(exc).__name__,
                            )
                finally:
                    if run_outcome is not None:
                        run_outcome.resolve_message_id(None)
                    try:
                        await cleanup_steering_queue_async(
                            session_id, run_id=steering_run_id
                        )
                    finally:
                        _stop_run_heartbeat()
                        if account_run_slot is not None:
                            await account_run_slot.release()

            total_duration = int((utcnow() - start_time).total_seconds() * 1000)
            if run_report is not None:
                from models.chat_message_ux_summary import iteration_exhausted_abnormal_reason

                usage_summary = assistant_usage if isinstance(assistant_usage, dict) else {}
                metric_stop_reason = assistant_stop_reason
                if metric_stop_reason == "end_turn" and assistant_status_cards:
                    exhausted_reason = iteration_exhausted_abnormal_reason(
                        {"status_cards": assistant_status_cards}
                    )
                    if exhausted_reason is not None:
                        metric_stop_reason = exhausted_reason
                run_report.update(
                    {
                        "model": run_meter.last_served_model or run_report.get("model") or primary_model_name(),
                        "input_tokens": usage_summary.get("input_tokens", 0),
                        "output_tokens": usage_summary.get("output_tokens", 0),
                        "cache_read_tokens": usage_summary.get("cache_read_tokens", 0),
                        "usage_reported": bool(usage_summary),
                        "model_calls": run_meter.model_calls,
                        "max_model_calls": run_meter.max_model_calls,
                        "agent_runs": run_meter.agent_runs,
                        "llm_duration_ms": round(run_meter.llm_duration_ms, 1),
                        **run_meter.timing_report(),
                        "duration_ms": total_duration,
                        "stop_reason": metric_stop_reason,
                        "tool_calls_count": len(all_tool_calls),
                        "response_length": len(assistant_response),
                        "session_id": session_id,
                    }
                )
            completed_with_errors = request_failed or had_stream_error
            if completed_with_errors:
                completion_level = 30  # WARNING
                completion_message = "Agent process_stream completed with errors"
            elif stream_cancelled:
                completion_level = 20  # INFO: client cancellation is a normal terminal state
                completion_message = "Agent process_stream cancelled"
            else:
                completion_level = 20  # INFO
                completion_message = "Agent process_stream completed successfully (workflow)"
            log_with_context(
                logger,
                completion_level,
                completion_message,
                project_id=project_id,
                user_id=user_id,
                tool_calls_count=len(all_tool_calls),
                response_length=len(assistant_response),
                duration_ms=total_duration,
                request_failed=request_failed,
                stream_error=had_stream_error,
                stream_cancelled=stream_cancelled,
            )

    @staticmethod
    async def _loop_events(
        writing_state: WritingState,
        *,
        get_steering_messages: Any,
        leading_usage: dict[str, int] | None,
    ) -> AsyncGenerator[Any, None]:
        """单循环 agent 的事件流；本轮压缩调用的用量先以 MESSAGE_END 交给 StreamAdapter 汇总。"""
        from .core.workflow_events import StreamEvent, StreamEventType

        if leading_usage:
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"usage": leading_usage, "stop_reason": None})
        async for event in run_loop_agent_streaming(writing_state, get_steering_messages=get_steering_messages):
            yield event


# Singleton
_service: AgentService | None = None


def get_agent_service() -> AgentService:
    """Get singleton agent service."""
    global _service
    if _service is None:
        _service = AgentService()
    return _service
