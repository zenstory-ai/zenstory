"""Real metadata compatibility regressions and prospective validation budgets."""

from __future__ import annotations

import inspect
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import chromadb
import pytest
from chromadb.config import Settings as ChromaSettings
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.schema import NodeWithScore, TextNode
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

import database
from agent.context.assembler import ContextAssembler
from models import File, Project, User
from services.infra import vector_search_service as vss

RAW_METADATA = [
    pytest.param(
        '{"role":"主角","category":"地理","importance":"high","source":"upload","nested":{"a":1},"traits":["勇敢"]}',
        id="dict",
    ),
    pytest.param("null", id="null"),
    pytest.param("[1]", id="list"),
    pytest.param('"legacy"', id="string"),
    pytest.param("7", id="number"),
    pytest.param("{broken", id="malformed"),
    pytest.param("", id="empty"),
]
BODY = "正文🙂" * 6553 + "abcdef"


def receipt(name, data):
    destination = os.environ.get("M11_PROOF_EVIDENCE")
    if destination:
        (Path(destination) / f"{os.environ.get('M11_RUN', 'local')}-{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )


@pytest.fixture
def owned_db(tmp_path, monkeypatch):
    path = tmp_path / "owned.sqlite"
    engine = create_engine(f"sqlite:///{path}")

    def fk(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", fk)
    SQLModel.metadata.create_all(engine, tables=[User.__table__, Project.__table__, File.__table__])
    with Session(engine) as session:
        session.add_all(
            [
                User(id="owner", username="owner", email="owner@example.invalid", hashed_password="dummy"),
                User(id="foreign", username="foreign", email="foreign@example.invalid", hashed_password="dummy"),
            ]
        )
        session.flush()
        session.add_all(
            [
                Project(id="owned-project", name="Owned", owner_id="owner"),
                Project(id="foreign-project", name="Foreign", owner_id="foreign"),
            ]
        )
        session.commit()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setenv("AGENT_ENABLE_RETRIEVAL_SNIPPETS", "false")
    try:
        yield engine
    finally:
        engine.dispose()
        event.remove(engine, "connect", fk)
        path.unlink(missing_ok=True)
        assert not path.exists()


def local_service(tmp_path):
    # Configure only the external embedding/persistence boundary; real methods execute.
    service = object.__new__(vss.LlamaIndexService)
    service.chroma_client = chromadb.PersistentClient(
        path=str(tmp_path / "owned-chroma"),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    service.embed_model = MockEmbedding(embed_dim=8)
    service._index_cache = {}
    service._cache_lock = threading.RLock()
    return service


@pytest.fixture
def indexed_service(tmp_path):
    service = local_service(tmp_path)
    try:
        yield service
    finally:
        # Stop only this positively-owned persistent client/system.
        service._index_cache.clear()
        service.chroma_client._system.stop()
        identifier = service.chroma_client._identifier
        chromadb.api.client.SharedSystemClient._identifier_to_system.pop(identifier, None)


def seed_file(engine, *, id="actual", project="owned-project", type="draft", metadata="", content="正文"):
    with Session(engine) as session:
        session.add(
            File(id=id, project_id=project, file_type=type, title="真实标题", content=content, file_metadata=metadata)
        )
        session.commit()


@pytest.mark.parametrize("raw", RAW_METADATA)
@pytest.mark.parametrize("file_type", ["character", "lore", "snippet"])
def test_legacy_metadata_context_assembly(owned_db, raw, file_type):
    seed_file(owned_db, type=file_type, metadata=raw)
    with Session(owned_db) as session:
        context = ContextAssembler().assemble(
            session,
            "owned-project",
            "owner",
            focus_file_id="actual",
            query="正常请求",
            include_characters=False,
            include_lores=False,
        )
    expected_content = (
        "正文\n角色: 主角" if file_type == "character" and raw.startswith("{") and raw != "{broken" else "正文"
    )
    assert context.refs == ["actual"]
    assert len(context.items) == 1
    item = context.items[0]
    expected_title = "地理 - 真实标题" if file_type == "lore" and raw.startswith('{"role"') else "真实标题"
    assert (item["id"], item["title"], item["content"]) == ("actual", expected_title, expected_content)
    assert item["metadata"]["file_type"] == file_type
    assert item["metadata"]["is_focus"] is True
    assert expected_content in context.context
    with Session(owned_db) as session:
        attached = ContextAssembler().assemble(
            session,
            "owned-project",
            "owner",
            attached_file_ids=["actual"],
            include_characters=False,
            include_lores=False,
        )
    assert attached.refs == ["actual"]
    assert attached.items[0]["metadata"]["relation"] == "attached"
    assert attached.items[0]["metadata"]["attached"] is True


@pytest.mark.parametrize("raw", RAW_METADATA)
def test_legacy_metadata_actual_document_and_index(owned_db, indexed_service, raw):
    seed_file(owned_db, metadata=raw)
    with Session(owned_db) as session:
        file = session.get(File, "actual")
        document = indexed_service._file_to_document(file)
        assert document.text == "# 真实标题\n\n正文"
        assert {key: document.metadata[key] for key in ["entity_id", "entity_type", "title"]} == {
            "entity_id": "actual",
            "entity_type": "draft",
            "title": "真实标题",
        }
        if raw.startswith('{"role"'):
            assert document.metadata["nested"] == '{"a": 1}'
            assert document.metadata["traits"] == '["勇敢"]'
        stats = indexed_service.index_project(session, "owned-project")
    assert stats.total_documents == 1
    results = indexed_service.semantic_search("owned-project", "正文", raise_on_error=True)
    assert [(r.entity_id, r.entity_type, r.title, r.content) for r in results] == [
        ("actual", "draft", "真实标题", "# 真实标题\n\n正文"),
    ]


@pytest.mark.parametrize("mode", ["rebuild", "incremental"])
@pytest.mark.parametrize("key,value", [("entity_id", "other"), ("entity_type", "lore"), ("title", "伪造标题")])
def test_reserved_metadata_preserves_real_indexed_identity(owned_db, indexed_service, key, value, mode):
    extra = {key: value, "nested": {"a": 1}, "traits": ["勇敢"]}
    seed_file(owned_db, metadata=json.dumps(extra))
    seed_file(owned_db, id="other", content="另一份正文")
    with Session(owned_db) as session:
        document = indexed_service._file_to_document(session.get(File, "actual"))
        assert document.metadata["nested"] == '{"a": 1}'
        assert document.metadata["traits"] == '["勇敢"]'
        if mode == "rebuild":
            stats = indexed_service.index_project(session, "owned-project")
            assert stats.total_documents == 2
        else:
            assert indexed_service.update_entity("owned-project", "draft", "actual", "真实标题", "正文", extra)
    collection = indexed_service.chroma_client.get_collection(indexed_service._get_collection_name("owned-project"))
    results = indexed_service.semantic_search("owned-project", "正文", top_k=10, raise_on_error=True)
    receipt(
        f"reserved-{key}-{mode}",
        {"stored": collection.get(include=["metadatas"])["metadatas"], "returned": [r.to_dict() for r in results]},
    )
    expected = [("actual", "draft", "真实标题", "# 真实标题\n\n正文")]
    if mode == "rebuild":
        expected.append(("other", "draft", "真实标题", "# 真实标题\n\n另一份正文"))
    assert sorted((r.entity_id, r.entity_type, r.title, r.content) for r in results) == sorted(expected)
    actual = next(r for r in results if r.entity_id == "actual")
    assert actual.metadata["nested"] == '{"a": 1}'
    assert actual.metadata["traits"] == '["勇敢"]'


def deterministic_service(project_id, nodes):
    service = object.__new__(vss.LlamaIndexService)
    service._cache_lock = threading.RLock()
    retriever = SimpleNamespace(retrieve=lambda _query: nodes)
    service._index_cache = {project_id: SimpleNamespace(as_retriever=lambda **_kwargs: retriever)}
    return service


def node(id, *, type="draft", title="索引标题", text="索引正文"):
    return NodeWithScore(
        node=TextNode(id_=f"node-{id}", text=text, metadata={"entity_id": id, "entity_type": type, "title": title}),
        score=0.75,
    )


@contextmanager
def observe(engine):
    record = {
        "file_selects": 0,
        "body_column_selects": 0,
        "file_instances": 0,
        "body_instance_utf8_bytes": 0,
        "sql": [],
        "source_frames": [],
    }

    def sql(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and "FROM file" in statement:
            record["file_selects"] += 1
            record["body_column_selects"] += int("file.content" in statement.split("FROM file")[0])
            if not record["sql"]:
                record["sql"].append(statement)
                record["source_frames"] = [
                    {"file": f.filename, "line": f.lineno, "function": f.function}
                    for f in inspect.stack()
                    if "/apps/server/" in f.filename and "/tests/" not in f.filename
                ]

    def loaded(_session, obj):
        if isinstance(obj, File):
            record["file_instances"] += 1
            body = obj.__dict__.get("content")
            if isinstance(body, str):
                record["body_instance_utf8_bytes"] += len(body.encode())

    event.listen(engine, "before_cursor_execute", sql)
    event.listen(Session, "loaded_as_persistent", loaded)
    try:
        yield record
    finally:
        event.remove(engine, "before_cursor_execute", sql)
        event.remove(Session, "loaded_as_persistent", loaded)


def measured_semantic_search(engine, scale):
    assert len(BODY.encode()) == 65536
    ids = [f"owned-{i:04d}" for i in range(scale)]
    with Session(engine) as session:
        session.add_all(
            [
                File(id=id, project_id="owned-project", title=f"File{i}", content=BODY, file_type="draft")
                for i, id in enumerate(ids)
            ]
        )
        session.commit()
    results_nodes = [node(id, type=" DRAFT ", text=f"独立索引段{i}") for i, id in enumerate(ids)]
    service = deterministic_service("owned-project", results_nodes)
    with observe(engine) as record:
        results = service.semantic_search("owned-project", "正文", top_k=scale, raise_on_error=True)
    assert [(r.entity_id, r.entity_type, r.title, r.content, r.score) for r in results] == [
        (id, "draft", "索引标题", f"独立索引段{i}", 0.75) for i, id in enumerate(ids)
    ]
    record.update({"scale": scale, "parity": True, "results": len(results)})
    return record


@pytest.mark.parametrize("scale", [32, 256])
def test_semantic_validation_cold_measurement(owned_db, scale):
    record = measured_semantic_search(owned_db, scale)
    receipt(f"measurement-{scale}", record)
    assert record["results"] == scale


@pytest.mark.parametrize("scale", [32, 256])
def test_prospective_semantic_validation_body_projection_budget(owned_db, scale):
    record = measured_semantic_search(owned_db, scale)
    receipt(f"prospective-budget-{scale}", record)
    assert record["body_column_selects"] == 0, record
    assert record["file_instances"] == 0, record
    assert record["body_instance_utf8_bytes"] == 0, record


def test_semantic_validation_scope_and_type_controls(owned_db):
    seed_file(owned_db)
    seed_file(owned_db, id="foreign", project="foreign-project")
    seed_file(owned_db, id="deleted")
    seed_file(owned_db, id="folder", type="folder")
    seed_file(owned_db, id="mismatch")
    with Session(owned_db) as session:
        session.get(File, "deleted").is_deleted = True
        session.commit()
    service = deterministic_service(
        "owned-project",
        [
            node("foreign"),
            node("deleted"),
            node("folder", type="folder"),
            node("missing"),
            node("mismatch", type="lore"),
            node("actual", type=" DRAFT "),
            NodeWithScore(
                node=TextNode(
                    id_="second-chunk",
                    text="另一索引段",
                    metadata={"entity_id": "actual", "entity_type": "draft", "title": "索引标题"},
                ),
                score=0.5,
            ),
        ],
    )
    with observe(owned_db) as record:
        results = service.semantic_search(
            "owned-project", "正文", top_k=10, entity_types=[" DRAFT "], raise_on_error=True
        )
    assert [(r.entity_id, r.entity_type, r.content) for r in results] == [
        ("actual", "draft", "索引正文"),
        ("actual", "draft", "另一索引段"),
    ]
    assert record["file_selects"] == 6  # repeated valid entity is cached; distinct invalid IDs still checked
    receipt("scope-duplicate-controls", record)
    assert service.semantic_search("owned-project", " ", raise_on_error=True) == []
    with Session(owned_db) as session:
        denied = ContextAssembler().assemble(session, "owned-project", "foreign", focus_file_id="actual")
    assert denied.items == [] and denied.refs == [] and denied.context == ""
