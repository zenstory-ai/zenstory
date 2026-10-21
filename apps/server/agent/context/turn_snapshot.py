"""Per-session project snapshot: unchanged context is sent once, changes every turn.

Each turn's project context (assembled items) and working set used to be rebuilt in
full inside the current user message, after the history, so none of it was ever a
prompt-cache hit: in production ~47k uncached input tokens per turn.

Now the first turn of a chat session (and every rebuild) renders those documents
into a snapshot placed right after the system prompt (runner.build_history_messages).
Later turns reuse the snapshot byte for byte and put only documents that are new or
changed since the snapshot into <turn_context>, labelled as replacing the snapshot
copy. Request layout: [system][snapshot][history][turn context with changes + message],
so [system][snapshot][history up to the previous turn] stays cacheable.

The snapshot is rebuilt when the changes outgrow it, after REBUILD_AFTER_TURNS turns
or REBUILD_AFTER_S seconds, or when the language/project/format changes. State lives
in Redis when configured (shared by API workers) and in process memory otherwise;
losing it only costs one rebuild.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

SNAPSHOT_FORMAT_VERSION = 1
SNAPSHOT_TTL_S = 6 * 3600
REBUILD_AFTER_TURNS = 20
REBUILD_AFTER_S = 2 * 3600
# Rebuild once the per-turn changes reach this share of the snapshot's size.
REBUILD_CHANGED_RATIO = 0.5

SNAPSHOT_OPEN = "<project_snapshot>"
SNAPSHOT_CLOSE = "</project_snapshot>"
SNAPSHOT_PREAMBLE = (
    "这一段是系统在本会话某一轮开始时附上的项目资料快照（不是作者写的）：项目状态、文件清单、"
    "当时组装的项目上下文和工作集文件全文。之后改动过或新加入上下文的条目，会在当轮 <turn_context> "
    "的「本轮更新的项目资料」里给出新版本，以新版本为准；快照里的文件若已不在当轮文件清单中，说明已被删除。"
)
CHANGED_CONTEXT_HEADER = "【本轮更新的项目资料】以下条目替换 <project_snapshot> 里的同名条目（以这里为准）："
WORKING_SET_LISTING_HEADER = (
    "以下为上一轮读取/修改过的文件；全文在 <project_snapshot> 或本轮 <turn_context> 的"
    "「本轮更新的项目资料」里（完整，非预览，截至本轮开始），无需再次 query_files："
)


@dataclass(frozen=True)
class TurnContextPlan:
    """What this turn sends: the snapshot message and only what changed since it."""

    snapshot_message: str
    # Changed/new context blocks for <turn_context> ("" when nothing changed).
    changed_context: str
    # Working set section for this turn: a listing plus changed full texts.
    working_set_context: str
    rebuilt: bool


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _render_snapshot(docs: list[dict[str, str]]) -> str:
    return "\n\n".join([SNAPSHOT_OPEN, SNAPSHOT_PREAMBLE, *(doc["text"] for doc in docs), SNAPSHOT_CLOSE])


def _render_working_set(listing: list[str], tail: str) -> str:
    if not listing and not tail:
        return ""
    lines = ["<previous_turn_working_set>", WORKING_SET_LISTING_HEADER, *listing]
    if tail:
        lines.extend(["", tail])
    lines.append("</previous_turn_working_set>")
    return "\n".join(lines)


class _MemoryStore:
    def __init__(self, capacity: int = 2000) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._capacity = capacity

    def get(self, key: str) -> str | None:
        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if item is None or item[0] < now:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return item[1]

    def set(self, key: str, value: str, ttl_s: int) -> None:
        with self._lock:
            self._items[key] = (time.time() + ttl_s, value)
            self._items.move_to_end(key)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)


_memory = _MemoryStore()


def _store_get(key: str) -> str | None:
    if os.getenv("REDIS_URL"):
        try:
            from services.infra.redis_client import get_redis_client

            value = get_redis_client().get(key)
            return value.decode() if isinstance(value, bytes) else value
        except Exception as exc:  # noqa: BLE001 - the snapshot is a cache
            log_with_context(logger, 30, "Context snapshot read failed; using memory", error_type=type(exc).__name__)
    return _memory.get(key)


def _store_set(key: str, value: str) -> None:
    if os.getenv("REDIS_URL"):
        try:
            from services.infra.redis_client import get_redis_client

            get_redis_client().set(key, value, ex=SNAPSHOT_TTL_S)
            return
        except Exception as exc:  # noqa: BLE001 - the snapshot is a cache
            log_with_context(logger, 30, "Context snapshot write failed; using memory", error_type=type(exc).__name__)
    _memory.set(key, value, SNAPSHOT_TTL_S)


def _snapshot_key(session_id: str) -> str:
    return f"agent:context_snapshot:{session_id}"


def plan_turn_context(
    *,
    session_id: str | None,
    project_id: str,
    language: str,
    context_blocks: list[dict[str, str]],
    working_set_blocks: list[dict[str, str]],
    working_set_tail: str = "",
) -> TurnContextPlan | None:
    """Split this turn's documents into the session snapshot and this turn's changes.

    context_blocks: keyed blocks from ContextAssembler (header + one per item).
    working_set_blocks: {"key", "text", "listing"} per working-set file with full text.
    working_set_tail: the rest of the working set section (files already in the
    context, files without full text); always sent per turn.
    Returns None without a session id (nothing to keep the snapshot under).
    """
    if not session_id:
        return None
    docs = [*context_blocks, *working_set_blocks]
    listing = [block["listing"] for block in working_set_blocks if block.get("listing")]
    hashes = {doc["key"]: _digest(doc["text"]) for doc in docs}
    key = _snapshot_key(session_id)

    state: dict[str, Any] | None = None
    raw = _store_get(key)
    if raw:
        try:
            state = json.loads(raw)
        except ValueError:
            state = None

    now = time.time()
    reason = None
    if not state:
        reason = "missing"
    elif (
        state.get("v") != SNAPSHOT_FORMAT_VERSION
        or state.get("project_id") != project_id
        or state.get("language") != language
    ):
        reason = "mismatch"
    elif int(state.get("turns") or 0) >= REBUILD_AFTER_TURNS or now - float(state.get("created_at") or 0) >= REBUILD_AFTER_S:
        reason = "aged"

    changed: list[dict[str, str]] = []
    if reason is None:
        snapshot_hashes: dict[str, str] = state.get("hashes") or {}
        changed = [doc for doc in docs if snapshot_hashes.get(doc["key"]) != hashes[doc["key"]]]
        changed_chars = sum(len(doc["text"]) for doc in changed)
        if changed_chars > REBUILD_CHANGED_RATIO * max(len(state.get("text") or ""), 1):
            reason = "outgrown"

    if reason is not None:
        snapshot_text = _render_snapshot(docs)
        _store_set(key, json.dumps({
            "v": SNAPSHOT_FORMAT_VERSION,
            "project_id": project_id,
            "language": language,
            "text": snapshot_text,
            "hashes": hashes,
            "turns": 1,
            "created_at": now,
        }, ensure_ascii=False))
        log_with_context(
            logger,
            20,  # INFO
            "Context snapshot rebuilt",
            reason=reason,
            snapshot_chars=len(snapshot_text),
            documents=len(docs),
        )
        return TurnContextPlan(
            snapshot_message=snapshot_text,
            changed_context="",
            working_set_context=_render_working_set(listing, working_set_tail),
            rebuilt=True,
        )

    assert state is not None
    state["turns"] = int(state.get("turns") or 0) + 1
    _store_set(key, json.dumps(state, ensure_ascii=False))
    changed_context_blocks = [doc["text"] for doc in changed if not doc["key"].startswith("file:")]
    changed_files = [doc["text"] for doc in changed if doc["key"].startswith("file:")]
    changed_context = "\n\n".join([CHANGED_CONTEXT_HEADER, *changed_context_blocks, *changed_files]) if changed else ""
    log_with_context(
        logger,
        20,  # INFO
        "Context snapshot reused",
        snapshot_chars=len(state.get("text") or ""),
        changed_documents=len(changed),
        changed_chars=sum(len(doc["text"]) for doc in changed),
        turn_in_snapshot=state["turns"],
    )
    return TurnContextPlan(
        snapshot_message=str(state.get("text") or ""),
        changed_context=changed_context,
        working_set_context=_render_working_set(listing, working_set_tail),
        rebuilt=False,
    )


def reset_snapshot_memory() -> None:
    """Clear the in-process snapshot store; intended for tests."""
    global _memory
    _memory = _MemoryStore()
