"""Assert that every production deployment was registered with Prefect Server."""

import asyncio

from prefect.client.orchestration import get_client


EXPECTED_DEPLOYMENTS = {
    "chapter_extraction",
    "novel_ingestion_v2",
    "novel_ingestion_v3",
    "relationship_extraction",
    "story_aggregation",
}


async def _check_deployments() -> None:
    async with get_client() as client:
        deployments = await client.read_deployments()

    registered = {deployment.name for deployment in deployments}
    missing = EXPECTED_DEPLOYMENTS.difference(registered)
    if missing:
        raise AssertionError(f"Missing Prefect deployments: {sorted(missing)}")

    print(f"Registered Prefect deployments: {sorted(EXPECTED_DEPLOYMENTS)}")


if __name__ == "__main__":
    asyncio.run(_check_deployments())
