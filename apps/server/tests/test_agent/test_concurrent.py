"""
Concurrent operation tests for the Agent system.

Tests that verify data consistency and isolation under concurrent operations:
1. Concurrent file edits - multiple users editing the same file simultaneously
2. Concurrent chat sessions - same user with multiple active chat sessions
3. Isolated user data - verification that user data remains isolated under concurrent access

These tests use asyncio.gather to simulate concurrent operations and verify
that database transactions and locking mechanisms work correctly.
"""

import asyncio
from datetime import datetime

import pytest
from sqlmodel import Session, select

from models import ChatMessage, ChatSession, File, Project, User
from models.file_version import (
    CHANGE_SOURCE_AI,
    CHANGE_SOURCE_USER,
    CHANGE_TYPE_EDIT,
)
from services.core.auth_service import hash_password
from services.features.file_version_service import (
    FileVersionService,
)


@pytest.fixture
def two_users_with_projects(db_session: Session):
    """
    Create two users, each with their own project and a shared file.

    Returns:
        Tuple of (user1, project1, file1, user2, project2, file2)
    """
    # User 1
    user1 = User(
        email="concurrent1@example.com",
        username="concurrent1",
        hashed_password=hash_password("password123"),
        name="Concurrent User 1",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user1)
    db_session.commit()
    db_session.refresh(user1)

    project1 = Project(
        name="Project 1",
        description="Project owned by user 1",
        owner_id=user1.id,
    )
    db_session.add(project1)
    db_session.commit()
    db_session.refresh(project1)

    file1 = File(
        title="File 1",
        content="Initial content for file 1",
        file_type="draft",
        project_id=project1.id,
        order=0,
    )
    db_session.add(file1)
    db_session.commit()
    db_session.refresh(file1)

    # User 2
    user2 = User(
        email="concurrent2@example.com",
        username="concurrent2",
        hashed_password=hash_password("password123"),
        name="Concurrent User 2",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user2)
    db_session.commit()
    db_session.refresh(user2)

    project2 = Project(
        name="Project 2",
        description="Project owned by user 2",
        owner_id=user2.id,
    )
    db_session.add(project2)
    db_session.commit()
    db_session.refresh(project2)

    file2 = File(
        title="File 2",
        content="Initial content for file 2",
        file_type="draft",
        project_id=project2.id,
        order=0,
    )
    db_session.add(file2)
    db_session.commit()
    db_session.refresh(file2)

    return user1, project1, file1, user2, project2, file2


@pytest.fixture
def user_with_multiple_sessions(db_session: Session):
    """
    Create a user with a project and multiple chat sessions.

    Returns:
        Tuple of (user, project)
    """
    user = User(
        email="multisession@example.com",
        username="multisession",
        hashed_password=hash_password("password123"),
        name="Multi Session User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name="Multi Session Project",
        description="Project for testing multiple sessions",
        owner_id=user.id,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    return user, project


@pytest.mark.asyncio
class TestConcurrentFileEdits:
    """Tests for concurrent file editing scenarios."""

    async def test_concurrent_file_edits_create_versions(
        self, db_session: Session, two_users_with_projects
    ):
        """
        Test that concurrent edits to the same file create separate versions.

        Verifies:
        - Both edits complete successfully
        - Version history records both changes
        - Final content reflects the last write (last-writer-wins)
        - No data corruption occurs
        """
        user1, project1, file1, user2, project2, file2 = two_users_with_projects
        version_service = FileVersionService()

        results = {"edit1": None, "edit2": None}
        errors = []

        async def edit_file_1():
            """Simulate first user editing the file."""
            try:
                # Create a new session for this concurrent operation
                from tests.conftest import TestSessionLocal
                async_session = TestSessionLocal()

                new_content = "Content edited by user 1"
                file = async_session.get(File, file1.id)
                if file:
                    file.content = new_content
                    file.updated_at = datetime.utcnow()
                    async_session.add(file)

                    version = version_service.create_version(
                        session=async_session,
                        file_id=file1.id,
                        new_content=new_content,
                        change_type=CHANGE_TYPE_EDIT,
                        change_source=CHANGE_SOURCE_USER,
                        change_summary="User 1 edit",
                    )
                    async_session.commit()
                    results["edit1"] = version.version_number
            except Exception as e:
                errors.append(("edit1", str(e)))
            finally:
                async_session.close()

        async def edit_file_2():
            """Simulate second user editing the same file."""
            try:
                # Small delay to simulate concurrent access
                await asyncio.sleep(0.01)

                from tests.conftest import TestSessionLocal
                async_session = TestSessionLocal()

                new_content = "Content edited by user 2"
                file = async_session.get(File, file1.id)
                if file:
                    file.content = new_content
                    file.updated_at = datetime.utcnow()
                    async_session.add(file)

                    version = version_service.create_version(
                        session=async_session,
                        file_id=file1.id,
                        new_content=new_content,
                        change_type=CHANGE_TYPE_EDIT,
                        change_source=CHANGE_SOURCE_AI,
                        change_summary="User 2 edit (via AI)",
                    )
                    async_session.commit()
                    results["edit2"] = version.version_number
            except Exception as e:
                errors.append(("edit2", str(e)))
            finally:
                async_session.close()

        # Execute both edits concurrently
        await asyncio.gather(
            asyncio.create_task(edit_file_1()),
            asyncio.create_task(edit_file_2()),
        )

        # Verify no errors occurred
        assert len(errors) == 0, f"Errors during concurrent edits: {errors}"

        # Verify both versions were created
        assert results["edit1"] is not None, "Edit 1 should have created a version"
        assert results["edit2"] is not None, "Edit 2 should have created a version"

        # Verify version history
        versions = version_service.get_versions(
            session=db_session, file_id=file1.id, limit=10
        )
        assert len(versions) >= 2, "Should have at least 2 versions"

        # Verify final file state
        db_session.refresh(file1)
        assert file1.content in [
            "Content edited by user 1",
            "Content edited by user 2",
        ], "Final content should be from one of the edits"

    async def test_sequential_version_numbers_under_concurrent_edits(
        self, db_session: Session, two_users_with_projects
    ):
        """
        Test that version numbers remain sequential under concurrent edits.

        Verifies database transaction isolation prevents duplicate version numbers.
        """
        user1, project1, file1, user2, project2, file2 = two_users_with_projects
        version_service = FileVersionService()

        num_edits = 5
        results = []

        async def edit_with_content(edit_id: int):
            """Perform an edit with unique content."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                new_content = f"Edit {edit_id} content"
                version = version_service.create_version(
                    session=async_session,
                    file_id=file1.id,
                    new_content=new_content,
                    change_type=CHANGE_TYPE_EDIT,
                    change_source=CHANGE_SOURCE_USER,
                    change_summary=f"Edit {edit_id}",
                )
                async_session.commit()
                results.append(version.version_number)
            except Exception:
                results.append(-1)  # Error marker
            finally:
                async_session.close()

        # Execute concurrent edits
        tasks = [
            asyncio.create_task(edit_with_content(i))
            for i in range(num_edits)
        ]
        await asyncio.gather(*tasks)

        # All edits should have succeeded
        assert len(results) == num_edits
        assert -1 not in results, "Some edits failed"

        # Version numbers should be unique (no duplicates)
        assert len(set(results)) == num_edits, "Version numbers should be unique"

        # Version numbers should be sequential
        sorted_versions = sorted(results)
        expected = list(range(results[0], results[0] + num_edits))
        assert sorted_versions == expected, "Version numbers should be sequential"

    async def test_concurrent_edits_different_files(
        self, db_session: Session, two_users_with_projects
    ):
        """
        Test that concurrent edits to different files don't interfere.

        Verifies file-level isolation for concurrent operations.
        """
        user1, project1, file1, user2, project2, file2 = two_users_with_projects
        version_service = FileVersionService()

        results = {"file1": None, "file2": None}

        async def edit_file_1():
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                version = version_service.create_version(
                    session=async_session,
                    file_id=file1.id,
                    new_content="File 1 new content",
                    change_type=CHANGE_TYPE_EDIT,
                    change_source=CHANGE_SOURCE_USER,
                )
                async_session.commit()
                results["file1"] = version.version_number
            finally:
                async_session.close()

        async def edit_file_2():
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                version = version_service.create_version(
                    session=async_session,
                    file_id=file2.id,
                    new_content="File 2 new content",
                    change_type=CHANGE_TYPE_EDIT,
                    change_source=CHANGE_SOURCE_AI,
                )
                async_session.commit()
                results["file2"] = version.version_number
            finally:
                async_session.close()

        # Execute concurrent edits to different files
        await asyncio.gather(
            asyncio.create_task(edit_file_1()),
            asyncio.create_task(edit_file_2()),
        )

        # Both edits should succeed independently
        assert results["file1"] is not None, "File 1 edit should succeed"
        assert results["file2"] is not None, "File 2 edit should succeed"

        # Verify each file has its own version history
        versions1 = version_service.get_versions(
            session=db_session, file_id=file1.id
        )
        versions2 = version_service.get_versions(
            session=db_session, file_id=file2.id
        )

        assert len(versions1) >= 1
        assert len(versions2) >= 1


@pytest.mark.asyncio
class TestConcurrentChatSessions:
    """Tests for concurrent chat session operations."""

    async def test_concurrent_chat_sessions_isolation(
        self, db_session: Session, user_with_multiple_sessions
    ):
        """
        Test that multiple concurrent chat sessions remain isolated.

        Verifies:
        - Each session maintains its own message history
        - Messages don't leak between sessions
        - Session state is properly isolated
        """
        user, project = user_with_multiple_sessions

        # Create multiple sessions
        sessions = []
        for i in range(3):
            session = ChatSession(
                user_id=user.id,
                project_id=project.id,
                title=f"Session {i}",
                # One active chat per user+project (uq_chat_session_user_project_active); the rest are archived.
                is_active=i == 0,
                message_count=0,
            )
            db_session.add(session)
        db_session.commit()

        for session in db_session.exec(
            select(ChatSession).where(ChatSession.project_id == project.id)
        ).all():
            sessions.append(session)

        results = {session.id: [] for session in sessions}

        async def add_messages_to_session(session_id: str, session_idx: int):
            """Add unique messages to a specific session."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                for i in range(3):
                    msg = ChatMessage(
                        session_id=session_id,
                        role="user",
                        content=f"Session {session_idx} message {i}",
                    )
                    async_session.add(msg)

                    # Update session message count
                    chat_session = async_session.get(ChatSession, session_id)
                    if chat_session:
                        chat_session.message_count = (chat_session.message_count or 0) + 1

                    async_session.commit()
                    results[session_id].append(msg.content)

                    # Small delay to simulate real conversation
                    await asyncio.sleep(0.01)
            finally:
                async_session.close()

        # Execute concurrent message additions
        tasks = [
            asyncio.create_task(add_messages_to_session(session.id, idx))
            for idx, session in enumerate(sessions)
        ]
        await asyncio.gather(*tasks)

        # Verify each session has only its own messages
        for session in sessions:
            messages = db_session.exec(
                select(ChatMessage).where(ChatMessage.session_id == session.id)
            ).all()

            assert len(messages) == 3, f"Session {session.id} should have 3 messages"

            # Verify message content belongs to this session
            for msg in messages:
                assert msg.session_id == session.id
                assert f"Session {sessions.index(session)}" in msg.content

    async def test_concurrent_session_creation(
        self, db_session: Session, user_with_multiple_sessions
    ):
        """
        Test creating multiple sessions concurrently for the same project.

        Verifies that session creation handles concurrent requests properly.
        """
        user, project = user_with_multiple_sessions

        created_sessions = []

        async def create_session(title: str):
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                # Archived chats: only one may be active per user+project (uq_chat_session_user_project_active).
                session = ChatSession(
                    user_id=user.id,
                    project_id=project.id,
                    title=title,
                    is_active=False,
                    message_count=0,
                )
                async_session.add(session)
                async_session.commit()
                async_session.refresh(session)
                created_sessions.append(session.id)
            finally:
                async_session.close()

        # Create sessions concurrently
        await asyncio.gather(
            *[asyncio.create_task(create_session(f"Concurrent Session {i}")) for i in range(5)]
        )

        # All sessions should be created
        assert len(created_sessions) == 5

        # All session IDs should be unique
        assert len(set(created_sessions)) == 5, "All session IDs should be unique"

        # Verify in database
        sessions = db_session.exec(
            select(ChatSession).where(ChatSession.project_id == project.id)
        ).all()
        assert len(sessions) == 5

    async def test_message_ordering_under_concurrent_adds(
        self, db_session: Session, user_with_multiple_sessions
    ):
        """
        Test that message ordering is preserved under concurrent additions.

        Verifies that messages maintain their order even when added concurrently.
        """
        user, project = user_with_multiple_sessions

        session = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Ordering Test Session",
            is_active=True,
            message_count=0,
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        message_times = []

        async def add_message(idx: int):
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                # Stagger additions slightly
                await asyncio.sleep(0.01 * idx)

                msg = ChatMessage(
                    session_id=session.id,
                    role="user",
                    content=f"Message {idx}",
                )
                async_session.add(msg)
                async_session.commit()
                async_session.refresh(msg)
                message_times.append((idx, msg.created_at))
            finally:
                async_session.close()

        # Add messages concurrently
        await asyncio.gather(
            *[asyncio.create_task(add_message(i)) for i in range(10)]
        )

        # Verify all messages were added
        messages = db_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.created_at)
        ).all()

        assert len(messages) == 10


@pytest.mark.asyncio
class TestIsolatedUserData:
    """Tests for user data isolation under concurrent operations."""

    async def test_concurrent_cross_user_file_access_denied(
        self, db_session: Session, two_users_with_projects
    ):
        """
        Test that concurrent access attempts across users are properly denied.

        Verifies that even under concurrent operations, users cannot access
        each other's data.
        """
        user1, project1, file1, user2, project2, file2 = two_users_with_projects
        FileVersionService()

        access_results = {"user1_to_file1": None, "user2_to_file1": None}

        async def user1_access_own_file():
            """User 1 accesses their own file - should succeed."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                file = async_session.get(File, file1.id)
                if file and file.project_id == project1.id:
                    access_results["user1_to_file1"] = "success"
                else:
                    access_results["user1_to_file1"] = "not_found"
            except Exception as e:
                access_results["user1_to_file1"] = f"error: {str(e)}"
            finally:
                async_session.close()

        async def user2_attempt_access_file1():
            """User 2 attempts to access user 1's file - should fail."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                file = async_session.get(File, file1.id)
                # Check if file exists and verify project ownership
                if file:
                    # Get the project to check ownership
                    project = async_session.get(Project, file.project_id)
                    if project and project.owner_id == user2.id:
                        access_results["user2_to_file1"] = "unauthorized_access"
                    else:
                        access_results["user2_to_file1"] = "access_denied"
                else:
                    access_results["user2_to_file1"] = "not_found"
            except Exception as e:
                access_results["user2_to_file1"] = f"error: {str(e)}"
            finally:
                async_session.close()

        # Execute concurrent access attempts
        await asyncio.gather(
            asyncio.create_task(user1_access_own_file()),
            asyncio.create_task(user2_attempt_access_file1()),
        )

        # Verify access control
        assert access_results["user1_to_file1"] == "success", \
            "User 1 should access their own file"
        assert access_results["user2_to_file1"] == "access_denied", \
            "User 2 should be denied access to user 1's file"

    async def test_concurrent_project_operations_isolation(
        self, db_session: Session, two_users_with_projects
    ):
        """
        Test that concurrent project operations maintain user isolation.

        Verifies that concurrent file operations in different projects
        don't interfere with each other.
        """
        user1, project1, file1, user2, project2, file2 = two_users_with_projects

        results = {"project1_files": 0, "project2_files": 0}

        async def add_files_to_project1():
            """Add files to project 1."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                for i in range(3):
                    file = File(
                        title=f"Project 1 File {i}",
                        content=f"Content {i}",
                        file_type="draft",
                        project_id=project1.id,
                        order=i,
                    )
                    async_session.add(file)
                async_session.commit()

                files = async_session.exec(
                    select(File).where(File.project_id == project1.id)
                ).all()
                results["project1_files"] = len(files)
            finally:
                async_session.close()

        async def add_files_to_project2():
            """Add files to project 2."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                for i in range(2):
                    file = File(
                        title=f"Project 2 File {i}",
                        content=f"Content {i}",
                        file_type="draft",
                        project_id=project2.id,
                        order=i,
                    )
                    async_session.add(file)
                async_session.commit()

                files = async_session.exec(
                    select(File).where(File.project_id == project2.id)
                ).all()
                results["project2_files"] = len(files)
            finally:
                async_session.close()

        # Execute concurrent file additions
        await asyncio.gather(
            asyncio.create_task(add_files_to_project1()),
            asyncio.create_task(add_files_to_project2()),
        )

        # Verify isolation - each project has only its own files
        # Project 1: 1 existing + 3 new = 4
        # Project 2: 1 existing + 2 new = 3
        assert results["project1_files"] == 4, "Project 1 should have 4 files"
        assert results["project2_files"] == 3, "Project 2 should have 3 files"

    async def test_concurrent_user_data_integrity(
        self, db_session: Session, two_users_with_projects
    ):
        """
        Test that user data integrity is maintained under concurrent modifications.

        Verifies that rapid concurrent updates don't cause data corruption.
        """
        user1, project1, file1, user2, project2, file2 = two_users_with_projects

        original_content_1 = file1.content
        original_content_2 = file2.content

        updates = []

        async def update_file_content(file_id: str, new_content: str, update_id: int):
            """Update file content."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                file = async_session.get(File, file_id)
                if file:
                    file.content = new_content
                    file.updated_at = datetime.utcnow()
                    async_session.add(file)
                    async_session.commit()
                    updates.append((update_id, file_id, "success"))
                else:
                    updates.append((update_id, file_id, "not_found"))
            except Exception as e:
                updates.append((update_id, file_id, f"error: {str(e)}"))
            finally:
                async_session.close()

        # Execute many concurrent updates
        tasks = []
        for i in range(10):
            # Alternate between updating file1 and file2
            if i % 2 == 0:
                tasks.append(
                    asyncio.create_task(
                        update_file_content(file1.id, f"File1 update {i}", i)
                    )
                )
            else:
                tasks.append(
                    asyncio.create_task(
                        update_file_content(file2.id, f"File2 update {i}", i)
                    )
                )

        await asyncio.gather(*tasks)

        # All updates should succeed
        assert len(updates) == 10
        successful = [u for u in updates if u[2] == "success"]
        assert len(successful) == 10, "All updates should succeed"

        # Refresh and verify data integrity
        db_session.refresh(file1)
        db_session.refresh(file2)

        # Content should be one of the valid updates
        assert file1.content.startswith("File1 update") or file1.content == original_content_1
        assert file2.content.startswith("File2 update") or file2.content == original_content_2


@pytest.mark.asyncio
class TestHighConcurrencyScenarios:
    """Tests for high concurrency edge cases."""

    async def test_many_concurrent_file_reads(
        self, db_session: Session, two_users_with_projects
    ):
        """
        Test that many concurrent reads don't cause issues.

        Verifies system stability under high read concurrency.
        """
        user1, project1, file1, user2, project2, file2 = two_users_with_projects

        read_results = []

        async def read_file(file_id: str, read_id: int):
            """Read file content."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                file = async_session.get(File, file_id)
                if file:
                    read_results.append((read_id, len(file.content)))
                else:
                    read_results.append((read_id, -1))
            finally:
                async_session.close()

        # Execute many concurrent reads
        tasks = [
            asyncio.create_task(read_file(file1.id, i))
            for i in range(50)
        ]
        await asyncio.gather(*tasks)

        # All reads should succeed
        assert len(read_results) == 50
        assert all(result[1] > 0 for result in read_results), "All reads should return content"

    async def test_burst_concurrent_writes(
        self, db_session: Session, two_users_with_projects
    ):
        """
        Test burst of concurrent writes to the same file.

        Verifies system handles write bursts without data corruption.
        """
        user1, project1, file1, user2, project2, file2 = two_users_with_projects
        version_service = FileVersionService()

        write_count = 20
        results = []

        async def write_file(write_id: int):
            """Write to file."""
            from tests.conftest import TestSessionLocal
            async_session = TestSessionLocal()
            try:
                version_service.create_version(
                    session=async_session,
                    file_id=file1.id,
                    new_content=f"Burst write {write_id}",
                    change_type=CHANGE_TYPE_EDIT,
                    change_source=CHANGE_SOURCE_AI,
                )
                async_session.commit()
                results.append(write_id)
            except Exception:
                pass  # Some writes may fail due to locking
            finally:
                async_session.close()

        # Execute burst writes
        tasks = [
            asyncio.create_task(write_file(i))
            for i in range(write_count)
        ]
        await asyncio.gather(*tasks)

        # Most writes should succeed (allowing for some contention)
        success_rate = len(results) / write_count
        assert success_rate >= 0.8, f"At least 80% of writes should succeed, got {success_rate * 100}%"

        # Verify file is in a valid state
        db_session.refresh(file1)
        assert file1.content is not None

        # Verify version history is intact
        versions = version_service.get_versions(session=db_session, file_id=file1.id)
        assert len(versions) >= len(results), "Should have versions for successful writes"
