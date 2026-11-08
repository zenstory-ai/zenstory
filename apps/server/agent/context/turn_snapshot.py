"""Keep relatively stable project documents before the conversation history.

The current chapter, inventory and other turn-only context come after history.
Rolling working-set files are rendered separately in the same user message.
The snapshot remains byte-identical when only those parts change. Changed
snapshot documents are sent in <turn_context>; documents not checked in this
turn are labelled as such until they leave the snapshot on a rebuild.

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

# Version 3 excludes turn-only blocks and working-set files from persisted snapshots.
SNAPSHOT_FORMAT_VERSION = 3
SNAPSHOT_TTL_S = 6 * 3600
REBUILD_AFTER_TURNS = 20
REBUILD_AFTER_S = 2 * 3600
# Rebuild once the per-turn changes reach this share of the snapshot's size.
REBUILD_CHANGED_RATIO = 0.5

SNAPSHOT_OPEN = "<project_snapshot>"
SNAPSHOT_CLOSE = "</project_snapshot>"
SNAPSHOT_PREAMBLE = (
    "这一段是系统在本会话某一轮开始时附上的项目资料快照（不是作者写的），包含相对稳定的项目资料。"
    "以本轮 <turn_context> 的资料为准；其中「本轮更新的项目资料」替换这里的同名条目。"
)
TURN_CONTEXT_HEADER = "【本轮项目资料】以下是本轮选用的资料，以本轮版本为准："
CHANGED_CONTEXT_HEADER = "【本轮更新的项目资料】以下条目替换 <project_snapshot> 里的同名条目（以这里为准）："
GONE_CONTEXT_HEADER = (
    "【本轮未核对的快照条目】以下条目在 <project_snapshot> 里，但不在本轮上下文中，本轮没有核对："
    "快照里的版本可能已过期（[全文] 不再保证是最新内容，「← 当前焦点」标记也已失效）；"
    "需要时先 query_files(id=…) 读取："
)


@dataclass(frozen=True)
class TurnContextPlan:
    """The stable snapshot and the current project context sent after history."""

    snapshot_message: str
    # Turn-only blocks plus snapshot updates and unchecked notices for <turn_context>.
    changed_context: str
    rebuilt: bool


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _render_snapshot(docs: list[dict[str, str]]) -> str:
    return "\n\n".join([SNAPSHOT_OPEN, SNAPSHOT_PREAMBLE, *(doc["text"] for doc in docs), SNAPSHOT_CLOSE])


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


def _stale_accounting(state: dict[str, Any], docs: list[dict[str, str]], hashes: dict[str, str]) -> dict[str, Any]:
    """Log-only breakdown of the previous snapshot against this turn; never affects the turn.

    live_chars / dead_chars split the old snapshot's documents by whether their stored text
    still matches. Keeping a snapshot costs its dead share on every later call, so a rebuild
    can't be judged without them. stale_chars is the rebuild decision's numerator (this turn's
    new text plus documents that left), not the dead share. Only section names and sizes:
    document keys can fall back to a title, so keys are never logged.
    """
    try:
        snapshot_hashes: dict[str, str] = state.get("hashes") or {}
        snapshot_sizes: dict[str, int] = state.get("sizes") or {}
        by_section: dict[str, int] = {}
        for doc in docs:
            if snapshot_hashes.get(doc["key"]) != hashes[doc["key"]]:
                section = doc["key"].split(":", 1)[0]
                by_section[section] = by_section.get(section, 0) + len(doc["text"])
        changed_chars = sum(by_section.values())
        live_chars = dead_chars = gone_chars = 0
        for key, digest in snapshot_hashes.items():
            size = int(snapshot_sizes.get(key) or 0)
            if hashes.get(key) == digest:
                live_chars += size
            else:
                dead_chars += size
                gone_chars += size if key not in hashes else 0
        return {
            "previous_snapshot_chars": len(state.get("text") or ""),
            "live_chars": live_chars,
            "dead_chars": dead_chars,
            "stale_chars": changed_chars + gone_chars,
            "changed_chars": changed_chars,
            "gone_chars": gone_chars,
            "changed_chars_by_section": by_section,
        }
    except Exception:  # noqa: BLE001 - old or foreign stored state must not fail the turn over a log line
        return {}


def plan_turn_context(
    *,
    session_id: str | None,
    project_id: str,
    language: str,
    context_blocks: list[dict[str, str]],
) -> TurnContextPlan | None:
    """Split this turn's documents into the session snapshot and this turn's changes.

    The assembler marks blocks with placement="turn" when they belong after
    history on every request. An optional label names a snapshot document when
    it leaves the current context.
    Returns None without a session id (nothing to keep the snapshot under).
    """
    if not session_id:
        return None
    docs = [block for block in context_blocks if block.get("placement") != "turn"]
    turn_docs = [block for block in context_blocks if block.get("placement") == "turn"]
    sections = [TURN_CONTEXT_HEADER, *(doc["text"] for doc in turn_docs)] if turn_docs else []
    hashes = {doc["key"]: _digest(doc["text"]) for doc in docs}
    sizes = {doc["key"]: len(doc["text"]) for doc in docs}
    labels = {doc["key"]: str(doc.get("label") or doc["key"]) for doc in docs}
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
    removed: list[str] = []
    if reason is None:
        snapshot_hashes: dict[str, str] = state.get("hashes") or {}
        changed = [doc for doc in docs if snapshot_hashes.get(doc["key"]) != hashes[doc["key"]]]
        # An old snapshot copy still costs tokens even when this turn supplies its replacement.
        removed = [key for key in snapshot_hashes if key not in hashes]
        snapshot_sizes: dict[str, int] = state.get("sizes") or {}
        stale_chars = sum(len(doc["text"]) for doc in changed) + sum(int(snapshot_sizes.get(key) or 0) for key in removed)
        if stale_chars > REBUILD_CHANGED_RATIO * max(len(state.get("text") or ""), 1):
            reason = "outgrown"

    if reason is not None:
        snapshot_text = _render_snapshot(docs)
        _store_set(key, json.dumps({
            "v": SNAPSHOT_FORMAT_VERSION,
            "project_id": project_id,
            "language": language,
            "text": snapshot_text,
            "hashes": hashes,
            "sizes": sizes,
            "labels": labels,
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
            **(_stale_accounting(state, docs, hashes) if state else {}),
        )
        return TurnContextPlan(
            snapshot_message=snapshot_text,
            changed_context="\n\n".join(sections),
            rebuilt=True,
        )

    assert state is not None
    state["turns"] = int(state.get("turns") or 0) + 1
    _store_set(key, json.dumps(state, ensure_ascii=False))
    if changed:
        sections.extend([CHANGED_CONTEXT_HEADER, *(doc["text"] for doc in changed)])
    turn_keys = {doc["key"] for doc in turn_docs}
    unchecked = [key for key in removed if key not in turn_keys]
    if unchecked:
        snapshot_labels: dict[str, str] = state.get("labels") or {}
        sections.append("\n".join([GONE_CONTEXT_HEADER, *(f"- {snapshot_labels.get(key) or key}" for key in unchecked)]))
    log_with_context(
        logger,
        20,  # INFO
        "Context snapshot reused",
        snapshot_chars=len(state.get("text") or ""),
        changed_documents=len(changed),
        gone_documents=len(removed),
        turn_in_snapshot=state["turns"],
        **_stale_accounting(state, docs, hashes),
    )
    return TurnContextPlan(
        snapshot_message=str(state.get("text") or ""),
        changed_context="\n\n".join(sections),
        rebuilt=False,
    )


def reset_snapshot_memory() -> None:
    """Clear the in-process snapshot store; intended for tests."""
    global _memory
    _memory = _MemoryStore()
