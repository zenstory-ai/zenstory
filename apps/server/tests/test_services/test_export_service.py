"""
Tests for ExportService.

Unit tests for the export service, covering:
- Chinese number parsing
- Chapter number extraction
- Draft sorting
- TXT export functionality
- Edge cases and error handling
"""

import pytest
from sqlmodel import Session

from models import File, Project, User
from services.features.export_service import (
    _extract_chapter_number,
    _parse_chinese_number,
    export_drafts_to_txt,
    get_sorted_drafts,
)


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user."""
    user = User(
        email="export_test@example.com",
        username="exporttest",
        hashed_password="hashed_password",
        name="Export Test User",
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
        name="Export Test Project",
        description="A test project for export",
        user_id=test_user.id,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def create_draft_file(db_session: Session, test_project, test_user):
    """Helper function to create draft files."""

    def _create(title: str, content: str, order: int = 0):
        draft = File(
            title=title,
            content=content,
            file_type="draft",
            project_id=test_project.id,
            user_id=test_user.id,
            order=order,
        )
        db_session.add(draft)
        db_session.commit()
        db_session.refresh(draft)
        return draft

    return _create


@pytest.mark.unit
class TestParseChineseNumber:
    """Tests for _parse_chinese_number helper function."""

    def test_parse_simple_chinese_numbers(self):
        """Test parsing simple Chinese numbers (1-10)."""
        assert _parse_chinese_number("一") == 1
        assert _parse_chinese_number("二") == 2
        assert _parse_chinese_number("三") == 3
        assert _parse_chinese_number("四") == 4
        assert _parse_chinese_number("五") == 5
        assert _parse_chinese_number("六") == 6
        assert _parse_chinese_number("七") == 7
        assert _parse_chinese_number("八") == 8
        assert _parse_chinese_number("九") == 9
        assert _parse_chinese_number("十") == 10

    def test_parse_complex_chinese_numbers(self):
        """Test parsing complex Chinese numbers."""
        assert _parse_chinese_number("十一") == 11
        assert _parse_chinese_number("十二") == 12
        assert _parse_chinese_number("二十") == 20
        assert _parse_chinese_number("二十一") == 21
        assert _parse_chinese_number("三十") == 30
        assert _parse_chinese_number("一百") == 100
        assert _parse_chinese_number("一百零一") == 101
        assert _parse_chinese_number("一百二十三") == 123

    def test_parse_empty_string(self):
        """Test parsing empty string returns 0."""
        assert _parse_chinese_number("") == 0
        assert _parse_chinese_number(None) == 0


@pytest.mark.unit
class TestExtractChapterNumber:
    """Tests for _extract_chapter_number helper function."""

    def test_extract_chinese_chapter_number(self):
        """Test extracting chapter number from Chinese chapter titles."""
        assert _extract_chapter_number("第一章 开始") == 1
        assert _extract_chapter_number("第二章 冲突") == 2
        assert _extract_chapter_number("第三章 高潮") == 3
        assert _extract_chapter_number("第十章 结局") == 10
        assert _extract_chapter_number("第十一章") == 11
        assert _extract_chapter_number("第二十章") == 20

    def test_extract_arabic_chapter_number(self):
        """Test extracting chapter number from Arabic chapter titles."""
        assert _extract_chapter_number("第1章 开始") == 1
        assert _extract_chapter_number("第2章 冲突") == 2
        assert _extract_chapter_number("第3章 高潮") == 3
        assert _extract_chapter_number("第10章 结局") == 10
        assert _extract_chapter_number("第11章") == 11
        assert _extract_chapter_number("第20章") == 20

    def test_extract_plain_number(self):
        """Test extracting plain number from start of title."""
        assert _extract_chapter_number("1. 开始") == 1
        assert _extract_chapter_number("2. 冲突") == 2
        assert _extract_chapter_number("3 高潮") == 3
        assert _extract_chapter_number("10 结局") == 10

    def test_extract_no_number_returns_default(self):
        """Test that titles without numbers return large default (999999)."""
        assert _extract_chapter_number("序章") == 999999
        assert _extract_chapter_number("序言") == 999999
        assert _extract_chapter_number("尾声") == 999999
        assert _extract_chapter_number("楔子") == 999999
        assert _extract_chapter_number("无名章节") == 999999

    def test_extract_empty_title(self):
        """Test extracting from empty title returns default."""
        assert _extract_chapter_number("") == 999999
        assert _extract_chapter_number(None) == 999999


@pytest.mark.unit
class TestGetSortedDrafts:
    """Tests for get_sorted_drafts function."""

    def test_sort_by_order_field(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test that drafts are sorted by order field first."""
        # Create drafts with different order values
        create_draft_file("第二章", "Content 2", order=2)
        create_draft_file("第一章", "Content 1", order=1)
        create_draft_file("第三章", "Content 3", order=3)

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert len(drafts) == 3
        assert drafts[0].title == "第一章"
        assert drafts[1].title == "第二章"
        assert drafts[2].title == "第三章"

    def test_sort_by_chapter_number_when_order_equal(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test that drafts with same order are sorted by chapter number."""
        # Create drafts with same order but different chapter numbers
        create_draft_file("第三章", "Content 3", order=0)
        create_draft_file("第一章", "Content 1", order=0)
        create_draft_file("第二章", "Content 2", order=0)

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert len(drafts) == 3
        assert drafts[0].title == "第一章"
        assert drafts[1].title == "第二章"
        assert drafts[2].title == "第三章"

    def test_sort_by_created_at_when_order_and_chapter_equal(
        self, db_session: Session, test_project, test_user, create_draft_file
    ):
        """Test that drafts with same order and chapter are sorted by creation date."""
        # Create drafts with same order and no chapter numbers
        draft1 = create_draft_file("序章 A", "Content A", order=0)
        draft2 = create_draft_file("序章 B", "Content B", order=0)
        draft3 = create_draft_file("序章 C", "Content C", order=0)

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert len(drafts) == 3
        # Should be sorted by created_at
        assert drafts[0].id == draft1.id
        assert drafts[1].id == draft2.id
        assert drafts[2].id == draft3.id

    def test_sort_chinese_chapter_numbers(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test sorting with Chinese chapter numbers."""
        create_draft_file("第三章", "Content 3", order=0)
        create_draft_file("第一章", "Content 1", order=0)
        create_draft_file("第二章", "Content 2", order=0)
        create_draft_file("第十章", "Content 10", order=0)
        create_draft_file("第二十章", "Content 20", order=0)

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert len(drafts) == 5
        assert drafts[0].title == "第一章"
        assert drafts[1].title == "第二章"
        assert drafts[2].title == "第三章"
        assert drafts[3].title == "第十章"
        assert drafts[4].title == "第二十章"

    def test_sort_arabic_chapter_numbers(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test sorting with Arabic chapter numbers."""
        create_draft_file("第3章", "Content 3", order=0)
        create_draft_file("第1章", "Content 1", order=0)
        create_draft_file("第2章", "Content 2", order=0)
        create_draft_file("第10章", "Content 10", order=0)
        create_draft_file("第20章", "Content 20", order=0)

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert len(drafts) == 5
        assert drafts[0].title == "第1章"
        assert drafts[1].title == "第2章"
        assert drafts[2].title == "第3章"
        assert drafts[3].title == "第10章"
        assert drafts[4].title == "第20章"

    def test_normalizes_trailing_zero_order_typos(self, db_session: Session, test_project, test_user, create_draft_file):
        """Bad stored orders like 580 for 第58章 should still export in chapter order."""
        create_draft_file("第57章", "Content 57", order=57)
        create_draft_file("第58章", "Content 58", order=580)
        create_draft_file("第59章", "Content 59", order=59)

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert [draft.title for draft in drafts] == ["第57章", "第58章", "第59章"]

    def test_prefers_title_sequence_for_chapter_like_drafts(self, db_session: Session, test_project, test_user, create_draft_file):
        """Chapter-like drafts should follow title order even when stored order conflicts."""
        create_draft_file("第57章", "Content 57", order=57)
        create_draft_file("第58章", "Content 58", order=1)
        create_draft_file("第59章", "Content 59", order=59)

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert [draft.title for draft in drafts] == ["第57章", "第58章", "第59章"]

    def test_only_includes_draft_type(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test that only draft type files are included."""
        # Create different file types
        create_draft_file("第一章", "Draft content", order=1)

        outline = File(
            title="大纲",
            content="Outline content",
            file_type="outline",
            project_id=test_project.id,
            user_id=test_user.id,
            order=0,
        )
        db_session.add(outline)
        db_session.commit()

        character = File(
            title="主角",
            content="Character content",
            file_type="character",
            project_id=test_project.id,
            user_id=test_user.id,
            order=2,
        )
        db_session.add(character)
        db_session.commit()

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert len(drafts) == 1
        assert drafts[0].file_type == "draft"
        assert drafts[0].title == "第一章"

    def test_excludes_deleted_files(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test that deleted files are excluded."""
        # Create drafts
        draft1 = create_draft_file("第一章", "Content 1", order=1)
        draft2 = create_draft_file("第二章", "Content 2", order=2)

        # Soft delete one draft
        draft2.is_deleted = True
        db_session.add(draft2)
        db_session.commit()

        drafts = get_sorted_drafts(db_session, test_project.id)

        assert len(drafts) == 1
        assert drafts[0].id == draft1.id

    def test_empty_project_returns_empty_list(self, db_session: Session, test_project):
        """Test that project with no drafts returns empty list."""
        drafts = get_sorted_drafts(db_session, test_project.id)
        assert drafts == []

    def test_only_returns_drafts_from_specified_project(
        self, db_session: Session, test_project, test_user, create_draft_file
    ):
        """Test that only drafts from the specified project are returned."""
        # Create another project
        other_project = Project(
            name="Other Project",
            description="Another project",
            user_id=test_user.id,
        )
        db_session.add(other_project)
        db_session.commit()
        db_session.refresh(other_project)

        # Create draft in other project
        other_draft = File(
            title="其他章节",
            content="Other content",
            file_type="draft",
            project_id=other_project.id,
            user_id=test_user.id,
            order=0,
        )
        db_session.add(other_draft)
        db_session.commit()

        # Create draft in test project
        create_draft_file("第一章", "Content 1", order=1)

        # Query for test project should only return its own drafts
        drafts = get_sorted_drafts(db_session, test_project.id)
        assert len(drafts) == 1
        assert drafts[0].title == "第一章"


@pytest.mark.unit
class TestExportDraftsToTxt:
    """Tests for export_drafts_to_txt function."""

    def test_export_single_chapter(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test exporting a single chapter."""
        create_draft_file("第一章", "这是第一章的内容。", order=1)

        exported = export_drafts_to_txt(db_session, test_project.id)

        assert exported == "第一章\n\n这是第一章的内容。"

    def test_export_multiple_chapters(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test exporting multiple chapters with separator."""
        create_draft_file("第一章", "第一章的内容。", order=1)
        create_draft_file("第二章", "第二章的内容。", order=2)
        create_draft_file("第三章", "第三章的内容。", order=3)

        exported = export_drafts_to_txt(db_session, test_project.id)

        expected = (
            "第一章\n\n第一章的内容。\n\n"
            "---\n\n"
            "第二章\n\n第二章的内容。\n\n"
            "---\n\n"
            "第三章\n\n第三章的内容。"
        )
        assert exported == expected

    def test_export_chapters_sorted_correctly(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test that chapters are exported in sorted order."""
        # Create chapters out of order
        create_draft_file("第三章", "内容 3", order=3)
        create_draft_file("第一章", "内容 1", order=1)
        create_draft_file("第二章", "内容 2", order=2)

        exported = export_drafts_to_txt(db_session, test_project.id)

        # Verify order
        lines = exported.split("\n\n")
        assert lines[0] == "第一章"
        assert lines[2] == "---"
        assert lines[3] == "第二章"
        assert lines[5] == "---"
        assert lines[6] == "第三章"

    def test_export_strips_whitespace_from_content(self, db_session: Session, test_project, test_user, create_draft_file):
        """Leading blank lines and trailing whitespace go; first-line indent stays."""
        create_draft_file("第一章", "\n  这是内容。  \n\n", order=1)

        exported = export_drafts_to_txt(db_session, test_project.id)

        assert exported == "第一章\n\n  这是内容。"

    def test_export_handles_empty_content(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test exporting chapter with empty content."""
        create_draft_file("第一章", "", order=1)

        exported = export_drafts_to_txt(db_session, test_project.id)

        assert exported == "第一章\n\n"

    def test_export_handles_special_characters(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test exporting content with special characters."""
        create_draft_file("第一章", "包含引号\"和单引号'的内容。\n还有换行符。", order=1)

        exported = export_drafts_to_txt(db_session, test_project.id)

        assert '包含引号"' in exported
        assert "单引号'" in exported
        assert "换行符" in exported

    def test_export_returns_empty_string_for_no_drafts(self, db_session: Session, test_project):
        """Test that exporting a project with no drafts returns empty string."""
        exported = export_drafts_to_txt(db_session, test_project.id)

        assert exported == ""

    def test_export_excludes_non_draft_files(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test that only draft files are exported."""
        # Create draft
        create_draft_file("第一章", "Draft content", order=1)

        # Create outline (should not be exported)
        outline = File(
            title="大纲",
            content="Outline content",
            file_type="outline",
            project_id=test_project.id,
            user_id=test_user.id,
            order=0,
        )
        db_session.add(outline)
        db_session.commit()

        exported = export_drafts_to_txt(db_session, test_project.id)

        assert "Draft content" in exported
        assert "Outline content" not in exported
        assert "大纲" not in exported

    def test_export_excludes_deleted_drafts(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test that deleted drafts are not exported."""
        create_draft_file("第一章", "Content 1", order=1)

        draft2 = create_draft_file("第二章", "Content 2", order=2)
        draft2.is_deleted = True
        db_session.add(draft2)
        db_session.commit()

        exported = export_drafts_to_txt(db_session, test_project.id)

        assert "Content 1" in exported
        assert "Content 2" not in exported
        assert "第二章" not in exported

    def test_export_handles_chinese_chapter_numbers(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test exporting with Chinese chapter numbers."""
        create_draft_file("第十章", "第十章内容", order=1)
        create_draft_file("第一章", "第一章内容", order=0)
        create_draft_file("第二十章", "第二十章内容", order=2)

        exported = export_drafts_to_txt(db_session, test_project.id)

        # Should be sorted: 第一章 -> 第十章 -> 第二十章
        lines = exported.split("\n\n---\n\n")
        assert "第一章" in lines[0]
        assert "第十章" in lines[1]
        assert "第二十章" in lines[2]

    def test_export_handles_mixed_chapter_formats(self, db_session: Session, test_project, test_user, create_draft_file):
        """Test exporting with mixed chapter number formats."""
        create_draft_file("第1章", "Content 1", order=1)
        create_draft_file("第二章", "Content 2", order=2)
        create_draft_file("3. 第三章", "Content 3", order=3)

        exported = export_drafts_to_txt(db_session, test_project.id)

        assert "第1章" in exported
        assert "第二章" in exported
        assert "3. 第三章" in exported
