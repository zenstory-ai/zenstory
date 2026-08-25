"""Small runtime contract for the Prefect server/client/flow boundary."""

from inspect import signature

from prefect import flow, task
from prefect.deployments import run_deployment


@task(retries=1)
def _double(value: int) -> int:
    return value * 2


@flow(name="zenstory-prefect-contract")
def _contract_flow() -> int:
    return _double.submit(21).result()


def main() -> None:
    deployment_parameters = signature(run_deployment).parameters
    required_parameters = {"name", "parameters", "timeout"}
    missing = required_parameters.difference(deployment_parameters)
    if missing:
        raise AssertionError(f"run_deployment lost required parameters: {sorted(missing)}")

    result = _contract_flow()
    if result != 42:
        raise AssertionError(f"Unexpected Prefect flow result: {result!r}")

    print("Prefect compatibility contract passed.")


if __name__ == "__main__":
    main()
