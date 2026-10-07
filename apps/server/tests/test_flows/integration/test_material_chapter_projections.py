"""Actual-caller parity and prospective Chapter projection budgets."""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from api.materials import entities, library
from core.error_handler import APIException
from flows import database_session
from flows.atomic_tasks.entities import meta_tasks
from flows.atomic_tasks.linking import relationship_tasks
from flows.atomic_tasks.summaries import novel_synopsis_tasks
from flows.utils.clients.llm import DeepSeekClient, LLMResponse
from models import User
from models.material_models import Chapter, GoldenFinger, Novel, Plot, WorldView

story_module = importlib.import_module("flows.pipelines.subflows.story_aggregate_flow")
BODY = "正文🙂" * 6553 + "abcdef"
BODY_BYTES = 65536
CREATED = datetime(2026, 6, 1, 1, 2, 3)
CALLERS = ("tree", "plots", "meta", "relationships", "synopsis")


@dataclass
class Store:
    engine: Any
    scale: int
    novel_id: int
    foreign_chapter_id: int
    rows: list[dict[str, Any]]
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ordered(self):
        return sorted(self.rows, key=lambda row: row["number"])


@pytest.fixture(scope="module", params=[32, 256])
def store(request, tmp_path_factory):
    scale = request.param
    directory = tmp_path_factory.mktemp(f"material-chapters-{scale}")
    path = directory / "owned.sqlite"
    engine = create_engine(f"sqlite:///{path}")

    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", enable_foreign_keys)
    SQLModel.metadata.create_all(engine)
    rows = []
    try:
        with Session(engine) as session:
            session.add(User(id="owned", username="owned", email="owned@example.invalid", hashed_password="dummy"))
            session.add(
                User(id="foreign", username="foreign", email="foreign@example.invalid", hashed_password="dummy")
            )
            session.flush()
            novel = Novel(user_id="owned", title="Measured novel", author="Dummy author")
            other = Novel(user_id="foreign", title="Foreign novel")
            session.add(novel)
            session.add(other)
            session.flush()
            novel_id = novel.id
            foreign = Chapter(novel_id=other.id, chapter_number=1, title="PRIVATE", original_content="PRIVATE")
            session.add(foreign)
            session.flush()
            foreign_id = foreign.id
            # Reverse insertion makes chapter-number order independent of row IDs.
            for number in range(scale, 0, -1):
                chapter = Chapter(
                    novel_id=novel_id,
                    chapter_number=number,
                    title=f"Chapter {number}",
                    summary=f"Summary {number}",
                    original_content=BODY,
                    created_at=CREATED,
                )
                session.add(chapter)
                session.flush()
                plot = Plot(
                    chapter_id=chapter.id,
                    index=0,
                    plot_type="OTHER",
                    description=f"Plot {number}",
                    characters='["人物"]',
                )
                session.add(plot)
                session.flush()
                rows.append(
                    {
                        "id": chapter.id,
                        "number": number,
                        "title": chapter.title,
                        "summary": chapter.summary,
                        "plot_id": plot.id,
                        "plot": plot.description,
                    }
                )
            session.commit()
        assert len(BODY.encode()) == BODY_BYTES
        state = Store(engine, scale, novel_id, foreign_id, rows)
        yield state
        evidence = os.environ.get("M09_PROJECTION_EVIDENCE")
        if evidence:
            (Path(evidence) / f"scale-{scale}.json").write_text(json.dumps(state.records, indent=2) + "\n")
    finally:
        engine.dispose()
        event.remove(engine, "connect", enable_foreign_keys)
        path.unlink(missing_ok=True)
        assert not path.exists()


@contextmanager
def observe(store, label):
    record = {
        "label": label,
        "scale": store.scale,
        "selects": 0,
        "chapter_selects": 0,
        "body_projection_selects": 0,
        "chapter_instance_loads": 0,
        "body_instance_loads": 0,
        "loaded_body_utf8_bytes": 0,
        "chapter_sql": [],
        "chapter_parameters": [],
        "source_frames": [],
        "parity": False,
    }
    ids = set()

    def sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        record["selects"] += 1
        if "FROM chapters" in statement:
            record["chapter_selects"] += 1
            if "original_content" in statement.split("FROM chapters", 1)[0]:
                record["body_projection_selects"] += 1
            if statement not in record["chapter_sql"]:
                record["chapter_sql"].append(statement)
                record["chapter_parameters"].append(list(_parameters))
            if not record["source_frames"]:
                record["source_frames"] = [
                    {"file": frame.filename, "line": frame.lineno, "function": frame.function}
                    for frame in inspect.stack()
                    if "/apps/server/" in frame.filename and "/tests/" not in frame.filename
                ]

    def loaded(_session, obj):
        if isinstance(obj, Chapter):
            ids.add(obj.id)
            record["chapter_instance_loads"] += 1
            # Observational only: never access deferred properties or cursor rows.
            body = obj.__dict__.get("original_content")
            if body is not None:
                record["body_instance_loads"] += 1
                record["loaded_body_utf8_bytes"] += len(body.encode())

    event.listen(store.engine, "before_cursor_execute", sql)
    event.listen(Session, "loaded_as_persistent", loaded)
    try:
        yield record
    finally:
        event.remove(store.engine, "before_cursor_execute", sql)
        event.remove(Session, "loaded_as_persistent", loaded)
        record["distinct_chapter_ids"] = len(ids)
        store.records.append(record)


@pytest.fixture
def workflow_boundary(store, monkeypatch):
    """Real task/flow functions and SQL; synthetic provider and no scheduler."""
    logger = logging.getLogger("material-projection-test")
    monkeypatch.setattr(database_session, "_prefect_engine", store.engine)
    modules = [meta_tasks, relationship_tasks, novel_synopsis_tasks, story_module]
    modules.extend(
        importlib.import_module(name)
        for name in (
            "flows.utils.helpers.checkpoint_manager",
            "flows.utils.helpers.performance_monitor",
            "flows.utils.decorators.prefect",
        )
    )
    for module in modules:
        monkeypatch.setattr(module, "get_run_logger", lambda: logger)
    captures = {}
    # Existing parser runs without constructing a network-capable SDK client.
    parser = object.__new__(DeepSeekClient)

    def provider(name, payload):
        def completion(messages, system_prompt=None, **kwargs):
            captures.setdefault(name, []).append(messages)
            return LLMResponse(json.dumps(payload, ensure_ascii=False), {}, "synthetic-local", "stop")

        return completion

    for module, name, payload in (
        (meta_tasks, "meta", {"golden_fingers": [], "world_view": None}),
        (relationship_tasks, "relationships", {"relationships": []}),
        (novel_synopsis_tasks, "synopsis", {"synopsis": "梗" * 250}),
    ):
        monkeypatch.setattr(module, "call_deepseek_api", provider(name, payload))
        monkeypatch.setattr(module, "get_deepseek_client", lambda: parser)
    monkeypatch.setattr(
        story_module, "generate_novel_synopsis_task", novel_synopsis_tasks.generate_novel_synopsis_task.fn
    )
    monkeypatch.setattr(story_module, "update_novel_synopsis_task", novel_synopsis_tasks.update_novel_synopsis_task.fn)
    flags = {
        "ENABLE_CHAPTER_SUMMARIES": True,
        "ENABLE_CHARACTER_EXTRACTION": False,
        "ENABLE_META_EXTRACTION": False,
        "ENABLE_PLOT_EXTRACTION": False,
        "ENABLE_NOVEL_SYNOPSIS": True,
        "ENABLE_STORY_AGGREGATION": False,
        "ENABLE_STORYLINE_GENERATION": False,
        "ENABLE_RELATIONSHIP_EXTRACTION": False,
        "ENABLE_NEO4J_STORAGE": False,
    }
    monkeypatch.setattr(story_module, "settings", story_module.settings.model_copy(update=flags))
    return captures


def exercise(caller, store, captures, chapter_ids=None):
    ordered = store.ordered
    ids = chapter_ids if chapter_ids is not None else [row["id"] for row in ordered]
    if caller in ("tree", "plots"):
        with Session(store.engine) as session:
            assert session.expire_on_commit is True
            owner = session.get(User, "owned")
            if caller == "tree":
                actual = library.get_material_tree(store.novel_id, owner, session)
                expected = {
                    "tree": [
                        {
                            "id": row["id"],
                            "type": "chapter",
                            "title": row["title"],
                            "metadata": {
                                "chapter_number": row["number"],
                                "summary": row["summary"],
                                "plots_count": 1,
                                "created_at": CREATED.isoformat(),
                            },
                        }
                        for row in ordered
                    ]
                }
                assert actual == expected
            else:
                actual = entities.get_plots(store.novel_id, owner, session)
                expected = [
                    {
                        "id": row["plot_id"],
                        "chapter_id": row["id"],
                        "index": 0,
                        "plot_type": "OTHER",
                        "description": row["plot"],
                        "characters": ["人物"],
                    }
                    for row in sorted(store.rows, key=lambda row: row["id"])
                ]
                assert [item.model_dump() for item in actual] == expected
    elif caller == "meta":
        actual = meta_tasks.extract_novel_meta_task.fn(store.novel_id)
        assert actual == {"golden_fingers": [], "world_view": None, "novel_id": store.novel_id}
        text = "\n\n".join(f"第{row['number']}章 {row['title']}\n{BODY}" for row in ordered[:20])
        message = f"\n章节范围: 前20章\n\n小说内容:\n{text}\n\n请提取金手指和世界观信息。\n"
        assert captures["meta"] == [[{"role": "user", "content": message}]]
        return {
            "body_values_in_prompt": captures["meta"][0][0]["content"].count(BODY),
            "body_prompt_utf8_bytes": 20 * BODY_BYTES,
        }
    elif caller == "relationships":
        actual = relationship_tasks.extract_character_relationships_task.fn(
            novel_id=store.novel_id,
            batch_size=5,
            chapter_ids=ids,
        )
        assert actual == {
            "relationships": [],
            "novel_id": store.novel_id,
            "total_count": 0,
            "batches_processed": (store.scale + 4) // 5,
        }
        expected = []
        for offset in range(0, store.scale, 5):
            batch = ordered[offset : offset + 5]
            text = "\n".join(f"[章节{row['number']}] {row['plot']}" for row in sorted(batch, key=lambda row: row["id"]))
            message = (
                f"\n请从以下情节点中提取人物关系（第{batch[0]['number']}-{batch[-1]['number']}章）:\n\n"
                f"{text}\n\n请识别所有重要的人物关系，包括新出现的关系和关系的变化。\n"
            )
            expected.append([{"role": "user", "content": message}])
        assert captures["relationships"] == expected
    else:
        assert caller == "synopsis"
        actual = story_module.story_aggregate_flow.fn(novel_id=store.novel_id, chapter_ids=ids)
        assert actual["synopsis_generated"] is True
        assert actual["status"] == "completed" and actual["stories_count"] == 0
        text = "\n\n".join(f"第 {row['number']} 章 - {row['title']}:\n{row['summary']}" for row in ordered)
        message = (
            f"\n小说标题: Measured novel\n作者: Dummy author\n总章节数: {store.scale}\n\n"
            f"各章节摘要:\n{text}\n\n请基于以上章节摘要,生成小说的整体梗概。\n"
        )
        assert captures["synopsis"] == [[{"role": "user", "content": message}]]
        with Session(store.engine) as session:
            assert session.get(Novel, store.novel_id).synopsis == "梗" * 250


@pytest.mark.parametrize("caller", CALLERS)
def test_actual_material_caller_parity(store, workflow_boundary, caller):
    with observe(store, f"parity-{caller}") as record:
        record.update(exercise(caller, store, workflow_boundary) or {})
        record["parity"] = True


@pytest.mark.parametrize("caller", CALLERS)
def test_prospective_material_chapter_projection_budget(store, workflow_boundary, caller):
    """Optimization targets; baseline failures are not wrong DTO/prompt incidents."""
    with observe(store, f"prospective-budget-{caller}") as record:
        record.update(exercise(caller, store, workflow_boundary) or {})
        record["parity"] = True
        assert record["chapter_selects"] == 1, record
        if caller == "meta":
            assert record["body_projection_selects"] == 1, record
            assert "LIMIT" in record["chapter_sql"][0].upper(), record
            assert record["chapter_parameters"][0][-2:] == [20, 0], record
            assert record["body_values_in_prompt"] == 20, record
            assert record["body_prompt_utf8_bytes"] == 20 * BODY_BYTES, record
        else:
            assert record["body_projection_selects"] == 0, record
        assert record["chapter_instance_loads"] == 0, record
        assert record["body_instance_loads"] == 0, record
        assert record["loaded_body_utf8_bytes"] == 0, record


@pytest.mark.parametrize("handler", [library.get_material_tree, entities.get_plots])
def test_foreign_owner_is_denied_before_chapter_loading(store, handler):
    with Session(store.engine) as session:
        foreign = session.get(User, "foreign")
        with observe(store, f"foreign-{handler.__name__}") as record:
            with pytest.raises(APIException) as caught:
                handler(store.novel_id, foreign, session)
            assert caught.value.status_code == 403
            assert record["chapter_selects"] == 0 and record["chapter_instance_loads"] == 0
            record["parity"] = True


@pytest.mark.parametrize("caller", ["relationships", "synopsis"])
@pytest.mark.parametrize("scope", ["mixed", "empty"])
def test_chapter_id_filter_preserves_scope_and_empty_list(store, workflow_boundary, caller, scope):
    ids = [row["id"] for row in store.rows] + [store.foreign_chapter_id] if scope == "mixed" else []
    with observe(store, f"scope-{scope}-{caller}") as record:
        exercise(caller, store, workflow_boundary, chapter_ids=ids)
        record["parity"] = True


def test_meta_projection_empty_novel_never_calls_provider(store, workflow_boundary):
    with Session(store.engine) as session:
        novel = Novel(user_id="owned", title="Empty metadata control")
        session.add(novel)
        session.commit()
        empty_novel_id = novel.id
    with pytest.raises(ValueError, match=f"小说 {empty_novel_id} 没有章节"):
        meta_tasks.extract_novel_meta_task.fn(empty_novel_id)
    assert workflow_boundary == {}


def test_meta_result_persistence_preserves_fallbacks_and_upserts(store, workflow_boundary):
    chapter_id = store.ordered[0]["id"]
    data = {
        "golden_fingers": [
            "ignored non-object",
            {"name": "Named", "type": "system", "description": "original"},
            {"type": "天赋"},
            {"description": "abcdefghijklmnopqrst"},
            {},
        ],
        "world_view": {"power_system": "levels", "key_factions": ["local faction"]},
    }
    first = meta_tasks.build_meta_entities_task.fn(data, store.novel_id, chapter_id)
    assert first == {"golden_finger_actions": ["created"] * 4, "world_view_action": "created"}
    data["golden_fingers"][1]["description"] = "updated"
    data["world_view"]["world_structure"] = "updated structure"
    second = meta_tasks.build_meta_entities_task.fn(data, store.novel_id, chapter_id)
    assert second == {"golden_finger_actions": ["updated"] * 4, "world_view_action": "updated"}
    with Session(store.engine) as session:
        rows = session.exec(select(GoldenFinger).where(GoldenFinger.novel_id == store.novel_id)).all()
        by_name = {row.name: row for row in rows}
        assert set(by_name) == {"Named", "天赋-金手指", "abcdefghijkl", "未命名金手指"}
        assert by_name["Named"].description == "updated"
        assert all(row.first_appearance_chapter_id == chapter_id for row in rows)
        world = session.exec(select(WorldView).where(WorldView.novel_id == store.novel_id)).one()
        assert world.power_system == "levels" and world.world_structure == "updated structure"
        assert json.loads(world.key_factions) == ["local faction"]
    empty = meta_tasks.build_meta_entities_task.fn({}, store.novel_id)
    assert empty == {"golden_finger_actions": [], "world_view_action": None}
    assert workflow_boundary == {}
