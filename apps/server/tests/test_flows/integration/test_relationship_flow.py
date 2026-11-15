from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from tests.test_flows.conftest import FakeMonitor

rel_mod = importlib.import_module("flows.pipelines.subflows.relationship_flow")


class _FakeCheckpointManager:
    def __init__(self):
        self.update_calls = []

    def update_checkpoint(self, stage, status=None, data=None):
        self.update_calls.append((stage, status, data))


@pytest.mark.integration
class TestRelationshipFlow:
    def test_flow_with_features_disabled(self, monkeypatch):
        cp = _FakeCheckpointManager()

        monkeypatch.setattr(rel_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(rel_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        checkpoint_args = []
        monkeypatch.setattr(
            rel_mod,
            "create_checkpoint_manager",
            lambda novel_id, job_id=None: checkpoint_args.append((novel_id, job_id)) or cp,
        )

        monkeypatch.setattr(rel_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", False)

        result = rel_mod.relationship_flow.fn(novel_id=1, chapter_ids=[1, 2], job_id=88)

        assert result["relationships_count"] == 0
        assert result["status"] == "completed"
        assert checkpoint_args == [(1, 88)]
        assert cp.update_calls[-1][0] == "stage2b"

    def test_flow_extracts_and_builds_relationships(self, monkeypatch):
        cp = _FakeCheckpointManager()

        monkeypatch.setattr(rel_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(rel_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        checkpoint_args = []
        monkeypatch.setattr(
            rel_mod,
            "create_checkpoint_manager",
            lambda novel_id, job_id=None: checkpoint_args.append((novel_id, job_id)) or cp,
        )

        monkeypatch.setattr(rel_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)
        monkeypatch.setattr(rel_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(rel_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)

        monkeypatch.setattr(
            rel_mod,
            "extract_character_relationships_task",
            lambda novel_id, chapter_ids, batch_size: {
                "relationships": [{"character_a_id": 1, "character_b_id": 2}],
                "batches_processed": 1,
            },
        )
        monkeypatch.setattr(
            rel_mod,
            "build_character_relationships_task",
            lambda novel_id, relationships_data: {"saved_count": 1},
        )

        result = rel_mod.relationship_flow.fn(novel_id=9, chapter_ids=[1, 2, 3])

        assert result["relationships_count"] == 1
        assert result["status"] == "completed"
        assert checkpoint_args == [(9, None)]

    def test_extract_relationships_propagates_api_and_fatal_errors_and_handles_partial_output_error(
        self, monkeypatch, db_session
    ):
        from contextlib import contextmanager

        from core.error_codes import ErrorCode
        from flows.atomic_tasks.linking import relationship_tasks as rel_tasks
        from flows.utils.clients.llm import LLMResponse
        from flows.utils.decorators import prefect as decorator_mod
        from flows.utils.helpers.exceptions import (
            LLMAPIError,
            LLMNonRetryableError,
            LLMOutputError,
        )
        from models.material_models import Chapter, Novel, Plot

        novel = Novel(user_id="u-rel-err", title="Rel Err Novel")
        db_session.add(novel)
        db_session.commit()
        ch1 = Chapter(novel_id=novel.id, chapter_number=1, title="c1")
        ch2 = Chapter(novel_id=novel.id, chapter_number=2, title="c2")
        db_session.add_all([ch1, ch2])
        db_session.commit()
        db_session.add_all([
            Plot(chapter_id=ch1.id, index=1, plot_type="SETUP", description="林雷初见贝贝"),
            Plot(chapter_id=ch2.id, index=1, plot_type="SETUP", description="林雷拜师德林"),
        ])
        db_session.commit()

        @contextmanager
        def _session_ctx():
            yield db_session

        monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(rel_tasks, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(rel_tasks, "get_prefect_db_session", _session_ctx)

        # 1. Transient LLMAPIError must NOT be swallowed (must trigger @api_task retry)
        monkeypatch.setattr(
            rel_tasks,
            "call_material_llm",
            MagicMock(side_effect=LLMAPIError("503 service unavailable")),
        )
        with pytest.raises(LLMAPIError):
            rel_tasks.extract_character_relationships_task.fn(
                novel_id=novel.id, batch_size=1
            )

        # 2. Fatal budget LLMNonRetryableError must abort immediately
        fatal = LLMNonRetryableError("trial cap", ErrorCode.MATERIAL_TRIAL_COST_LIMIT)
        monkeypatch.setattr(rel_tasks, "call_material_llm", MagicMock(side_effect=fatal))
        with pytest.raises(LLMNonRetryableError) as raised:
            rel_tasks.extract_character_relationships_task.fn(
                novel_id=novel.id, batch_size=1
            )
        assert raised.value is fatal

        # 3. Single malformed output skips failed batch while keeping valid batch
        responses = [
            LLMResponse(content="not-json", usage={}, model="m", finish_reason="stop"),
            LLMResponse(
                content='{"relationships": [{"character_a": "林雷", "character_b": "贝贝", "relationship_type": "挚友", "description": "生死相伴"}]}',
                usage={},
                model="m",
                finish_reason="stop",
            ),
        ]
        monkeypatch.setattr(
            rel_tasks, "call_material_llm", lambda *_a, **_k: responses.pop(0)
        )
        client = MagicMock()
        client.extract_json_from_response.side_effect = [
            LLMOutputError("bad json"),
            {
                "relationships": [
                    {
                        "character_a": "林雷",
                        "character_b": "贝贝",
                        "relationship_type": "friend",
                        "sentiment": "positive",
                        "description": "林雷与贝贝自幼缔结平等契约，生死相伴并肩作战。",
                    }
                ]
            },
        ]
        monkeypatch.setattr(rel_tasks, "get_material_llm_client", lambda: client)

        extracted = rel_tasks.extract_character_relationships_task.fn(
            novel_id=novel.id, batch_size=1
        )
        assert len(extracted["relationships"]) == 1
        assert extracted["relationships"][0]["character_a"] == "林雷"

    def test_build_character_relationships_resolves_names_and_json_aliases(
        self, monkeypatch, db_session
    ):
        import json
        from contextlib import contextmanager

        from sqlmodel import select

        from flows.atomic_tasks.linking import relationship_tasks as rel_tasks
        from flows.utils.decorators import prefect as decorator_mod
        from models.material_models import Character, CharacterRelationship, Novel

        novel = Novel(user_id="u-rel-build", title="Rel Build Novel")
        db_session.add(novel)
        db_session.commit()
        linlei = Character(
            novel_id=novel.id,
            name="林雷",
            aliases=json.dumps(["龙血战士", "雷哥"], ensure_ascii=False),
        )
        beibei = Character(
            novel_id=novel.id,
            name="贝贝",
            aliases=json.dumps(["小黑鼠"], ensure_ascii=False),
        )
        delin = Character(novel_id=novel.id, name="德林·柯沃特", aliases=None)
        db_session.add_all([linlei, beibei, delin])
        db_session.commit()

        @contextmanager
        def _session_ctx():
            yield db_session

        monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(rel_tasks, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(rel_tasks, "get_prefect_db_session", _session_ctx)

        result = rel_tasks.build_character_relationships_task.fn(
            novel_id=novel.id,
            relationships_data=[
                # Resolved via JSON alias ("龙血战士" -> linlei) + primary name ("贝贝" -> beibei)
                {
                    "character_a": "龙血战士",
                    "character_b": "贝贝",
                    "relationship_type": "伙伴",
                    "description": "生死契约伙伴",
                },
                # Self-link via primary name + own alias ("林雷" & "雷哥") -> must be skipped
                {
                    "character_a": "林雷",
                    "character_b": "雷哥",
                    "relationship_type": "自己",
                },
                # Unresolved character -> skipped
                {
                    "character_a": "林雷",
                    "character_b": "路人甲",
                    "relationship_type": "陌生",
                },
                # Backward-compatible explicit IDs
                {
                    "character_a_id": linlei.id,
                    "character_b_id": delin.id,
                    "relationship_type": "师徒",
                    "description": "戒指中的导师",
                },
            ],
        )
        db_session.commit()

        assert result["saved_count"] == 2
        persisted = db_session.exec(
            select(CharacterRelationship).where(CharacterRelationship.novel_id == novel.id)
        ).all()
        assert len(persisted) == 2
        pairs = {(r.character_a_id, r.character_b_id): r.relationship_type for r in persisted}
        assert pairs == {
            (linlei.id, beibei.id): "伙伴",
            (linlei.id, delin.id): "师徒",
        }
