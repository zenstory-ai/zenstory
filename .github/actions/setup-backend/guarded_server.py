"""Run the disposable E2E backend without allowing external network calls.

Page routes provide deterministic model/SSE responses. An accidentally unmocked
request must fail locally, not send even a placeholder key to a live provider.
This entrypoint is test infrastructure; production still uses main.py.
"""

import ipaddress
import os
import socket
import sys
from pathlib import Path


def is_loopback(host: str | bytes | None) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host in {None, "", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install_egress_guard() -> None:
    # A localhost HTTP proxy could forward requests beyond the socket guard.
    # Remove inherited proxies before importing the app or constructing SDK clients.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)

    original_dns = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def require_loopback(host: str | bytes | None) -> None:
        if not is_loopback(host):
            raise PermissionError("E2E backend forbids external network connections")

    def local_dns(host, *args, **kwargs):
        require_loopback(host)
        return original_dns(host, *args, **kwargs)

    def local_connect(connection, address):
        if isinstance(address, tuple):
            require_loopback(address[0])
        return original_connect(connection, address)

    def local_connect_ex(connection, address):
        if isinstance(address, tuple):
            require_loopback(address[0])
        return original_connect_ex(connection, address)

    socket.getaddrinfo = local_dns
    socket.socket.connect = local_connect
    socket.socket.connect_ex = local_connect_ex


def main() -> None:
    install_egress_guard()
    sys.path.insert(0, str(Path.cwd()))
    import uvicorn  # noqa: PLC0415

    # Use asyncio's Python DNS/socket path so the guard also covers async SDKs.
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), loop="asyncio")


if __name__ == "__main__":
    main()
