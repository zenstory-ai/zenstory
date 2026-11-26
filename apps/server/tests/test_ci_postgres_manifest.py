"""CI manifest contract: no PostgreSQL test file may be silently dropped from CI."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_ROOT = REPO_ROOT / "apps" / "server"
TESTS_ROOT = SERVER_ROOT / "tests"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Unit tests that only mock ZENSTORY_TEST_POSTGRES_URL or inspect the CI manifest itself.
_NON_PG_TEST_EXCLUDES = frozenset(
    {
        "tests/conftest.py",
        "tests/test_ci_postgres_manifest.py",
        "tests/test_core/test_postgres_module_isolation.py",
    }
)


def _extract_ci_postgres_test_paths() -> list[str]:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["backend-integration"]["steps"]
    pg_step = next(
        (step for step in steps if step.get("name") == "Run PostgreSQL regressions serially"),
        None,
    )
    assert pg_step is not None, "Missing 'Run PostgreSQL regressions serially' step in ci.yml"
    run_script = pg_step["run"]
    return re.findall(r"(tests/[A-Za-z0-9_./-]+\.py)", run_script)


@pytest.mark.unit
def test_every_postgres_test_file_is_registered_in_ci():
    """Every tests/**/test_*_postgres.py file must appear in backend-integration PG step."""
    registered = _extract_ci_postgres_test_paths()
    assert registered, "CI PostgreSQL regression step has no registered test files"

    duplicates = sorted({path for path in registered if registered.count(path) > 1})
    assert duplicates == [], f"Duplicate PG test entries in ci.yml: {duplicates}"

    registered_set = set(registered)

    # 1. All test_*_postgres.py files on disk must be registered.
    on_disk_pg_named = {
        path.relative_to(SERVER_ROOT).as_posix()
        for path in TESTS_ROOT.rglob("test_*_postgres.py")
    }
    missing_named = sorted(on_disk_pg_named - registered_set)
    assert missing_named == [], (
        "PostgreSQL test files missing from .github/workflows/ci.yml "
        f"'Run PostgreSQL regressions serially': {missing_named}"
    )

    # 2. All test files referencing ZENSTORY_TEST_POSTGRES_URL (e.g. timezone/startup tests)
    #    must also be registered.
    on_disk_env_gated = {
        path.relative_to(SERVER_ROOT).as_posix()
        for path in TESTS_ROOT.rglob("test_*.py")
        if "ZENSTORY_TEST_POSTGRES_URL" in path.read_text(encoding="utf-8")
    } - _NON_PG_TEST_EXCLUDES
    missing_env_gated = sorted(on_disk_env_gated - registered_set)
    assert missing_env_gated == [], (
        "ZENSTORY_TEST_POSTGRES_URL test files missing from .github/workflows/ci.yml "
        f"'Run PostgreSQL regressions serially': {missing_env_gated}"
    )

    # 3. Every registered path must exist on disk (catch stale CI paths).
    missing_on_disk = sorted(
        path for path in registered_set if not (SERVER_ROOT / path).is_file()
    )
    assert missing_on_disk == [], (
        "Stale test paths in .github/workflows/ci.yml 'Run PostgreSQL regressions serially': "
        f"{missing_on_disk}"
    )
