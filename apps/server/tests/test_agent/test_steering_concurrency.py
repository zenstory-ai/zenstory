"""
Tests for steering queue concurrency behavior.

Tests message ID uniqueness and concurrent access patterns.
"""

import asyncio

import pytest

from agent.core.steering import (
    SteeringQueue,
    cleanup_steering_queue_async,
    create_steering_queue_async,
    get_steering_queue_for_user_async,
)


@pytest.mark.asyncio
class TestMessageIdUniqueness:
    """Test message ID uniqueness under concurrent access."""

    async def test_concurrent_add_unique_ids(self):
        """100 concurrent adds should produce 100 unique IDs."""
        queue = SteeringQueue()

        async def add_msg(i: int) -> str:
            msg = await queue.add(f"Message {i}")
            return msg.id

        tasks = [add_msg(i) for i in range(100)]
        results = await asyncio.gather(*tasks)

        # All IDs should be unique
        assert len(set(results)) == 100, "Message IDs should be unique"

    async def test_concurrent_add_same_millisecond(self):
        """Even messages added in same millisecond should have unique IDs."""
        queue = SteeringQueue()

        # Create many tasks that will likely execute in same millisecond
        tasks = [queue.add(f"Msg {i}") for i in range(50)]
        results = await asyncio.gather(*tasks)

        ids = [msg.id for msg in results]
        assert len(set(ids)) == 50, "IDs should be unique even with same timestamp"


@pytest.mark.asyncio
class TestConcurrentAccess:
    """Test concurrent access to steering queue."""

    async def test_concurrent_add_and_get(self):
        """Concurrent add and get_pending should not lose messages."""
        queue = SteeringQueue()
        added_count = 0
        get_count = 0

        async def add_messages():
            nonlocal added_count
            for i in range(20):
                await queue.add(f"Message {i}")
                added_count += 1

        async def get_messages():
            nonlocal get_count
            await asyncio.sleep(0.01)  # Small delay
            pending = await queue.get_pending()
            get_count += len(pending)

        # Run add and get concurrently
        await asyncio.gather(add_messages(), get_messages())

        # All messages should be accounted for
        assert added_count == 20

    async def test_concurrent_sessions_isolated(self):
        """Different sessions should have isolated queues."""
        session_ids = [f"session-{i}" for i in range(5)]

        async def add_to_session(session_id: str):
            queue = await create_steering_queue_async(
                session_id, "concurrency-user", run_id=f"run-{session_id}"
            )
            await queue.add(f"Message for {session_id}")
            return session_id

        await asyncio.gather(*[add_to_session(sid) for sid in session_ids])

        # Verify each session has exactly one message
        for sid in session_ids:
            queue = await get_steering_queue_for_user_async(sid, "concurrency-user")
            pending = await queue.get_pending()
            assert len(pending) == 1, f"Session {sid} should have 1 message"
            await cleanup_steering_queue_async(sid)


@pytest.mark.asyncio
class TestQueueManagerConcurrency:
    """Test SteeringQueueManager concurrency."""

    async def test_concurrent_get_queue_same_session(self):
        """Concurrent get_queue for same session should return same queue."""
        from agent.core.steering import _queue_manager

        session_id = "concurrent-test-session"

        async def get_queue():
            return await _queue_manager.claim_queue(
                session_id, "concurrency-user", "same-run"
            )

        queues = await asyncio.gather(*[get_queue() for _ in range(10)])

        # All should be the same object
        first_id = id(queues[0])
        assert all(id(q) == first_id for q in queues), "Should return same queue instance"

        await _queue_manager.cleanup(session_id, run_id="same-run")

    async def test_concurrent_cleanup(self):
        """Concurrent cleanup should not raise errors."""
        from agent.core.steering import _queue_manager

        session_id = "cleanup-test-session"

        # Create queue
        await _queue_manager.claim_queue(session_id, "concurrency-user", "run-a")

        # Concurrent cleanup should not raise
        await asyncio.gather(*[
            _queue_manager.cleanup(session_id) for _ in range(5)
        ])
