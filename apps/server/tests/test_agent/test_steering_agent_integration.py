"""
Integration tests for steering message injection into agent loop.

Tests the interaction between SteeringQueue and agent loop execution.
"""

import pytest

from agent.core.steering import (
    SteeringQueue,
    cleanup_steering_queue_async,
    create_steering_queue_async,
)

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")


@pytest.mark.integration
class TestSteeringAgentLoopIntegration:
    """Test steering message integration with agent loop."""

    @pytest.mark.asyncio
    async def test_steering_callback_returns_messages(self):
        """Test that steering callback correctly returns pending messages."""
        session_id = "test-integration-session"
        queue = await create_steering_queue_async(
            session_id, "integration-user", run_id="integration-run"
        )

        try:
            # Add a steering message
            await queue.add("Please focus on the outline")

            # Simulate the callback used in service.py
            async def get_steering_messages():
                messages = await queue.get_pending()
                return [{"id": m.id, "content": m.content} for m in messages]

            # Call the callback
            result = await get_steering_messages()

            assert len(result) == 1
            assert result[0]["content"] == "Please focus on the outline"
            assert result[0]["id"].startswith("steer-")

        finally:
            await cleanup_steering_queue_async(session_id)

    @pytest.mark.asyncio
    async def test_get_pending_marks_as_processed(self):
        """Test that get_pending marks messages as processed."""
        queue = SteeringQueue()

        await queue.add("Message 1")
        await queue.add("Message 2")

        # First get_pending should return both
        pending = await queue.get_pending()
        assert len(pending) == 2

        # Second get_pending should return empty (already processed)
        pending2 = await queue.get_pending()
        assert len(pending2) == 0

    @pytest.mark.asyncio
    async def test_message_added_during_processing(self):
        """Test that messages added during processing are available next iteration."""
        queue = SteeringQueue()

        await queue.add("Initial message")

        # Get first batch
        first_batch = await queue.get_pending()
        assert len(first_batch) == 1

        # Add new message while "processing" first
        await queue.add("New message during processing")

        # Simulate next iteration - should get new message
        second_batch = await queue.get_pending()
        assert len(second_batch) == 1
        assert second_batch[0].content == "New message during processing"

    @pytest.mark.asyncio
    async def test_sanitize_called_on_add(self):
        """Test that sanitization is applied on add."""
        queue = SteeringQueue()

        # Add message with control characters
        msg = await queue.add("Hello\x00World\x08")

        # Content should be sanitized
        assert "\x00" not in msg.content
        assert "\x08" not in msg.content
        assert "HelloWorld" in msg.content


@pytest.mark.integration
class TestSteeringQueueLifecycle:
    """Test steering queue lifecycle management."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_queue(self):
        """Test that cleanup removes the queue."""

        session_id = "lifecycle-test"

        # Create queue
        queue = await create_steering_queue_async(
            session_id, "lifecycle-user", run_id="run-a"
        )
        await queue.add("Test message")

        # Cleanup
        await cleanup_steering_queue_async(session_id)

        # A new exclusive claim creates a fresh queue.
        queue2 = await create_steering_queue_async(
            session_id, "lifecycle-user", run_id="run-b"
        )
        pending = await queue2.get_pending()
        assert len(pending) == 0  # New queue should be empty

        await cleanup_steering_queue_async(session_id)

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self):
        """Test that multiple sessions have independent queues."""
        sessions = ["session-a", "session-b", "session-c"]

        # Add different messages to each session
        for sid in sessions:
            queue = await create_steering_queue_async(
                sid, "multi-user", run_id=f"run-{sid}"
            )
            await queue.add(f"Message for {sid}")

        # Verify each session has its own message
        for sid in sessions:
            from agent.core.steering import get_steering_queue_for_user_async

            queue = await get_steering_queue_for_user_async(sid, "multi-user")
            pending = await queue.get_pending()
            assert len(pending) == 1
            assert sid in pending[0].content
            await cleanup_steering_queue_async(sid)
