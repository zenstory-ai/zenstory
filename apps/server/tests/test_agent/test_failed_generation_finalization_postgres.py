"""Real failed-run history compensation must retain exclusive generation ownership.

Run with both PostgreSQL URLs set before imports. Only the synthetic workflow
boundary is replaced; steering, context/history loading and persistence are real.
"""

import asyncio
import hashlib
import inspect
import json
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, select

import database
from agent import service as service_module
from agent.core.steering import (
    SteeringSessionBusyError,
    get_steering_queue_for_user_async,
    has_active_runs_async,
)
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.service import AgentService
from models import ChatMessage, ChatSession, Project, SystemPromptConfig, User

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL URL not configured",
)


@pytest.fixture(scope="module")
def local_generation_settings():
    # This is the real in-memory configuration, not a mocked steering gate.
    # Restore the caller's Redis/retrieval environment when this suite finishes.
    with pytest.MonkeyPatch.context() as settings:
        settings.delenv("REDIS_URL", raising=False)
        settings.setenv("AGENT_ENABLE_RETRIEVAL_SNIPPETS", "false")
        yield


@pytest.fixture(scope="module")
def pg_engine(local_generation_settings):
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert os.environ["DATABASE_URL"] == url
    assert database.is_postgres and database.sync_engine.dialect.name == "postgresql"
    expected = os.getenv("M07_EXPECTED_PRODUCT")
    if expected:
        expected_service = os.getenv("M07_EXPECTED_SERVICE", str(Path(expected) / "agent/service.py"))
        assert Path(service_module.__file__).resolve() == Path(expected_service)
    # Existing explicit backend switches, not mocked gate or retrieval functions.
    assert not os.getenv("REDIS_URL")
    assert os.getenv("AGENT_ENABLE_RETRIEVAL_SNIPPETS") == "false"
    engine = database.sync_engine
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_session_user_project_active "
            "ON chat_session (user_id, project_id) WHERE is_active = true"
        ))
        index = connection.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE "
            "indexname='uq_chat_session_user_project_active'"
        )).scalar_one()
        assert "UNIQUE" in index and "WHERE (is_active = true)" in index
    directory = os.getenv("M07_PROOF_EVIDENCE")
    if directory:
        metadata = {
            "product": service_module.__file__, "database": database.__file__,
            "service_sha256": hashlib.sha256(Path(service_module.__file__).read_bytes()).hexdigest(),
            "create_session_source": inspect.getsourcefile(service_module.create_session),
            "dialect": engine.dialect.name, "partial_active_index": index,
            "global_conftest_loaded": any(name.endswith("conftest") for name in sys.modules),
            "redis_url_absent": not os.getenv("REDIS_URL"),
            "retrieval_disabled_by_existing_setting": True,
        }
        (Path(directory) / f"{os.getenv('M07_PROOF_RUN', 'run')}-fixture.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
    with Session(engine) as session:
        assert session.expire_on_commit is True
        session.add(SystemPromptConfig(
            project_type="novel", role_definition="Local synthetic writing role",
            capabilities="Local synthetic conversation; no tools",
        ))
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def isolated_runtime_prompt_source(pg_engine, monkeypatch):
    # Override the shared SQLite prompt fixture with this suite's actual PG
    # engine; prompt queries/configuration loading remain real, not mocked.
    from agent import prompts

    monkeypatch.setattr(prompts, "sync_engine", pg_engine)
    monkeypatch.setattr(prompts, "_db_config_cache", None)


class Trace:
    def __init__(self, name):
        self.name = name
        self.rows = []
        self.lock = threading.Lock()

    def record(self, kind, **values):
        with self.lock:
            self.rows.append({"sequence": len(self.rows), "kind": kind,
                              "thread": threading.get_ident(), **values})

    def write(self):
        directory = os.getenv("M07_PROOF_EVIDENCE")
        if directory:
            path = Path(directory) / f"{os.getenv('M07_PROOF_RUN', 'run')}-{self.name}.json"
            path.write_text(json.dumps(self.rows, indent=2, ensure_ascii=False) + "\n")


@pytest.fixture
def actors(pg_engine):
    suffix = uuid.uuid4().hex
    with Session(pg_engine) as session:
        user = User(username=f"finalize-{suffix}", email=f"finalize-{suffix}@example.test",
                    hashed_password="unused", is_active=True)
        session.add(user)
        session.flush()
        project = Project(name="Finalization proof", owner_id=user.id)
        session.add(project)
        session.flush()
        chat = ChatSession(user_id=user.id, project_id=project.id, title="Proof",
                           is_active=True, message_count=2)
        session.add(chat)
        session.flush()
        session.add_all([
            ChatMessage(session_id=chat.id, role="user", content="Previously committed user"),
            ChatMessage(session_id=chat.id, role="assistant", content="Previously committed assistant"),
        ])
        ids = (user.id, project.id, chat.id)
        session.commit()
    return ids


def history(engine, chat_id):
    with Session(engine) as session:
        messages = session.exec(select(ChatMessage).where(
            ChatMessage.session_id == chat_id
        ).order_by(ChatMessage.created_at, ChatMessage.id)).all()
        return session.get(ChatSession, chat_id).message_count, [
            {"role": item.role, "content": item.content} for item in messages
        ]


async def consume(service, engine, actors, message, trace):
    user_id, project_id, chat_id = actors
    try:
        with Session(engine) as session:
            assert session.expire_on_commit is True
            generator = service.process_stream(project_id, user_id, message, session,
                                               session_id=chat_id, language="en")
            try:
                async for chunk in generator:
                    trace.record("sse", run=message, chunk=chunk)
            finally:
                await generator.aclose()
                trace.record("generator_closed", run=message)
    finally:
        trace.record("request_session_closed", run=message)


async def owner_present(chat_id, user_id):
    try:
        await get_steering_queue_for_user_async(chat_id, user_id)
    except KeyError:
        return False
    return True


@pytest.mark.parametrize("ending", ["failure", "cancellation"])
def test_compensating_history_retains_exclusive_run(pg_engine, actors, monkeypatch, ending):
    """B must be rejected while A's actual compensating SQL has not executed."""
    trace = Trace(ending)
    held = threading.Event()
    release_sql = threading.Event()
    a_waiting = None
    b_called = None
    b_release = None
    held_once = False

    def before_sql(connection, cursor, statement, parameters, context, executemany):
        nonlocal held_once
        frames = [{"file": frame.filename, "line": frame.lineno,
                   "function": frame.function} for frame in inspect.stack()
                  if "/agent/" in frame.filename]
        names = {frame["function"] for frame in frames}
        trace.record("sql", sql=statement,
                     backend_pid=connection.connection.driver_connection.info.backend_pid,
                     frames=frames)
        is_compensation = "_save_partial_history_sync" in names
        is_history_write = statement.lstrip().upper().startswith(("UPDATE CHAT_SESSION", "INSERT INTO CHAT_MESSAGE"))
        if is_compensation and is_history_write and not held_once:
            held_once = True
            trace.record("compensation_dml_held", sql=statement, frames=frames)
            held.set()
            if not release_sql.wait(30):
                raise AssertionError("fixture deadline: compensating SQL not released")
            trace.record("compensation_dml_released")

    async def workflow(state, **kwargs):
        trace.record("workflow_entry", run=state["router_message"], messages=state["messages"])
        if state["router_message"] == "A failed request":
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "A issued partial output"})
            if ending == "cancellation":
                a_waiting.set()
                await asyncio.Event().wait()
            raise RuntimeError("synthetic provider failure after issued output")
        b_called.set()
        await b_release.wait()
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "B successful output"})

    monkeypatch.setattr(service_module, "run_writing_workflow_streaming", workflow)
    event.listen(pg_engine, "before_cursor_execute", before_sql)

    async def scenario():
        nonlocal a_waiting, b_called, b_release
        a_waiting, b_called, b_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        service = AgentService()
        user_id, _, chat_id = actors
        a = asyncio.create_task(consume(service, pg_engine, actors, "A failed request", trace))
        b = None
        waiter = None
        results = []
        try:
            if ending == "cancellation":
                await asyncio.wait_for(a_waiting.wait(), 20)
                a.cancel()
            assert await asyncio.to_thread(held.wait, 20), "fixture: A did not reach real compensating DML"
            active_at_hold = await has_active_runs_async(chat_id)
            owner_at_hold = await owner_present(chat_id, user_id)
            count_before, rows_before = history(pg_engine, chat_id)
            trace.record("held_observation", active=active_at_hold, owner_present=owner_at_hold,
                         counter=count_before, messages=rows_before)
            b = asyncio.create_task(consume(service, pg_engine, actors, "B next request", trace))
            waiter = asyncio.create_task(b_called.wait())
            done, _ = await asyncio.wait({b, waiter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
            assert done, "fixture: B neither entered workflow nor completed"
            b_error = b.exception() if b.done() and not b.cancelled() else None
            observed_rejection = isinstance(b_error, SteeringSessionBusyError)
            trace.record("b_observation", rejected_busy=observed_rejection,
                         workflow_called=b_called.is_set(), error=repr(b_error),
                         active=await has_active_runs_async(chat_id),
                         owner_present=await owner_present(chat_id, user_id))
            if b_called.is_set():
                # Diagnose the real loaded-history path before testing exclusivity.
                entry = next(row for row in trace.rows if row["kind"] == "workflow_entry"
                             and row["run"] == "B next request")
                assert [item["content"] for item in entry["messages"]] == [
                    "Previously committed user", "Previously committed assistant", "B next request"
                ]
                hold_sequence = next(row["sequence"] for row in trace.rows
                                     if row["kind"] == "compensation_dml_held")
                assert any(row["kind"] == "sql" and hold_sequence < row["sequence"] < entry["sequence"]
                           and any(frame["function"] == "_load_history_window_under_token_budget"
                                   for frame in row["frames"]) for row in trace.rows)
        finally:
            release_sql.set()
            if waiter is not None:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            results.extend(await asyncio.gather(a, return_exceptions=True))
            # Cancellation uses real background save/cleanup tasks; await both.
            cleanup = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()
                       and task.get_coro().__name__ in {
                           "_drain_then_save_partial_history", "_cleanup_after_cancellation_save"}]
            if cleanup:
                results.extend(await asyncio.gather(*cleanup, return_exceptions=True))
            b_release.set()
            if b is not None:
                results.extend(await asyncio.gather(b, return_exceptions=True))
            trace.record("consumers_finished", results=[repr(result) for result in results])
        count, rows = history(pg_engine, chat_id)
        active_after = await has_active_runs_async(chat_id)
        owner_after = await owner_present(chat_id, user_id)
        trace.record("final_observation", counter=count, messages=rows,
                     active=active_after, owner_present=owner_after)
        assert count == len(rows)
        assert count == (4 if observed_rejection else 6)
        assert {"role": "user", "content": "A failed request"} in rows
        assert {"role": "assistant", "content": "A issued partial output"} in rows
        assert not active_after and not owner_after
        assert active_at_hold and owner_at_hold and observed_rejection and not b_called.is_set(), (
            "exclusive generation ownership released before failed history finalization: "
            f"active={active_at_hold}, owner={owner_at_hold}, "
            f"B_rejected={observed_rejection}, B_workflow_called={b_called.is_set()}"
        )

    try:
        asyncio.run(scenario())
    finally:
        release_sql.set()
        event.remove(pg_engine, "before_cursor_execute", before_sql)
        trace.record("listener_removed")
        trace.write()


@pytest.mark.parametrize("ending", ["success", "failure"])
def test_sequential_history_and_run_release(pg_engine, actors, monkeypatch, ending):
    trace = Trace(f"sequential-{ending}")

    async def workflow(state, **kwargs):
        trace.record("workflow_entry", run=state["router_message"], messages=state["messages"])
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Sequential output"})
        if ending == "failure" and state["router_message"] == "Sequential request":
            raise RuntimeError("synthetic sequential provider failure")

    monkeypatch.setattr(service_module, "run_writing_workflow_streaming", workflow)

    async def scenario():
        await consume(AgentService(), pg_engine, actors, "Sequential request", trace)
        count, rows = history(pg_engine, actors[2])
        active = await has_active_runs_async(actors[2])
        owner = await owner_present(actors[2], actors[0])
        trace.record("final_observation", counter=count, messages=rows,
                     active=active, owner_present=owner)
        assert count == len(rows) == 4
        assert {"role": "user", "content": "Sequential request"} in rows
        assert {"role": "assistant", "content": "Sequential output"} in rows
        assert not active and not owner
        # A later ordinary generation sees finalized history and can claim again.
        await consume(AgentService(), pg_engine, actors, "Sequential followup", trace)
        followup = next(row for row in trace.rows if row["kind"] == "workflow_entry"
                        and row["run"] == "Sequential followup")
        assert [item["content"] for item in followup["messages"]] == [
            "Previously committed user", "Previously committed assistant",
            "Sequential request", "Sequential output", "Sequential followup",
        ]
        count, rows = history(pg_engine, actors[2])
        assert count == len(rows) == 6
        assert not await has_active_runs_async(actors[2])
        assert not await owner_present(actors[2], actors[0])
        trace.record("followup_final_observation", counter=count, messages=rows,
                     active=False, owner_present=False)
        chunks = [row["chunk"] for row in trace.rows if row["kind"] == "sse"]
        assert any("event: error" in chunk for chunk in chunks) == (ending == "failure")

    try:
        asyncio.run(scenario())
    finally:
        trace.write()


@pytest.mark.parametrize("boundary", [
    "late_append", "drained_compensation", "cancel_compensation_awaiter",
    "cancel_append_awaiter", "cancel_drained_compensation", "cancel_late_append",
])
def test_finalization_write_keeps_owner_until_worker_finishes(pg_engine, actors, monkeypatch, boundary):
    """Real late/partial writes and cancelled awaiters must not open generation."""
    trace = Trace(boundary)
    primary_held, primary_release = threading.Event(), threading.Event()
    final_held, final_release = threading.Event(), threading.Event()
    final_committed = threading.Event()
    primary_once = final_once = False
    append = "append" in boundary
    cancel_workflow = boundary == "cancel_drained_compensation"
    cancel_primary = boundary == "cancel_late_append"
    cancel_awaiter = boundary in {"cancel_compensation_awaiter", "cancel_append_awaiter"}
    workflow_waiting = b_called = b_release = None
    steering = f"Exactly once steering: {boundary}"

    def after_commit(session):
        names = {frame.function for frame in inspect.stack()}
        expected = "_append_user_messages_sync" if append else "_save_partial_history_sync"
        if final_once and expected in names:
            trace.record("finalization_commit_finished")
            final_committed.set()

    def before_sql(connection, cursor, statement, parameters, context, executemany):
        nonlocal primary_once, final_once
        frames = [{"file": f.filename, "line": f.lineno, "function": f.function}
                  for f in inspect.stack() if "/agent/" in f.filename]
        names = {f["function"] for f in frames}
        trace.record("sql", sql=statement, frames=frames,
                     backend_pid=connection.connection.driver_connection.info.backend_pid)
        dml = statement.lstrip().upper().startswith(("INSERT INTO CHAT_MESSAGE", "UPDATE CHAT_SESSION"))
        if not dml:
            return
        if append and "_save_messages_sync" in names and not primary_once:
            primary_once = True
            trace.record("primary_dml_held", frames=frames, sql=statement)
            primary_held.set()
            assert primary_release.wait(30), "fixture deadline: primary history release"
        expected = "_append_user_messages_sync" if append else "_save_partial_history_sync"
        if expected in names and not final_once:
            final_once = True
            trace.record("finalization_dml_held", frames=frames, sql=statement)
            final_held.set()
            assert final_release.wait(30), "fixture deadline: final history release"
            trace.record("finalization_dml_released")

    async def workflow(state, **kwargs):
        trace.record("workflow_entry", run=state["router_message"], messages=state["messages"])
        if state["router_message"] == "A boundary request":
            if not append:
                queue = await get_steering_queue_for_user_async(actors[2], actors[0])
                await queue.add(steering)
                trace.record("steering_queued", text=steering)
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "A boundary output"})
            if cancel_workflow:
                workflow_waiting.set()
                await asyncio.Event().wait()
            if not append:
                raise RuntimeError("synthetic provider failure with queued steering")
        else:
            if state["router_message"] == "B boundary request":
                b_called.set()
                await b_release.wait()
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Later successful output"})

    monkeypatch.setattr(service_module, "run_writing_workflow_streaming", workflow)
    event.listen(pg_engine, "before_cursor_execute", before_sql)
    event.listen(Session, "after_commit", after_commit)

    async def scenario():
        nonlocal workflow_waiting, b_called, b_release
        workflow_waiting, b_called, b_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        service = AgentService()
        a = asyncio.create_task(consume(service, pg_engine, actors, "A boundary request", trace))
        b = waiter = None
        try:
            if append:
                assert await asyncio.to_thread(primary_held.wait, 20), "fixture: primary write not reached"
                queue = await get_steering_queue_for_user_async(actors[2], actors[0])
                await queue.add(steering)
                trace.record("steering_queued_after_presave_drain", text=steering)
                if cancel_primary:
                    a.cancel()
                primary_release.set()
            if cancel_workflow:
                await asyncio.wait_for(workflow_waiting.wait(), 20)
                a.cancel()
            assert await asyncio.to_thread(final_held.wait, 20), "fixture: final write not reached"
            if cancel_awaiter:
                a.cancel()
                delivered = asyncio.Event()
                asyncio.get_running_loop().call_soon(delivered.set)
                await delivered.wait()
                trace.record("awaiter_cancellation_requested")
            active = await has_active_runs_async(actors[2])
            owner = await owner_present(actors[2], actors[0])
            count, held_rows = history(pg_engine, actors[2])
            heartbeat_alive = any(t.get_coro().__name__ == "_run_heartbeat_loop" and not t.done()
                                  for t in asyncio.all_tasks())
            trace.record("held_observation", active=active, owner_present=owner,
                         heartbeat_alive=heartbeat_alive, counter=count, messages=held_rows)
            b = asyncio.create_task(consume(service, pg_engine, actors, "B boundary request", trace))
            waiter = asyncio.create_task(b_called.wait())
            done, _ = await asyncio.wait({b, waiter}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
            assert done, "fixture: no actual B boundary"
            error = b.exception() if b.done() and not b.cancelled() else None
            busy = isinstance(error, SteeringSessionBusyError)
            a_finished_while_write_held = a.done()
            trace.record("b_observation", rejected_busy=busy, workflow_called=b_called.is_set(),
                         a_done=a_finished_while_write_held, error=repr(error))
            if b_called.is_set():
                entry = next(r for r in trace.rows if r["kind"] == "workflow_entry"
                             and r["run"] == "B boundary request")
                assert [m["content"] for m in entry["messages"]] == [
                    *[m["content"] for m in held_rows], "B boundary request",
                ]
        finally:
            primary_release.set()
            final_release.set()
            if waiter:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            results = await asyncio.gather(a, return_exceptions=True)
            background = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                          and t.get_coro().__name__ in {
                              "_drain_then_save_partial_history", "_cleanup_after_cancellation_save"}]
            if background:
                results.extend(await asyncio.gather(*background, return_exceptions=True))
            # A cancelled awaiter may finish before its uncancellable SQL worker.
            # Observe the real commit before permitting B to write in the RED source.
            worker_finished = await asyncio.to_thread(final_committed.wait, 20)
            b_release.set()
            if b:
                results.extend(await asyncio.gather(b, return_exceptions=True))
            trace.record("consumers_finished", results=[repr(r) for r in results])
            assert worker_finished, "fixture: actual finalization commit not observed"
        count, rows = history(pg_engine, actors[2])
        assert count == len(rows)
        assert sum(r["role"] == "user" and r["content"] == steering for r in rows) == 1
        assert {"role": "assistant", "content": "A boundary output"} in rows
        assert not await has_active_runs_async(actors[2])
        assert not await owner_present(actors[2], actors[0])
        # Actual later claim/history sees finalized output and steering exactly once.
        await consume(service, pg_engine, actors, "C followup request", trace)
        followup = next(r for r in trace.rows if r["kind"] == "workflow_entry"
                        and r["run"] == "C followup request")
        contents = [m["content"] for m in followup["messages"]]
        assert contents.count(steering) == 1 and contents.count("A boundary output") == 1
        count, rows = history(pg_engine, actors[2])
        assert count == len(rows) == (7 if busy else 9)
        assert not await has_active_runs_async(actors[2])
        assert not await owner_present(actors[2], actors[0])
        heartbeat_remaining = [t for t in asyncio.all_tasks()
                               if t.get_coro().__name__ == "_run_heartbeat_loop" and not t.done()]
        assert not heartbeat_remaining
        trace.record("final_observation", counter=count, messages=rows, heartbeat_tasks=0,
                     active=False, owner_present=False)
        assert active and owner and heartbeat_alive and busy and not b_called.is_set(), (
            f"finalization opened generation at {boundary}: owner={owner}, active={active}, "
            f"heartbeat={heartbeat_alive}, busy={busy}, B_called={b_called.is_set()}"
        )
        if cancel_awaiter:
            assert not a_finished_while_write_held, "cancelled awaiter released before worker completion"

    try:
        asyncio.run(scenario())
    finally:
        primary_release.set()
        final_release.set()
        event.remove(pg_engine, "before_cursor_execute", before_sql)
        event.remove(Session, "after_commit", after_commit)
        trace.record("listener_removed")
        trace.write()


@pytest.mark.parametrize("failure", ["drain", "compensation", "late_append"])
def test_finalization_failure_is_best_effort_and_releases(pg_engine, actors, monkeypatch, caplog, failure):
    trace = Trace(f"injected-{failure}")
    primary_held, primary_release = threading.Event(), threading.Event()
    injected = primary_once = False

    def before_sql(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected, primary_once
        names = {frame.function for frame in inspect.stack()}
        dml = statement.lstrip().upper().startswith(("INSERT INTO CHAT_MESSAGE", "UPDATE CHAT_SESSION"))
        if dml and failure == "late_append" and "_save_messages_sync" in names and not primary_once:
            primary_once = True
            primary_held.set()
            assert primary_release.wait(30), "fixture: primary release"
        expected = "_append_user_messages_sync" if failure == "late_append" else "_save_partial_history_sync"
        if dml and failure != "drain" and expected in names and not injected:
            injected = True
            trace.record("injected_sql_failure", sql=statement, source_function=expected)
            raise RuntimeError(f"injected {failure} SQL failure")

    async def workflow(state, **kwargs):
        nonlocal injected
        if failure == "drain":
            queue = await get_steering_queue_for_user_async(actors[2], actors[0])
            original = queue.get_pending

            async def fail_drain_once():
                nonlocal injected
                if not injected:
                    injected = True
                    trace.record("injected_drain_failure")
                    raise RuntimeError("injected final drain failure")
                return await original()

            monkeypatch.setattr(queue, "get_pending", fail_drain_once)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Issued failure-control output"})
        if failure != "late_append":
            raise RuntimeError("synthetic provider failure for cleanup control")

    monkeypatch.setattr(service_module, "run_writing_workflow_streaming", workflow)
    event.listen(pg_engine, "before_cursor_execute", before_sql)

    async def scenario():
        a = asyncio.create_task(consume(AgentService(), pg_engine, actors, "Failure control", trace))
        try:
            if failure == "late_append":
                assert await asyncio.to_thread(primary_held.wait, 20), "fixture: primary not reached"
                queue = await get_steering_queue_for_user_async(actors[2], actors[0])
                await queue.add("Late failure-control steering")
                primary_release.set()
            await a
        finally:
            primary_release.set()
            await asyncio.gather(a, return_exceptions=True)
        assert injected
        assert not await has_active_runs_async(actors[2])
        assert not await owner_present(actors[2], actors[0])
        assert not any(t.get_coro().__name__ == "_run_heartbeat_loop" and not t.done()
                       for t in asyncio.all_tasks())
        count, rows = history(pg_engine, actors[2])
        assert count == len(rows) == (2 if failure == "compensation" else 4)
        expected_log = {
            "drain": "Failed to drain steering queue before cleanup",
            "compensation": "Failed to persist partial history after stream failure",
            "late_append": "Failed to persist late steering messages after history save",
        }[failure]
        assert any(expected_log in record.getMessage() for record in caplog.records)
        trace.record("final_observation", counter=count, messages=rows, active=False,
                     owner_present=False, heartbeat_tasks=0, expected_log=expected_log)

    try:
        asyncio.run(scenario())
    finally:
        primary_release.set()
        event.remove(pg_engine, "before_cursor_execute", before_sql)
        trace.record("listener_removed")
        trace.write()


def test_late_steering_is_handed_back_to_real_legacy_holder(pg_engine, actors, monkeypatch):
    from agent.core.steering import cleanup_steering_queue_async, create_steering_queue_async

    trace = Trace("legacy-handback")
    held, release = threading.Event(), threading.Event()
    once = False
    legacy_id = uuid.uuid4().hex

    def before_sql(connection, cursor, statement, parameters, context, executemany):
        nonlocal once
        names = {frame.function for frame in inspect.stack()}
        if "_save_messages_sync" in names and not once and statement.startswith("INSERT INTO chat_message"):
            once = True
            held.set()
            assert release.wait(30), "fixture: legacy control primary release"

    async def workflow(state, **kwargs):
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Legacy control output"})

    monkeypatch.setattr(service_module, "run_writing_workflow_streaming", workflow)
    event.listen(pg_engine, "before_cursor_execute", before_sql)

    async def scenario():
        a = asyncio.create_task(consume(AgentService(), pg_engine, actors, "Legacy handback", trace))
        try:
            assert await asyncio.to_thread(held.wait, 20), "fixture: legacy primary not reached"
            queue = await create_steering_queue_async(actors[2], actors[0], run_id=legacy_id)
            await queue.add("Steering belongs to still-active legacy holder")
            release.set()
            await a
            pending = await queue.get_pending()
            assert [message.content for message in pending] == ["Steering belongs to still-active legacy holder"]
            assert await has_active_runs_async(actors[2])
            count, rows = history(pg_engine, actors[2])
            assert count == len(rows) == 4
            assert all(r["content"] != "Steering belongs to still-active legacy holder" for r in rows)
            trace.record("legacy_observation", pending=[m.content for m in pending], counter=count,
                         messages=rows, legacy_active=True)
        finally:
            release.set()
            await asyncio.gather(a, return_exceptions=True)
            await cleanup_steering_queue_async(actors[2], run_id=legacy_id)
        assert not await has_active_runs_async(actors[2])
        assert not await owner_present(actors[2], actors[0])
        assert not any(t.get_coro().__name__ == "_run_heartbeat_loop" and not t.done()
                       for t in asyncio.all_tasks())

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        event.remove(pg_engine, "before_cursor_execute", before_sql)
        trace.record("listener_removed")
        trace.write()


def test_real_primary_history_failure_emits_error_without_done(pg_engine, actors, monkeypatch):
    """Actual failed history SQL, unlike an unused save mock, exercises the policy."""
    trace = Trace("primary-history-failure")
    injected = False

    def before_sql(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected
        names = {frame.function for frame in inspect.stack()}
        if not injected and "_save_messages_sync" in names and statement.startswith("INSERT INTO chat_message"):
            injected = True
            trace.record("injected_primary_history_failure", sql=statement)
            raise RuntimeError("injected primary history SQL failure")

    async def workflow(state, **kwargs):
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Issued before history failure"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    monkeypatch.setattr(service_module, "run_writing_workflow_streaming", workflow)
    event.listen(pg_engine, "before_cursor_execute", before_sql)

    async def scenario():
        await consume(AgentService(), pg_engine, actors, "Primary history failure", trace)
        chunks = [r["chunk"] for r in trace.rows if r["kind"] == "sse"]
        assert injected and any("event: error" in chunk for chunk in chunks)
        assert not any("event: done" in chunk for chunk in chunks)
        count, rows = history(pg_engine, actors[2])
        assert count == len(rows) == 4
        assert {"role": "assistant", "content": "Issued before history failure"} in rows
        assert not await has_active_runs_async(actors[2])
        assert not await owner_present(actors[2], actors[0])
        trace.record("final_observation", counter=count, messages=rows, error_sse=True,
                     done_sse=False, active=False, owner_present=False)

    try:
        asyncio.run(scenario())
    finally:
        event.remove(pg_engine, "before_cursor_execute", before_sql)
        trace.record("listener_removed")
        trace.write()
