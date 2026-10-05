from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from flows.pipelines import novel_ingestion_v3_flow as flow_mod


class _FakeSession:
    def __init__(self):
        self.flush_calls = 0
        self.commit_calls = 0

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        self.commit_calls += 1


class _FakeSessionCtx:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.integration
class TestNovelIngestionResume:
    def test_full_flow_resumes_persisted_owned_chapters_without_original_file(
        self, monkeypatch, db_session, tmp_path
    ):
        from models.material_models import Chapter, IngestionJob, Novel

        novel = Novel(user_id="owner-1", title="durable novel")
        db_session.add(novel)
        db_session.flush()
        chapter_1 = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="chapter 1",
            original_content="durable content 1",
        )
        chapter_2 = Chapter(
            novel_id=novel.id,
            chapter_number=2,
            title="chapter 2",
            original_content="durable content 2",
        )
        db_session.add(chapter_1)
        db_session.add(chapter_2)
        db_session.flush()
        job = IngestionJob(
            novel_id=novel.id,
            source_path=str(tmp_path / "gone-after-redeploy.txt"),
            status="pending",
            total_chapters=2,
        )
        db_session.add(job)
        db_session.commit()

        monkeypatch.setattr(
            flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(db_session)
        )
        monkeypatch.setattr(flow_mod, "get_run_logger", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(flow_mod, "ProgressPublisher", MagicMock())
        monkeypatch.setattr(
            flow_mod,
            "_ensure_file_local",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("durable chapter resume must not access the original file")
            ),
        )
        checkpoint_manager = MagicMock()
        monkeypatch.setattr(
            flow_mod,
            "create_checkpoint_manager",
            lambda _novel_id, _job_id=None: checkpoint_manager,
        )

        class FakeExecutor:
            def __init__(self, **kwargs):
                assert kwargs["novel_id"] == novel.id
                assert kwargs["chapter_ids"] == [chapter_1.id, chapter_2.id]
                assert kwargs["job_id"] == job.id

            def record_enabled_stages(self):
                return {}

            def execute_stage1(self):
                return {"summaries_count": 2}

            def execute_stage2(self, stage1_result, _flow_start):
                assert stage1_result == {"summaries_count": 2}
                return {"novel_id": novel.id, "status": "completed", "elapsed_ms": 1}

        monkeypatch.setattr(flow_mod, "StageExecutor", FakeExecutor)

        result = flow_mod.novel_ingestion_v3.fn(
            file_path=job.source_path,
            user_id="owner-1",
            novel_id=novel.id,
            job_id=job.id,
            correlation_id="retry-run",
        )

        assert result == {
            "novel_id": novel.id,
            "status": "completed",
            "elapsed_ms": 1,
        }

    def test_persisted_resume_preserves_stage_failure_without_file_cleanup_warning(
        self, monkeypatch, db_session, tmp_path
    ):
        from models.material_models import Chapter, IngestionJob, Novel

        novel = Novel(user_id="owner-1", title="durable novel")
        db_session.add(novel)
        db_session.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="chapter 1",
            original_content="durable content",
        )
        db_session.add(chapter)
        db_session.flush()
        job = IngestionJob(
            novel_id=novel.id,
            source_path=str(tmp_path / "gone-after-redeploy.txt"),
            status="pending",
        )
        db_session.add(job)
        db_session.commit()

        logger = MagicMock()
        monkeypatch.setattr(
            flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(db_session)
        )
        monkeypatch.setattr(flow_mod, "get_run_logger", MagicMock(return_value=logger))
        monkeypatch.setattr(flow_mod, "ProgressPublisher", MagicMock())
        monkeypatch.setattr(
            flow_mod,
            "_ensure_file_local",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("durable chapter resume must not access the original file")
            ),
        )
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", MagicMock())
        monkeypatch.setattr(flow_mod, "_mark_job_as_failed", MagicMock())

        class FailingExecutor:
            def __init__(self, **_kwargs):
                pass

            def record_enabled_stages(self):
                return {}

            def execute_stage1(self):
                raise RuntimeError("original stage failure")

        monkeypatch.setattr(flow_mod, "StageExecutor", FailingExecutor)

        with pytest.raises(RuntimeError, match="original stage failure"):
            flow_mod.novel_ingestion_v3.fn(
                file_path=job.source_path,
                user_id="owner-1",
                novel_id=novel.id,
                job_id=job.id,
            )

        assert not any(
            "清理临时文件时出错" in str(call)
            for call in logger.warning.call_args_list
        )

    @pytest.mark.parametrize(
        ("caller_user_id", "deleted_at", "error"),
        [
            ("other-user", None, "不属于"),
            ("owner-1", datetime(2026, 10, 4), "已删除"),
        ],
    )
    def test_persisted_resume_rejects_wrong_actor_or_deleted_novel(
        self, monkeypatch, db_session, tmp_path, caller_user_id, deleted_at, error
    ):
        from models.material_models import Chapter, IngestionJob, Novel

        novel = Novel(
            user_id="owner-1",
            title="protected novel",
            deleted_at=deleted_at,
        )
        db_session.add(novel)
        db_session.flush()
        db_session.add(
            Chapter(
                novel_id=novel.id,
                chapter_number=1,
                title="chapter 1",
                original_content="durable content",
            )
        )
        job = IngestionJob(
            novel_id=novel.id,
            source_path=str(tmp_path / "gone.txt"),
            status="pending",
        )
        db_session.add(job)
        db_session.commit()

        monkeypatch.setattr(
            flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(db_session)
        )
        monkeypatch.setattr(flow_mod, "get_run_logger", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(flow_mod, "ProgressPublisher", MagicMock())
        monkeypatch.setattr(
            flow_mod,
            "_ensure_file_local",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("identity rejection must happen before file access")
            ),
        )

        with pytest.raises(ValueError, match=error):
            flow_mod.novel_ingestion_v3.fn(
                file_path=job.source_path,
                user_id=caller_user_id,
                novel_id=novel.id,
                job_id=job.id,
            )

    @pytest.mark.parametrize("persisted_contents", [[], [None]])
    def test_empty_or_content_missing_chapters_still_require_original_file(
        self, monkeypatch, db_session, tmp_path, persisted_contents
    ):
        from models.material_models import Chapter, IngestionJob, Novel

        missing_path = tmp_path / "gone-after-redeploy.txt"
        novel = Novel(user_id="owner-1", title="incomplete novel")
        db_session.add(novel)
        db_session.flush()
        for index, content in enumerate(persisted_contents, start=1):
            db_session.add(
                Chapter(
                    novel_id=novel.id,
                    chapter_number=index,
                    title=f"chapter {index}",
                    original_content=content,
                )
            )
        job = IngestionJob(
            novel_id=novel.id,
            source_path=str(missing_path),
            status="pending",
        )
        db_session.add(job)
        db_session.commit()

        monkeypatch.setattr(
            flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(db_session)
        )
        monkeypatch.setattr(flow_mod, "get_run_logger", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(flow_mod, "ProgressPublisher", MagicMock())
        monkeypatch.delenv("API_SERVER_INTERNAL_URL", raising=False)
        monkeypatch.delenv("MATERIAL_INTERNAL_TOKEN", raising=False)

        with pytest.raises(FileNotFoundError, match="API_SERVER_INTERNAL_URL"):
            flow_mod.novel_ingestion_v3.fn(
                file_path=job.source_path,
                user_id="owner-1",
                novel_id=novel.id,
                job_id=job.id,
            )

    def test_check_and_resume_returns_incomplete_when_no_existing_novel(self, monkeypatch, fake_logger):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))

        import services.material.novels_service as novels_mod

        class FakeNovelsService:
            def get_by_content_hash(self, _session, _content_hash, _user_id):
                return None

        monkeypatch.setattr(novels_mod, "NovelsService", FakeNovelsService)

        result = flow_mod._check_and_resume_from_checkpoint(
            content_hash="abc",
            user_id="u1",
            novel_id=None,
            correlation_id=None,
            flow_start=0.0,
            logger=fake_logger,
            publisher=MagicMock(),
            job_id=900,
        )

        assert result == {"completed": False}

    def test_check_and_resume_returns_completed_when_latest_checkpoint_completed(
        self, monkeypatch, fake_logger, fake_checkpoint_record_factory
    ):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))

        import services.material.novels_service as novels_mod

        class FakeNovelsService:
            def get_by_content_hash(self, _session, _content_hash, _user_id):
                return SimpleNamespace(id=99)

            def list_chapter_ids(self, _session, _novel_id):
                return [1, 2, 3]

        monkeypatch.setattr(novels_mod, "NovelsService", FakeNovelsService)

        cp_mgr = MagicMock()
        cp_mgr.get_latest_checkpoint.return_value = fake_checkpoint_record_factory(
            {}, stage="completed", stage_status="completed"
        )
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp_mgr)

        result = flow_mod._check_and_resume_from_checkpoint(
            content_hash="abc",
            user_id="u1",
            novel_id=None,
            correlation_id=None,
            flow_start=0.0,
            logger=fake_logger,
            publisher=MagicMock(),
            job_id=900,
        )

        assert result["completed"] is True
        assert result["result"]["novel_id"] == 99
        assert result["result"]["status"] == "already_completed"

    def test_check_and_resume_executes_stage1_and_stage2_when_resume_stage1(self, monkeypatch, fake_logger):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))

        import services.material.novels_service as novels_mod

        class FakeNovelsService:
            def get_by_content_hash(self, _session, _content_hash, _user_id):
                return SimpleNamespace(id=88)

            def list_chapter_ids(self, _session, _novel_id):
                return [7, 8]

        monkeypatch.setattr(novels_mod, "NovelsService", FakeNovelsService)

        cp_mgr = MagicMock()
        cp_mgr.get_latest_checkpoint.return_value = None
        cp_mgr.can_resume.return_value = True
        cp_mgr.get_resume_point.return_value = {"stage": "stage1", "status": "processing"}
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp_mgr)

        calls = {"stage1": 0, "stage2": 0}

        class FakeExecutor:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def record_enabled_stages(self):
                calls["recorded"] = calls.get("recorded", 0) + 1
                return {}

            def execute_stage1(self):
                calls["stage1"] += 1
                return {"summaries_count": 2}

            def execute_stage2(self, stage1_result, _flow_start):
                calls["stage2"] += 1
                assert stage1_result == {"summaries_count": 2}
                return {"novel_id": 88, "status": "completed"}

        monkeypatch.setattr(flow_mod, "StageExecutor", FakeExecutor)

        result = flow_mod._check_and_resume_from_checkpoint(
            content_hash="abc",
            user_id="u1",
            novel_id=None,
            correlation_id="cid",
            flow_start=0.0,
            logger=fake_logger,
            publisher=MagicMock(),
            job_id=900,
        )

        assert result["completed"] is True
        assert result["result"]["novel_id"] == 88
        assert calls == {"stage1": 1, "stage2": 1, "recorded": 1}

    def test_check_and_resume_returns_incomplete_when_resume_not_allowed(self, monkeypatch, fake_logger):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))

        import services.material.novels_service as novels_mod

        class FakeNovelsService:
            def get_by_content_hash(self, _session, _content_hash, _user_id):
                return SimpleNamespace(id=77)

            def list_chapter_ids(self, _session, _novel_id):
                return [1]

        monkeypatch.setattr(novels_mod, "NovelsService", FakeNovelsService)

        cp_mgr = MagicMock()
        cp_mgr.get_latest_checkpoint.return_value = None
        cp_mgr.can_resume.return_value = False
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp_mgr)

        result = flow_mod._check_and_resume_from_checkpoint(
            content_hash="abc",
            user_id="u1",
            novel_id=None,
            correlation_id="cid",
            flow_start=0.0,
            logger=fake_logger,
            publisher=MagicMock(),
            job_id=900,
        )

        assert result == {"completed": False, "novel_id": 77}
        cp_mgr.get_resume_point.assert_not_called()

    def test_check_and_resume_executes_stage2_only_when_resume_stage2(self, monkeypatch, fake_logger):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))

        import services.material.novels_service as novels_mod

        class FakeNovelsService:
            def get_by_content_hash(self, _session, _content_hash, _user_id):
                return SimpleNamespace(id=55)

            def list_chapter_ids(self, _session, _novel_id):
                return [3, 4]

        monkeypatch.setattr(novels_mod, "NovelsService", FakeNovelsService)

        cp_mgr = MagicMock()
        cp_mgr.get_latest_checkpoint.return_value = None
        cp_mgr.can_resume.return_value = True
        cp_mgr.get_resume_point.return_value = {"stage": "stage2", "status": "processing"}
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp_mgr)

        calls = {"stage1": 0, "stage2": 0}

        class FakeExecutor:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def record_enabled_stages(self):
                calls["recorded"] = calls.get("recorded", 0) + 1
                return {}

            def execute_stage1(self):
                calls["stage1"] += 1
                raise AssertionError("stage2 恢复路径不应执行 stage1")

            def execute_stage2(self, stage1_result, _flow_start):
                calls["stage2"] += 1
                assert stage1_result is None
                return {"novel_id": 55, "status": "completed"}

        monkeypatch.setattr(flow_mod, "StageExecutor", FakeExecutor)

        result = flow_mod._check_and_resume_from_checkpoint(
            content_hash="abc",
            user_id="u1",
            novel_id=None,
            correlation_id="cid",
            flow_start=0.0,
            logger=fake_logger,
            publisher=MagicMock(),
            job_id=900,
        )

        assert result["completed"] is True
        assert result["result"]["novel_id"] == 55
        assert calls == {"stage1": 0, "stage2": 1, "recorded": 1}

    def test_check_and_resume_treats_unknown_stage_as_fresh_start(self, monkeypatch, fake_logger):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))

        import services.material.novels_service as novels_mod

        class FakeNovelsService:
            def get_by_content_hash(self, _session, _content_hash, _user_id):
                return SimpleNamespace(id=44)

            def list_chapter_ids(self, _session, _novel_id):
                return [9]

        monkeypatch.setattr(novels_mod, "NovelsService", FakeNovelsService)

        cp_mgr = MagicMock()
        cp_mgr.get_latest_checkpoint.return_value = None
        cp_mgr.can_resume.return_value = True
        cp_mgr.get_resume_point.return_value = {"stage": "weird_stage", "status": "processing"}
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp_mgr)
        monkeypatch.setattr(flow_mod, "StageExecutor", lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应创建执行器")))

        result = flow_mod._check_and_resume_from_checkpoint(
            content_hash="abc",
            user_id="u1",
            novel_id=None,
            correlation_id="cid",
            flow_start=0.0,
            logger=fake_logger,
            publisher=MagicMock(),
            job_id=900,
        )

        assert result == {"completed": False, "novel_id": 44}

    def test_check_and_resume_cleans_failed_stage(self, monkeypatch, fake_logger):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))

        import services.material.checkpoint_service as cp_service_mod
        import services.material.ingestion_jobs_service as jobs_mod
        import services.material.novels_service as novels_mod

        class FakeNovelsService:
            def get_by_content_hash(self, _session, _content_hash, _user_id):
                return SimpleNamespace(id=66)

            def list_chapter_ids(self, _session, _novel_id):
                return [1]

        class FakeCheckpointService:
            def __init__(self):
                self.deleted = []

            def delete_all(self, _session, novel_id):
                self.deleted.append(novel_id)

        old_job = SimpleNamespace(status="failed")

        class FakeIngestionJobsService:
            def get_latest_by_novel(self, _session, _novel_id):
                return old_job

        monkeypatch.setattr(novels_mod, "NovelsService", FakeNovelsService)
        fake_cp_service = FakeCheckpointService()
        monkeypatch.setattr(cp_service_mod, "CheckpointService", lambda: fake_cp_service)
        monkeypatch.setattr(jobs_mod, "IngestionJobsService", FakeIngestionJobsService)

        cp_mgr = MagicMock()
        cp_mgr.get_latest_checkpoint.return_value = None
        cp_mgr.can_resume.return_value = True
        cp_mgr.get_resume_point.return_value = {"stage": "failed", "status": "failed"}
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp_mgr)

        result = flow_mod._check_and_resume_from_checkpoint(
            content_hash="abc",
            user_id="u1",
            novel_id=None,
            correlation_id=None,
            flow_start=0.0,
            logger=fake_logger,
            publisher=MagicMock(),
            job_id=900,
        )

        assert result == {"completed": False, "novel_id": 66}
        assert old_job.status == "failed"
        assert session.flush_calls == 0
        assert session.commit_calls == 0
        assert fake_cp_service.deleted == []


@pytest.mark.integration
class TestMarkJobAsFailed:
    def test_mark_job_as_failed_publishes_even_without_novel(self, fake_logger):
        publisher = MagicMock()
        flow_mod._mark_job_as_failed(
            novel_id=None,
            _correlation_id=None,
            error_code="ERR_MATERIAL_DECOMPOSE_FAILED",
            flow_start=0.0,
            logger=fake_logger,
            publisher=publisher,
        )

        publisher.publish.assert_called_once()


class TestJobIdentity:
    def test_validate_job_ownership_and_repair_correlation(self, monkeypatch):
        job = SimpleNamespace(
            id=12, novel_id=5, correlation_id="stale", total_chapters=0, status="pending"
        )
        neighboring_job = SimpleNamespace(
            id=13, novel_id=5, correlation_id="neighbor", total_chapters=0, status="pending"
        )

        class _Session:
            def __init__(self):
                self.commits = 0

            def get(self, _model, job_id):
                return {12: job, 13: neighboring_job}.get(job_id)

            def add(self, _obj):
                return None

            def commit(self):
                self.commits += 1

        session = _Session()
        monkeypatch.setattr(
            flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session)
        )

        assert flow_mod._validate_and_repair_job_identity(
            5, 12, "prefect-run", total_chapters=3
        )
        assert job.correlation_id == "prefect-run"
        assert job.total_chapters == 3
        assert neighboring_job.total_chapters == 0
        assert session.commits == 1

        with pytest.raises(ValueError, match="不属于"):
            flow_mod._validate_and_repair_job_identity(6, 12, "wrong-novel")

    def test_mark_job_as_failed_updates_job_and_checkpoint(self, monkeypatch, fake_logger):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))

        import services.material.ingestion_jobs_service as jobs_mod

        failed_calls = []
        latest_job = SimpleNamespace(id=123)

        class FakeIngestionJobsService:
            def get_latest_by_novel(self, _session, _novel_id):
                return latest_job

            def fail_job(self, _session, job, *, error_code, stage, reason, details):
                failed_calls.append(job)
                assert error_code == "ERR_MATERIAL_LLM_UNAVAILABLE"
                assert stage == "flow"
                assert reason == "ERR_MATERIAL_LLM_UNAVAILABLE"
                assert isinstance(details.get("elapsed_ms"), int)
                assert details["elapsed_ms"] >= 0
                assert details["exception_type"] == "LLMNonRetryableError"
                # Raw exception text is never persisted on the job.
                assert "message" not in details
                return True

        monkeypatch.setattr(jobs_mod, "IngestionJobsService", FakeIngestionJobsService)

        cp_mgr = MagicMock()
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp_mgr)

        publisher = MagicMock()
        flow_mod._mark_job_as_failed(
            novel_id=5,
            _correlation_id=None,
            error_code="ERR_MATERIAL_LLM_UNAVAILABLE",
            exception_type="LLMNonRetryableError",
            flow_start=0.0,
            logger=fake_logger,
            publisher=publisher,
        )

        assert failed_calls == [latest_job]
        cp_mgr.mark_stage_failed.assert_called_once_with("failed", "ERR_MATERIAL_LLM_UNAVAILABLE")
        publisher.publish.assert_called_once()

    def test_terminal_job_is_not_runnable(self, monkeypatch):
        job = SimpleNamespace(
            id=12, novel_id=5, correlation_id=None, total_chapters=0, status="failed"
        )

        class _Session:
            commits = 0

            def get(self, _model, _job_id):
                return job

            def add(self, _obj):
                raise AssertionError("terminal job must not be modified")

            def commit(self):
                self.commits += 1

        monkeypatch.setattr(
            flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(_Session())
        )

        assert flow_mod._validate_and_repair_job_identity(5, 12, "late-run") is False
        assert job.correlation_id is None

    def test_flow_run_for_reconciled_job_exits_without_work(self, monkeypatch, db_session):
        """A run Prefect starts after the watchdog failed (and refunded) its job does nothing."""
        from models.material_models import IngestionJob, Novel

        novel = Novel(user_id="owner-1", title="late run")
        db_session.add(novel)
        db_session.flush()
        job = IngestionJob(
            novel_id=novel.id,
            source_path="/tmp/late.txt",
            status="failed",
            error_message="ERR_MATERIAL_DISPATCH_TIMEOUT",
        )
        db_session.add(job)
        db_session.commit()

        monkeypatch.setattr(
            flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(db_session)
        )
        monkeypatch.setattr(flow_mod, "get_run_logger", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(flow_mod, "ProgressPublisher", MagicMock())
        monkeypatch.setattr(
            flow_mod,
            "StageExecutor",
            MagicMock(side_effect=AssertionError("reconciled job must not run")),
        )
        mark_failed = MagicMock()
        monkeypatch.setattr(flow_mod, "_mark_job_as_failed", mark_failed)

        result = flow_mod.novel_ingestion_v3.fn(
            file_path="/tmp/late.txt",
            user_id="owner-1",
            novel_id=novel.id,
            job_id=job.id,
        )

        assert result["status"] == "skipped"
        mark_failed.assert_not_called()
        db_session.refresh(job)
        assert job.status == "failed"
        assert job.error_message == "ERR_MATERIAL_DISPATCH_TIMEOUT"
