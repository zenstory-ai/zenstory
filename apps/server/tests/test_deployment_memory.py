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

    assert 'startCommand = "python scripts/start_api.py"' in railway_config
    assert 'CMD ["python", "scripts/start_api.py"]' in dockerfile
    result = _run_isolated_import(
        """
from scripts.start_api import main
commands = []
main(environment={}, execvp=lambda executable, command: commands.append(command))
assert len(commands) == 1
assert commands[0][commands[0].index('--workers') + 1] == '1'
"""
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_auth_import_does_not_load_vector_stack():
    result = _run_isolated_import(
        """
import sys
import services.core.auth_service

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


def test_keyword_search_import_does_not_load_vector_stack():
    result = _run_isolated_import(
        """
import sys
from services.infra.file_search_service import FileSearchService
assert callable(FileSearchService.search)
assert not any(name.startswith(("llama_index", "chromadb")) for name in sys.modules)
"""
    )
    assert result.returncode == 0, result.stderr or result.stdout
