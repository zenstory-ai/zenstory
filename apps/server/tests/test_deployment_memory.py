import os
import subprocess
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]


def _run_isolated_import(source: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVER_ROOT)
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=SERVER_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_production_server_defaults_to_one_worker():
    railway_config = (SERVER_ROOT / "railway.toml").read_text()
    dockerfile = (SERVER_ROOT / "Dockerfile").read_text()

    assert "${WEB_CONCURRENCY:-1}" in railway_config
    assert "${WEB_CONCURRENCY:-1}" in dockerfile
    assert "${WEB_CONCURRENCY:-3}" not in railway_config
    assert "${WEB_CONCURRENCY:-3}" not in dockerfile


def test_auth_import_does_not_load_vector_stack():
    result = _run_isolated_import(
        """
import sys
import services.auth

unexpected = [
    name
    for name in (
        "services.infra.vector_search_service",
        "services.llama_index",
        "chromadb",
        "llama_index.core",
    )
    if name in sys.modules
]
if unexpected:
    raise SystemExit(f"unexpected eager imports: {unexpected}")
"""
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_legacy_llama_index_imports_remain_available():
    result = _run_isolated_import(
        """
from services import LlamaIndexService as package_export
from services.llama_index import (
    LlamaIndexService,
    get_llama_index_service,
    schedule_index_delete,
    schedule_index_upsert,
)

assert package_export is LlamaIndexService
assert callable(get_llama_index_service)
assert callable(schedule_index_delete)
assert callable(schedule_index_upsert)
"""
    )

    assert result.returncode == 0, result.stderr or result.stdout
