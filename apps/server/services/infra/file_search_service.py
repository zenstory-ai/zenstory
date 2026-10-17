"""Project-scoped keyword retrieval from the primary file database."""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, select

from models import File
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

def serialize_datetime(obj: Any) -> Any:
    """
    递归转换对象中的datetime为ISO字符串。

    用于确保通过SSE发送的数据都是JSON可序列化的。

    处理:
    - datetime对象 -> ISO格式字符串
    - list/dict -> 递归处理每个元素
    - 其他类型 -> 保持不变

    Args:
        obj: 任意对象

    Returns:
        转换后的对象，datetime变为ISO字符串
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: serialize_datetime(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_datetime(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(serialize_datetime(item) for item in obj)
    else:
        return obj

def _get_int_env(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default

    if min_value is not None and value < min_value:
        return min_value
    return value

LEXICAL_MAX_QUERY_TOKENS = 8

LEXICAL_MAX_TOKEN_CHARS = 32

LEXICAL_MAX_PHRASE_CHARS = 64

FILE_SEARCH_DB_CANDIDATE_MULTIPLIER = _get_int_env(
    "FILE_SEARCH_DB_CANDIDATE_MULTIPLIER",
    3,  # previously 6x in _lexical_search
    min_value=1,
)

FILE_SEARCH_DB_CANDIDATE_CAP = _get_int_env(
    "FILE_SEARCH_DB_CANDIDATE_CAP",
    200,
    min_value=1,
)

FILE_SEARCH_TIME_BUDGET_MS = _get_int_env(
    "FILE_SEARCH_TIME_BUDGET_MS",
    80,
    min_value=0,
)

FILE_SEARCH_TIME_BUDGET_MIN_CANDIDATES = _get_int_env(
    "FILE_SEARCH_TIME_BUDGET_MIN_CANDIDATES",
    60,
    min_value=1,
)

FILE_SEARCH_CASE_INSENSITIVE_CONTENT_MAX_CHARS = _get_int_env(
    "FILE_SEARCH_CASE_INSENSITIVE_CONTENT_MAX_CHARS",
    200_000,
    min_value=1,
)

@dataclass
class SearchResult:
    """A matched file with a bounded preview and a line-numbered snippet."""

    entity_type: str  # file_type (outline, draft, character, lore, snippet)
    entity_id: str
    title: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    snippet: str = ""
    line_start: int | None = None
    fused_score: float | None = None
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        snippet = self.snippet or (self.content[:240] if self.content else "")
        resolved_line_start = self.line_start
        if resolved_line_start is None and snippet:
            resolved_line_start = 1
        resolved_fused_score = self.fused_score
        if resolved_fused_score is None:
            resolved_fused_score = self.score

        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "title": self.title,
            "content": self.content[:500] if len(self.content) > 500 else self.content,
            "score": self.score,
            "snippet": snippet,
            "line_start": resolved_line_start,
            "fused_score": resolved_fused_score,
            "sources": self.sources or ["lexical"],
            "metadata": serialize_datetime(self.metadata),
        }


class FileSearchUnavailableError(RuntimeError):
    """Database retrieval is busy or unavailable."""


_SEARCH_SLOTS = threading.BoundedSemaphore(2)


class FileSearchService:
    def search(
        self,
        project_id: str,
        query: str,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        include_content: bool = False,
        session: Session | None = None,
    ) -> list[SearchResult]:
        if not (query or "").strip():
            return []
        if not _SEARCH_SLOTS.acquire(blocking=False):
            raise FileSearchUnavailableError("file search concurrency limit reached")
        try:
            top_k = max(1, min(int(top_k), 50))
            # Score a wider pool than we return, as the removed hybrid_search did: the SQL step
            # orders candidates by updated_at, so an old file with a strong title match must
            # still reach the scorer.
            kwargs = {"project_id": project_id, "query": query, "top_k": min(top_k * 3, 120),
                      "entity_types": entity_types, "include_content": include_content}
            if session is not None:
                return self._lexical_search(session, **kwargs)[:top_k]
            from database import create_session

            with create_session() as owned_session:
                return self._lexical_search(owned_session, **kwargs)[:top_k]
        finally:
            _SEARCH_SLOTS.release()

    @staticmethod
    def _normalize_entity_types(entity_types: list[str] | None) -> set[str]:
        return {
            t.strip().lower()
            for t in (entity_types or [])
            if isinstance(t, str) and t.strip()
        }

    @staticmethod
    def _build_snippet(
        content: str,
        query: str,
        max_chars: int = 260,
    ) -> tuple[str, int | None]:
        text = (content or "").strip()
        if not text:
            return "", None

        normalized_query = (query or "").strip()
        if not normalized_query:
            return text[:max_chars], 1

        # Fast path: try case-sensitive match first (no allocations).
        raw_terms: list[str] = [normalized_query]
        raw_terms.extend(
            token
            for token in re.split(r"\s+", normalized_query)
            if token and token not in raw_terms
        )

        match_index = -1
        for term in raw_terms:
            idx = text.find(term)
            if idx == -1:
                continue
            if match_index == -1 or idx < match_index:
                match_index = idx

        # Slow path: case-insensitive search (may allocate), guarded for huge texts.
        if match_index == -1:
            if len(text) > FILE_SEARCH_CASE_INSENSITIVE_CONTENT_MAX_CHARS:
                return text[:max_chars], 1

            lowered_text = text.lower()
            lowered_query = normalized_query.lower()

            search_terms: list[str] = [lowered_query]
            search_terms.extend(
                token
                for token in re.split(r"\s+", lowered_query)
                if token and token not in search_terms
            )

            for term in search_terms:
                idx = lowered_text.find(term)
                if idx == -1:
                    continue
                if match_index == -1 or idx < match_index:
                    match_index = idx

        if match_index == -1:
            return text[:max_chars], 1

        start_idx = max(0, match_index - max_chars // 3)
        end_idx = min(len(text), start_idx + max_chars)
        snippet = text[start_idx:end_idx]
        line_start = text.count("\n", 0, start_idx) + 1
        return snippet, line_start

    @staticmethod
    def _compute_lexical_score(
        title: str,
        content: str,
        query: str,
    ) -> float:
        raw_query = (query or "").strip()
        if not raw_query:
            return 0.0

        normalized_query = raw_query.lower()
        normalized_title = (title or "").lower()
        phrase_term = normalized_query if len(normalized_query) <= LEXICAL_MAX_PHRASE_CHARS else ""
        raw_phrase_term = raw_query if len(raw_query) <= LEXICAL_MAX_PHRASE_CHARS else ""

        raw_content = content or ""
        normalized_content: str | None = None
        use_case_insensitive_content = (
            len(raw_content) <= FILE_SEARCH_CASE_INSENSITIVE_CONTENT_MAX_CHARS
        )
        if raw_content and use_case_insensitive_content:
            # NOTE: This allocates a lowercase copy; guardrails prevent doing this for huge drafts.
            normalized_content = raw_content.lower()

        score = 0.0
        if phrase_term and phrase_term in normalized_title:
            score += 3.0
        if raw_content:
            if normalized_content is not None:
                if phrase_term and phrase_term in normalized_content:
                    score += 2.0
            else:
                # Case-sensitive fallback for oversized content (avoids allocating large lowercase copy).
                if raw_phrase_term and raw_phrase_term in raw_content:
                    score += 2.0

        tokens: list[str] = []
        seen_tokens: set[str] = set()
        for token in re.split(r"\s+", normalized_query):
            normalized_token = token.strip()
            if not normalized_token:
                continue
            if len(normalized_token) > LEXICAL_MAX_TOKEN_CHARS:
                continue
            if normalized_token in seen_tokens:
                continue
            seen_tokens.add(normalized_token)
            tokens.append(normalized_token)
            if len(tokens) >= LEXICAL_MAX_QUERY_TOKENS:
                break

        if not tokens:
            fallback = normalized_query[:LEXICAL_MAX_TOKEN_CHARS].strip()
            if fallback:
                tokens = [fallback]
            elif phrase_term:
                tokens = [phrase_term]
            else:
                return 0.0

        title_hits = sum(1 for token in tokens if token in normalized_title)
        if not raw_content:
            content_hits = 0
        elif normalized_content is not None:
            content_hits = sum(1 for token in tokens if token in normalized_content)
        else:
            # Case-sensitive fallback for oversized content. This matches the SQL prefilter
            # behavior (contains/LIKE) in Postgres and avoids allocating huge strings.
            raw_tokens: list[str] = []
            seen_raw: set[str] = set()
            for token in re.split(r"\s+", raw_query):
                normalized_token = token.strip()
                if not normalized_token:
                    continue
                if len(normalized_token) > LEXICAL_MAX_TOKEN_CHARS:
                    continue
                dedupe_key = normalized_token.lower()
                if dedupe_key in seen_raw:
                    continue
                seen_raw.add(dedupe_key)
                raw_tokens.append(normalized_token)
                if len(raw_tokens) >= LEXICAL_MAX_QUERY_TOKENS:
                    break
            content_hits = sum(1 for token in raw_tokens if token in raw_content)

        token_count = max(1, len(tokens))
        score += 1.5 * (title_hits / token_count)
        score += 1.0 * (content_hits / token_count)
        return score

    def _lexical_search(
        self,
        session: Session,
        project_id: str,
        query: str,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        include_content: bool = False,
    ) -> list[SearchResult]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        target_entity_types = self._normalize_entity_types(entity_types) or None
        candidate_limit = min(
            max(top_k * FILE_SEARCH_DB_CANDIDATE_MULTIPLIER, top_k),
            FILE_SEARCH_DB_CANDIDATE_CAP,
        )
        search_tokens: list[str] = []
        seen_tokens: set[str] = set()
        for token in re.split(r"\s+", normalized_query):
            normalized_token = token.strip()
            if not normalized_token:
                continue
            if len(normalized_token) > LEXICAL_MAX_TOKEN_CHARS:
                continue
            lowered = normalized_token.lower()
            if lowered in seen_tokens:
                continue
            seen_tokens.add(lowered)
            search_tokens.append(normalized_token)
            if len(search_tokens) >= LEXICAL_MAX_QUERY_TOKENS:
                break

        phrase_term = normalized_query if len(normalized_query) <= LEXICAL_MAX_PHRASE_CHARS else ""
        if not search_tokens:
            fallback = normalized_query[:LEXICAL_MAX_TOKEN_CHARS].strip()
            if fallback:
                search_tokens = [fallback]
            elif phrase_term:
                search_tokens = [phrase_term]
            else:
                return []

        text_conditions: list[Any] = []
        # Include full phrase and token-level match to improve multi-term recall.
        seen_terms: set[str] = set()
        for term in [phrase_term, *search_tokens]:
            normalized_term = term.strip()
            if not normalized_term:
                continue
            lowered_term = normalized_term.lower()
            if lowered_term in seen_terms:
                continue
            seen_terms.add(lowered_term)
            text_conditions.append(File.title.contains(normalized_term))
            text_conditions.append(File.content.contains(normalized_term))  # type: ignore[attr-defined]

        stmt = (
            select(File)
            .options(
                load_only(
                    File.id,
                    File.title,
                    File.content,
                    File.file_type,
                    File.updated_at,
                )
            )
            .where(
                File.project_id == project_id,
                File.file_type != "folder",
                File.is_deleted.is_(False),
                or_(*text_conditions),
            )
            .order_by(col(File.updated_at).desc())
            .limit(candidate_limit)
        )
        if target_entity_types:
            stmt = stmt.where(col(File.file_type).in_(list(target_entity_types)))

        candidates = list(session.exec(stmt).all())
        ranked: list[tuple[float, File]] = []
        deadline: float | None = None
        if (
            FILE_SEARCH_TIME_BUDGET_MS > 0
            and len(candidates) >= FILE_SEARCH_TIME_BUDGET_MIN_CANDIDATES
        ):
            deadline = time.monotonic() + (FILE_SEARCH_TIME_BUDGET_MS / 1000.0)

        processed = 0
        for file in candidates:
            processed += 1
            score = self._compute_lexical_score(
                file.title or "", file.content or "", normalized_query
            )
            if score <= 0:
                continue
            ranked.append((score, file))

            if deadline is not None and processed % 5 == 0 and time.monotonic() >= deadline:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Lexical search time budget exceeded; early stopping",
                    project_id=project_id,
                    top_k=top_k,
                    candidate_limit=candidate_limit,
                    candidates_seen=processed,
                    candidates_total=len(candidates),
                    time_budget_ms=FILE_SEARCH_TIME_BUDGET_MS,
                )
                break

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].updated_at.isoformat() if item[1].updated_at else "",
            ),
            reverse=True,
        )

        results: list[SearchResult] = []
        for rank, (lexical_score, file) in enumerate(ranked[:top_k], start=1):
            snippet, line_start = self._build_snippet(file.content or "", normalized_query)
            raw_content = file.content or ""
            response_content = raw_content if include_content else raw_content[:500]
            results.append(
                SearchResult(
                    entity_type=file.file_type,
                    entity_id=file.id,
                    title=file.title or "",
                    content=response_content,
                    score=lexical_score,
                    metadata={"lexical_rank": rank},
                    snippet=snippet,
                    line_start=line_start,
                    fused_score=lexical_score,
                    sources=["lexical"],
                )
            )

        return results
