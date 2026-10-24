"""Small runtime contract for the Prefect server/client/flow boundary."""

import asyncio
from datetime import UTC, datetime, timedelta
from inspect import signature
from uuid import UUID, uuid4

import sqlalchemy as sa
from prefect import flow, task
from prefect.client.orchestration import get_client
from prefect.deployments import run_deployment
from prefect.server.database import provide_database_interface
from prefect.server.events.schemas.events import ReceivedEvent
from prefect.server.schemas.states import StateType
from prefect.server.services.task_run_recorder import record_bulk_task_run_events


@task(retries=1)
def _double(value: int) -> int:
    return value * 2


@flow(name="zenstory-prefect-contract")
def _contract_flow() -> int:
    result = _double.submit(21).result()
    if result != 42:
        raise AssertionError(f"Unexpected Prefect task result: {result!r}")
    return result


def _task_run_event(
    *,
    flow_run_id: UUID,
    task_run_id: UUID,
    task_key: str,
    dynamic_key: str,
    occurred: datetime,
    state_type: StateType,
) -> ReceivedEvent:
    state_name = state_type.name.title()
    return ReceivedEvent(
        occurred=occurred,
        event=f"prefect.task-run.{state_name}",
        resource={
            "prefect.resource.id": f"prefect.task-run.{task_run_id}",
            "prefect.resource.name": "zenstory-prefect-recorder-contract",
            "prefect.state-message": "",
            "prefect.state-name": state_name,
            "prefect.state-timestamp": occurred.isoformat(),
            "prefect.state-type": state_type.name,
            "prefect.orchestration": "client",
        },
        related=[
            {
                "prefect.resource.id": f"prefect.flow-run.{flow_run_id}",
                "prefect.resource.role": "flow-run",
            }
        ],
        payload={
            "intended": {"from": "PENDING", "to": state_type.name},
            "validated_state": {
                "type": state_type.name,
                "name": state_name,
                "message": "",
            },
            "task_run": {
                "name": "zenstory-prefect-recorder-contract",
                "task_key": task_key,
                "dynamic_key": dynamic_key,
            },
        },
        id=uuid4(),
        received=occurred,
    )


async def _assert_canonical_task_run(
    *,
    flow_run_id: UUID,
    task_key: str,
    dynamic_key: str,
    canonical_task_run_id: UUID,
    discarded_task_run_id: UUID,
    state_ids: set[UUID],
    current_state_id: UUID,
) -> None:
    db = provide_database_interface()
    async with db.session_context() as session:
        task_runs = (
            await session.execute(
                sa.select(db.TaskRun).where(
                    db.TaskRun.flow_run_id == flow_run_id,
                    db.TaskRun.task_key == task_key,
                    db.TaskRun.dynamic_key == dynamic_key,
                )
            )
        ).scalars().all()
        if len(task_runs) != 1:
            raise AssertionError(
                "Natural-key recorder contract expected exactly one task run, "
                f"found {len(task_runs)}"
            )

        task_run = task_runs[0]
        if task_run.id != canonical_task_run_id:
            raise AssertionError(
                "Natural-key recorder contract selected the wrong canonical id: "
                f"expected {canonical_task_run_id}, found {task_run.id}"
            )
        if task_run.id == discarded_task_run_id:
            raise AssertionError("Recorder persisted the discarded task-run id")
        if task_run.state_id != current_state_id:
            raise AssertionError(
                "Canonical task run does not point to the latest recorded state"
            )

        states = (
            await session.execute(
                sa.select(db.TaskRunState).where(db.TaskRunState.id.in_(state_ids))
            )
        ).scalars().all()
        if {state.id for state in states} != state_ids:
            raise AssertionError("Recorder did not persist every synthetic state")
        if {state.task_run_id for state in states} != {canonical_task_run_id}:
            raise AssertionError("A state foreign key points at a discarded task-run id")
        if {state.state_details.task_run_id for state in states} != {
            canonical_task_run_id
        }:
            raise AssertionError(
                "A state_details task_run_id points at a discarded task-run id"
            )


async def _exercise_task_run_recorder() -> None:
    async with get_client() as client:
        flow_run = await client.create_flow_run(
            flow=_contract_flow,
            name=f"zenstory-prefect-recorder-contract-{uuid4()}",
        )

    base_time = datetime.now(UTC)
    task_key = f"zenstory-prefect-recorder-contract-{uuid4()}"

    sequential_ids = [uuid4(), uuid4()]
    sequential_events = [
        _task_run_event(
            flow_run_id=flow_run.id,
            task_run_id=sequential_ids[0],
            task_key=task_key,
            dynamic_key="sequential",
            occurred=base_time,
            state_type=StateType.PENDING,
        ),
        _task_run_event(
            flow_run_id=flow_run.id,
            task_run_id=sequential_ids[1],
            task_key=task_key,
            dynamic_key="sequential",
            occurred=base_time + timedelta(seconds=1),
            state_type=StateType.RUNNING,
        ),
    ]
    await record_bulk_task_run_events([sequential_events[0]])
    await record_bulk_task_run_events([sequential_events[1]])
    await _assert_canonical_task_run(
        flow_run_id=flow_run.id,
        task_key=task_key,
        dynamic_key="sequential",
        canonical_task_run_id=sequential_ids[0],
        discarded_task_run_id=sequential_ids[1],
        state_ids={event.id for event in sequential_events},
        current_state_id=sequential_events[-1].id,
    )

    same_batch_ids = [uuid4(), uuid4()]
    same_batch_events = [
        _task_run_event(
            flow_run_id=flow_run.id,
            task_run_id=same_batch_ids[0],
            task_key=task_key,
            dynamic_key="same-batch",
            occurred=base_time + timedelta(seconds=2),
            state_type=StateType.PENDING,
        ),
        _task_run_event(
            flow_run_id=flow_run.id,
            task_run_id=same_batch_ids[1],
            task_key=task_key,
            dynamic_key="same-batch",
            occurred=base_time + timedelta(seconds=3),
            state_type=StateType.RUNNING,
        ),
    ]
    await record_bulk_task_run_events(same_batch_events)
    await _assert_canonical_task_run(
        flow_run_id=flow_run.id,
        task_key=task_key,
        dynamic_key="same-batch",
        canonical_task_run_id=same_batch_ids[1],
        discarded_task_run_id=same_batch_ids[0],
        state_ids={event.id for event in same_batch_events},
        current_state_id=same_batch_events[-1].id,
    )


def main() -> None:
    deployment_parameters = signature(run_deployment).parameters
    required_parameters = {"name", "parameters", "timeout"}
    missing = required_parameters.difference(deployment_parameters)
    if missing:
        raise AssertionError(f"run_deployment lost required parameters: {sorted(missing)}")

    result = _contract_flow()
    if result != 42:
        raise AssertionError(f"Unexpected Prefect flow result: {result!r}")

    asyncio.run(_exercise_task_run_recorder())

    print("Prefect compatibility contract passed.")


if __name__ == "__main__":
    main()
