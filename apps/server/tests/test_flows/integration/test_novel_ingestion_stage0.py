from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from flows.pipelines import novel_ingestion_v3_flow as flow_mod


class _FakeSession:
    def __init__(self, novel=None):
        self._novel = novel
        self.commit_calls = 0

    def get(self, _model_cls, _id):
        return self._novel

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
class TestExecuteStage0:
    def test_returns_existing_novel_short_circuit(self, monkeypatch):
        session = _FakeSession()
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))
        monkeypatch.setattr(
            flow_mod,
            "parse_novel_chapters",
            lambda _file_path, _encoding: {
                "chapters": [{"chapter_number": 1, "title": "c1", "content": "x"}],
                "novel_title": "inferred",
            },
        )

        import services.material.chapters_service as chapters_mod
        import services.material.novels_service as novels_mod

        class FakeNovelsService:
            def get_by_content_hash(self, _session, _content_hash, _user_id):
                return SimpleNamespace(id=42)

            def list_chapter_ids(self, _session, _novel_id):
                return [101, 102]

        class FakeChaptersService:
            def create_chapters(self, *_args, **_kwargs):
                raise AssertionError("should not create chapters when novel already exists")

        monkeypatch.setattr(novels_mod, "NovelsService", FakeNovelsService)
        monkeypatch.setattr(chapters_mod, "ChaptersService", FakeChaptersService)
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: "cp-42")

        result = flow_mod._execute_stage0(
            file_path="/tmp/demo.txt",
            novel_title=None,
            author=None,
            user_id="u1",
            content_hash="hash",
            encoding="utf-8",
            file_size=10,
            correlation_id=None,
            logger=MagicMock(),
            publisher=MagicMock(),
            existing_novel_id=None,
        )

        assert result == {
            "novel_id": 42,
            "chapter_ids": [101, 102],
            "checkpoint_manager": "cp-42",
            "job_id": None,
        }

    def test_existing_novel_id_missing_record_raises(self, monkeypatch):
        session = _FakeSession(novel=None)
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))
        monkeypatch.setattr(
            flow_mod,
            "parse_novel_chapters",
            lambda _file_path, _encoding: {
                "chapters": [{"chapter_number": 1, "title": "c1", "content": "x"}],
                "novel_title": "x",
            },
        )

        with pytest.raises(ValueError, match="预创建的小说不存在"):
            flow_mod._execute_stage0(
                file_path="/tmp/demo.txt",
                novel_title="title",
                author="author",
                user_id="u1",
                content_hash="hash",
                encoding="utf-8",
                file_size=10,
                correlation_id="cid",
                logger=MagicMock(),
                publisher=MagicMock(),
                existing_novel_id=999,
            )

    def test_existing_novel_id_reuses_job_and_creates_chapters(self, monkeypatch):
        novel = SimpleNamespace(
            id=9,
            source_meta=json.dumps(
                {
                    "original_filename": "original.txt",
                    "char_count": 1234,
                    "file_path": "/uploads/original.txt",
                    "file_size": 4321,
                    "future_key": "preserve-me",
                }
            ),
        )
        session = _FakeSession(novel=novel)
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))
        monkeypatch.setattr(
            flow_mod,
            "parse_novel_chapters",
            lambda _file_path, _encoding: {
                "chapters": [{"chapter_number": 1, "title": "c1", "content": "hello"}],
                "novel_title": "inferred",
            },
        )

        import services.material.chapters_service as chapters_mod
        import services.material.checkpoint_service as cp_mod
        import services.material.ingestion_jobs_service as jobs_mod

        class FakeChaptersService:
            def create_chapters(self, _session, _novel, chapter_dicts):
                assert len(chapter_dicts) == 1
                assert chapter_dicts[0]["title"] == "c1"
                return [11]

        existing_job = SimpleNamespace(id=77, status="pending", total_chapters=0, correlation_id=None)

        class FakeIngestionJobsService:
            def get_latest_by_novel(self, _session, _novel_id):
                return existing_job

            def create_job(self, *_args, **_kwargs):
                raise AssertionError("should reuse existing job")

        upserts = []

        class FakeCheckpointService:
            def upsert(self, _session, novel_id, stage, data, status=None, job_id=None):
                upserts.append((novel_id, stage, data, status, job_id))

        monkeypatch.setattr(chapters_mod, "ChaptersService", FakeChaptersService)
        monkeypatch.setattr(jobs_mod, "IngestionJobsService", FakeIngestionJobsService)
        monkeypatch.setattr(cp_mod, "CheckpointService", FakeCheckpointService)
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: "cp-9")

        publisher = MagicMock()
        result = flow_mod._execute_stage0(
            file_path="/tmp/demo.txt",
            novel_title="title",
            author="author",
            user_id="u1",
            content_hash="hash",
            encoding="utf-8",
            file_size=10,
            correlation_id="cid-1",
            logger=MagicMock(),
            publisher=publisher,
            existing_novel_id=9,
        )

        assert existing_job.status == "processing"
        assert existing_job.total_chapters == 1
        assert existing_job.correlation_id == "cid-1"
        source_meta = json.loads(novel.source_meta)
        assert source_meta["original_filename"] == "original.txt"
        assert source_meta["char_count"] == 1234
        assert source_meta["future_key"] == "preserve-me"
        assert source_meta["file_path"] == "/uploads/original.txt"
        assert source_meta["file_size"] == 4321
        assert source_meta["md5_checksum"] == "hash"
        assert source_meta["encoding"] == "utf-8"
        assert upserts == [(9, "stage0", {}, "completed", 77)]
        assert session.commit_calls == 1
        assert result["novel_id"] == 9
        assert result["chapter_ids"] == [11]
        publisher.publish.assert_called_once()

    def test_free_trial_job_creates_only_the_first_chapters(self, monkeypatch):
        """免费试拆：任务上记了 chapter_limit 时，阶段0 只建前 N 章（只为这些章付模型费用）。"""
        novel = SimpleNamespace(id=9, source_meta=json.dumps({"file_path": "/uploads/a.txt"}))
        session = _FakeSession(novel=novel)
        monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(session))
        monkeypatch.setattr(
            flow_mod,
            "parse_novel_chapters",
            lambda _file_path, _encoding: {
                "chapters": [
                    {"chapter_number": n, "title": f"c{n}", "content": f"text {n}"} for n in range(1, 6)
                ],
                "novel_title": "inferred",
            },
        )

        import services.material.chapters_service as chapters_mod
        import services.material.checkpoint_service as cp_mod
        import services.material.ingestion_jobs_service as jobs_mod

        created_titles: list[str] = []

        class FakeChaptersService:
            def create_chapters(self, _session, _novel, chapter_dicts):
                created_titles.extend(chapter["title"] for chapter in chapter_dicts)
                return list(range(len(chapter_dicts)))

        existing_job = SimpleNamespace(
            id=77,
            status="pending",
            total_chapters=0,
            correlation_id=None,
            stage_progress=json.dumps({"billing": {"quota_mode": "trial", "chapter_limit": 2}}),
        )

        class FakeIngestionJobsService:
            def get_latest_by_novel(self, _session, _novel_id):
                return existing_job

        class FakeCheckpointService:
            def upsert(self, *_args, **_kwargs):
                return None

        monkeypatch.setattr(chapters_mod, "ChaptersService", FakeChaptersService)
        monkeypatch.setattr(jobs_mod, "IngestionJobsService", FakeIngestionJobsService)
        monkeypatch.setattr(cp_mod, "CheckpointService", FakeCheckpointService)
        monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: "cp")

        flow_mod._execute_stage0(
            file_path="/tmp/demo.txt",
            novel_title="title",
            author=None,
            user_id="u1",
            content_hash="hash",
            encoding="utf-8",
            file_size=10,
            correlation_id=None,
            logger=MagicMock(),
            publisher=MagicMock(),
            existing_novel_id=9,
        )

        assert created_titles == ["c1", "c2"]
        assert existing_job.total_chapters == 2
