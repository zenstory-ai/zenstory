from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from flows.pipelines.helpers.progress_publisher import ProgressPublisher
from flows.pipelines.helpers.result_builder import ResultBuilder


class TestProgressPublisher:
    def test_no_correlation_id_disables_client(self):
        publisher = ProgressPublisher(correlation_id=None, logger=MagicMock())
        assert publisher._client is None

        # should not raise
        publisher.publish("flow_started", progress=0)

    def test_publish_sends_json_payload_to_expected_channel(self, monkeypatch):
        fake_client = MagicMock()
        monkeypatch.setattr(
            ProgressPublisher,
            "_create_redis_client",
            lambda self: fake_client,
        )

        publisher = ProgressPublisher(correlation_id="cid-123", logger=MagicMock())
        publisher.publish("stage1_completed", progress=50, message="ok")

        fake_client.publish.assert_called_once()
        channel, payload = fake_client.publish.call_args.args
        parsed = json.loads(payload)

        assert channel == "ingestion:cid-123"
        assert parsed["type"] == "stage1_completed"
        assert parsed["progress"] == 50
        assert parsed["message"] == "ok"
        assert "timestamp" in parsed

    def test_publish_with_unavailable_client_only_warns(self, monkeypatch):
        logger = MagicMock()
        monkeypatch.setattr(ProgressPublisher, "_create_redis_client", lambda self: None)

        publisher = ProgressPublisher(correlation_id="cid-123", logger=logger)
        publisher.publish("stage2_started", progress=60)

        assert logger.warning.call_count >= 1

    def test_publish_completion_includes_summary(self, monkeypatch):
        publisher = ProgressPublisher(correlation_id=None, logger=MagicMock())
        publisher.publish = MagicMock()
        monkeypatch.setattr(
            publisher,
            "_build_novel_summary",
            lambda novel_id, chapters_count: {
                "id": novel_id,
                "chapters_count": chapters_count,
            },
        )

        publisher.publish_completion(
            novel_id=7,
            chapter_ids=[1, 2, 3],
            result={"elapsed_ms": 88},
        )

        publisher.publish.assert_called_once()
        args, kwargs = publisher.publish.call_args
        assert args[0] == "completed"
        assert kwargs["novel_summary"] == {"id": 7, "chapters_count": 3}
        assert kwargs["progress"] == 100
        assert kwargs["status"] == "completed"

    def test_publish_completion_allows_missing_summary(self, monkeypatch):
        publisher = ProgressPublisher(correlation_id=None, logger=MagicMock())
        publisher.publish = MagicMock()
        monkeypatch.setattr(publisher, "_build_novel_summary", lambda _novel_id, _chapters_count: None)

        publisher.publish_completion(
            novel_id=9,
            chapter_ids=[10],
            result={"elapsed_ms": 12},
        )

        args, kwargs = publisher.publish.call_args
        assert args[0] == "completed"
        assert kwargs["novel_summary"] is None

    @pytest.mark.parametrize("status", ["completed", "completed_with_errors"])
    def test_publish_completion_preserves_final_result_status_without_mutation(
        self,
        monkeypatch,
        status,
    ):
        publisher = ProgressPublisher(correlation_id=None, logger=MagicMock())
        publisher.publish = MagicMock()
        monkeypatch.setattr(
            publisher,
            "_build_novel_summary",
            lambda novel_id, chapters_count: {
                "id": novel_id,
                "chapters_count": chapters_count,
            },
        )
        failed_count = 1 if status == "completed_with_errors" else 0
        result = ResultBuilder.build_final_result(
            novel_id=7,
            job_id=33,
            chapter_ids=[1, 2],
            stage1_result={
                "summaries_count": 2,
                "plots_count": 4,
                "failed_count": failed_count,
                "failed_chapters": [2] if failed_count else [],
            },
            story_result={"stories_count": 3, "failed_stories": []},
            relationship_result={"relationships_count": 5},
            character_entity_result={"created_count": 2, "failed_count": 0},
            status=status,
            elapsed_ms=321,
        )
        result.update(progress=3, message="stale", novel_summary={"id": 999})
        original_result = deepcopy(result)

        publisher.publish_completion(
            novel_id=7,
            chapter_ids=[1, 2],
            result=result,
        )

        args, kwargs = publisher.publish.call_args
        assert args == ("completed",)
        assert kwargs == {
            **original_result,
            "progress": 100,
            "message": "小说解析完成！",
            "novel_summary": {"id": 7, "chapters_count": 2},
        }
        assert result == original_result

    @pytest.mark.parametrize("status", [None, ""])
    def test_publish_completion_defaults_falsey_status(self, monkeypatch, status):
        publisher = ProgressPublisher(correlation_id=None, logger=MagicMock())
        publisher.publish = MagicMock()
        monkeypatch.setattr(publisher, "_build_novel_summary", lambda *_args: None)
        result = {"status": status, "elapsed_ms": 12}
        original_result = deepcopy(result)

        publisher.publish_completion(9, [10], result)

        assert publisher.publish.call_args.kwargs["status"] == "completed"
        assert result == original_result

    def test_build_novel_summary_returns_none_on_query_error(self, monkeypatch):
        logger = MagicMock()
        publisher = ProgressPublisher(correlation_id=None, logger=logger)

        import flows.database_session as db_session_mod

        @contextmanager
        def _broken_session():
            class _Broken:
                def exec(self, _stmt):
                    raise RuntimeError("db unavailable")

            yield _Broken()

        monkeypatch.setattr(db_session_mod, "get_prefect_db_session", _broken_session)

        summary = publisher._build_novel_summary(novel_id=1, chapters_count=2)
        assert summary is None
        logger.warning.assert_called_once()

    def test_create_redis_client_uses_redis_url_even_when_flag_defaults_false(self, monkeypatch):
        fake_client = MagicMock()
        captured: dict[str, object] = {}

        def _fake_from_url(url: str, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return fake_client

        monkeypatch.setenv("REDIS_URL", "redis://default:secret@redis.railway.internal:6379/0")
        monkeypatch.delenv("MATERIAL_REDIS_ENABLED", raising=False)
        monkeypatch.setattr("flows.pipelines.helpers.progress_publisher.redis.from_url", _fake_from_url)

        publisher = ProgressPublisher(correlation_id="cid-prod", logger=MagicMock())

        assert publisher._client is fake_client
        fake_client.ping.assert_called_once()
        assert captured["url"] == "redis://default:secret@redis.railway.internal:6379/0"
        assert captured["kwargs"] == {
            "decode_responses": True,
            "socket_timeout": 2.0,
            "socket_connect_timeout": 2.0,
        }

    def test_create_redis_client_respects_explicit_disable_over_redis_url(self, monkeypatch):
        from_url_mock = MagicMock()
        monkeypatch.setenv("REDIS_URL", "redis://redis.railway.internal:6379/0")
        monkeypatch.setenv("MATERIAL_REDIS_ENABLED", "false")
        monkeypatch.setattr("flows.pipelines.helpers.progress_publisher.redis.from_url", from_url_mock)

        publisher = ProgressPublisher(correlation_id="cid-disabled", logger=MagicMock())

        assert publisher._client is None
        from_url_mock.assert_not_called()

    def test_create_redis_client_falls_back_to_host_port_when_enabled_without_url(self, monkeypatch):
        fake_client = MagicMock()
        redis_ctor = MagicMock(return_value=fake_client)
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("MATERIAL_REDIS_ENABLED", "true")
        monkeypatch.setenv("REDIS_HOST", "redis-local")
        monkeypatch.setenv("REDIS_PORT", "6380")
        monkeypatch.setattr("flows.pipelines.helpers.progress_publisher.redis.Redis", redis_ctor)

        publisher = ProgressPublisher(correlation_id="cid-host", logger=MagicMock())

        assert publisher._client is fake_client
        fake_client.ping.assert_called_once()
        assert redis_ctor.call_args.kwargs["host"] == "redis-local"
        assert redis_ctor.call_args.kwargs["port"] == 6380

    def test_create_redis_client_skips_when_neither_url_nor_flag_configured(self, monkeypatch):
        from_url_mock = MagicMock()
        redis_ctor = MagicMock()
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("MATERIAL_REDIS_ENABLED", "   ")
        monkeypatch.setattr("flows.pipelines.helpers.progress_publisher.redis.from_url", from_url_mock)
        monkeypatch.setattr("flows.pipelines.helpers.progress_publisher.redis.Redis", redis_ctor)

        publisher = ProgressPublisher(correlation_id="cid-none", logger=MagicMock())

        assert publisher._client is None
        from_url_mock.assert_not_called()
        redis_ctor.assert_not_called()

    def test_create_redis_client_closes_failed_client_and_redacts_url_in_logs(self, monkeypatch):
        secret_url = "redis://default:super-secret-pass@redis.railway.internal:6379/0"
        fake_client = MagicMock()
        fake_client.ping.side_effect = ConnectionError(f"failed connecting to {secret_url}")
        logger = MagicMock()

        monkeypatch.setenv("REDIS_URL", secret_url)
        monkeypatch.delenv("MATERIAL_REDIS_ENABLED", raising=False)
        monkeypatch.setattr("flows.pipelines.helpers.progress_publisher.redis.from_url", lambda *_a, **_kw: fake_client)
        monkeypatch.setattr("flows.pipelines.helpers.progress_publisher.time.sleep", lambda _s: None)

        publisher = ProgressPublisher(correlation_id="cid-fail", logger=logger)

        assert publisher._client is None
        assert fake_client.close.call_count == 3
        all_logged = " ".join(str(call.args) for call in [*logger.warning.call_args_list, *logger.error.call_args_list])
        assert "super-secret-pass" not in all_logged
        assert "ConnectionError" in all_logged
