"""
Tests for Material Library Services.

Unit tests for the material library services, covering:
- Novel CRUD operations (NovelsService)
- Chapter management (ChaptersService)
- Character upsert and queries (CharactersService)
- Plot bulk insert and queries (PlotsService)
- Ingestion job tracking (IngestionJobsService)
- Process checkpoint management (CheckpointService)
- Statistics aggregation (StatsService)
- User isolation and data integrity
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

from models.material_models import (
    Chapter,
    Character,
    IngestionJob,
    Novel,
    Plot,
    ProcessCheckpoint,
)
from services.material import (
    ChaptersService,
    CharactersService,
    CheckpointService,
    IngestionJobsService,
    NovelsService,
    PlotsService,
    StatsService,
)

# ============ Fixtures ============

@pytest.fixture
def novels_svc():
    """Return fresh NovelsService instance."""
    return NovelsService()


@pytest.fixture
def chapters_svc():
    """Return fresh ChaptersService instance."""
    return ChaptersService()


@pytest.fixture
def characters_svc():
    """Return fresh CharactersService instance."""
    return CharactersService()


@pytest.fixture
def plots_svc():
    """Return fresh PlotsService instance."""
    return PlotsService()


@pytest.fixture
def ingestion_jobs_svc():
    """Return fresh IngestionJobsService instance."""
    return IngestionJobsService()


@pytest.fixture
def checkpoint_svc():
    """Return fresh CheckpointService instance."""
    return CheckpointService()


@pytest.fixture
def stats_svc():
    """Return fresh StatsService instance."""
    return StatsService()


@pytest.fixture
def test_user_id():
    """Return test user ID."""
    return "user-material-123"


@pytest.fixture
def test_novel(db_session: Session, test_user_id: str):
    """Create a test novel."""
    novel = Novel(
        user_id=test_user_id,
        title="Test Novel",
        author="Test Author",
        synopsis="A test novel for unit testing",
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    return novel


@pytest.fixture
def test_chapter(db_session: Session, test_novel: Novel):
    """Create a test chapter."""
    chapter = Chapter(
        novel_id=test_novel.id,
        chapter_number=1,
        title="Chapter 1",
        original_content="Test content for chapter 1",
        summary="Summary of chapter 1",
    )
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)
    return chapter


@pytest.fixture
def test_chapters(db_session: Session, test_novel: Novel):
    """Create multiple test chapters."""
    chapters = []
    for i in range(1, 4):
        chapter = Chapter(
            novel_id=test_novel.id,
            chapter_number=i,
            title=f"Chapter {i}",
            original_content=f"Content for chapter {i}",
            summary=f"Summary {i}" if i < 3 else None,  # Last chapter has no summary
        )
        db_session.add(chapter)
        chapters.append(chapter)
    db_session.commit()
    for chapter in chapters:
        db_session.refresh(chapter)
    return chapters


@pytest.fixture
def test_character(db_session: Session, test_novel: Novel):
    """Create a test character."""
    character = Character(
        novel_id=test_novel.id,
        name="张三",
        aliases=json.dumps(["小张", "张哥"]),
        description="A test character",
        archetype="protagonist",
    )
    db_session.add(character)
    db_session.commit()
    db_session.refresh(character)
    return character


@pytest.fixture
def test_plot(db_session: Session, test_chapter: Chapter):
    """Create a test plot."""
    plot = Plot(
        chapter_id=test_chapter.id,
        index=0,
        plot_type="CONFLICT",
        description="A conflict occurs",
        characters=json.dumps(["张三", "李四"]),
    )
    db_session.add(plot)
    db_session.commit()
    db_session.refresh(plot)
    return plot


@pytest.fixture
def test_ingestion_job(db_session: Session, test_novel: Novel):
    """Create a test ingestion job."""
    job = IngestionJob(
        novel_id=test_novel.id,
        source_path="/test/path/novel.txt",
        status="pending",
        total_chapters=10,
        processed_chapters=0,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


# ============ NovelsService Tests ============

@pytest.mark.unit
class TestNovelsServiceGetById:
    """Tests for NovelsService.get_by_id method."""

    def test_get_by_id_existing(
        self, db_session: Session, novels_svc: NovelsService, test_novel: Novel
    ):
        """Test getting an existing novel by ID."""
        novel = novels_svc.get_by_id(db_session, test_novel.id)

        assert novel is not None
        assert novel.id == test_novel.id
        assert novel.title == "Test Novel"
        assert novel.author == "Test Author"

    def test_get_by_id_not_found(self, db_session: Session, novels_svc: NovelsService):
        """Test getting a non-existent novel."""
        novel = novels_svc.get_by_id(db_session, 99999)

        assert novel is None


@pytest.mark.unit
class TestNovelsServiceGetByContentHash:
    """Tests for NovelsService.get_by_content_hash method."""

    def test_get_by_content_hash_found(
        self, db_session: Session, novels_svc: NovelsService, test_user_id: str
    ):
        """Test finding novel by content hash."""
        source_meta = json.dumps({"md5_checksum": "abc123", "file_size": 1024})
        novel = Novel(
            user_id=test_user_id,
            title="Hashed Novel",
            source_meta=source_meta,
        )
        db_session.add(novel)
        db_session.commit()

        found = novels_svc.get_by_content_hash(db_session, "abc123", test_user_id)

        assert found is not None
        assert found.title == "Hashed Novel"

    def test_get_by_content_hash_not_found(
        self, db_session: Session, novels_svc: NovelsService
    ):
        """Test finding novel by non-existent hash."""
        found = novels_svc.get_by_content_hash(db_session, "nonexistent")

        assert found is None

    def test_get_by_content_hash_user_filter(
        self, db_session: Session, novels_svc: NovelsService, test_user_id: str
    ):
        """Test content hash search with user filtering."""
        source_meta = json.dumps({"md5_checksum": "user123hash"})
        novel = Novel(
            user_id="other-user",
            title="Other User Novel",
            source_meta=source_meta,
        )
        db_session.add(novel)
        db_session.commit()

        # Should not find when filtering by different user
        found = novels_svc.get_by_content_hash(db_session, "user123hash", test_user_id)
        assert found is None

        # Should find without user filter
        found = novels_svc.get_by_content_hash(db_session, "user123hash")
        assert found is not None


@pytest.mark.unit
class TestNovelsServiceCreateNovel:
    """Tests for NovelsService.create_novel method."""

    def test_create_novel_basic(
        self, db_session: Session, novels_svc: NovelsService, test_user_id: str
    ):
        """Test creating a basic novel."""
        data = {
            "user_id": test_user_id,
            "title": "New Novel",
            "author": "New Author",
        }
        novel = novels_svc.create_novel(db_session, data)

        assert novel.id is not None
        assert novel.title == "New Novel"
        assert novel.author == "New Author"
        assert novel.created_at is not None

    def test_create_novel_with_source_meta(
        self, db_session: Session, novels_svc: NovelsService, test_user_id: str
    ):
        """Test creating a novel with source metadata."""
        source_meta_dict = {
            "file_path": "/path/to/file.txt",
            "file_size": 2048,
            "md5_checksum": "def456",
        }
        data = {
            "user_id": test_user_id,
            "title": "Novel with Meta",
            "source_meta": source_meta_dict,
        }
        novel = novels_svc.create_novel(db_session, data)

        assert novel.id is not None
        # source_meta should be serialized to JSON string
        assert isinstance(novel.source_meta, str)
        parsed = json.loads(novel.source_meta)
        assert parsed["md5_checksum"] == "def456"


@pytest.mark.unit
class TestNovelsServiceUpdateSynopsis:
    """Tests for NovelsService.update_synopsis method."""

    def test_update_synopsis_success(
        self, db_session: Session, novels_svc: NovelsService, test_novel: Novel
    ):
        """Test updating novel synopsis."""
        novels_svc.update_synopsis(db_session, test_novel.id, "Updated synopsis")

        db_session.refresh(test_novel)
        assert test_novel.synopsis == "Updated synopsis"

    def test_update_synopsis_novel_not_found(
        self, db_session: Session, novels_svc: NovelsService
    ):
        """Test updating synopsis for non-existent novel."""
        with pytest.raises(ValueError, match="小说不存在"):
            novels_svc.update_synopsis(db_session, 99999, "New synopsis")


@pytest.mark.unit
class TestNovelsServiceListChapterIds:
    """Tests for NovelsService.list_chapter_ids method."""

    def test_list_chapter_ids(
        self, db_session: Session, novels_svc: NovelsService, test_chapters: list[Chapter]
    ):
        """Test listing chapter IDs for a novel."""
        novel_id = test_chapters[0].novel_id
        ids = novels_svc.list_chapter_ids(db_session, novel_id)

        assert len(ids) == 3
        assert all(isinstance(id, int) for id in ids)

    def test_list_chapter_ids_empty(
        self, db_session: Session, novels_svc: NovelsService, test_novel: Novel
    ):
        """Test listing chapter IDs for novel with no chapters."""
        ids = novels_svc.list_chapter_ids(db_session, test_novel.id)

        assert ids == []


@pytest.mark.unit
class TestNovelsServiceIntelligentChunks:
    """Tests for intelligent chunks read/write on source_meta."""

    def test_set_and_get_intelligent_chunks(
        self, db_session: Session, novels_svc: NovelsService, test_novel: Novel
    ):
        """Should persist chunk metadata into source_meta and read it back."""
        test_novel.source_meta = json.dumps({"file_path": "/tmp/test.txt"})
        db_session.add(test_novel)
        db_session.commit()

        chunks_data = {
            "chunk_count": 2,
            "chunks": [{"chunk_id": 1, "start_chapter": 1, "end_chapter": 3}],
        }
        novels_svc.set_intelligent_chunks(db_session, test_novel.id, chunks_data)
        db_session.commit()

        db_session.refresh(test_novel)
        parsed_meta = json.loads(test_novel.source_meta or "{}")
        assert parsed_meta.get("file_path") == "/tmp/test.txt"
        assert parsed_meta.get("intelligent_chunks") == chunks_data

        loaded = novels_svc.get_intelligent_chunks(db_session, test_novel.id)
        assert loaded == chunks_data

    def test_get_intelligent_chunks_when_missing(
        self, db_session: Session, novels_svc: NovelsService, test_novel: Novel
    ):
        """Should return None when intelligent chunk data is absent."""
        loaded = novels_svc.get_intelligent_chunks(db_session, test_novel.id)
        assert loaded is None

    def test_set_intelligent_chunks_novel_not_found(
        self, db_session: Session, novels_svc: NovelsService
    ):
        """Setting chunks for missing novel should raise ValueError."""
        with pytest.raises(ValueError, match="小说不存在"):
            novels_svc.set_intelligent_chunks(db_session, 99999, {"chunk_count": 1})


# ============ ChaptersService Tests ============

@pytest.mark.unit
class TestChaptersServiceGetById:
    """Tests for ChaptersService.get_by_id method."""

    def test_get_by_id_existing(
        self, db_session: Session, chapters_svc: ChaptersService, test_chapter: Chapter
    ):
        """Test getting an existing chapter."""
        chapter = chapters_svc.get_by_id(db_session, test_chapter.id)

        assert chapter is not None
        assert chapter.title == "Chapter 1"

    def test_get_by_id_not_found(self, db_session: Session, chapters_svc: ChaptersService):
        """Test getting a non-existent chapter."""
        chapter = chapters_svc.get_by_id(db_session, 99999)

        assert chapter is None


@pytest.mark.unit
class TestChaptersServiceListByNovel:
    """Tests for ChaptersService list methods."""

    def test_list_ids_by_novel(
        self, db_session: Session, chapters_svc: ChaptersService, test_chapters: list[Chapter]
    ):
        """Test listing chapter IDs by novel."""
        novel_id = test_chapters[0].novel_id
        ids = chapters_svc.list_ids_by_novel(db_session, novel_id)

        assert len(ids) == 3

    def test_list_by_novel_ordered(
        self, db_session: Session, chapters_svc: ChaptersService, test_chapters: list[Chapter]
    ):
        """Test listing chapters ordered by chapter number."""
        novel_id = test_chapters[0].novel_id
        chapters = chapters_svc.list_by_novel_ordered(db_session, novel_id)

        assert len(chapters) == 3
        assert chapters[0].chapter_number == 1
        assert chapters[1].chapter_number == 2
        assert chapters[2].chapter_number == 3

    def test_list_by_novel_with_filter(
        self, db_session: Session, chapters_svc: ChaptersService, test_chapters: list[Chapter]
    ):
        """Test listing chapters with ID filter."""
        novel_id = test_chapters[0].novel_id
        chapter_ids = [test_chapters[0].id, test_chapters[2].id]
        chapters = chapters_svc.list_by_novel_ordered(
            db_session, novel_id, chapter_ids=chapter_ids
        )

        assert len(chapters) == 2

    def test_list_ids_by_number_range(
        self, db_session: Session, chapters_svc: ChaptersService, test_chapters: list[Chapter]
    ):
        """Test listing chapter IDs by number range."""
        novel_id = test_chapters[0].novel_id
        ids = chapters_svc.list_ids_by_number_range(
            db_session, novel_id, start_number=1, end_number=2
        )

        assert len(ids) == 2


@pytest.mark.unit
class TestChaptersServiceGetCoreFields:
    """Tests for ChaptersService.get_chapter_core_fields method."""

    def test_get_chapter_core_fields(
        self, db_session: Session, chapters_svc: ChaptersService, test_chapter: Chapter
    ):
        """Test getting chapter core fields."""
        fields = chapters_svc.get_chapter_core_fields(db_session, test_chapter.id)

        assert fields is not None
        assert fields["title"] == "Chapter 1"
        assert fields["number"] == 1
        assert "content" in fields

    def test_get_chapter_core_fields_not_found(
        self, db_session: Session, chapters_svc: ChaptersService
    ):
        """Test getting core fields for non-existent chapter."""
        fields = chapters_svc.get_chapter_core_fields(db_session, 99999)

        assert fields is None


@pytest.mark.unit
class TestChaptersServiceCreateChapters:
    """Tests for ChaptersService.create_chapters method."""

    def test_create_chapters(
        self, db_session: Session, chapters_svc: ChaptersService, test_novel: Novel
    ):
        """Test creating multiple chapters."""
        chapters_data = [
            {"chapter_number": 1, "title": "First", "content": "Content 1"},
            {"chapter_number": 2, "title": "Second", "content": "Content 2"},
        ]
        ids = chapters_svc.create_chapters(db_session, test_novel, chapters_data)

        assert len(ids) == 2
        assert all(isinstance(id, int) for id in ids)


@pytest.mark.unit
class TestChaptersServiceSaveSummary:
    """Tests for ChaptersService.save_summary method."""

    def test_save_summary(
        self, db_session: Session, chapters_svc: ChaptersService, test_chapter: Chapter
    ):
        """Test saving chapter summary."""
        chapters_svc.save_summary(db_session, test_chapter.id, "New summary")

        db_session.refresh(test_chapter)
        assert test_chapter.summary == "New summary"


# ============ CharactersService Tests ============

@pytest.mark.unit
class TestCharactersServiceGetByName:
    """Tests for CharactersService.get_by_name method."""

    def test_get_by_name_found(
        self, db_session: Session, characters_svc: CharactersService,
        test_novel: Novel, test_character: Character
    ):
        """Test finding character by name."""
        char = characters_svc.get_by_name(db_session, test_novel.id, "张三")

        assert char is not None
        assert char.name == "张三"

    def test_get_by_name_not_found(
        self, db_session: Session, characters_svc: CharactersService, test_novel: Novel
    ):
        """Test finding non-existent character."""
        char = characters_svc.get_by_name(db_session, test_novel.id, "不存在")

        assert char is None


@pytest.mark.unit
class TestCharactersServiceListByNovel:
    """Tests for CharactersService.list_by_novel method."""

    def test_list_by_novel(
        self, db_session: Session, characters_svc: CharactersService,
        test_novel: Novel, test_character: Character
    ):
        """Test listing characters for a novel."""
        chars = characters_svc.list_by_novel(db_session, test_novel.id)

        assert len(chars) == 1
        assert chars[0].name == "张三"

    def test_list_by_novel_empty(
        self, db_session: Session, characters_svc: CharactersService, test_novel: Novel
    ):
        """Test listing characters for novel with no characters."""
        # Create novel without characters
        novel2 = Novel(user_id="user2", title="Empty Novel")
        db_session.add(novel2)
        db_session.commit()

        chars = characters_svc.list_by_novel(db_session, novel2.id)

        assert chars == []


@pytest.mark.unit
class TestCharactersServiceUpsert:
    """Tests for CharactersService.upsert_characters method."""

    def test_upsert_create_new(
        self, db_session: Session, characters_svc: CharactersService, test_novel: Novel
    ):
        """Test creating new characters via upsert."""
        items = [
            {"name": "李四", "description": "New character"},
            {"name": "王五", "description": "Another character"},
        ]
        created, updated = characters_svc.upsert_characters(db_session, test_novel.id, items)

        assert created == 2
        assert updated == 0

    def test_upsert_update_existing(
        self, db_session: Session, characters_svc: CharactersService,
        test_novel: Novel, test_character: Character
    ):
        """Test updating existing character via upsert."""
        items = [
            {"name": "张三", "description": "Updated description"},
        ]
        created, updated = characters_svc.upsert_characters(db_session, test_novel.id, items)

        assert created == 0
        assert updated == 1

        db_session.refresh(test_character)
        assert test_character.description == "Updated description"

    def test_upsert_mixed(
        self, db_session: Session, characters_svc: CharactersService,
        test_novel: Novel, test_character: Character
    ):
        """Test upsert with both create and update."""
        items = [
            {"name": "张三", "description": "Updated"},
            {"name": "新角色", "description": "New"},
        ]
        created, updated = characters_svc.upsert_characters(db_session, test_novel.id, items)

        assert created == 1
        assert updated == 1

    def test_upsert_skip_missing_name(
        self, db_session: Session, characters_svc: CharactersService, test_novel: Novel
    ):
        """Test upsert skips items without name."""
        items = [
            {"description": "No name"},
            {"name": "有名字", "description": "Has name"},
        ]
        created, updated = characters_svc.upsert_characters(db_session, test_novel.id, items)

        assert created == 1


# ============ PlotsService Tests ============

@pytest.mark.unit
class TestPlotsServiceBulkInsert:
    """Tests for PlotsService.bulk_insert method."""

    def test_bulk_insert(
        self, db_session: Session, plots_svc: PlotsService,
        test_novel: Novel, test_chapter: Chapter
    ):
        """Test bulk inserting plots."""
        plots_data = [
            {
                "chapter_id": test_chapter.id,
                "index": 0,
                "type": "CONFLICT",
                "description": "First plot",
            },
            {
                "chapter_id": test_chapter.id,
                "index": 1,
                "type": "RESOLUTION",
                "description": "Second plot",
            },
        ]
        count = plots_svc.bulk_insert(db_session, test_novel.id, plots_data)

        assert count == 2

    def test_bulk_insert_with_characters(
        self, db_session: Session, plots_svc: PlotsService,
        test_novel: Novel, test_chapter: Chapter
    ):
        """Test bulk insert with character list."""
        plots_data = [
            {
                "chapter_id": test_chapter.id,
                "index": 0,
                "type": "DIALOGUE",
                "description": "Dialogue scene",
                "characters": ["张三", "李四"],
            },
        ]
        count = plots_svc.bulk_insert(db_session, test_novel.id, plots_data)

        assert count == 1


@pytest.mark.unit
class TestPlotsServiceList:
    """Tests for PlotsService list methods."""

    def test_list_by_novel(
        self, db_session: Session, plots_svc: PlotsService,
        test_novel: Novel, test_plot: Plot
    ):
        """Test listing plots by novel."""
        plots = plots_svc.list_by_novel(db_session, test_novel.id)

        assert len(plots) == 1
        assert plots[0].description == "A conflict occurs"

    def test_list_by_chapter_ids(
        self, db_session: Session, plots_svc: PlotsService,
        test_chapters: list[Chapter], test_chapter: Chapter, test_plot: Plot
    ):
        """Test listing plots by chapter IDs."""
        chapter_ids = [test_chapter.id]
        plots = plots_svc.list_by_chapter_ids(db_session, chapter_ids)

        assert len(plots) >= 1

    def test_list_by_chapter_ids_empty(
        self, db_session: Session, plots_svc: PlotsService
    ):
        """Test listing plots with empty chapter IDs."""
        plots = plots_svc.list_by_chapter_ids(db_session, [])

        assert plots == []


# ============ IngestionJobsService Tests ============

@pytest.mark.unit
class TestIngestionJobsServiceCreate:
    """Tests for IngestionJobsService.create_job method."""

    def test_create_job(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_novel: Novel
    ):
        """Test creating an ingestion job."""
        job = ingestion_jobs_svc.create_job(
            db_session,
            novel_id=test_novel.id,
            total_chapters=20,
            source_path="/path/to/novel.txt",
        )

        assert job.id is not None
        assert job.novel_id == test_novel.id
        assert job.status == "pending"
        assert job.total_chapters == 20
        assert job.processed_chapters == 0

    def test_create_job_with_correlation_id(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_novel: Novel
    ):
        """Test creating job with correlation ID."""
        job = ingestion_jobs_svc.create_job(
            db_session,
            novel_id=test_novel.id,
            total_chapters=10,
            correlation_id="corr-123",
        )

        assert job.correlation_id == "corr-123"


@pytest.mark.unit
class TestIngestionJobsServiceGetLatest:
    """Tests for IngestionJobsService.get_latest_by_novel method."""

    def test_get_latest_by_novel(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_ingestion_job: IngestionJob
    ):
        """Test getting latest job for a novel."""
        job = ingestion_jobs_svc.get_latest_by_novel(
            db_session, test_ingestion_job.novel_id
        )

        assert job is not None
        assert job.id == test_ingestion_job.id

    def test_get_latest_returns_most_recent(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_novel: Novel
    ):
        """Test that get_latest returns the most recent job."""
        # Create multiple jobs
        ingestion_jobs_svc.create_job(
            db_session, novel_id=test_novel.id, total_chapters=10
        )
        db_session.commit()
        job2 = ingestion_jobs_svc.create_job(
            db_session, novel_id=test_novel.id, total_chapters=15
        )
        db_session.commit()

        latest = ingestion_jobs_svc.get_latest_by_novel(db_session, test_novel.id)

        assert latest.id == job2.id


@pytest.mark.unit
class TestIngestionJobsServiceUpdate:
    """Tests for IngestionJobsService update methods."""

    def test_update_status(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_ingestion_job: IngestionJob
    ):
        """Test updating job status."""
        ingestion_jobs_svc.update_status(
            db_session, test_ingestion_job.id, "processing"
        )

        db_session.refresh(test_ingestion_job)
        assert test_ingestion_job.status == "processing"

    def test_update_processed(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_ingestion_job: IngestionJob
    ):
        """Test updating processed chapters count."""
        ingestion_jobs_svc.update_processed(
            db_session, test_ingestion_job.id, processed_chapters=5
        )

        db_session.refresh(test_ingestion_job)
        assert test_ingestion_job.processed_chapters == 5

    def test_update_processed_and_status(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_ingestion_job: IngestionJob
    ):
        """Test updating both processed count and status."""
        ingestion_jobs_svc.update_processed(
            db_session,
            test_ingestion_job.id,
            processed_chapters=10,
            status="completed",
        )

        db_session.refresh(test_ingestion_job)
        assert test_ingestion_job.processed_chapters == 10
        assert test_ingestion_job.status == "completed"

    def test_update_processed_completed_with_errors_sets_completion_time(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_ingestion_job: IngestionJob
    ):
        ingestion_jobs_svc.update_processed(
            db_session,
            test_ingestion_job.id,
            processed_chapters=3,
            status="completed_with_errors",
        )

        db_session.refresh(test_ingestion_job)
        assert test_ingestion_job.status == "completed_with_errors"
        assert test_ingestion_job.completed_at is not None

    def test_reconcile_stale_pending_job_marks_failed(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_ingestion_job: IngestionJob
    ):
        stale_time = datetime.utcnow() - timedelta(hours=1)
        test_ingestion_job.updated_at = stale_time
        test_ingestion_job.created_at = stale_time
        test_ingestion_job.status = "pending"
        test_ingestion_job.correlation_id = None
        db_session.add(test_ingestion_job)
        db_session.commit()

        ingestion_jobs_svc.reconcile_stale_job(db_session, test_ingestion_job)

        db_session.refresh(test_ingestion_job)
        assert test_ingestion_job.status == "failed"
        assert test_ingestion_job.error_message == "ERR_MATERIAL_DISPATCH_TIMEOUT"

    def test_reconcile_stale_processing_job_marks_failed(
        self, db_session: Session, ingestion_jobs_svc: IngestionJobsService,
        test_ingestion_job: IngestionJob
    ):
        stale_time = datetime.utcnow() - timedelta(hours=5)
        test_ingestion_job.updated_at = stale_time
        test_ingestion_job.created_at = stale_time
        test_ingestion_job.status = "processing"
        db_session.add(test_ingestion_job)
        db_session.commit()

        ingestion_jobs_svc.reconcile_stale_job(db_session, test_ingestion_job)

        db_session.refresh(test_ingestion_job)
        assert test_ingestion_job.status == "failed"
        assert test_ingestion_job.error_message == "ERR_MATERIAL_PROCESSING_TIMEOUT"


# ============ CheckpointService Tests ============

@pytest.mark.unit
class TestCheckpointServiceGet:
    """Tests for CheckpointService.get method."""

    def test_get_checkpoint(
        self, db_session: Session, checkpoint_svc: CheckpointService,
        test_novel: Novel
    ):
        """Test getting a checkpoint."""
        cp = ProcessCheckpoint(
            novel_id=test_novel.id,
            stage="stage0",
            stage_status="completed",
            checkpoint_data=json.dumps({"chapters": 10}),
        )
        db_session.add(cp)
        db_session.commit()

        found = checkpoint_svc.get(db_session, test_novel.id, "stage0")

        assert found is not None
        assert found.stage_status == "completed"

    def test_get_checkpoint_not_found(
        self, db_session: Session, checkpoint_svc: CheckpointService,
        test_novel: Novel
    ):
        """Test getting non-existent checkpoint."""
        found = checkpoint_svc.get(db_session, test_novel.id, "stage1")

        assert found is None


@pytest.mark.unit
class TestCheckpointServiceGetLatest:
    """Tests for CheckpointService.get_latest method."""

    def test_get_latest(
        self, db_session: Session, checkpoint_svc: CheckpointService,
        test_novel: Novel
    ):
        """Test getting latest checkpoint."""
        cp1 = ProcessCheckpoint(
            novel_id=test_novel.id,
            stage="stage0",
            stage_status="completed",
        )
        cp2 = ProcessCheckpoint(
            novel_id=test_novel.id,
            stage="stage1",
            stage_status="processing",
        )
        db_session.add_all([cp1, cp2])
        db_session.commit()

        latest = checkpoint_svc.get_latest(db_session, test_novel.id)

        assert latest is not None


@pytest.mark.unit
class TestCheckpointServiceUpsert:
    """Tests for CheckpointService.upsert method."""

    def test_upsert_create(
        self, db_session: Session, checkpoint_svc: CheckpointService,
        test_novel: Novel
    ):
        """Test creating a new checkpoint."""
        cp = checkpoint_svc.upsert(
            db_session,
            novel_id=test_novel.id,
            stage="stage0",
            data={"chapters": 10},
            status="processing",
        )

        assert cp.id is not None
        assert cp.stage == "stage0"
        assert cp.stage_status == "processing"

    def test_upsert_update(
        self, db_session: Session, checkpoint_svc: CheckpointService,
        test_novel: Novel
    ):
        """Test updating an existing checkpoint."""
        # Create initial
        checkpoint_svc.upsert(
            db_session,
            novel_id=test_novel.id,
            stage="stage0",
            data={"chapters": 10},
            status="processing",
        )

        # Update
        cp = checkpoint_svc.upsert(
            db_session,
            novel_id=test_novel.id,
            stage="stage0",
            data={"processed": 5},
            status="completed",
        )

        data = json.loads(cp.checkpoint_data)
        assert data["chapters"] == 10
        assert data["processed"] == 5
        assert cp.stage_status == "completed"

    def test_upsert_with_error(
        self, db_session: Session, checkpoint_svc: CheckpointService,
        test_novel: Novel
    ):
        """Test upsert with error on update (error is only applied on update, not create)."""
        # First create the checkpoint
        checkpoint_svc.upsert(
            db_session,
            novel_id=test_novel.id,
            stage="stage0",
            data={},
            status="processing",
        )

        # Then update with error - this triggers mark_failed
        cp = checkpoint_svc.upsert(
            db_session,
            novel_id=test_novel.id,
            stage="stage0",
            data={},
            error="Something went wrong",
        )

        assert cp.stage_status == "failed"
        assert cp.error_message == "Something went wrong"


@pytest.mark.unit
class TestCheckpointServiceDeleteAll:
    """Tests for CheckpointService.delete_all method."""

    def test_delete_all(
        self, db_session: Session, checkpoint_svc: CheckpointService,
        test_novel: Novel
    ):
        """Test deleting all checkpoints for a novel."""
        cp1 = ProcessCheckpoint(novel_id=test_novel.id, stage="stage0", stage_status="completed")
        cp2 = ProcessCheckpoint(novel_id=test_novel.id, stage="stage1", stage_status="completed")
        db_session.add_all([cp1, cp2])
        db_session.commit()

        checkpoint_svc.delete_all(db_session, test_novel.id)

        remaining = db_session.exec(
            ProcessCheckpoint.__table__.select().where(
                ProcessCheckpoint.novel_id == test_novel.id
            )
        ).all()
        assert len(remaining) == 0


# ============ StatsService Tests ============

@pytest.mark.unit
class TestStatsServiceGetNovelStats:
    """Tests for StatsService.get_novel_stats method."""

    def test_get_novel_stats_empty(
        self, db_session: Session, stats_svc: StatsService, test_novel: Novel
    ):
        """Test stats for novel with no content."""
        stats = stats_svc.get_novel_stats(db_session, test_novel.id)

        assert stats["novel_id"] == test_novel.id
        assert stats["chapter_count"] == 0
        assert stats["plot_count"] == 0
        assert stats["character_count"] == 0

    def test_get_novel_stats_with_content(
        self, db_session: Session, stats_svc: StatsService,
        test_novel: Novel
    ):
        """Test stats for novel with content."""
        # Create chapters
        for i in range(1, 4):
            chapter = Chapter(
                novel_id=test_novel.id,
                chapter_number=i,
                title=f"Chapter {i}",
                original_content=f"Content {i}",
                summary=f"Summary {i}" if i < 3 else None,
            )
            db_session.add(chapter)
        db_session.commit()

        # Create character
        character = Character(
            novel_id=test_novel.id,
            name="测试角色",
        )
        db_session.add(character)
        db_session.commit()

        stats = stats_svc.get_novel_stats(db_session, test_novel.id)

        assert stats["chapter_count"] == 3
        assert stats["character_count"] == 1


@pytest.mark.unit
class TestStatsServiceGetChapterStats:
    """Tests for StatsService.get_chapter_stats method."""

    def test_get_chapter_stats(
        self, db_session: Session, stats_svc: StatsService,
        test_chapter: Chapter, test_plot: Plot
    ):
        """Test getting chapter stats."""
        stats = stats_svc.get_chapter_stats(db_session, test_chapter.id)

        assert stats["chapter_id"] == test_chapter.id
        assert stats["chapter_number"] == 1
        assert stats["plot_count"] >= 1

    def test_get_chapter_stats_not_found(
        self, db_session: Session, stats_svc: StatsService
    ):
        """Test stats for non-existent chapter."""
        stats = stats_svc.get_chapter_stats(db_session, 99999)

        assert stats == {}


@pytest.mark.unit
class TestStatsServiceCountStage1:
    """Tests for StatsService.count_stage1 method."""

    def test_count_stage1(
        self, db_session: Session, stats_svc: StatsService,
        test_novel: Novel
    ):
        """Test counting stage1 completion."""
        # Create chapters - 2 with summaries, 1 without
        for i in range(1, 4):
            chapter = Chapter(
                novel_id=test_novel.id,
                chapter_number=i,
                title=f"Chapter {i}",
                summary=f"Summary {i}" if i < 3 else None,  # Last one has no summary
            )
            db_session.add(chapter)
        db_session.commit()

        counts = stats_svc.count_stage1(db_session, test_novel.id)

        # 2 chapters have summaries, 1 has None
        assert counts["summaries_count"] == 2


# ============ Edge Cases and Data Isolation Tests ============

@pytest.mark.unit
class TestMaterialDataIsolation:
    """Tests for user data isolation in material library."""

    def test_novel_user_isolation(
        self, db_session: Session, novels_svc: NovelsService, test_novel: Novel
    ):
        """Test that novels are isolated by user_id."""
        # Create novel for different user
        other_novel = Novel(
            user_id="other-user-456",
            title="Other User Novel",
        )
        db_session.add(other_novel)
        db_session.commit()

        # Verify user_id is set correctly
        assert test_novel.user_id != other_novel.user_id

    def test_character_novel_isolation(
        self, db_session: Session, characters_svc: CharactersService,
        test_novel: Novel, test_character: Character
    ):
        """Test that characters are isolated by novel."""
        # Create another novel with same character name
        novel2 = Novel(user_id="user2", title="Novel 2")
        db_session.add(novel2)
        db_session.commit()

        # Same character name should not conflict across novels
        char2 = Character(
            novel_id=novel2.id,
            name="张三",  # Same name as test_character
            description="Different character",
        )
        db_session.add(char2)
        db_session.commit()

        # Both should exist
        char1 = characters_svc.get_by_name(db_session, test_novel.id, "张三")
        char2_found = characters_svc.get_by_name(db_session, novel2.id, "张三")

        assert char1 is not None
        assert char2_found is not None
        assert char1.novel_id != char2_found.novel_id


@pytest.mark.unit
class TestIngestionJobProgress:
    """Tests for IngestionJob progress calculations."""

    def test_progress_percentage(
        self, db_session: Session, test_novel: Novel
    ):
        """Test progress percentage calculation."""
        job = IngestionJob(
            novel_id=test_novel.id,
            source_path="/test/path",
            status="processing",
            total_chapters=10,
            processed_chapters=3,
        )
        db_session.add(job)
        db_session.commit()

        assert job.progress_percentage == 30.0

    def test_progress_percentage_zero_total(
        self, db_session: Session, test_novel: Novel
    ):
        """Test progress when total is zero."""
        job = IngestionJob(
            novel_id=test_novel.id,
            source_path="/test/path",
            status="pending",
            total_chapters=0,
            processed_chapters=0,
        )
        db_session.add(job)
        db_session.commit()

        assert job.progress_percentage == 0.0

    def test_stage_progress_update(
        self, db_session: Session, test_novel: Novel
    ):
        """Test updating stage progress."""
        job = IngestionJob(
            novel_id=test_novel.id,
            source_path="/test/path",
            status="processing",
            total_chapters=10,
        )
        db_session.add(job)
        db_session.commit()

        job.update_stage_progress("stage0", "completed", chapters=10)
        db_session.commit()

        progress = json.loads(job.stage_progress)
        assert progress["stage0"]["status"] == "completed"
        assert progress["stage0"]["chapters"] == 10


@pytest.mark.unit
class TestProcessCheckpointHelpers:
    """Tests for ProcessCheckpoint helper methods."""

    def test_mark_completed(
        self, db_session: Session, test_novel: Novel
    ):
        """Test marking checkpoint as completed."""
        cp = ProcessCheckpoint(
            novel_id=test_novel.id,
            stage="stage0",
            stage_status="processing",
        )
        db_session.add(cp)
        db_session.commit()

        cp.mark_completed({"processed": 10})
        db_session.commit()

        assert cp.stage_status == "completed"
        data = json.loads(cp.checkpoint_data)
        assert data["processed"] == 10

    def test_mark_failed(
        self, db_session: Session, test_novel: Novel
    ):
        """Test marking checkpoint as failed."""
        cp = ProcessCheckpoint(
            novel_id=test_novel.id,
            stage="stage0",
            stage_status="processing",
            retry_count=0,
        )
        db_session.add(cp)
        db_session.commit()

        cp.mark_failed("Connection timeout")
        db_session.commit()

        assert cp.stage_status == "failed"
        assert cp.error_message == "Connection timeout"
        assert cp.retry_count == 1

    def test_can_retry(
        self, db_session: Session, test_novel: Novel
    ):
        """Test retry check logic."""
        cp = ProcessCheckpoint(
            novel_id=test_novel.id,
            stage="stage0",
            stage_status="failed",
            retry_count=2,
        )
        db_session.add(cp)
        db_session.commit()

        assert cp.can_retry(max_retries=3) is True
        assert cp.can_retry(max_retries=2) is False
