import asyncio
import importlib.util
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("guarded_server", Path(__file__).with_name("guarded_server.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class EgressGuardTests(unittest.TestCase):
    def test_only_loopback_addresses_are_allowed(self):
        for host in ["localhost", "127.0.0.1", "127.0.0.2", "::1", b"127.0.0.1"]:
            with self.subTest(host=host):
                self.assertTrue(guard.is_loopback(host))
        for host in ["api.openai.com", "api.anthropic.com", "8.8.8.8", "10.0.0.1", "::ffff:8.8.8.8"]:
            with self.subTest(host=host):
                self.assertFalse(guard.is_loopback(host))

    def test_loopback_proxy_environment_cannot_tunnel_external_requests(self):
        proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        with patch.dict(os.environ, dict.fromkeys(proxy_keys, "http://127.0.0.1:7890"), clear=True):
            with patch.object(socket, "getaddrinfo") as dns, patch.object(socket.socket, "connect"), patch.object(socket.socket, "connect_ex"):
                guard.install_egress_guard()
                for key in proxy_keys:
                    self.assertFalse(key in os.environ, f"{key} must not remain configured")
                with self.assertRaises(PermissionError):
                    socket.getaddrinfo("api.anthropic.com", 443)
                dns.assert_not_called()

    def test_external_dns_is_rejected_before_resolving(self):
        with patch.object(socket, "getaddrinfo") as dns, patch.object(socket.socket, "connect"), patch.object(socket.socket, "connect_ex"):
            guard.install_egress_guard()
            with self.assertRaises(PermissionError):
                socket.getaddrinfo("api.openai.com", 443)
            dns.assert_not_called()
            socket.getaddrinfo("127.0.0.1", 5433)
            dns.assert_called_once_with("127.0.0.1", 5433)

    def test_direct_ip_connections_cannot_bypass_dns_guard(self):
        with patch.object(socket, "getaddrinfo"), patch.object(socket.socket, "connect") as connect, patch.object(socket.socket, "connect_ex") as connect_ex:
            guard.install_egress_guard()
            with socket.socket() as connection:
                with self.assertRaises(PermissionError):
                    connection.connect(("8.8.8.8", 443))
                with self.assertRaises(PermissionError):
                    connection.connect_ex(("8.8.8.8", 443))
                connect.assert_not_called()
                connect_ex.assert_not_called()
                connection.connect(("127.0.0.1", 8000))
                connect.assert_called_once()

    def test_asyncio_connections_are_blocked_before_external_io(self):
        with patch.object(socket, "getaddrinfo") as dns, patch.object(socket.socket, "connect") as connect, patch.object(socket.socket, "connect_ex") as connect_ex:
            guard.install_egress_guard()
            for host in ("api.openai.com", "203.0.113.1"):
                with self.subTest(host=host), self.assertRaises(PermissionError):
                    asyncio.run(asyncio.open_connection(host, 443))
            dns.assert_not_called()
            connect.assert_not_called()
            connect_ex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
