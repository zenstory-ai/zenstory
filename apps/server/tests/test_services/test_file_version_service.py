"""
Tests for FileVersionService.

Unit tests for the file version management service, covering:
- Version creation (base and delta versions)
- Version content retrieval
- Version comparison (diff)
- Version rollback
- Edge cases and error handling
"""

import random

import pytest
from sqlmodel import Session

from models import File, Project, User
from models.file_version import (
    CHANGE_TYPE_AI_EDIT,
    CHANGE_TYPE_AUTO_SAVE,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_RESTORE,
    VERSION_BASE_INTERVAL,
)
from services.features.file_version_service import FileVersionService


@pytest.fixture
def file_version_service():
    """Return FileVersionService instance."""
    return FileVersionService()


@pytest.fixture
def test_file_with_project(db_session: Session):
    """Create a test file with project for version testing."""
    # Create user
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password="hashed_password",
        name="Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Create project
    project = Project(
        name="Test Project",
        description="A test project",
        user_id=user.id,
    )
    db_session.add(project)
    db_session.commit()

    # Create file
    file = File(
        title="Test File",
        content="Initial content",
        file_type="draft",
        project_id=project.id,
        user_id=user.id,
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    return file


@pytest.mark.unit
class TestFileVersionServiceCreateVersion:
    """Tests for create_version method."""

    def test_create_first_version_is_base(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that the first version is always a base version."""
        version = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="First version content",
        )

        assert version.version_number == 1
        assert version.is_base_version is True
        assert version.content == "First version content"
        assert version.word_count == 3  # "First version content"
        assert version.char_count == len("First version content")
        assert version.change_type == CHANGE_TYPE_EDIT

    def test_create_delta_version(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that subsequent versions are delta versions by default."""
        # Create base version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="First content",
        )

        # Create delta version
        version = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="First content\nSecond line added",
        )

        assert version.version_number == 2
        assert version.is_base_version is False
        # Delta version stores JSON diff, not full content
        assert version.content != "First content\nSecond line added"
        assert version.lines_added > 0

    def test_create_base_version_at_interval(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that every Nth version is a base version."""
        # Create versions up to VERSION_BASE_INTERVAL
        for i in range(1, VERSION_BASE_INTERVAL):
            file_version_service.create_version(
                session=db_session,
                file_id=test_file_with_project.id,
                new_content=f"Version {i}",
            )

        # This should be a base version
        version = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content=f"Version {VERSION_BASE_INTERVAL}",
        )

        assert version.version_number == VERSION_BASE_INTERVAL
        assert version.is_base_version is True

    def test_create_version_with_force_base(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that force_base=True creates a base version regardless of conditions."""
        # Create first version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="First",
        )

        # Force second version to be base
        version = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Second",
            force_base=True,
        )

        assert version.version_number == 2
        assert version.is_base_version is True

    def test_create_version_with_change_metadata(self, db_session: Session, file_version_service, test_file_with_project):
        """Test creating version with custom change metadata."""
        version = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="AI edited content",
            change_type=CHANGE_TYPE_AI_EDIT,
            change_source="ai",
            change_summary="AI improved the writing",
        )

        assert version.change_type == CHANGE_TYPE_AI_EDIT
        assert version.change_source == "ai"
        assert version.change_summary == "AI improved the writing"

    def test_create_version_file_not_found(self, db_session: Session, file_version_service):
        """Test creating version for non-existent file raises ValueError."""
        with pytest.raises(ValueError, match="File .* not found"):
            file_version_service.create_version(
                session=db_session,
                file_id="non-existent-file-id",
                new_content="Content",
            )

    def test_create_version_soft_deleted_file(self, db_session: Session, file_version_service, test_file_with_project):
        """Test creating version for soft-deleted file raises ValueError."""
        # Soft delete the file
        test_file_with_project.is_deleted = True
        db_session.add(test_file_with_project)
        db_session.commit()

        with pytest.raises(ValueError, match="File .* not found"):
            file_version_service.create_version(
                session=db_session,
                file_id=test_file_with_project.id,
                new_content="Content",
            )


@pytest.mark.unit
class TestFileVersionServiceGetVersions:
    """Tests for get_versions method."""

    def test_get_versions_empty(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting versions when file has no versions."""
        versions = file_version_service.get_versions(
            session=db_session,
            file_id=test_file_with_project.id,
        )

        assert versions == []

    def test_get_versions_returns_newest_first(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that versions are returned in descending order (newest first)."""
        # Create multiple versions
        for i in range(5):
            file_version_service.create_version(
                session=db_session,
                file_id=test_file_with_project.id,
                new_content=f"Version {i}",
            )

        versions = file_version_service.get_versions(
            session=db_session,
            file_id=test_file_with_project.id,
        )

        assert len(versions) == 5
        assert versions[0].version_number == 5
        assert versions[1].version_number == 4
        assert versions[4].version_number == 1

    def test_get_versions_with_limit_and_offset(self, db_session: Session, file_version_service, test_file_with_project):
        """Test pagination with limit and offset."""
        # Create 10 versions
        for i in range(10):
            file_version_service.create_version(
                session=db_session,
                file_id=test_file_with_project.id,
                new_content=f"Version {i}",
            )

        # Get first 5
        page1 = file_version_service.get_versions(
            session=db_session,
            file_id=test_file_with_project.id,
            limit=5,
            offset=0,
        )
        assert len(page1) == 5
        assert page1[0].version_number == 10

        # Get next 5
        page2 = file_version_service.get_versions(
            session=db_session,
            file_id=test_file_with_project.id,
            limit=5,
            offset=5,
        )
        assert len(page2) == 5
        assert page2[0].version_number == 5

    def test_get_versions_excludes_auto_save_by_default(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that auto-save versions are excluded by default."""
        # Create normal version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Normal version",
            change_type=CHANGE_TYPE_EDIT,
        )

        # Create auto-save version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Auto-save version",
            change_type=CHANGE_TYPE_AUTO_SAVE,
        )

        versions = file_version_service.get_versions(
            session=db_session,
            file_id=test_file_with_project.id,
            include_auto_save=False,
        )

        # Should only get the normal version
        assert len(versions) == 1
        assert versions[0].change_type != CHANGE_TYPE_AUTO_SAVE

    def test_get_versions_includes_auto_save_when_requested(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that auto-save versions are included when requested."""
        # Create normal version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Normal version",
        )

        # Create auto-save version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Auto-save version",
            change_type=CHANGE_TYPE_AUTO_SAVE,
        )

        versions = file_version_service.get_versions(
            session=db_session,
            file_id=test_file_with_project.id,
            include_auto_save=True,
        )

        # Should get both versions
        assert len(versions) == 2


@pytest.mark.unit
class TestFileVersionServiceGetContentAtVersion:
    """Tests for get_content_at_version method."""

    def test_get_content_base_version(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting content from a base version."""
        version = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Base version content",
        )

        content = file_version_service.get_content_at_version(
            session=db_session,
            file_id=test_file_with_project.id,
            version_number=version.version_number,
        )

        assert content == "Base version content"

    def test_get_content_delta_version(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting content from a delta version (applies diffs)."""
        # Create base version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Line 1\nLine 2\nLine 3",
        )

        # Create delta version (modifies content)
        v2 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Line 1\nLine 2 modified\nLine 3",
        )
        db_session.flush()
        db_session.refresh(v2)

        # Check what's stored
        assert v2.is_base_version is False, "v2 should be a delta version"
        assert v2.content.startswith("["), "v2 content should be JSON diff array"
        # count_words counts latin tokens and ignores numeric-only tokens.
        assert v2.word_count == 4
        assert v2.lines_added >= 0
        assert v2.lines_removed >= 0

        # get_content_at_version should reconstruct the content from base + diffs
        content = file_version_service.get_content_at_version(
            session=db_session,
            file_id=test_file_with_project.id,
            version_number=v2.version_number,
        )

        # The reconstructed content should match the new content
        assert content == "Line 1\nLine 2 modified\nLine 3"

    def test_get_content_multiple_deltas(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting content after multiple delta versions."""
        # Create base
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="A\nB\nC",
        )

        # Apply first change
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="A\nB modified\nC",
        )

        # Apply second change
        version3 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="A\nB modified\nC added",
        )

        content = file_version_service.get_content_at_version(
            session=db_session,
            file_id=test_file_with_project.id,
            version_number=version3.version_number,
        )

        assert content == "A\nB modified\nC added"

    def test_get_content_version_not_found(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting content for non-existent version raises ValueError."""
        with pytest.raises(ValueError, match="Version .* not found"):
            file_version_service.get_content_at_version(
                session=db_session,
                file_id=test_file_with_project.id,
                version_number=999,
            )


@pytest.mark.unit
class TestFileVersionServiceCompareVersions:
    """Tests for compare_versions method."""

    def test_compare_versions_basic(self, db_session: Session, file_version_service, test_file_with_project):
        """Test basic version comparison."""
        # Create two versions
        v1 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Line 1\nLine 2\nLine 3",
        )

        v2 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Line 1\nLine 2 modified\nLine 3",
        )

        comparison = file_version_service.compare_versions(
            session=db_session,
            file_id=test_file_with_project.id,
            version1=v1.version_number,
            version2=v2.version_number,
        )

        assert "file_id" in comparison
        assert "version1" in comparison
        assert "version2" in comparison
        assert "unified_diff" in comparison
        assert "html_diff" in comparison
        assert "stats" in comparison
        assert comparison["version1"]["number"] == v1.version_number
        assert comparison["version2"]["number"] == v2.version_number

    def test_compare_versions_unified_diff(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that unified diff is generated correctly."""
        v1 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Hello\nWorld",
        )

        v2 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Hello\nBeautiful\nWorld",
        )

        comparison = file_version_service.compare_versions(
            session=db_session,
            file_id=test_file_with_project.id,
            version1=v1.version_number,
            version2=v2.version_number,
        )

        # Unified diff should contain the changes
        unified_diff = comparison["unified_diff"]
        # The diff should show "Beautiful" was added
        assert len(unified_diff) > 0  # Should have some diff content
        assert "Beautiful" in unified_diff or "Hello" in unified_diff  # At least some content

    def test_compare_versions_stats(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that diff statistics are calculated correctly."""
        v1 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Line 1\nLine 2",
        )

        v2 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Line 1\nLine 2 modified\nLine 3 added",
        )

        comparison = file_version_service.compare_versions(
            session=db_session,
            file_id=test_file_with_project.id,
            version1=v1.version_number,
            version2=v2.version_number,
        )

        stats = comparison["stats"]
        assert "lines_added" in stats
        assert "lines_removed" in stats
        assert "word_diff" in stats
        assert stats["lines_added"] >= 0


@pytest.mark.unit
class TestFileVersionServiceRollback:
    """Tests for rollback_to_version method."""

    def test_rollback_to_previous_version(self, db_session: Session, file_version_service, test_file_with_project):
        """Test rolling back a file to a previous version."""
        # Create initial version
        v1 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Original content",
        )

        # Create new version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Modified content",
        )

        # Rollback to v1
        file, new_version, version_quota_exceeded = (
            file_version_service.rollback_to_version(
                session=db_session,
                file_id=test_file_with_project.id,
                version_number=v1.version_number,
                user_id="test-user",
            )
        )

        # File content should be restored
        assert file.content == "Original content"

        # A new version should be created
        assert new_version.change_type == CHANGE_TYPE_RESTORE
        assert new_version.is_base_version is True
        assert "Restored to version" in new_version.change_summary
        assert version_quota_exceeded is False

    def test_rollback_creates_new_version(self, db_session: Session, file_version_service, test_file_with_project):
        """Test that rollback creates a new version (doesn't delete history)."""
        # Create two versions
        v1 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Version 1",
        )
        v2 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Version 2",
        )
        # Live content matches the latest version, so no pre-restore backup.
        test_file_with_project.content = "Version 2"
        db_session.add(test_file_with_project)
        db_session.commit()

        # Rollback
        _, new_version, version_quota_exceeded = (
            file_version_service.rollback_to_version(
                session=db_session,
                file_id=test_file_with_project.id,
                version_number=v1.version_number,
                user_id="test-user",
            )
        )

        # New version number should be v2 + 1
        assert new_version.version_number == v2.version_number + 1
        assert version_quota_exceeded is False

        # Check all versions still exist
        all_versions = file_version_service.get_versions(
            session=db_session,
            file_id=test_file_with_project.id,
            include_auto_save=True,
        )
        assert len(all_versions) == 3

    def test_rollback_snapshot_failure_is_not_reported_as_quota_exhaustion(
        self,
        db_session: Session,
        file_version_service,
        test_file_with_project,
        monkeypatch,
    ):
        """Restore succeeds even if snapshot persistence fails for a non-quota reason."""
        v1 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Version 1",
        )
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Version 2",
        )
        # Live content is already in history, so only the restore snapshot runs.
        test_file_with_project.content = "Version 2"
        db_session.add(test_file_with_project)
        db_session.commit()

        def fail_snapshot(*_args, **_kwargs):
            raise RuntimeError("snapshot storage unavailable")

        monkeypatch.setattr(file_version_service, "create_version", fail_snapshot)

        file, new_version, version_quota_exceeded = (
            file_version_service.rollback_to_version(
                session=db_session,
                file_id=test_file_with_project.id,
                version_number=v1.version_number,
                user_id="test-user",
            )
        )

        assert file.content == "Version 1"
        assert new_version is None
        assert version_quota_exceeded is False

    def test_rollback_file_not_found(self, db_session: Session, file_version_service):
        """Test rollback for non-existent file raises ValueError."""
        # Conditional restore must lock and verify the live File before history.
        with pytest.raises(ValueError, match="File non-existent-file-id not found"):
            file_version_service.rollback_to_version(
                session=db_session,
                file_id="non-existent-file-id",
                version_number=1,
                user_id="user-id",
            )

    def test_rollback_version_not_found(self, db_session: Session, file_version_service, test_file_with_project):
        """Test rollback to non-existent version raises ValueError."""
        # Create a version
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Content",
        )

        # Try to rollback to non-existent version
        with pytest.raises(ValueError, match="Version .* not found"):
            file_version_service.rollback_to_version(
                session=db_session,
                file_id=test_file_with_project.id,
                version_number=999,
                user_id="test-user",
            )


@pytest.mark.unit
class TestFileVersionServiceDiffReplay:
    """Tests for diff replay when content lines look like diff headers.

    Deleted lines starting with "--" (e.g. Markdown "---" separators) and
    added lines starting with "++" produce diff lines that share a prefix
    with unified diff file headers, and must not be skipped during replay.
    """

    def _roundtrip(self, db_session, service, file, contents):
        """Create a version per content, then reconstruct each and compare."""
        versions = [
            service.create_version(
                session=db_session,
                file_id=file.id,
                new_content=content,
            )
            for content in contents
        ]
        for version, expected in zip(versions, contents, strict=True):
            actual = service.get_content_at_version(
                session=db_session,
                file_id=file.id,
                version_number=version.version_number,
            )
            assert actual == expected, (
                f"Version {version.version_number} replayed incorrectly:\n"
                f"expected: {expected!r}\nactual: {actual!r}"
            )
        return versions

    def test_delete_markdown_separator_line(self, db_session: Session, file_version_service, test_file_with_project):
        """Deleting a '---' separator line must survive delta replay."""
        versions = self._roundtrip(
            db_session,
            file_version_service,
            test_file_with_project,
            [
                "章节开头\n---\n结尾\n",
                "章节开头\n***\n结尾\n",
            ],
        )
        assert versions[1].is_base_version is False

    def test_add_line_starting_with_plus_plus(self, db_session: Session, file_version_service, test_file_with_project):
        """Adding a line starting with '++' must survive delta replay."""
        versions = self._roundtrip(
            db_session,
            file_version_service,
            test_file_with_project,
            [
                "第一行\n第二行\n",
                "第一行\n++重点标记\n第二行\n",
            ],
        )
        assert versions[1].is_base_version is False

    def test_remove_frontmatter_block(self, db_session: Session, file_version_service, test_file_with_project):
        """Removing a YAML frontmatter block deletes '---' delimiter lines."""
        versions = self._roundtrip(
            db_session,
            file_version_service,
            test_file_with_project,
            [
                "---\ntitle: 第一章\ntags: 修仙\n---\n\n正文开始\n",
                "正文开始\n",
                "---\ntitle: 第一章（修订）\n---\n\n正文开始\n",
            ],
        )
        assert versions[1].is_base_version is False
        assert versions[2].is_base_version is False

    def test_rollback_restores_content_with_separator(self, db_session: Session, file_version_service, test_file_with_project):
        """Rollback across a '---' deletion must restore exact content."""
        v1_content = "章节开头\n---\n结尾\n"
        file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content=v1_content,
        )
        v2 = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="章节开头\n***\n结尾\n",
        )
        assert v2.is_base_version is False

        file, _, _ = file_version_service.rollback_to_version(
            session=db_session,
            file_id=test_file_with_project.id,
            version_number=v2.version_number,
            user_id="test-user",
        )
        assert file.content == "章节开头\n***\n结尾\n"

    def test_create_apply_diff_fuzz_roundtrip(self, file_version_service):
        """_create_diff -> _apply_diff must round-trip arbitrary content."""
        rng = random.Random(20260726)
        line_pool = [
            "---",
            "----",
            "+++",
            "++++",
            "--",
            "++",
            "-",
            "+",
            "-- 注释风格",
            "++重点",
            "@@ -1,2 +1,2 @@",
            "@@",
            "普通的一行",
            "another plain line",
            "",
            "  缩进行",
            "***",
        ]

        for _ in range(100):
            old_lines = [rng.choice(line_pool) for _ in range(rng.randint(0, 15))]
            # Mutate: random deletions, insertions, replacements
            new_lines = list(old_lines)
            for _ in range(rng.randint(0, 6)):
                op = rng.choice(["insert", "delete", "replace"])
                if op == "insert":
                    new_lines.insert(rng.randint(0, len(new_lines)), rng.choice(line_pool))
                elif op == "delete" and new_lines:
                    new_lines.pop(rng.randrange(len(new_lines)))
                elif op == "replace" and new_lines:
                    new_lines[rng.randrange(len(new_lines))] = rng.choice(line_pool)

            old_content = "\n".join(old_lines) + ("\n" if rng.random() < 0.7 else "")
            new_content = "\n".join(new_lines) + ("\n" if rng.random() < 0.7 else "")

            diff = file_version_service._create_diff(old_content, new_content)
            result = file_version_service._apply_diff(old_content, diff)
            assert result == new_content, (
                f"Round-trip failed:\nold: {old_content!r}\n"
                f"new: {new_content!r}\ngot: {result!r}"
            )


@pytest.mark.unit
class TestFileVersionServiceHelpers:
    """Tests for helper methods."""

    def test_get_latest_version(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting the latest version."""
        # Create multiple versions
        for i in range(5):
            file_version_service.create_version(
                session=db_session,
                file_id=test_file_with_project.id,
                new_content=f"Version {i}",
            )

        latest = file_version_service.get_latest_version(
            session=db_session,
            file_id=test_file_with_project.id,
        )

        assert latest is not None
        assert latest.version_number == 5

    def test_get_latest_version_when_empty(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting latest version when file has no versions."""
        latest = file_version_service.get_latest_version(
            session=db_session,
            file_id=test_file_with_project.id,
        )

        assert latest is None

    def test_get_version_by_id(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting a specific version by ID."""
        created = file_version_service.create_version(
            session=db_session,
            file_id=test_file_with_project.id,
            new_content="Test content",
        )

        retrieved = file_version_service.get_version(
            session=db_session,
            version_id=created.id,
        )

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.version_number == created.version_number

    def test_get_version_count(self, db_session: Session, file_version_service, test_file_with_project):
        """Test getting version count."""
        # Initially should be 0
        count = file_version_service.get_version_count(
            session=db_session,
            file_id=test_file_with_project.id,
        )
        assert count == 0

        # Create some versions
        for _ in range(5):
            file_version_service.create_version(
                session=db_session,
                file_id=test_file_with_project.id,
                new_content="Content",
            )

        count = file_version_service.get_version_count(
            session=db_session,
            file_id=test_file_with_project.id,
        )
        assert count == 5
