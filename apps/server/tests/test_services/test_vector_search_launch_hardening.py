"""上线前加固：embedding 客户端时限、语义检索外层 deadline、元数据扁平化、安全重建。"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import chromadb
import httpx
import pytest
from llama_index.core.embeddings import MockEmbedding
from sqlmodel import Session

from models import File, Project, User
from services.core.auth_service import hash_password
from services.infra import vector_search_service as vss


def _make_service(tmp_path) -> vss.LlamaIndexService:
    with patch.object(vss.LlamaIndexService, "__init__", return_value=None):
        service = vss.LlamaIndexService()
    service.chroma_client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    service.embed_model = MockEmbedding(embed_dim=8)
    service._index_cache = {}
    service._cache_lock = threading.RLock()
    return service


def _seed_project(db_session: Session) -> Project:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"vector-hardening-{suffix}@example.com",
        username=f"vector_hardening_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name=f"Vector Hardening {suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


# ---------------------------------------------------------------- 客户端时限


def test_zhipu_client_options_bound_timeout_and_retries():
    options = vss.build_zhipu_client_options()

    timeout = options["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == pytest.approx(3.0)
    assert timeout.read == pytest.approx(10.0)
    assert options["max_retries"] <= 1


def test_zhipu_embedding_passes_timeout_and_retries_to_sdk_client():
    captured: dict = {}

    class FakeZhipuClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    with patch("zai.ZhipuAiClient", FakeZhipuClient):
        vss.ZhipuEmbedding(api_key="test-key", model_name="embedding-3")

    assert captured["api_key"] == "test-key"
    assert isinstance(captured["timeout"], httpx.Timeout)
    assert captured["timeout"].read == pytest.approx(10.0)
    assert captured["max_retries"] <= 1


# ---------------------------------------------------------------- 语义 deadline


@patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
def test_hybrid_search_falls_back_to_lexical_when_semantic_exceeds_deadline(
    _mock_init, monkeypatch
):
    monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
    monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", threading.Semaphore(1))

    release = threading.Event()

    def slow_semantic(**_kwargs):
        release.wait(5)
        return [
            vss.SearchResult(
                entity_type="draft", entity_id="late", title="late", content="x", score=0.9
            )
        ]

    lexical_hit = vss.SearchResult(
        entity_type="draft", entity_id="lexical", title="lexical", content="x", score=0.5
    )
    service = vss.LlamaIndexService()
    service.semantic_search = MagicMock(side_effect=slow_semantic)  # type: ignore[method-assign]
    service._lexical_search = MagicMock(return_value=[lexical_hit])  # type: ignore[method-assign]

    import database

    monkeypatch.setattr(database, "create_session", lambda: MagicMock())

    started = time.monotonic()
    try:
        results = service.hybrid_search(
            project_id="p1", query="needle", top_k=5, semantic_timeout_s=0.2
        )
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert results == [lexical_hit]
    assert elapsed < 2.0


def test_context_assembler_passes_semantic_deadline_to_hybrid_search(monkeypatch):
    from agent.context.assembler import ContextAssembler

    fake_service = MagicMock()
    fake_service.hybrid_search.return_value = []
    monkeypatch.setenv("AGENT_RETRIEVAL_SEMANTIC_TIMEOUT_S", "2.5")

    with patch("services.llama_index.get_llama_index_service", return_value=fake_service):
        ContextAssembler()._get_retrieved_snippets(project_id="p1", query="林动的身世")

    kwargs = fake_service.hybrid_search.call_args.kwargs
    assert kwargs["semantic_timeout_s"] == pytest.approx(2.5)


# ---------------------------------------------------------------- 元数据扁平化


def test_nested_file_metadata_is_flattened_and_indexable(tmp_path, db_session: Session):
    project = _seed_project(db_session)
    file = File(
        project_id=project.id,
        title="参考素材（上）",
        file_type="snippet",
        content="山风吹过断崖。",
        file_metadata='{"split": {"index": 1, "total": 2}, "traits": ["沉默", "固执"], "source": "upload"}',
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    service = _make_service(tmp_path)
    document = service._file_to_document(file)

    assert document.metadata["split"] == '{"index": 1, "total": 2}'
    assert document.metadata["traits"] == '["沉默", "固执"]'
    assert document.metadata["source"] == "upload"

    stats = service.index_project(db_session, project.id)
    assert stats.total_documents == 1
    collection = service.chroma_client.get_collection(service._get_collection_name(project.id))
    assert collection.count() >= 1


# ---------------------------------------------------------------- 安全重建


def test_failed_rebuild_keeps_existing_index(tmp_path, db_session: Session):
    project = _seed_project(db_session)
    db_session.add(
        File(project_id=project.id, title="第一章", file_type="draft", content="旧的正文")
    )
    db_session.commit()

    service = _make_service(tmp_path)
    service.index_project(db_session, project.id)
    collection_name = service._get_collection_name(project.id)
    before = service.chroma_client.get_collection(collection_name).count()
    assert before >= 1

    with (
        patch.object(
            vss.VectorStoreIndex,
            "from_documents",
            side_effect=RuntimeError("embedding provider down"),
        ),
        pytest.raises(RuntimeError, match="embedding provider down"),
    ):
        service.index_project(db_session, project.id)

    # 旧索引仍在，且没有遗留临时 collection。
    assert service.chroma_client.get_collection(collection_name).count() == before
    names = [c.name if hasattr(c, "name") else str(c) for c in service.chroma_client.list_collections()]
    assert names == [collection_name]


def test_successful_rebuild_replaces_index_contents(tmp_path, db_session: Session):
    project = _seed_project(db_session)
    first = File(project_id=project.id, title="第一章", file_type="draft", content="旧的正文")
    db_session.add(first)
    db_session.commit()

    service = _make_service(tmp_path)
    service.index_project(db_session, project.id)

    first.is_deleted = True
    db_session.add(first)
    db_session.add(
        File(project_id=project.id, title="第二章", file_type="draft", content="新的正文")
    )
    db_session.commit()

    service.index_project(db_session, project.id)

    collection = service.chroma_client.get_collection(service._get_collection_name(project.id))
    titles = {meta.get("title") for meta in collection.get(include=["metadatas"])["metadatas"]}
    assert titles == {"第二章"}
    assert project.id not in service._index_cache


def test_entity_embedding_failure_keeps_old_searchable_rows(tmp_path):
    service = _make_service(tmp_path)
    assert service.update_entity("project-a", "draft", "file-a", "旧标题", "旧正文")

    with patch.object(
        type(service.embed_model),
        "get_text_embedding_batch",
        side_effect=RuntimeError("embedding provider down"),
    ):
        assert not service.update_entity("project-a", "draft", "file-a", "新标题", "新正文")

    collection = service.chroma_client.get_collection(service._get_collection_name("project-a"))
    stored = collection.get(include=["documents", "metadatas"])
    assert any("旧正文" in document for document in stored["documents"])
    assert {metadata["title"] for metadata in stored["metadatas"]} == {"旧标题"}


def test_entity_insert_failure_restores_old_searchable_rows(tmp_path):
    service = _make_service(tmp_path)
    assert service.update_entity("project-b", "draft", "file-b", "旧标题", "旧正文")
    index = service.get_or_create_index("project-b")

    with patch.object(index, "insert_nodes", side_effect=RuntimeError("chroma write failed")):
        assert not service.update_entity("project-b", "draft", "file-b", "新标题", "新正文")

    collection = service.chroma_client.get_collection(service._get_collection_name("project-b"))
    stored = collection.get(include=["documents", "metadatas"])
    assert any("旧正文" in document for document in stored["documents"])
    assert {metadata["title"] for metadata in stored["metadatas"]} == {"旧标题"}


def test_embedding_quota_circuit_pauses_then_allows_one_recovery_probe(monkeypatch):
    class QuotaError(RuntimeError):
        code = 1113

    response = MagicMock()
    response.data = [MagicMock(embedding=[0.1, 0.2])]
    client = MagicMock()
    client.embeddings.create.side_effect = [QuotaError("balance unavailable"), response]

    monkeypatch.setattr(vss, "_embedding_quota_blocked_until", 0.0)
    monkeypatch.setattr(vss, "_embedding_quota_probe_in_flight", False)
    with patch("zai.ZhipuAiClient", return_value=client):
        embedding = vss.ZhipuEmbedding(api_key="test-key", model_name="embedding-3")

    with pytest.raises(QuotaError):
        embedding._get_text_embedding("first")
    with pytest.raises(vss.EmbeddingQuotaCircuitOpenError):
        embedding._get_text_embedding("blocked")
    assert client.embeddings.create.call_count == 1

    monkeypatch.setattr(vss, "_embedding_quota_blocked_until", time.monotonic() - 1)
    assert embedding._get_text_embedding("probe") == [0.1, 0.2]
    assert client.embeddings.create.call_count == 2
    assert vss._embedding_quota_blocked_until == 0.0


def test_actual_zhipu_1113_error_is_quota_but_plain_429_is_not():
    from zai.core._errors import APIReachLimitError

    request = httpx.Request("POST", "https://example.invalid")
    quota_error = APIReachLimitError(
        "错误代码：1113，余额不足",
        response=httpx.Response(429, request=request),
    )
    rate_limit = APIReachLimitError(
        "Too many requests",
        response=httpx.Response(429, request=request),
    )

    assert vss._embedding_error_code(quota_error) == "1113"
    assert vss._is_embedding_quota_error(quota_error) is True
    assert vss._embedding_error_code(rate_limit) == "429"
    assert vss._is_embedding_quota_error(rate_limit) is False


def test_disabled_background_upsert_does_not_start_worker_or_enqueue(monkeypatch):
    monkeypatch.setenv("ASYNC_VECTOR_INDEX_ENABLED", "false")
    ensure_worker = MagicMock()
    enqueue = MagicMock()
    monkeypatch.setattr(vss, "_ensure_index_worker", ensure_worker)
    monkeypatch.setattr(vss._INDEX_TASK_QUEUE, "put", enqueue)

    vss.schedule_index_upsert(
        project_id="project-c",
        entity_type="draft",
        entity_id="file-c",
        title="title",
        content="content",
    )

    ensure_worker.assert_not_called()
    enqueue.assert_not_called()


@patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
def test_quota_circuit_keeps_hybrid_search_on_lexical_fallback(_mock_init, monkeypatch):
    monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
    monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", threading.Semaphore(1))
    monkeypatch.setattr(vss, "_embedding_quota_blocked_until", time.monotonic() + 60)
    service = vss.LlamaIndexService()
    service.semantic_search = MagicMock()  # type: ignore[method-assign]
    lexical = vss.SearchResult(
        entity_type="draft",
        entity_id="lexical",
        title="关键词结果",
        content="",
        score=0.5,
    )
    service._lexical_search = MagicMock(return_value=[lexical])  # type: ignore[method-assign]

    import database

    monkeypatch.setattr(database, "create_session", lambda: MagicMock())
    assert service.hybrid_search(project_id="project-d", query="needle") == [lexical]
    service.semantic_search.assert_not_called()
