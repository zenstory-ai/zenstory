"""
Regression tests for hybrid lexical search guardrails.

These cover the defensive limits added in:
- HYBRID_LEXICAL_DB_CANDIDATE_* (cap/multiplier)
- HYBRID_LEXICAL_TIME_BUDGET_* (early stopping)
- HYBRID_ENABLE_LEXICAL + concurrency semaphore gating in hybrid_search()
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models import File, Project, User
from services.core.auth_service import hash_password
from services.infra import vector_search_service as vss


def _seed_project_with_files(
    db_session: Session,
    *,
    file_count: int,
    query_token: str,
) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"vector-search-guardrails-{suffix}@example.com",
        username=f"vector_search_guardrails_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Vector Search Guardrails {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    base_time = utcnow() - timedelta(minutes=10)
    files: list[File] = []
    for i in range(file_count):
        files.append(
            File(
                project_id=project.id,
                title=f"{query_token} file {i}",
                content=f"{query_token} content {i}",
                file_type="draft",
                is_deleted=False,
                updated_at=base_time + timedelta(seconds=i),
            )
        )

    db_session.add_all(files)
    db_session.commit()

    return user, project


@pytest.mark.integration
class TestHybridLexicalGuardrails:
    @pytest.mark.parametrize("lexical_state", ["disabled", "missing_semaphore", "busy"])
    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_hybrid_search_raises_when_semantic_fails_and_lexical_never_runs(
        self, _mock_init, monkeypatch, lexical_state
    ):
        service = vss.LlamaIndexService()
        service.semantic_search = MagicMock(side_effect=RuntimeError("semantic down"))  # type: ignore[method-assign]
        service._lexical_search = MagicMock(return_value=[])  # type: ignore[method-assign]

        if lexical_state == "disabled":
            monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", False)
        elif lexical_state == "missing_semaphore":
            monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
            monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", None)
        else:
            semaphore = MagicMock()
            semaphore.acquire.return_value = False
            monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
            monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", semaphore)

        with pytest.raises(vss.VectorSearchUnavailableError):
            service.hybrid_search(project_id="project-1", query="needle")

        service._lexical_search.assert_not_called()

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_hybrid_search_accepts_semantic_empty_when_lexical_is_skipped(
        self, _mock_init, monkeypatch
    ):
        monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", False)
        service = vss.LlamaIndexService()
        service.semantic_search = MagicMock(return_value=[])  # type: ignore[method-assign]

        assert service.hybrid_search(project_id="project-1", query="needle") == []

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_hybrid_search_accepts_lexical_empty_after_semantic_failure(
        self, _mock_init, monkeypatch
    ):
        import database

        semaphore = MagicMock()
        semaphore.acquire.return_value = True
        monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
        monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", semaphore)
        monkeypatch.setattr(database, "create_session", lambda: nullcontext(object()))
        service = vss.LlamaIndexService()
        service.semantic_search = MagicMock(side_effect=RuntimeError("semantic down"))  # type: ignore[method-assign]
        service._lexical_search = MagicMock(return_value=[])  # type: ignore[method-assign]

        assert service.hybrid_search(project_id="project-1", query="needle") == []

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_hybrid_search_raises_when_semantic_and_lexical_both_fail(
        self, _mock_init, monkeypatch
    ):
        """A total backend outage must not look like a valid empty result."""
        import database

        semaphore = MagicMock()
        semaphore.acquire.return_value = True
        monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
        monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", semaphore)
        monkeypatch.setattr(database, "create_session", lambda: nullcontext(object()))

        service = vss.LlamaIndexService()
        service.semantic_search = MagicMock(side_effect=RuntimeError("semantic down"))  # type: ignore[method-assign]
        service._lexical_search = MagicMock(side_effect=RuntimeError("lexical down"))  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="[Vv]ector search"):
            service.hybrid_search(project_id="project-1", query="needle")

        service.semantic_search.assert_called_once_with(
            project_id="project-1",
            query="needle",
            top_k=30,
            entity_types=None,
            raise_on_error=True,
        )
        semaphore.release.assert_called_once_with()

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_hybrid_search_keeps_lexical_fallback_after_semantic_failure(
        self, _mock_init, monkeypatch
    ):
        import database

        lexical_result = vss.SearchResult(
            entity_type="draft", entity_id="file-1", title="Lexical", content=None, score=0.8
        )
        semaphore = MagicMock()
        semaphore.acquire.return_value = True
        monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
        monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", semaphore)
        monkeypatch.setattr(database, "create_session", lambda: nullcontext(object()))

        service = vss.LlamaIndexService()
        service.semantic_search = MagicMock(side_effect=RuntimeError("semantic down"))  # type: ignore[method-assign]
        service._lexical_search = MagicMock(return_value=[lexical_result])  # type: ignore[method-assign]

        assert service.hybrid_search(project_id="project-1", query="needle") == [lexical_result]

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_lexical_search_candidate_limit_respects_cap(self, _mock_init, db_session: Session, monkeypatch):
        """DB prefilter should not fetch more than HYBRID_LEXICAL_DB_CANDIDATE_CAP rows."""
        _seed_project_with_files(db_session, file_count=25, query_token="needle")

        monkeypatch.setattr(vss, "HYBRID_LEXICAL_DB_CANDIDATE_MULTIPLIER", 1)
        monkeypatch.setattr(vss, "HYBRID_LEXICAL_DB_CANDIDATE_CAP", 5)

        service = vss.LlamaIndexService()
        project_id = db_session.exec(select(Project.id)).first()
        assert project_id is not None

        results = service._lexical_search(
            session=db_session,
            project_id=project_id,
            query="needle",
            top_k=10,
            entity_types=None,
            include_content=False,
        )

        assert len(results) == 5

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_lexical_search_time_budget_logs_and_stops_early(self, _mock_init, db_session: Session, monkeypatch):
        """When time budget is exceeded, lexical scoring loop should early stop (and log)."""
        _seed_project_with_files(db_session, file_count=120, query_token="needle")

        monkeypatch.setattr(vss, "HYBRID_LEXICAL_TIME_BUDGET_MS", 1)
        monkeypatch.setattr(vss, "HYBRID_LEXICAL_TIME_BUDGET_MIN_CANDIDATES", 1)

        monotonic_values = iter([0.0, 999.0])

        def _fake_monotonic():
            return next(monotonic_values)

        mock_log = MagicMock()
        monkeypatch.setattr(vss, "log_with_context", mock_log)
        monkeypatch.setattr(vss.time, "monotonic", _fake_monotonic)

        service = vss.LlamaIndexService()
        project_id = db_session.exec(select(Project.id)).first()
        assert project_id is not None

        results = service._lexical_search(
            session=db_session,
            project_id=project_id,
            query="needle",
            top_k=20,
            entity_types=None,
            include_content=False,
        )

        # Early stopping happens after 5 candidates, so results should be <= 5.
        assert len(results) <= 5

        assert any(
            (len(call.args) >= 3 and call.args[2] == "Lexical search time budget exceeded; early stopping")
            for call in mock_log.call_args_list
        )

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_hybrid_search_skips_lexical_when_semaphore_unavailable(self, _mock_init, db_session: Session, monkeypatch):
        """hybrid_search() should skip lexical branch when semaphore is disabled/unavailable."""
        _seed_project_with_files(db_session, file_count=2, query_token="needle")

        monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
        monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", None)

        semantic_result = vss.SearchResult(
            entity_type="draft",
            entity_id="file-1",
            title="Semantic",
            content="semantic",
            score=0.9,
        )

        service = vss.LlamaIndexService()
        service.semantic_search = MagicMock(return_value=[semantic_result])  # type: ignore[method-assign]
        service._lexical_search = MagicMock(return_value=[])  # type: ignore[method-assign]

        results = service.hybrid_search(
            project_id="project-1",
            query="needle",
            top_k=10,
            entity_types=None,
            include_content=False,
        )

        service._lexical_search.assert_not_called()
        assert results == [semantic_result]


class TestSemanticSearchGuardrails:
    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_semantic_search_truncates_overlong_query_before_embedding(self, _mock_init, monkeypatch):
        """semantic_search() should not send extremely long input to the embedding provider."""
        import database

        service = vss.LlamaIndexService()

        dummy_session = object()
        monkeypatch.setattr(database, "create_session", lambda: nullcontext(dummy_session))
        monkeypatch.setattr(service, "_validate_entity_ownership", lambda *args, **kwargs: True)
        monkeypatch.setattr(service, "_build_snippet", lambda *args, **kwargs: ("snippet", 1))

        class DummyNode:
            def __init__(self):
                self.metadata = {"entity_type": "draft", "entity_id": "file-1", "title": "Title"}
                self.text = "content"
                self.node_id = "node-1"

        class DummyNodeWithScore:
            def __init__(self):
                self.node = DummyNode()
                self.score = 0.9

        retriever = MagicMock()
        retriever.retrieve = MagicMock(return_value=[DummyNodeWithScore()])
        index = MagicMock()
        index.as_retriever = MagicMock(return_value=retriever)
        monkeypatch.setattr(service, "get_or_create_index", lambda *args, **kwargs: index)

        original_query = " ".join(f"word{i}" for i in range(10000))
        results = service.semantic_search(project_id="project-1", query=original_query, top_k=1)

        assert results
        called_query = retriever.retrieve.call_args[0][0]
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        assert len(encoding.encode(called_query)) <= vss.SEMANTIC_QUERY_MAX_TOKENS
        assert called_query != original_query

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_truncation_log_carries_no_query_text(self, _mock_init, monkeypatch):
        """The truncation log identifies the query by hash and length only."""
        import database

        service = vss.LlamaIndexService()
        monkeypatch.setattr(database, "create_session", lambda: nullcontext(object()))
        retriever = MagicMock()
        retriever.retrieve = MagicMock(return_value=[])
        index = MagicMock()
        index.as_retriever = MagicMock(return_value=retriever)
        monkeypatch.setattr(service, "get_or_create_index", lambda *args, **kwargs: index)
        log_calls = []
        monkeypatch.setattr(vss, "log_with_context", lambda *args, **kwargs: log_calls.append((args, kwargs)))

        secret_query = "SECRET-PLOT " + " ".join(f"word{i}" for i in range(10000))
        service.semantic_search(project_id="project-1", query=secret_query, top_k=1)

        truncation_logs = [kw for args, kw in log_calls if "truncated for embedding" in args[2]]
        assert truncation_logs
        for fields in truncation_logs:
            assert fields["query_hash"]
            assert not any("SECRET-PLOT" in str(value) for value in fields.values())

    @patch("services.infra.vector_search_service.LlamaIndexService.__init__", return_value=None)
    def test_semantic_search_does_not_retry_on_param_error(self, _mock_init, monkeypatch):
        """We truncate upfront; semantic_search() should not do fallback retries."""
        import database

        service = vss.LlamaIndexService()

        dummy_session = object()
        monkeypatch.setattr(database, "create_session", lambda: nullcontext(dummy_session))
        monkeypatch.setattr(service, "_validate_entity_ownership", lambda *args, **kwargs: True)
        monkeypatch.setattr(service, "_build_snippet", lambda *args, **kwargs: ("snippet", 1))

        class DummyNode:
            def __init__(self):
                self.metadata = {"entity_type": "draft", "entity_id": "file-1", "title": "Title"}
                self.text = "content"
                self.node_id = "node-1"

        class DummyNodeWithScore:
            def __init__(self):
                self.node = DummyNode()
                self.score = 0.9

        retriever = MagicMock()
        retriever.retrieve = MagicMock(
            side_effect=[
                Exception(
                    'Error code: 400, with error text {"error":{"code":"1210","message":"API 调用参数有误，请检查文档。"}}'
                ),
            ]
        )
        index = MagicMock()
        index.as_retriever = MagicMock(return_value=retriever)
        monkeypatch.setattr(service, "get_or_create_index", lambda *args, **kwargs: index)

        original_query = "hello world"
        results = service.semantic_search(project_id="project-1", query=original_query, top_k=1)

        assert results == []
        assert retriever.retrieve.call_count == 1
