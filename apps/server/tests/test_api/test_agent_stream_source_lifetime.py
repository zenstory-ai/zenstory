"""Real registered handler/body iterator closes its nested synthetic provider source.

Direct route call with real owned ORM actors/Session/quota, not an HTTP-auth proof.
"""

import asyncio
from uuid import uuid4

import pytest

import api.agent as agent_api
from models import Project, User


@pytest.mark.asyncio
async def test_stream_body_close_reaches_inner_source(db_session, monkeypatch, record_property):
    suffix = uuid4().hex
    user = User(username="pump-" + suffix, email=suffix + "@example.test",
                hashed_password="unused-local-hash", is_active=True, email_verified=True)
    db_session.add(user)
    db_session.flush()
    project = Project(name="Owned source lifetime fixture", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    project_id = project.id
    reached = asyncio.Event()
    source_closed = asyncio.Event()

    class SyntheticProviderService:
        async def process_stream(self, **kwargs):
            assert kwargs["project_id"] == project_id
            try:
                yield 'event: session_started\ndata: {"session_id":"owned-source"}\n\n'
                for index in range(256):
                    if index == 32:
                        reached.set()
                    yield 'event: content\ndata: {"text":"local chunk"}\n\n'
            finally:
                source_closed.set()

    with monkeypatch.context() as overrides:
        overrides.setattr(agent_api, "get_agent_service", SyntheticProviderService)
        response = await agent_api.stream_request(
            agent_api.AgentRequest(project_id=project_id, message="Local source fixture"),
            session=db_session, current_user=user, accept_language="zh", _rate_limit=0,
        )
        iterator = response.body_iterator
        pump = None
        try:
            frame = await iterator.__anext__()
            assert "session_started" in frame
            await asyncio.wait_for(reached.wait(), timeout=2)
            # Observation only: real event_generator local, no factory/method replacement.
            pump = iterator.ag_frame.f_locals["pump"]
            assert isinstance(pump, agent_api.SSEStreamPump)
            assert pump._queue.qsize() == 32 and not pump._task.done()
            assert not source_closed.is_set()
            await iterator.aclose()
            with pytest.raises(asyncio.CancelledError):
                await pump._task
            assert source_closed.is_set()
            record_property("source_lifetime", "actualroute/bodyiterator/pump/primed-wrapper; directownedORMactors; syntheticproviderclosed beforecancelreturned")
        finally:
            await iterator.aclose()
            if pump is not None:
                pump.cancel()
                await asyncio.gather(pump._task, return_exceptions=True)
                assert pump._task.done()
