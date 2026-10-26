"""
Tests for Agent context assembler.

Tests ContextAssembler for gathering and formatting project context.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlmodel import Session

from agent.context.assembler import ContextAssembler, get_context_assembler
from agent.schemas.context import (
    ContextData,
    ContextItem,
    ContextPriority,
)
from models import File, Project, User


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user."""
    user = User(
        email="context_test@example.com",
        username="contexttest",
        hashed_password="hashed_password",
        name="Context Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_project(db_session: Session, test_user):
    """Create a test project."""
    project = Project(
        name="Context Test Project",
        description="A test project for context assembly",
        user_id=test_user.id,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def create_file(db_session: Session, test_project, test_user):
    """Helper function to create test files."""

    def _create(
        title: str,
        file_type: str,
        content: str = "",
        file_metadata: str | None = None,
        parent_id: str | None = None,
        order: int = 0,
    ):
        file = File(
            title=title,
            content=content,
            file_type=file_type,
            file_metadata=file_metadata,
            project_id=test_project.id,
            user_id=test_user.id,
            parent_id=parent_id,
            order=order,
        )
        db_session.add(file)
        db_session.commit()
        db_session.refresh(file)
        return file

    return _create


@pytest.fixture
def create_folder(db_session: Session, test_project, test_user):
    """Helper function to create folder files."""

    def _create(title: str, order: int = 0):
        folder = File(
            title=title,
            content="",
            file_type="folder",
            project_id=test_project.id,
            user_id=test_user.id,
            parent_id=None,
            order=order,
        )
        db_session.add(folder)
        db_session.commit()
        db_session.refresh(folder)
        return folder

    return _create


@pytest.mark.unit
class TestContextAssembler:
    """Test ContextAssembler class."""

    def test_init(self):
        """Test assembler initialization."""
        assembler = ContextAssembler()
        assert assembler.prioritizer is not None

    def test_singleton(self):
        """Test singleton pattern."""
        assembler1 = get_context_assembler()
        assembler2 = get_context_assembler()
        assert assembler1 is assembler2

    def test_assemble_empty_project(
        self,
        db_session,
        test_project,
    ):
        """Test assembling context for an empty project."""
        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
        )

        assert isinstance(result, ContextData)
        assert result.context is not None
        assert isinstance(result.context, str)
        assert result.original_item_count == 0
        assert result.trimmed_item_count == 0
        assert result.refs == []
        assert "项目状态" in result.context
        assert "项目文件清单" in result.context

    def test_assemble_with_characters(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test assembling context with character files."""
        # Create character files
        create_file(
            title="张三",
            file_type="character",
            content="主角，勇敢善良",
            file_metadata='{"role": "主角", "age": "25", "gender": "男"}',
        )
        create_file(
            title="李四",
            file_type="character",
            content="配角，聪明机智",
            file_metadata='{"role": "配角", "age": "30", "gender": "女"}',
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
            include_characters=True,
        )

        assert isinstance(result, ContextData)
        assert result.original_item_count == 2
        assert len(result.items) == 2
        assert "张三" in result.context
        assert "李四" in result.context
        assert "【角色信息】" in result.context

    def test_assemble_without_characters(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test assembling context without characters."""
        # Create character file
        create_file(
            file_type="character",
            title="张三",
            content="主角",
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
            include_characters=False,
        )

        # Character should not be included in detailed content
        # but will still appear in file inventory
        assert result.original_item_count == 0
        # Character should not be in the detailed sections
        assert "【角色信息】" not in result.context

    def test_assemble_with_lores(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test assembling context with lore files."""
        # Create lore files with different importance
        create_file(
            file_type="lore",
            title="魔法体系",
            content="魔法分为火、水、风、土四种元素",
            file_metadata='{"category": "魔法", "importance": "high"}',
        )
        create_file(
            file_type="lore",
            title="地理环境",
            content="位于大陆东部的沿海国家",
            file_metadata='{"category": "地理", "importance": "medium"}',
        )
        create_file(
            file_type="lore",
            title="民间传说",
            content="关于古代巨龙的传说",
            file_metadata='{"category": "传说", "importance": "low"}',
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
            include_lores=True,
        )

        assert result.original_item_count == 3
        assert "【世界设定】" in result.context
        # High importance lore should come first
        assert "魔法体系" in result.context
        assert "地理环境" in result.context

    def test_assemble_without_lores(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test assembling context without lores."""
        # Create lore file
        create_file(
            file_type="lore",
            title="魔法体系",
            content="魔法说明",
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
            include_lores=False,
        )

        # Lore should not be included in detailed content
        # but will still appear in file inventory
        assert result.original_item_count == 0
        # Lore should not be in the detailed sections
        assert "【世界设定】" not in result.context

    def test_assemble_with_focus_file(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test assembling context with a focus file."""
        # Create outline files
        outline1 = create_file(
            file_type="outline",
            title="第一章",
            content="第一章大纲内容",
        )
        create_file(
            file_type="outline",
            title="第二章",
            content="第二章大纲内容",
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            focus_file_id=outline1.id,
            max_tokens=1000,
        )

        # Focus file should be included
        assert result.original_item_count >= 1
        assert "第一章" in result.context
        # Focus file should have special marker
        assert "当前焦点" in result.context or any(
            item.get("metadata", {}).get("is_focus")
            for item in result.items
        )

    def test_assemble_with_deleted_focus_file(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that deleted focus file is not included."""
        outline = create_file(
            file_type="outline",
            title="第一章",
            content="第一章大纲",
        )
        # Soft delete the file
        outline.is_deleted = True
        db_session.commit()

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            focus_file_id=outline.id,
            max_tokens=1000,
        )

        # Deleted file should not be included
        assert result.original_item_count == 0

    def test_assemble_with_focus_file_siblings(
        self,
        db_session,
        test_project,
        create_file,
        create_folder,
    ):
        """Test that siblings of focus file are included."""
        # Create folder and files
        folder = create_folder(
            title="第一卷",
        )
        create_file(
            file_type="outline",
            title="第一章",
            parent_id=folder.id,
            order=1,
        )
        outline2 = create_file(
            file_type="outline",
            title="第二章",
            parent_id=folder.id,
            order=2,
        )
        create_file(
            file_type="outline",
            title="第三章",
            parent_id=folder.id,
            order=3,
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            focus_file_id=outline2.id,
            max_tokens=2000,
        )

        # Focus file and siblings should be included
        assert result.original_item_count >= 1
        assert "第二章" in result.context

    def test_assemble_with_attached_files(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test assembling context with manually attached files."""
        # Create draft files
        draft1 = create_file(
            file_type="draft",
            title="第一章草稿",
            content="第一章正文内容",
        )
        draft2 = create_file(
            file_type="draft",
            title="第二章草稿",
            content="第二章正文内容",
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            attached_file_ids=[draft1.id, draft2.id],
            max_tokens=1000,
        )

        # Attached files should be included with CRITICAL priority
        assert result.original_item_count == 2
        assert len(result.items) == 2
        assert any(
            item.get("metadata", {}).get("attached")
            for item in result.items
        )
        # Check that attached files have CRITICAL priority
        for item in result.items:
            if item.get("metadata", {}).get("attached"):
                assert item["priority"] == ContextPriority.CRITICAL.value

    def test_assemble_with_deleted_attached_file(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that deleted attached files are not included."""
        draft = create_file(
            file_type="draft",
            title="草稿",
            content="内容",
        )
        # Soft delete the file
        draft.is_deleted = True
        db_session.commit()

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            attached_file_ids=[draft.id],
            max_tokens=1000,
        )

        # Deleted attached file should not be included
        assert result.original_item_count == 0

    def test_assemble_file_inventory(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that file inventory is included in context."""
        # Create various file types
        outline = create_file(
            file_type="outline",
            title="第一章大纲",
            content="大纲内容",
        )
        draft = create_file(
            file_type="draft",
            title="第一章草稿",
            content="草稿内容" * 100,  # Add some content for word count
        )
        create_file(
            file_type="character",
            title="张三",
            content="角色描述",
        )
        create_file(
            file_type="lore",
            title="魔法体系",
            content="魔法说明",
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=2000,
        )

        # Check inventory section
        assert "项目文件清单" in result.context
        assert "第一章大纲" in result.context
        assert "第一章草稿" in result.context
        assert "张三" in result.context
        assert "魔法体系" in result.context
        # File IDs should be included
        assert outline.id in result.context
        assert draft.id in result.context

    def test_file_inventory_sorts_order0_by_title_sequence(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """
        Regression: many agent-created files historically used order=0.
        In that case, inventory ordering should fall back to parsing the
        sequence number from the title (e.g. 第3章 before 第10章).
        """
        create_file(
            file_type="draft",
            title="第10章",
            content="内容",
            order=0,
        )
        create_file(
            file_type="draft",
            title="第3章",
            content="内容",
            order=0,
        )
        create_file(
            file_type="outline",
            title="第10章大纲",
            content="大纲",
            order=0,
        )
        create_file(
            file_type="outline",
            title="第3章大纲",
            content="大纲",
            order=0,
        )

        assembler = ContextAssembler()
        inventory = assembler._get_file_inventory(db_session, test_project.id)

        assert [f["title"] for f in inventory["draft"]][:2] == ["第3章", "第10章"]
        assert [f["title"] for f in inventory["outline"]][:2] == ["第3章大纲", "第10章大纲"]

    def test_file_inventory_uses_metadata_before_title_but_after_explicit_order(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Inventory uses the same order > metadata > leading-title precedence."""
        create_file(
            file_type="draft",
            title="第1章 元数据十二",
            content="内容",
            file_metadata='{"chapter_number": 12}',
            order=0,
        )
        create_file(
            file_type="draft",
            title="第2章 标题二",
            content="内容",
            order=0,
        )
        create_file(
            file_type="draft",
            title="第99章 显式三",
            content="内容",
            file_metadata='{"chapter_number": 50}',
            order=3,
        )

        inventory = ContextAssembler()._get_file_inventory(db_session, test_project.id)

        assert [item["title"] for item in inventory["draft"]][:3] == [
            "第2章 标题二",
            "第99章 显式三",
            "第1章 元数据十二",
        ]

    def test_file_inventory_preserves_explicit_trailing_zero_order(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Persisted nonzero structured order wins over title parsing, including 580."""
        create_file(
            file_type="draft",
            title="第57章",
            content="内容",
            order=57,
        )
        create_file(
            file_type="draft",
            title="第58章",
            content="内容",
            order=580,
        )
        create_file(
            file_type="draft",
            title="第59章",
            content="内容",
            order=59,
        )

        assembler = ContextAssembler()
        inventory = assembler._get_file_inventory(db_session, test_project.id)

        assert [f["title"] for f in inventory["draft"]][:3] == ["第57章", "第59章", "第58章"]

    def test_file_inventory_prefers_explicit_order_for_chapter_like_writing_files(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Chapter-like writing files also follow conflicting nonzero structured order."""
        create_file(
            file_type="draft",
            title="第57章",
            content="内容",
            order=57,
        )
        create_file(
            file_type="draft",
            title="第58章",
            content="内容",
            order=1,
        )
        create_file(
            file_type="draft",
            title="第59章",
            content="内容",
            order=59,
        )

        assembler = ContextAssembler()
        inventory = assembler._get_file_inventory(db_session, test_project.id)

        assert [f["title"] for f in inventory["draft"]][:3] == ["第58章", "第57章", "第59章"]

    def test_assemble_project_status(
        self,
        db_session,
        test_project,
    ):
        """Test that project status is included in context."""
        # Update project with status info
        test_project.summary = "一部关于勇者的冒险故事"
        test_project.current_phase = "大纲阶段"
        test_project.writing_style = "轻松幽默"
        test_project.notes = "注意保持角色性格一致"
        db_session.commit()

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
        )

        # Check project status section
        assert "项目状态" in result.context
        assert "勇者的冒险故事" in result.context
        assert "大纲阶段" in result.context
        assert "轻松幽默" in result.context
        assert "保持角色性格一致" in result.context

    def test_assemble_empty_project_status(
        self,
        db_session,
        test_project,
    ):
        """Test context when project status is empty."""
        # Project has default empty status fields
        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
        )

        # Should show reminder to collect project info
        assert "项目状态 [待收集]" in result.context
        assert "update_project" in result.context

    def test_assemble_token_budget(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that context respects token budget."""
        # Create many files with content
        for i in range(10):
            create_file(
                file_type="lore",
                title=f"设定{i}",
                content=f"设定内容{i} " * 100,  # Large content
                file_metadata='{"importance": "medium"}',
            )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=500,  # Small budget
        )

        # Some items should be trimmed
        assert result.original_item_count > 0
        assert result.token_estimate <= 500 * 2.0  # Allow margin for accurate tokenizer
        # Trimmed count should be less than or equal to original
        assert result.trimmed_item_count <= result.original_item_count

    def test_deduplicate_items(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that duplicate items are removed."""
        # Create a file (use outline type since it supports relation parameter)
        outline = create_file(
            file_type="outline",
            title="第一章",
            content="第一章内容",
        )

        assembler = ContextAssembler()

        # Add the same file multiple times (simulate different sources)
        item1 = assembler._file_to_context_item(outline, relation="attached")
        item2 = assembler._file_to_context_item(outline, relation="sibling")

        # Manually add to items list to test deduplication
        items = [item1, item2]
        deduplicated = assembler._deduplicate_items(items)

        # Should have only one item
        assert len(deduplicated) == 1
        # Should prefer the one with higher priority (attached > sibling)
        assert deduplicated[0].metadata.get("relation") == "attached"

    def test_format_context_sections(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that context is formatted with proper sections."""
        # Create different file types
        outline = create_file(
            file_type="outline",
            title="第一章大纲",
            content="大纲内容",
        )
        create_file(
            file_type="draft",
            title="第一章草稿",
            content="草稿内容",
        )
        create_file(
            file_type="character",
            title="张三",
            content="角色描述",
        )
        create_file(
            file_type="lore",
            title="魔法",
            content="魔法体系",
            file_metadata='{"category": "魔法"}',
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=2000,
            focus_file_id=outline.id,
        )

        context = result.context

        # Check sections exist
        assert "项目状态" in context
        assert "项目文件清单" in context
        # Check detail sections
        assert "【大纲详情】" in context
        # Check specific sections based on content
        if "【大纲详情】" in context or "【正文详情】" in context:
            assert True  # At least one detail section exists
        if "【角色信息】" in context:
            assert "张三" in context
        if "【世界设定】" in context:
            assert "魔法" in context

    def test_file_to_context_item_character(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test converting File to ContextItem for character."""
        char_file = create_file(
            file_type="character",
            title="张三",
            content="勇敢的主角",
            file_metadata='{"role": "主角", "age": "25", "gender": "男", "personality": "勇敢"}',
        )

        assembler = ContextAssembler()
        item = assembler._file_to_context_item(char_file)

        assert item.type == "character"
        assert item.title == "张三"
        assert "勇敢的主角" in item.content
        assert "角色: 主角" in item.content
        assert "年龄: 25" in item.content

    def test_file_to_context_item_lore(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test converting File to ContextItem for lore."""
        lore_file = create_file(
            file_type="lore",
            title="魔法体系",
            content="魔法分为四种元素",
            file_metadata='{"category": "魔法", "importance": "high"}',
        )

        assembler = ContextAssembler()
        item = assembler._file_to_context_item(lore_file)

        assert item.type == "lore"
        assert "魔法" in item.title  # Category prefix
        assert "魔法分为四种元素" in item.content
        assert item.metadata.get("category") == "魔法"
        assert item.metadata.get("importance") == "high"

    def test_file_to_context_item_outline(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test converting File to ContextItem for outline."""
        outline_file = create_file(
            file_type="outline",
            title="第一章大纲",
            content="第一章内容",
        )

        assembler = ContextAssembler()
        item = assembler._file_to_context_item(outline_file, is_focus=True)

        assert item.type == "outline"
        assert item.title == "第一章大纲"
        assert item.is_focus is True
        assert item.metadata.get("is_focus") is True

    def test_file_to_context_item_draft(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test converting File to ContextItem for draft."""
        draft_file = create_file(
            file_type="draft",
            title="第一章草稿",
            content="第一章正文",
        )

        assembler = ContextAssembler()
        item = assembler._file_to_context_item(draft_file)

        assert item.type == "outline"  # Drafts are stored as outline type with file_type metadata
        assert item.metadata.get("file_type") == "draft"

    def test_lore_sorting_by_importance(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that lores are sorted by importance."""
        # Create lores with different importance (out of order)
        create_file(
            file_type="lore",
            title="低重要性",
            content="低",
            file_metadata='{"importance": "low"}',
            order=1,
        )
        create_file(
            file_type="lore",
            title="高重要性",
            content="高",
            file_metadata='{"importance": "high"}',
            order=2,
        )
        create_file(
            file_type="lore",
            title="中重要性",
            content="中",
            file_metadata='{"importance": "medium"}',
            order=3,
        )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=2000,
        )

        # High importance should come first in the items list
        lore_items = [item for item in result.items if item["type"] == "lore"]
        if len(lore_items) >= 2:
            # First item should be high importance
            assert lore_items[0]["metadata"]["importance"] == "high"

    def test_assemble_soft_deleted_files_excluded(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that soft-deleted files are excluded from context."""
        # Create files
        create_file(
            file_type="character",
            title="活跃角色",
            content="这个角色是活跃的",
        )
        char2 = create_file(
            file_type="character",
            title="已删除角色",
            content="这个角色已删除",
        )

        # Soft delete one file
        char2.is_deleted = True
        db_session.commit()

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
            include_characters=True,
        )

        # Only active file should be included
        assert result.original_item_count == 1
        assert "活跃角色" in result.context
        assert "已删除角色" not in result.context

    def test_budget_used_tracking(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Test that budget usage is tracked correctly."""
        # Create some files
        for i in range(3):
            create_file(
                file_type="lore",
                title=f"设定{i}",
                content=f"内容{i}",
                file_metadata='{"importance": "medium"}',
            )

        assembler = ContextAssembler()
        result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            max_tokens=1000,
        )

        # Budget usage should be tracked
        assert isinstance(result.budget_used, dict)
        # Should have entries for priorities
        assert any(key in result.budget_used for key in ["critical", "constraint", "relevant", "inspiration"])
        # Token estimate should be reasonable
        assert result.token_estimate > 0

    def test_get_files_by_types_stable_order_on_same_updated_at(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """When updated_at ties, file ordering should be deterministic by id."""
        first = create_file(
            file_type="character",
            title="角色A",
            content="描述A",
        )
        second = create_file(
            file_type="character",
            title="角色B",
            content="描述B",
        )
        fixed_time = datetime(2026, 1, 1, tzinfo=UTC)
        first.updated_at = fixed_time
        second.updated_at = fixed_time
        db_session.commit()

        assembler = ContextAssembler()
        items = assembler._get_files_by_types(  # noqa: SLF001
            session=db_session,
            project_id=test_project.id,
            file_types=["character"],
            limit_per_type=10,
        )

        ids = [item.id for item in items]
        assert ids == sorted(ids)

    def test_get_related_files_stable_order_on_same_updated_at(
        self,
        db_session,
        test_project,
        create_file,
        create_folder,
    ):
        """Sibling ordering should be deterministic by id when recency ties."""
        parent = create_folder(title="第一卷")
        focus = create_file(
            file_type="draft",
            title="当前章节",
            content="focus",
            parent_id=parent.id,
            order=10,
        )
        sibling_a = create_file(
            file_type="draft",
            title="相关章节A",
            content="a",
            parent_id=parent.id,
            order=10,
        )
        sibling_b = create_file(
            file_type="draft",
            title="相关章节B",
            content="b",
            parent_id=parent.id,
            order=10,
        )
        fixed_time = datetime(2026, 1, 2, tzinfo=UTC)
        sibling_a.updated_at = fixed_time
        sibling_b.updated_at = fixed_time
        db_session.commit()

        assembler = ContextAssembler()
        related = assembler._get_related_files(  # noqa: SLF001
            session=db_session,
            project_id=test_project.id,
            focus=focus,
        )
        sibling_ids = [item.id for item in related if item.metadata.get("relation") == "sibling"]

        assert sibling_ids == sorted([sibling_a.id, sibling_b.id])

    def test_empty_query_keeps_original_ranking(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Empty query should keep ranking unchanged."""
        first = create_file(
            file_type="draft",
            title="第一份素材",
            content="普通内容",
        )
        second = create_file(
            file_type="draft",
            title="第二份素材",
            content="普通内容",
        )

        assembler = ContextAssembler()
        none_query_result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            query=None,
            attached_file_ids=[first.id, second.id],
            max_tokens=2000,
            include_characters=False,
            include_lores=False,
        )
        blank_query_result = assembler.assemble(
            session=db_session,
            project_id=test_project.id,
            query="   ",
            attached_file_ids=[first.id, second.id],
            max_tokens=2000,
            include_characters=False,
            include_lores=False,
        )

        none_query_ids = [item["id"] for item in none_query_result.items]
        blank_query_ids = [item["id"] for item in blank_query_result.items]

        assert none_query_ids == blank_query_ids

    def test_assemble_includes_hybrid_retrieved_snippets(
        self,
        db_session,
        test_project,
    ):
        """Query context should include hybrid-retrieved snippet payload metadata."""
        mock_results = [
            SimpleNamespace(
                entity_type="draft",
                entity_id="file-snippet-1",
                title="关键片段",
                snippet="这是检索命中的关键片段",
                content="这是检索命中的关键片段（全文）",
                score=0.83,
                fused_score=0.91,
                line_start=12,
                sources=["semantic", "lexical"],
            )
        ]

        assembler = ContextAssembler()
        with patch("services.infra.file_search_service.FileSearchService") as mock_factory:
            mock_factory.return_value.search.return_value = mock_results
            result = assembler.assemble(
                session=db_session,
                project_id=test_project.id,
                query="关键片段",
                max_tokens=2000,
                include_characters=False,
                include_lores=False,
            )

        assert result.original_item_count >= 1
        assert "【参考素材】" in result.context
        assert "line_start=12" in result.context
        assert "fused_score=" in result.context
        assert "sources=semantic+lexical" in result.context
        assert any(item["metadata"].get("retrieved") for item in result.items)

    def test_assemble_hybrid_retrieval_failure_does_not_break_context(
        self,
        db_session,
        test_project,
        create_file,
    ):
        """Hybrid retrieval failure should fail-open and keep normal assembly."""
        create_file(
            file_type="character",
            title="张三",
            content="主角",
        )

        assembler = ContextAssembler()
        with patch("services.infra.file_search_service.FileSearchService", side_effect=RuntimeError("svc down")):
            result = assembler.assemble(
                session=db_session,
                project_id=test_project.id,
                query="主角",
                max_tokens=1500,
                include_characters=True,
                include_lores=False,
            )

        assert "【角色信息】" in result.context
        assert result.original_item_count >= 1


@pytest.mark.unit
class TestContextPrioritizer:
    """Test ContextPrioritizer class (used by ContextAssembler)."""

    def test_classify_priority_focus_item(self):
        """Test that focus items get CRITICAL priority."""
        from agent.context.prioritizer import ContextPrioritizer

        prioritizer = ContextPrioritizer()
        item = ContextItem(
            id="1",
            type="outline",
            title="Test",
            content="Content",
            metadata={"is_focus": True},
        )

        priority = prioritizer.classify_priority(item)
        assert priority == ContextPriority.CRITICAL

    def test_classify_priority_character(self):
        """Test that characters get CONSTRAINT priority."""
        from agent.context.prioritizer import ContextPrioritizer

        prioritizer = ContextPrioritizer()
        item = ContextItem.from_character(
            id="1",
            name="张三",
            profile="角色描述",
        )

        priority = prioritizer.classify_priority(item)
        assert priority == ContextPriority.CONSTRAINT

    def test_classify_priority_lore_high_importance(self):
        """Test that high importance lore gets CONSTRAINT priority."""
        from agent.context.prioritizer import ContextPrioritizer

        prioritizer = ContextPrioritizer()
        item = ContextItem.from_lore(
            id="1",
            title="魔法体系",
            content="魔法说明",
            importance="high",
        )

        priority = prioritizer.classify_priority(item)
        assert priority == ContextPriority.CONSTRAINT

    def test_classify_priority_lore_medium_importance(self):
        """Test that medium importance lore gets RELEVANT priority."""
        from agent.context.prioritizer import ContextPrioritizer

        prioritizer = ContextPrioritizer()
        item = ContextItem.from_lore(
            id="1",
            title="地理环境",
            content="地理说明",
            importance="medium",
        )

        priority = prioritizer.classify_priority(item)
        assert priority == ContextPriority.RELEVANT

    def test_classify_priority_lore_low_importance(self):
        """Test that low importance lore gets INSPIRATION priority."""
        from agent.context.prioritizer import ContextPrioritizer

        prioritizer = ContextPrioritizer()
        item = ContextItem.from_lore(
            id="1",
            title="民间传说",
            content="传说内容",
            importance="low",
        )

        priority = prioritizer.classify_priority(item)
        assert priority == ContextPriority.INSPIRATION

    def test_prioritize_sorting(self):
        """Test that items are sorted by priority."""
        from agent.context.prioritizer import ContextPrioritizer

        prioritizer = ContextPrioritizer()

        items = [
            ContextItem.from_lore(
                id="1",
                title="低重要性",
                content="内容",
                importance="low",
            ),
            ContextItem.from_character(
                id="2",
                name="角色",
                profile="描述",
            ),
            ContextItem.from_outline(
                id="3",
                title="大纲",
                content="内容",
                is_focus=True,
            ),
        ]

        sorted_items = prioritizer.prioritize(items)

        # Focus item (CRITICAL) should be first
        assert sorted_items[0].is_focus is True
        # Character (CONSTRAINT) should come before low importance lore (INSPIRATION)
        assert sorted_items[1].type == "character"
        assert sorted_items[2].type == "lore"

    def test_group_by_priority(self):
        """Test grouping items by priority."""
        from agent.context.prioritizer import ContextPrioritizer

        prioritizer = ContextPrioritizer()

        items = [
            ContextItem.from_outline(
                id="1",
                title="焦点",
                content="内容",
                is_focus=True,
            ),
            ContextItem.from_character(
                id="2",
                name="角色",
                profile="描述",
            ),
            ContextItem.from_lore(
                id="3",
                title="设定",
                content="内容",
                importance="low",
            ),
        ]

        groups = prioritizer.group_by_priority(items)

        # Should have all priority levels
        assert ContextPriority.CRITICAL in groups
        assert ContextPriority.CONSTRAINT in groups
        assert ContextPriority.INSPIRATION in groups

        # Check group sizes
        assert len(groups[ContextPriority.CRITICAL]) == 1
        assert len(groups[ContextPriority.CONSTRAINT]) == 1
        assert len(groups[ContextPriority.INSPIRATION]) == 1


@pytest.mark.unit
def test_project_notes_render_author_sections():
    from config.project_notes import CONFIRMED_HEADER

    notes = "反派身份第20章前不得揭露\n\n" f"{CONFIRMED_HEADER}\n主角不会游泳"
    context = ContextAssembler()._format_context([], None, {"notes": notes})

    confirmed_at = context.index("【备注·作者确认】")
    unlabeled_at = context.index("【备注·未标来源】")
    assert confirmed_at < context.index("主角不会游泳") < unlabeled_at
    assert unlabeled_at < context.index("反派身份第20章前不得揭露")


_LEGACY_NOTES = (
    "主角冷静\n\n"
    "【作者确认】\n女主要到第五章才出场\n\n"
    "【AI 提议·待作者确认】\n沧不夺舍；反派第20章揭露"
)


@pytest.mark.unit
def test_legacy_proposal_block_is_not_in_the_model_context(db_session, test_project):
    test_project.notes = _LEGACY_NOTES
    db_session.commit()
    context = ContextAssembler().assemble(
        session=db_session, project_id=test_project.id, max_tokens=4000
    ).context
    assert "女主要到第五章才出场" in context and "主角冷静" in context
    assert "沧不夺舍" not in context and "AI 提议" not in context

    # Notes that are only a legacy block count as no notes at all.
    test_project.notes = "【AI 提议·待作者确认】\n沧不夺舍"
    db_session.commit()
    context = ContextAssembler().assemble(
        session=db_session, project_id=test_project.id, max_tokens=4000
    ).context
    assert "沧不夺舍" not in context
    assert "项目状态 [待收集]" in context


@pytest.mark.unit
def test_project_notes_copied_back_from_context_change_nothing():
    """模型把上下文里的备注块（连同标题行）照抄回 notes：标题不变成条目，不追加任何条目。"""
    from agent.context.assembler import _render_project_notes
    from config.project_notes import merge_project_notes

    rendered = "\n".join(_render_project_notes(_LEGACY_NOTES))
    merged = merge_project_notes(_LEGACY_NOTES, rendered, from_chat=True)
    assert merged.added == []
    assert merged.text == "主角冷静\n\n【作者确认】\n女主要到第五章才出场"


@pytest.mark.parametrize('status', [{}, {'summary': '故事简介', 'notes': ''}])
def test_empty_notes_are_explicit_current_context(status):
    context = ContextAssembler()._format_context([], None, status)
    assert '【当前写作约定】' in context
    assert '（空）' in context
    assert '历史对话' in context
