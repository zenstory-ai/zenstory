"""Real owned Unix-socket Redis proves one-time consumption and failed RPC behavior."""
import asyncio
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import redis

from services.features import verification_service as service
from services.infra import redis_client as storage


@pytest.fixture(scope="module")
def owned_redis(record_testsuite_property):
    # CI already provisions Redis; lease only unique test keys, never its server.
    url = os.getenv("ZENSTORY_TEST_REDIS_URL")
    if url:
        assert urlparse(url).hostname in {"localhost", "127.0.0.1", "::1"}
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        assert client.ping()
        try:
            yield client
        finally:
            client.close()
            record_testsuite_property("test_redis_cleanup", "client closed; only own UUID keys deleted; no flush/shutdown")
        return
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("local Redis executable unavailable; run Redis integration locally")
    with tempfile.TemporaryDirectory(prefix="zv-", dir="/tmp") as directory:
        socket = Path(directory) / "owned.sock"
        log = Path(directory) / "redis.log"
        with log.open("w") as output:
            process = subprocess.Popen([executable, "--port", "0", "--unixsocket", str(socket),
                                        "--save", "", "--appendonly", "no"], stdout=output, stderr=subprocess.STDOUT)
        # Redis 8 enables RESP3 maintenance notifications by default; Unix sockets
        # have no host for that feature. This owned local fixture uses RESP2.
        client = redis.Redis(unix_socket_path=str(socket), decode_responses=True, protocol=2,
                             socket_connect_timeout=0.2, socket_timeout=2)
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    assert client.ping()
                    break
                except redis.ConnectionError:
                    assert process.poll() is None and time.monotonic() < deadline, log.read_text()
                    time.sleep(0.01)  # Startup readiness only; race uses a barrier.
            record_testsuite_property("owned_redis", f"pid={process.pid};unix={socket};TCP=disabled;persistence=disabled")
            yield client
        finally:
            try:
                if process.poll() is None:
                    client.shutdown(nosave=True)
            finally:
                client.close()
                process.wait(timeout=5)
                record_testsuite_property("owned_redis_cleanup", f"pid={process.pid};exit={process.returncode};server exited; own directory cleanup follows")

    assert not Path(directory).exists()
    record_testsuite_property("owned_redis_directory_removed", directory)


@pytest.fixture
def code_store(owned_redis, monkeypatch):
    email = f"{uuid4().hex}@example.com"
    monkeypatch.setattr(storage, "get_redis_client", lambda: owned_redis)
    assert storage.store_verification_code(email, "123456", 300)
    yield email, owned_redis
    for prefix in ("verification", "attempts", "resend_cooldown"):
        owned_redis.delete(f"{prefix}:{email}")


@pytest.mark.asyncio
async def test_only_one_concurrent_consumer_succeeds(code_store, monkeypatch):
    email, client = code_store
    original = service.get_verification_code
    readers = threading.Barrier(2)

    def read_before_consumption(identity):
        result = original(identity)
        assert result == "123456"
        readers.wait(timeout=5)
        return result

    monkeypatch.setattr(service, "get_verification_code", read_before_consumption)
    results = await asyncio.gather(service.verify_code(email, "123456"), service.verify_code(email, "123456"))
    assert client.get(f"verification:{email}") is None
    assert sum(success for success, _error in results) == 1, results


@pytest.mark.asyncio
async def test_consumption_rpc_failure_cannot_verify(code_store, monkeypatch):
    email, client = code_store
    key = f"verification:{email}"
    execute = client.execute_command

    def fail_consume(*args, **kwargs):
        if str(args[0]).upper() in {"DEL", "EVAL"} and key in args:
            raise redis.ConnectionError("owned consumption RPC failure")
        return execute(*args, **kwargs)

    with monkeypatch.context() as rpc:
        rpc.setattr(client, "execute_command", fail_consume)
        success, error = await service.verify_code(email, "123456")
    assert client.get(key) == "123456"
    assert success is False and error is not None


@pytest.mark.asyncio
async def test_current_valid_and_invalid_code_controls(code_store):
    email, client = code_store
    assert (await service.verify_code(email, "wrong"))[0] is False
    assert client.get(f"verification:{email}") == "123456"
    assert storage.get_verification_attempts(email) == 1
    assert await service.verify_code(email, "123456") == (True, None)
    assert client.get(f"verification:{email}") is None
    assert storage.get_verification_attempts(email) == 0
    assert (await service.verify_code(email, "123456"))[0] is False


@pytest.mark.asyncio
async def test_late_old_code_does_not_delete_replacement_or_reset_attempts(code_store, monkeypatch):
    email, client = code_store
    original = service.get_verification_code
    client.setex(f"attempts:{email}", 300, 2)

    def read_then_replace(identity):
        old = original(identity)
        assert old == "123456"
        client.setex(f"verification:{email}", 300, "654321")
        return old

    monkeypatch.setattr(service, "get_verification_code", read_then_replace)
    success, error = await service.verify_code(email, "123456")
    assert client.get(f"verification:{email}") == "654321"
    assert storage.get_verification_attempts(email) == 2
    assert success is False and error is not None


def test_real_storage_helpers_keep_current_contracts(code_store):
    email, client = code_store
    assert storage.get_verification_code(email) == "123456"
    assert storage.delete_verification_code(email) is True
    assert storage.get_verification_code(email) is None
    assert storage.consume_verification_code(email, "123456") is False
    assert storage.store_verification_code(email, "654321", 300) is True
    assert storage.consume_verification_code(email, "wrong") is False
    assert storage.consume_verification_code(email, "654321") is True
    assert storage.set_resend_cooldown(email, 60) is True
    assert storage.check_resend_cooldown(email) is True
    assert storage.delete_resend_cooldown(email) is True
    assert storage.check_resend_cooldown(email) is False
    assert storage.increment_verification_attempts(email, 1) is True
    assert client.ttl(f"attempts:{email}") > 0
    assert storage.increment_verification_attempts(email, 1) is False
    assert storage.get_verification_attempts(email) == 2
    assert storage.reset_verification_attempts(email) is True
    assert storage.get_verification_attempts(email) == 0


@pytest.mark.parametrize("function,args,expected", [
    (storage.store_verification_code, ("123456",), False),
    (storage.get_verification_code, (), None),
    (storage.delete_verification_code, (), False),
    (storage.consume_verification_code, ("123456",), False),
    (storage.check_resend_cooldown, (), False),
    (storage.set_resend_cooldown, (), False),
    (storage.delete_resend_cooldown, (), False),
    (storage.get_verification_attempts, (), 0),
    (storage.increment_verification_attempts, (), False),
    (storage.reset_verification_attempts, (), False),
])
def test_storage_client_failures_keep_current_return_contract(function, args, expected, monkeypatch):
    def unavailable():
        raise redis.ConnectionError("owned client failure")

    monkeypatch.setattr(storage, "get_redis_client", unavailable)
    assert function(f"{uuid4().hex}@example.com", *args) is expected
