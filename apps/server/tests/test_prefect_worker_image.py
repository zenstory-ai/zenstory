from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
PREFECT_VERSION = "3.6.28"
FASTAPI_VERSION = "0.123.7"


def test_prefect_runtime_versions_are_aligned():
    requirements = (SERVER_ROOT / "requirements.txt").read_text()
    server_dockerfile = (SERVER_ROOT / "docker" / "Dockerfile.prefect-server").read_text()
    worker_dockerfile = (SERVER_ROOT / "docker" / "Dockerfile.prefect-worker").read_text()
    prefect_yaml = (SERVER_ROOT / "prefect.yaml").read_text()

    prefect_requirements = [
        line.strip() for line in requirements.splitlines() if line.strip().startswith("prefect")
    ]
    fastapi_requirements = [
        line.strip() for line in requirements.splitlines() if line.strip().startswith("fastapi")
    ]

    assert prefect_requirements == [f"prefect=={PREFECT_VERSION}"]
    assert fastapi_requirements == [f"fastapi=={FASTAPI_VERSION}"]
    assert f"prefect=={PREFECT_VERSION}" in server_dockerfile
    assert f'"fastapi=={FASTAPI_VERSION}"' in server_dockerfile
    assert f"prefect-version: {PREFECT_VERSION}" in prefect_yaml
    assert any(line.strip().startswith("importlib-metadata") for line in requirements.splitlines())
    assert "pip install --no-cache-dir prefect" not in worker_dockerfile


def test_prefect_worker_checks_the_cli_during_image_build():
    worker_dockerfile = (SERVER_ROOT / "docker" / "Dockerfile.prefect-worker").read_text()

    assert "prefect version" in worker_dockerfile


def test_prefect_worker_fails_when_server_or_deployment_registration_fails():
    start_script = (SERVER_ROOT / "docker" / "start-prefect-worker.sh").read_text()

    assert 'if [ "$server_ready" != "true" ]' in start_script
    assert 'echo "ERROR: Prefect Server did not become ready within 150 seconds."' in start_script
    assert 'prefect deploy --all\n' in start_script
    assert 'prefect deploy --all ||' not in start_script
