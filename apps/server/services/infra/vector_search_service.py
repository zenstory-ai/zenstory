"""
LlamaIndex + ChromaDB vector retrieval service for semantic search.

Provides:
- Project-scoped vector indexes with ChromaDB persistence
- Full project indexing using unified File model
- Incremental updates (add/update/delete files)
- Semantic search across all file types
"""

import asyncio
import contextlib
import functools
import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ChromaDB
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except Exception as exc:  # pragma: no cover - import-time optional dependency
    chromadb = None  # type: ignore[assignment]
    ChromaSettings = None  # type: ignore[assignment]
    _CHROMADB_IMPORT_ERROR: Exception | None = exc
else:
    _CHROMADB_IMPORT_ERROR = None

# LlamaIndex imports
from llama_index.core import (
    Document,
    Settings,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import NodeWithScore
from pydantic import Field, PrivateAttr
from sqlalchemy import or_
from sqlmodel import Session, col, select

try:
    from llama_index.vector_stores.chroma import ChromaVectorStore
except Exception as exc:  # pragma: no cover - depends on optional chroma stack
    ChromaVectorStore = None  # type: ignore[assignment]
    if _CHROMADB_IMPORT_ERROR is None:
        _CHROMADB_IMPORT_ERROR = exc

# Local models
from config.datetime_utils import utcnow
from models import File
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


# Chroma 元数据只接受标量（llama-index ChromaVectorStore 默认 flat_metadata=True，
# 遇到 dict/list 直接 ValueError）。bool 是 int 的子类，同样可以写入。
_INDEXABLE_METADATA_TYPES = (str, int, float, type(None))


def _to_indexable_metadata_value(value: Any) -> Any:
    """把 file_metadata 里的嵌套 dict/list 等值转成 Chroma 能存的标量。"""
    if isinstance(value, _INDEXABLE_METADATA_TYPES):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


class VectorSearchUnavailableError(RuntimeError):
    """Raised when all attempted hybrid retrieval backends fail."""


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


# Configuration
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_data")
COLLECTION_PREFIX = "zenstory_project_"
EMBEDDING_MODEL = os.getenv("ZHIPU_EMBEDDINGS_MODEL", "embedding-3")
HYBRID_RRF_K = 60
HYBRID_SEMANTIC_WEIGHT = 0.65
HYBRID_LEXICAL_WEIGHT = 0.35
LEXICAL_MAX_QUERY_TOKENS = 8
LEXICAL_MAX_TOKEN_CHARS = 32
LEXICAL_MAX_PHRASE_CHARS = 64


def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


def _get_float_env(name: str, default: float, *, min_value: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default

    if min_value is not None and value < min_value:
        return min_value
    return value


# Zhipu embedding 客户端的网络上限。SDK 默认 300 秒读超时 + 3 次重试：上游挂起
# （不回包也不断连）时一次 query embedding 能卡住约 20 分钟，期间占着 asyncio
# 默认 executor 的一条线程。这里显式收紧：连接 3 秒、读 10 秒、最多重试 1 次。
ZHIPU_EMBEDDING_CONNECT_TIMEOUT_S = _get_float_env(
    "ZHIPU_EMBEDDING_CONNECT_TIMEOUT_S", 3.0, min_value=0.1
)
ZHIPU_EMBEDDING_READ_TIMEOUT_S = _get_float_env(
    "ZHIPU_EMBEDDING_READ_TIMEOUT_S", 10.0, min_value=0.1
)
ZHIPU_EMBEDDING_MAX_RETRIES = min(
    _get_int_env("ZHIPU_EMBEDDING_MAX_RETRIES", 1, min_value=0), 1
)


def build_zhipu_client_options() -> dict[str, Any]:
    """ZhipuAiClient 的 timeout / max_retries 参数（单独抽出便于测试）。"""
    import httpx

    return {
        "timeout": httpx.Timeout(
            ZHIPU_EMBEDDING_READ_TIMEOUT_S,
            connect=ZHIPU_EMBEDDING_CONNECT_TIMEOUT_S,
        ),
        "max_retries": ZHIPU_EMBEDDING_MAX_RETRIES,
    }


# 混合检索里语义分支（query embedding + Chroma 查询）的外层时限。调用方传
# semantic_timeout_s 时语义检索放进专用线程池执行，超时即按语义失败处理、只用
# 词法结果；工作线程会在上面的客户端超时内自行结束。
_SEMANTIC_SEARCH_EXECUTOR_WORKERS = _get_int_env(
    "HYBRID_SEMANTIC_EXECUTOR_WORKERS", 4, min_value=1
)
_semantic_search_executor: Any = None
_semantic_search_executor_lock = threading.Lock()


def _get_semantic_search_executor() -> Any:
    global _semantic_search_executor
    with _semantic_search_executor_lock:
        if _semantic_search_executor is None:
            from concurrent.futures import ThreadPoolExecutor

            _semantic_search_executor = ThreadPoolExecutor(
                max_workers=_SEMANTIC_SEARCH_EXECUTOR_WORKERS,
                thread_name_prefix="semantic-search",
            )
        return _semantic_search_executor


# Semantic search guardrails (embedding safety).
#
# Some embedding providers reject overly long `input` with generic parameter
# errors. The agent frequently passes long user text as retrieval query (e.g.
# draft chapters), so we defensively truncate queries before embedding.
#
# Zhipu embedding docs: each input supports up to 3072 tokens, array <= 64.
#
# For retrieval queries, we intentionally stay well below the hard limit to:
# - reduce cost/latency
# - avoid diluting semantic intent with chapter-length text
SEMANTIC_QUERY_MAX_TOKENS = 768
# Fallback when tokenizer is unavailable.
SEMANTIC_QUERY_MAX_CHARS = 3000
# How many chars of query preview to include in logs (set to 0 to disable).
SEMANTIC_QUERY_LOG_PREVIEW_CHARS = 120


def _safe_sha256(text: str) -> str | None:
    """Return short sha256 for logs; never raises."""
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    except Exception:  # pragma: no cover - extremely defensive
        return None


def _truncate_middle(text: str, max_chars: int, *, tail_chars: int) -> str:
    """Keep head+tail with a marker when text is too long."""
    normalized = (text or "").strip()
    if not normalized:
        return ""

    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized

    marker = "\n...\n"
    if tail_chars <= 0 or max_chars <= (len(marker) + 32):
        return normalized[:max_chars].rstrip()

    tail_chars = min(tail_chars, max_chars - len(marker) - 16)
    head_chars = max_chars - tail_chars - len(marker)
    if head_chars <= 0:
        return normalized[:max_chars].rstrip()

    head = normalized[:head_chars].rstrip()
    tail = normalized[-tail_chars:].lstrip()
    return f"{head}{marker}{tail}"


def _truncate_query_for_embedding(query: str) -> tuple[str, dict[str, Any]]:
    """
    Truncate query to stay within embedding provider input limits.

    Prefer token-based truncation (via tiktoken) because provider limits are in
    tokens. Fall back to character-based truncation if tokenizer is unavailable.
    """
    normalized_query = (query or "").strip()
    info: dict[str, Any] = {
        "original_length": len(normalized_query),
    }
    if not normalized_query:
        return "", info

    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(normalized_query)
        info["original_tokens"] = len(tokens)
        if len(tokens) <= SEMANTIC_QUERY_MAX_TOKENS:
            return normalized_query, info

        marker = "\n...\n"
        marker_tokens = encoding.encode(marker)
        marker_cost = len(marker_tokens)

        tail_tokens = min(256, max(0, SEMANTIC_QUERY_MAX_TOKENS // 3))
        middle_tokens = min(128, max(0, (SEMANTIC_QUERY_MAX_TOKENS - tail_tokens) // 3))
        available = SEMANTIC_QUERY_MAX_TOKENS - tail_tokens - middle_tokens
        if middle_tokens > 0:
            available -= marker_cost * 2
        head_tokens = max(0, available)

        if head_tokens <= 0 or tail_tokens <= 0:
            kept_tokens = tokens[:SEMANTIC_QUERY_MAX_TOKENS]
        elif middle_tokens <= 0:
            kept_tokens = tokens[:head_tokens] + tokens[-tail_tokens:]
        else:
            # Sample head + middle + tail to reduce semantic dilution.
            mid_center = len(tokens) // 2
            mid_start = max(head_tokens, mid_center - (middle_tokens // 2))
            mid_end = min(len(tokens) - tail_tokens, mid_start + middle_tokens)
            if mid_end - mid_start < middle_tokens:
                mid_start = max(head_tokens, mid_end - middle_tokens)

            kept_tokens = (
                tokens[:head_tokens]
                + marker_tokens
                + tokens[mid_start:mid_end]
                + marker_tokens
                + tokens[-tail_tokens:]
            )

        truncated = (encoding.decode(kept_tokens) or "").strip()
        if not truncated:
            tail_chars = min(800, max(0, SEMANTIC_QUERY_MAX_CHARS // 3))
            truncated = _truncate_middle(
                normalized_query,
                SEMANTIC_QUERY_MAX_CHARS,
                tail_chars=tail_chars,
            )

        info.update(
            {
                "truncated": True,
                "used_tokens": len(kept_tokens),
                "used_length": len(truncated),
            }
        )
        return truncated, info
    except Exception as exc:  # pragma: no cover - fallback for tokenizers
        marker = "\n...\n"
        max_chars = SEMANTIC_QUERY_MAX_CHARS
        tail_chars = min(800, max(0, max_chars // 3))
        middle_chars = min(600, max(0, (max_chars - tail_chars) // 3))
        available = max_chars - tail_chars - middle_chars
        if middle_chars > 0:
            available -= len(marker) * 2
        head_chars = max(0, available)

        if head_chars <= 0:
            truncated = normalized_query[:max_chars].rstrip()
        elif middle_chars <= 0 or tail_chars <= 0:
            truncated = _truncate_middle(normalized_query, max_chars, tail_chars=tail_chars)
        else:
            mid_center = len(normalized_query) // 2
            mid_start = max(head_chars, mid_center - (middle_chars // 2))
            mid_end = min(len(normalized_query) - tail_chars, mid_start + middle_chars)
            if mid_end - mid_start < middle_chars:
                mid_start = max(head_chars, mid_end - middle_chars)
            head = normalized_query[:head_chars].rstrip()
            mid = normalized_query[mid_start:mid_end].strip()
            tail = normalized_query[-tail_chars:].lstrip()
            truncated = f"{head}{marker}{mid}{marker}{tail}".strip()

        info.update(
            {
                "truncated": truncated != normalized_query,
                "used_length": len(truncated),
                "tokenizer_error": str(exc),
            }
        )
        return truncated, info


# Hybrid search guardrails (perf safety).
#
# These defaults aim to keep hybrid retrieval quality while preventing CPU spikes
# during high-concurrency agent streaming.
HYBRID_ENABLE_LEXICAL = _get_bool_env("HYBRID_ENABLE_LEXICAL", True)
HYBRID_LEXICAL_DB_CANDIDATE_MULTIPLIER = _get_int_env(
    "HYBRID_LEXICAL_DB_CANDIDATE_MULTIPLIER",
    3,  # previously 6x in _lexical_search
    min_value=1,
)
HYBRID_LEXICAL_DB_CANDIDATE_CAP = _get_int_env(
    "HYBRID_LEXICAL_DB_CANDIDATE_CAP",
    200,
    min_value=1,
)
HYBRID_LEXICAL_MAX_CONCURRENCY = _get_int_env(
    "HYBRID_LEXICAL_MAX_CONCURRENCY",
    2,
    min_value=0,
)
HYBRID_LEXICAL_TIME_BUDGET_MS = _get_int_env(
    "HYBRID_LEXICAL_TIME_BUDGET_MS",
    80,
    min_value=0,
)
HYBRID_LEXICAL_TIME_BUDGET_MIN_CANDIDATES = _get_int_env(
    "HYBRID_LEXICAL_TIME_BUDGET_MIN_CANDIDATES",
    60,
    min_value=1,
)
# Avoid allocating huge lowercase copies for very large draft files.
HYBRID_LEXICAL_CASE_INSENSITIVE_CONTENT_MAX_CHARS = _get_int_env(
    "HYBRID_LEXICAL_CASE_INSENSITIVE_CONTENT_MAX_CHARS",
    200_000,
    min_value=1,
)

_LEXICAL_SEARCH_SEMAPHORE: threading.Semaphore | None = (
    threading.Semaphore(HYBRID_LEXICAL_MAX_CONCURRENCY)
    if HYBRID_LEXICAL_MAX_CONCURRENCY > 0
    else None
)


class ZhipuEmbedding(BaseEmbedding):
    """Zhipu embedding adapter for LlamaIndex."""

    api_key: str = Field(exclude=True)
    base_url: str | None = Field(default=None, exclude=True)

    # BaseEmbedding already defines model_name; we reuse it as Zhipu model.
    model_name: str = Field(default="embedding-3", description="Zhipu embedding model")

    _client: Any = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        # Local import to avoid hard dependency at import time
        from zai import ZhipuAiClient

        kwargs: dict[str, Any] = {"api_key": self.api_key, **build_zhipu_client_options()}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = ZhipuAiClient(**kwargs)

    def _get_text_embedding(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(
            model=self.model_name,
            input=text,
        )
        return resp.data[0].embedding

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return await asyncio.to_thread(self._get_query_embedding, query)

    def get_text_embedding_batch(
        self,
        texts: list[str],
        _show_progress: bool = False,
        **_kwargs: Any,
    ) -> list[list[float]]:
        if not texts:
            return []

        resp = self._client.embeddings.create(
            model=self.model_name,
            input=texts,
        )
        data = resp.data

        # If indexes exist, keep original order
        if data and getattr(data[0], "index", None) is not None:
            data = sorted(data, key=lambda x: x.index or 0)

        return [item.embedding for item in data]


@dataclass
class SearchResult:
    """Search result from semantic search."""

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
            "sources": self.sources or ["semantic"],
            "metadata": serialize_datetime(self.metadata),
        }


@dataclass
class IndexStats:
    """Statistics from indexing operation."""

    project_id: str
    total_documents: int
    outline_count: int
    draft_count: int
    character_count: int
    lore_count: int
    snippet_count: int
    duration_ms: float
    timestamp: str = field(default_factory=lambda: utcnow().isoformat())


class LlamaIndexService:
    """
    LlamaIndex + ChromaDB vector retrieval service.

    Features:
    - Project-scoped collections (one per project)
    - Multi-type file indexing (outline, draft, character, lore, snippet)
    - Semantic search with metadata filtering
    - Incremental updates
    """

    def __init__(self, persist_dir: str = CHROMA_PERSIST_DIR):
        """
        Initialize LlamaIndex service with ChromaDB backend.

        Args:
            persist_dir: Directory for ChromaDB persistence
        """
        self.persist_dir = persist_dir

        if chromadb is None or ChromaSettings is None or ChromaVectorStore is None:
            raise RuntimeError(
                "ChromaDB dependency is unavailable. "
                "Install compatible versions for this Python runtime to enable vector search."
            ) from _CHROMADB_IMPORT_ERROR

        # Clear Chroma shared system cache to avoid stale client issues
        chromadb.api.client.SharedSystemClient.clear_system_cache()

        # Initialize ChromaDB client with persistence
        self.chroma_client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(
                anonymized_telemetry=False,
                allow_reset=True,
            )
        )

        # Initialize embedding model (Zhipu)
        api_key = os.getenv("ZHIPU_EMBEDDINGS_API_KEY")
        base_url = os.getenv("ZHIPU_EMBEDDINGS_BASE_URL")

        if not api_key:
            raise ValueError("ZHIPU_EMBEDDINGS_API_KEY not found in environment")

        self.embed_model = ZhipuEmbedding(
            api_key=api_key,
            base_url=base_url,
            model_name=EMBEDDING_MODEL,
        )

        # Set global settings for LlamaIndex
        Settings.embed_model = self.embed_model
        Settings.chunk_size = 1024
        Settings.chunk_overlap = 128

        # Cache for indexes (project_id -> index)
        self._index_cache: dict[str, VectorStoreIndex] = {}
        self._cache_lock = threading.RLock()

    def _get_collection_name(self, project_id: str) -> str:
        """Get ChromaDB collection name for a project."""
        return f"{COLLECTION_PREFIX}{project_id}"

    def _get_doc_id(self, entity_type: str, entity_id: str) -> str:
        """Generate unique document ID."""
        return f"{entity_type}_{entity_id}"

    def get_or_create_index(self, project_id: str) -> VectorStoreIndex:
        """
        Get or create a VectorStoreIndex for a project (thread-safe).

        Args:
            project_id: Project ID

        Returns:
            VectorStoreIndex instance
        """
        # Fast path: check cache without lock (dict reads are atomic in CPython)
        if project_id in self._index_cache:
            return self._index_cache[project_id]

        # Slow path: acquire lock, double-check, then create
        with self._cache_lock:
            # Double-check: another thread may have created it
            if project_id in self._index_cache:
                return self._index_cache[project_id]

            # Create new index (inside lock to ensure only one creation)
            collection_name = self._get_collection_name(project_id)
            chroma_collection = self.chroma_client.get_or_create_collection(
                name=collection_name,
                metadata={"project_id": project_id},
            )

            vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
            storage_context = StorageContext.from_defaults(vector_store=vector_store)

            index = VectorStoreIndex.from_documents(
                documents=[],
                storage_context=storage_context,
                embed_model=self.embed_model,
            )

            self._index_cache[project_id] = index
            return index

    def _entity_to_document(
        self,
        entity_type: str,
        entity_id: str,
        title: str,
        content: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> Document:
        """
        Convert an entity to a LlamaIndex Document.

        Args:
            entity_type: Type of entity (outline, draft, character, lore, snippet)
            entity_id: Entity ID
            title: Entity title/name
            content: Entity content
            extra_metadata: Additional metadata fields

        Returns:
            LlamaIndex Document
        """
        # Build metadata
        metadata = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "title": title,
        }
        if extra_metadata:
            metadata.update(
                {key: _to_indexable_metadata_value(value) for key, value in extra_metadata.items()}
            )

        # Combine title and content for better retrieval
        text = f"# {title}\n\n{content}" if content else f"# {title}"

        return Document(
            text=text,
            doc_id=self._get_doc_id(entity_type, entity_id),
            metadata=metadata,
        )

    def _file_to_document(self, file: File) -> Document:
        """Convert File entity to Document."""
        # Parse file_metadata if present
        extra_metadata = {"parent_id": file.parent_id}
        if file.file_metadata:
            try:
                parsed_meta = json.loads(file.file_metadata)
                extra_metadata.update(parsed_meta)
            except (json.JSONDecodeError, TypeError):
                pass

        return self._entity_to_document(
            entity_type=file.file_type,
            entity_id=file.id,
            title=file.title,
            content=file.content or "",
            extra_metadata=extra_metadata,
        )

    def index_project(self, session: Session, project_id: str) -> IndexStats:
        """
        Index all files for a project (full rebuild).

        Args:
            session: Database session
            project_id: Project ID to index

        Returns:
            IndexStats with indexing results
        """
        import time
        start_time = time.time()

        documents: list[Document] = []
        counts = {
            "outline": 0,
            "draft": 0,
            "character": 0,
            "lore": 0,
            "snippet": 0,
        }

        # Collect all files (exclude folders)
        files = session.exec(
            select(File).where(
                File.project_id == project_id,
                File.file_type != "folder",
                File.is_deleted.is_(False)
            )
        ).all()

        for file in files:
            documents.append(self._file_to_document(file))
            file_type = file.file_type
            if file_type in counts:
                counts[file_type] += 1

        # 先在临时 collection 里建好新索引（embedding 失败、某个文件的元数据
        # 写不进去等任何异常都发生在这一步），成功后才删除旧 collection 并把
        # 临时 collection 改名接替。失败时只清理临时 collection，旧索引原样保留，
        # 不会出现「旧的已删、新的没建成」导致整个项目语义检索变空。
        collection_name = self._get_collection_name(project_id)
        staging_name = f"{collection_name}_rebuild_{uuid.uuid4().hex[:12]}"
        staging_collection = self.chroma_client.create_collection(
            name=staging_name,
            metadata={"project_id": project_id},
        )
        try:
            if documents:
                vector_store = ChromaVectorStore(chroma_collection=staging_collection)
                storage_context = StorageContext.from_defaults(vector_store=vector_store)
                VectorStoreIndex.from_documents(
                    documents=documents,
                    storage_context=storage_context,
                    embed_model=self.embed_model,
                    show_progress=True,
                )
        except BaseException:
            with contextlib.suppress(Exception):
                self.chroma_client.delete_collection(staging_name)
            raise

        with self._cache_lock:
            with contextlib.suppress(Exception):
                self.chroma_client.delete_collection(collection_name)
            staging_collection.modify(name=collection_name)
            # 缓存里的旧 index 指向已删除的 collection：清掉，下次按名字重新加载。
            self._index_cache.pop(project_id, None)

        duration_ms = (time.time() - start_time) * 1000

        return IndexStats(
            project_id=project_id,
            total_documents=len(documents),
            outline_count=counts["outline"],
            draft_count=counts["draft"],
            character_count=counts["character"],
            lore_count=counts["lore"],
            snippet_count=counts["snippet"],
            duration_ms=duration_ms,
        )

    async def aindex_project(self, session: Session, project_id: str) -> IndexStats:
        """
        Async version of index_project.

        Runs the synchronous indexing in a thread pool to avoid blocking.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.index_project,
            session,
            project_id,
        )

    def update_entity(
        self,
        project_id: str,
        entity_type: str,
        entity_id: str,
        title: str,
        content: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Update a single entity in the index.

        Args:
            project_id: Project ID
            entity_type: Type of entity (file_type)
            entity_id: Entity ID
            title: Entity title
            content: Entity content
            extra_metadata: Additional metadata

        Returns:
            True if successful
        """
        try:
            index = self.get_or_create_index(project_id)
            doc_id = self._get_doc_id(entity_type, entity_id)

            # Create new document
            doc = self._entity_to_document(
                entity_type=entity_type,
                entity_id=entity_id,
                title=title,
                content=content,
                extra_metadata=extra_metadata,
            )

            # Delete old document if exists
            with contextlib.suppress(Exception):
                index.delete_ref_doc(doc_id, delete_from_docstore=True)

            # Insert new document
            index.insert(doc)

            return True
        except Exception as e:
            log_with_context(
                logger,
                40,  # ERROR
                "Error updating entity in index",
                project_id=project_id,
                entity_type=entity_type,
                entity_id=entity_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    def delete_entity(
        self,
        project_id: str,
        entity_type: str,
        entity_id: str,
    ) -> bool:
        """
        Delete an entity from the index.

        Args:
            project_id: Project ID
            entity_type: Type of entity
            entity_id: Entity ID

        Returns:
            True if successful
        """
        try:
            index = self.get_or_create_index(project_id)
            doc_id = self._get_doc_id(entity_type, entity_id)

            index.delete_ref_doc(doc_id, delete_from_docstore=True)
            return True
        except Exception as e:
            log_with_context(
                logger,
                40,  # ERROR
                "Error deleting entity from index",
                project_id=project_id,
                entity_type=entity_type,
                entity_id=entity_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    def _validate_entity_ownership(
        self,
        project_id: str,
        entity_type: str,
        entity_id: str,
        session: Session,
    ) -> bool:
        """
        Validate that an entity belongs to the specified project.
        Defense-in-depth check to prevent data leakage.

        Args:
            project_id: Project ID to validate against
            entity_type: Type of entity (file_type)
            entity_id: Entity ID to validate
            session: Database session

        Returns:
            True if entity belongs to project and is not deleted, False otherwise
        """
        file = session.get(File, entity_id)
        if file is None:
            return False

        # Compare as strings to handle UUID vs string comparison
        if str(file.project_id) != str(project_id):
            return False

        if file.is_deleted:
            return False

        # Folders are intentionally excluded from semantic search index/results.
        if file.file_type == "folder":
            return False

        # 防御性校验：索引中的 entity_type 必须与数据库真实 file_type 一致。
        normalized_entity_type = (entity_type or "").strip().lower()
        normalized_file_type = (file.file_type or "").strip().lower()
        return not (normalized_entity_type and normalized_entity_type != normalized_file_type)

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
            if len(text) > HYBRID_LEXICAL_CASE_INSENSITIVE_CONTENT_MAX_CHARS:
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
            len(raw_content) <= HYBRID_LEXICAL_CASE_INSENSITIVE_CONTENT_MAX_CHARS
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
            max(top_k * HYBRID_LEXICAL_DB_CANDIDATE_MULTIPLIER, top_k),
            HYBRID_LEXICAL_DB_CANDIDATE_CAP,
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
            HYBRID_LEXICAL_TIME_BUDGET_MS > 0
            and len(candidates) >= HYBRID_LEXICAL_TIME_BUDGET_MIN_CANDIDATES
        ):
            deadline = time.monotonic() + (HYBRID_LEXICAL_TIME_BUDGET_MS / 1000.0)

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
                    time_budget_ms=HYBRID_LEXICAL_TIME_BUDGET_MS,
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

    def _fuse_hybrid_results(
        self,
        semantic_results: list[SearchResult],
        lexical_results: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        semantic_ranked: dict[str, tuple[int, SearchResult]] = {
            result.entity_id: (rank, result)
            for rank, result in enumerate(semantic_results, start=1)
        }
        lexical_ranked: dict[str, tuple[int, SearchResult]] = {
            result.entity_id: (rank, result)
            for rank, result in enumerate(lexical_results, start=1)
        }

        merged_ids: set[str] = set()
        merged_ids.update(semantic_ranked.keys())
        merged_ids.update(lexical_ranked.keys())

        fused_results: list[SearchResult] = []
        for entity_id in merged_ids:
            semantic_entry = semantic_ranked.get(entity_id)
            lexical_entry = lexical_ranked.get(entity_id)
            semantic_rank = semantic_entry[0] if semantic_entry else None
            lexical_rank = lexical_entry[0] if lexical_entry else None
            semantic_result = semantic_entry[1] if semantic_entry else None
            lexical_result = lexical_entry[1] if lexical_entry else None

            semantic_rrf = 1.0 / (HYBRID_RRF_K + semantic_rank) if semantic_rank else 0.0
            lexical_rrf = 1.0 / (HYBRID_RRF_K + lexical_rank) if lexical_rank else 0.0
            fused_score = (HYBRID_SEMANTIC_WEIGHT * semantic_rrf) + (
                HYBRID_LEXICAL_WEIGHT * lexical_rrf
            )

            primary = semantic_result or lexical_result
            if primary is None:
                continue

            metadata = dict(primary.metadata)
            if semantic_rank is not None:
                metadata["semantic_rank"] = semantic_rank
            if lexical_rank is not None:
                metadata["lexical_rank"] = lexical_rank

            sources: list[str] = []
            if semantic_rank is not None:
                sources.append("semantic")
            if lexical_rank is not None:
                sources.append("lexical")

            fallback_snippet = lexical_result.snippet if lexical_result else ""
            fallback_line_start = lexical_result.line_start if lexical_result else None

            fused_results.append(
                SearchResult(
                    entity_type=primary.entity_type,
                    entity_id=primary.entity_id,
                    title=primary.title,
                    content=primary.content,
                    score=primary.score,
                    metadata=metadata,
                    snippet=primary.snippet or fallback_snippet,
                    line_start=(
                        primary.line_start
                        if primary.line_start is not None
                        else fallback_line_start
                    ),
                    fused_score=fused_score,
                    sources=sources,
                )
            )

        fused_results.sort(
            key=lambda result: (
                result.fused_score or 0.0,
                result.score or 0.0,
            ),
            reverse=True,
        )
        return fused_results[:top_k]

    def semantic_search(
        self,
        project_id: str,
        query: str,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[SearchResult]:
        """
        Perform semantic search across project files with project_id validation.

        Args:
            project_id: Project ID
            query: Search query
            top_k: Number of top results to return
            entity_types: Filter by file types (optional)

        Returns:
            List of SearchResult objects
        """
        from database import create_session

        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        query_hash = _safe_sha256(normalized_query)
        retrieval_query, trunc_info = _truncate_query_for_embedding(normalized_query)
        if trunc_info.get("truncated"):
            log_with_context(
                logger,
                20,  # INFO
                "Semantic search query truncated for embedding",
                project_id=project_id,
                query_length=len(normalized_query),
                query_hash=query_hash,
                query_tokens=trunc_info.get("original_tokens"),
                used_length=trunc_info.get("used_length"),
                used_tokens=trunc_info.get("used_tokens"),
                preview=retrieval_query[:SEMANTIC_QUERY_LOG_PREVIEW_CHARS],
            )

        top_k = max(1, int(top_k or 10))
        index = self.get_or_create_index(project_id)
        normalized_entity_types = self._normalize_entity_types(entity_types)
        target_entity_types = normalized_entity_types or None

        initial_candidate_k = top_k
        initial_max_candidate_k = top_k
        if target_entity_types:
            initial_candidate_k = min(max(top_k * 2, top_k), 200)
            initial_max_candidate_k = 200

        def _run_search(retrieval_query: str) -> list[SearchResult]:
            candidate_k = initial_candidate_k
            max_candidate_k = initial_max_candidate_k

            # Convert to SearchResult with validation
            results: list[SearchResult] = []
            skipped_count = 0
            validated_cache: set[str] = set()  # Request-level cache
            seen_nodes: set[str] = set()  # Avoid duplicate nodes across adaptive fetches

            with create_session() as session:
                while True:
                    retriever = index.as_retriever(similarity_top_k=candidate_k)
                    nodes: list[NodeWithScore] = retriever.retrieve(retrieval_query)
                    if not nodes:
                        break

                    for node in nodes:
                        metadata = node.node.metadata
                        entity_type = str(metadata.get("entity_type", "unknown")).strip().lower()
                        entity_id = str(metadata.get("entity_id", "")).strip()
                        if not entity_id:
                            continue

                        node_id = (
                            getattr(node.node, "node_id", None)
                            or getattr(node.node, "id_", None)
                        )
                        if isinstance(node_id, str) and node_id.strip():
                            node_key = node_id.strip()
                        else:
                            node_key = f"{entity_type}:{entity_id}:{hash(node.node.text or '')}"

                        if node_key in seen_nodes:
                            continue
                        seen_nodes.add(node_key)

                        # Secondary validation: ensure result belongs to requested project
                        cache_key = f"{project_id}:{entity_type}:{entity_id}"
                        if cache_key not in validated_cache:
                            if not self._validate_entity_ownership(
                                project_id, entity_type, entity_id, session
                            ):
                                skipped_count += 1
                                log_with_context(
                                    logger,
                                    30,  # WARNING
                                    "Skipped search result: entity does not belong to project",
                                    project_id=project_id,
                                    entity_type=entity_type,
                                    entity_id=entity_id,
                                )
                                continue
                            validated_cache.add(cache_key)

                        # Filter by entity type if specified
                        if target_entity_types and entity_type not in target_entity_types:
                            continue

                        snippet, line_start = self._build_snippet(node.node.text or "", retrieval_query)
                        result = SearchResult(
                            entity_type=entity_type,
                            entity_id=entity_id,
                            title=metadata.get("title", ""),
                            content=node.node.text,
                            score=node.score or 0.0,
                            metadata={
                                k: v for k, v in metadata.items()
                                if k not in ("entity_type", "entity_id", "title")
                            },
                            snippet=snippet,
                            line_start=line_start,
                            fused_score=node.score or 0.0,
                            sources=["semantic"],
                        )
                        results.append(result)

                        if len(results) >= top_k:
                            break

                    if len(results) >= top_k:
                        break

                    # No type filter: a single retrieval pass is enough.
                    if not target_entity_types:
                        break

                    # No more candidates available from retriever.
                    if len(nodes) < candidate_k:
                        break

                    # Adaptive over-fetch for heavy post-filters.
                    if candidate_k >= max_candidate_k:
                        break
                    candidate_k = min(candidate_k * 2, max_candidate_k)

            if skipped_count > 0:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Search results filtered due to project mismatch",
                    project_id=project_id,
                    skipped_count=skipped_count,
                    returned_count=len(results),
                )

            return results

        try:
            return _run_search(retrieval_query)
        except Exception as e:
            log_with_context(
                logger,
                40,  # ERROR
                "Semantic search error",
                project_id=project_id,
                query_length=len(normalized_query),
                query_hash=query_hash,
                query_tokens=trunc_info.get("original_tokens"),
                used_tokens=trunc_info.get("used_tokens"),
                truncated=bool(trunc_info.get("truncated")),
                used_length=len(retrieval_query),
                error=str(e),
                error_type=type(e).__name__,
            )
            if raise_on_error:
                raise
            return []

    def hybrid_search(
        self,
        project_id: str,
        query: str,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        include_content: bool = False,
        semantic_timeout_s: float | None = None,
    ) -> list[SearchResult]:
        """
        Perform hybrid search by fusing semantic and lexical retrieval.

        semantic_timeout_s: 语义分支的外层时限（秒）。超时按语义失败处理，
        只返回词法结果；None 表示不设外层时限（沿用客户端自身的超时）。
        """

        from database import create_session

        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        top_k = max(1, int(top_k or 10))
        semantic_candidate_k = min(max(top_k * 3, top_k), 120)
        lexical_candidate_k = min(max(top_k * 3, top_k), 120)

        semantic_results: list[SearchResult] = []
        semantic_error: Exception | None = None
        semantic_completed = False
        try:
            if semantic_timeout_s is None:
                semantic_results = self.semantic_search(
                    project_id=project_id,
                    query=normalized_query,
                    top_k=semantic_candidate_k,
                    entity_types=entity_types,
                    raise_on_error=True,
                )
            else:
                import contextvars
                from concurrent.futures import TimeoutError as FutureTimeoutError

                future = _get_semantic_search_executor().submit(
                    contextvars.copy_context().run,
                    functools.partial(
                        self.semantic_search,
                        project_id=project_id,
                        query=normalized_query,
                        top_k=semantic_candidate_k,
                        entity_types=entity_types,
                        raise_on_error=True,
                    ),
                )
                try:
                    semantic_results = future.result(timeout=max(0.0, semantic_timeout_s))
                except FutureTimeoutError as timeout_exc:
                    future.cancel()
                    raise TimeoutError(
                        f"semantic search exceeded {semantic_timeout_s}s"
                    ) from timeout_exc
            semantic_completed = True
        except Exception as sem_err:
            semantic_error = sem_err
            log_with_context(
                logger,
                30,  # WARNING
                "Hybrid semantic search failed, lexical fallback only",
                project_id=project_id,
                query=normalized_query[:100],
                error=str(sem_err),
                error_type=type(sem_err).__name__,
            )

        lexical_results: list[SearchResult] = []
        lexical_error: Exception | None = None
        lexical_completed = False
        if not HYBRID_ENABLE_LEXICAL:
            pass
        elif _LEXICAL_SEARCH_SEMAPHORE is None:
            log_with_context(
                logger,
                20,  # INFO
                "Hybrid lexical search skipped",
                project_id=project_id,
                query=normalized_query[:100],
                reason="concurrency_limit_disabled",
            )
        else:
            acquired = _LEXICAL_SEARCH_SEMAPHORE.acquire(blocking=False)
            if not acquired:
                log_with_context(
                    logger,
                    20,  # INFO
                    "Hybrid lexical search skipped",
                    project_id=project_id,
                    query=normalized_query[:100],
                    reason="concurrency_limit_reached",
                )
            else:
                try:
                    try:
                        with create_session() as session:
                            lexical_results = self._lexical_search(
                                session=session,
                                project_id=project_id,
                                query=normalized_query,
                                top_k=lexical_candidate_k,
                                entity_types=entity_types,
                                include_content=include_content,
                            )
                            lexical_completed = True
                    except Exception as lexical_exc:
                        log_with_context(
                            logger,
                            30,  # WARNING
                            "Hybrid lexical search failed, semantic fallback only",
                            project_id=project_id,
                            query=normalized_query[:100],
                            error=str(lexical_exc),
                            error_type=type(lexical_exc).__name__,
                        )
                        lexical_error = lexical_exc
                finally:
                    _LEXICAL_SEARCH_SEMAPHORE.release()

        if not semantic_completed and not lexical_completed:
            raise VectorSearchUnavailableError(
                "Vector search backends are unavailable"
            ) from (lexical_error or semantic_error)

        if not semantic_results and not lexical_results:
            return []

        if semantic_results and not lexical_results:
            return semantic_results[:top_k]
        if lexical_results and not semantic_results:
            return lexical_results[:top_k]

        fused_results = self._fuse_hybrid_results(
            semantic_results=semantic_results,
            lexical_results=lexical_results,
            top_k=top_k,
        )
        if fused_results:
            return fused_results
        return semantic_results[:top_k]

    def get_index_stats(self, project_id: str) -> dict[str, Any]:
        """
        Get statistics about the project index.

        Args:
            project_id: Project ID

        Returns:
            Dict with index statistics
        """
        try:
            collection_name = self._get_collection_name(project_id)
            collection = self.chroma_client.get_collection(collection_name)
            count = collection.count()

            return {
                "project_id": project_id,
                "collection_name": collection_name,
                "document_count": count,
                "exists": True,
            }
        except Exception as e:
            return {
                "project_id": project_id,
                "exists": False,
                "error": str(e),
            }

    def delete_project_index(self, project_id: str) -> bool:
        """
        Delete the entire index for a project.

        Args:
            project_id: Project ID

        Returns:
            True if successful
        """
        try:
            collection_name = self._get_collection_name(project_id)
            self.chroma_client.delete_collection(collection_name)

            # Clear cache (thread-safe)
            with self._cache_lock:
                if project_id in self._index_cache:
                    del self._index_cache[project_id]

            return True
        except Exception as e:
            log_with_context(
                logger,
                40,  # ERROR
                "Error deleting project index",
                project_id=project_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    # ==================== Embedding Methods ====================

    def generate_embedding(self, text: str) -> list[float]:
        """
        Generate embedding for a single text string.

        Args:
            text: Text to embed

        Returns:
            List of float values representing the embedding vector
        """
        return self.embed_model.get_text_embedding(text)

    def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts in a batch.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        return self.embed_model.get_text_embedding_batch(texts)

    def compute_cosine_similarity(
        self,
        embedding1: list[float],
        embedding2: list[float]
    ) -> float:
        """
        Compute cosine similarity between two embedding vectors.

        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector

        Returns:
            Cosine similarity score between -1 and 1
        """
        import numpy as np

        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)

        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    @staticmethod
    def embedding_to_json(embedding: list[float]) -> str:
        """Convert embedding to JSON string for storage."""
        return json.dumps(embedding)

    @staticmethod
    def embedding_from_json(json_str: str) -> list[float]:
        """Convert JSON string back to embedding list."""
        return json.loads(json_str)


# Singleton instance
_llama_index_service: LlamaIndexService | None = None


def get_llama_index_service() -> LlamaIndexService:
    """
    Get or create the singleton LlamaIndex service instance.

    Returns:
        LlamaIndexService instance
    """
    global _llama_index_service
    if _llama_index_service is None:
        _llama_index_service = LlamaIndexService()
    return _llama_index_service


def reset_llama_index_service() -> None:
    """Reset the singleton instance (useful for testing)."""
    global _llama_index_service
    _llama_index_service = None


# ==================== Async Indexing (Fire-and-Forget) ====================

_INDEX_TASK_QUEUE: "queue.Queue[dict[str, Any]]" = queue.Queue()
_INDEX_WORKER_STARTED = False
_INDEX_WORKER_LOCK = threading.Lock()


def _ensure_index_worker() -> None:
    global _INDEX_WORKER_STARTED
    with _INDEX_WORKER_LOCK:
        if _INDEX_WORKER_STARTED:
            return

        t = threading.Thread(
            target=_index_worker_loop,
            name="zenstory-index-worker",
            daemon=True,
        )
        t.start()
        _INDEX_WORKER_STARTED = True


def _index_worker_loop() -> None:
    while True:
        task = _INDEX_TASK_QUEUE.get()
        try:
            op = task.get("op")
            if op == "upsert":
                _run_index_upsert(task)
            elif op == "delete":
                _run_index_delete(task)
        except Exception:
            logger.exception("Error in index worker loop", exc_info=True)
        finally:
            _INDEX_TASK_QUEUE.task_done()


def _run_index_upsert(task: dict[str, Any]) -> None:
    """Execute upsert task with validation."""
    if task.get("entity_type") == "folder":
        return

    project_id = task["project_id"]
    entity_id = task["entity_id"]
    user_id = task.get("user_id")

    # Verify entity still belongs to the project
    from agent.tools.permissions import ForbiddenError, NotFoundError, check_project_ownership
    from database import create_session

    with create_session() as session:
        # Verify user ownership of the project
        if user_id is not None:
            try:
                check_project_ownership(session, project_id, user_id)
            except (ForbiddenError, NotFoundError) as e:
                log_with_context(
                    logger,
                    40,  # ERROR
                    "Index upsert blocked: permission denied",
                    project_id=project_id,
                    entity_id=entity_id,
                    user_id=user_id,
                    error=str(e),
                )
                return

        file = session.get(File, entity_id)
        if file is None:
            log_with_context(
                logger,
                30,  # WARNING
                "Index upsert skipped: entity not found",
                project_id=project_id,
                entity_id=entity_id,
                user_id=user_id,
            )
            return

        if file.project_id != project_id:
            log_with_context(
                logger,
                30,  # WARNING
                "Index upsert skipped: entity moved to different project",
                expected_project_id=project_id,
                actual_project_id=file.project_id,
                entity_id=entity_id,
                user_id=user_id,
            )
            return

        if file.is_deleted:
            log_with_context(
                logger,
                20,  # INFO
                "Index upsert skipped: entity is deleted",
                project_id=project_id,
                entity_id=entity_id,
            )
            return

    # Execute actual index update
    svc = get_llama_index_service()
    _ = svc.update_entity(
        project_id=project_id,
        entity_type=task["entity_type"],
        entity_id=entity_id,
        title=task.get("title", ""),
        content=task.get("content", ""),
        extra_metadata=task.get("extra_metadata"),
    )


def _run_index_delete(task: dict[str, Any]) -> None:
    if task.get("entity_type") == "folder":
        return

    project_id = task["project_id"]
    entity_id = task["entity_id"]
    user_id = task.get("user_id")

    # Verify user ownership of the project
    if user_id is not None:
        from agent.tools.permissions import ForbiddenError, NotFoundError, check_project_ownership
        from database import create_session

        with create_session() as session:
            try:
                check_project_ownership(session, project_id, user_id)
            except (ForbiddenError, NotFoundError) as e:
                log_with_context(
                    logger,
                    40,  # ERROR
                    "Index delete blocked: permission denied",
                    project_id=project_id,
                    entity_id=entity_id,
                    user_id=user_id,
                    error=str(e),
                )
                return

    svc = get_llama_index_service()
    _ = svc.delete_entity(
        project_id=project_id,
        entity_type=task["entity_type"],
        entity_id=entity_id,
    )


def schedule_index_upsert(
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    title: str,
    content: str,
    extra_metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> None:
    """Enqueue an upsert task for the vector index (non-blocking)."""
    _ensure_index_worker()
    _INDEX_TASK_QUEUE.put(
        {
            "op": "upsert",
            "project_id": project_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "title": title,
            "content": content,
            "extra_metadata": extra_metadata or {},
            "user_id": user_id,
        }
    )


def schedule_index_delete(
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    user_id: str | None = None,
) -> None:
    """Enqueue a delete task for the vector index (non-blocking)."""
    _ensure_index_worker()
    _INDEX_TASK_QUEUE.put(
        {
            "op": "delete",
            "project_id": project_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "user_id": user_id,
        }
    )
