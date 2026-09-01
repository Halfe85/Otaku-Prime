# -*- coding: utf-8 -*-
from __future__ import annotations

import socket
import unittest
from urllib.error import URLError

from resources.lib.services.artwork_diagnostics import (
    ArtworkDiagnosticProbe,
    _failure_kind,
    _safe_url,
)


class ArtworkDiagnosticTests(unittest.TestCase):
    def test_stopped_probe_rejects_new_diagnostics(self):
        probe=ArtworkDiagnosticProbe(); probe.stop()
        self.assertFalse(probe.schedule("https://assets.fanart.tv/fanart/a.jpg"))

    def test_safe_url_removes_query_and_fragment(self):
        safe,parsed=_safe_url("https://assets.fanart.tv/fanart/a.jpg?token=x#fragment")
        self.assertEqual("https://assets.fanart.tv/fanart/a.jpg",safe)
        self.assertEqual("assets.fanart.tv",parsed.hostname)

    def test_connection_refusal_is_classified(self):
        self.assertEqual(
            "connection_refused",
            _failure_kind(ConnectionRefusedError(111,"Connection refused")),
        )

    def test_probe_logs_dns_addresses_and_connection_refusal(self):
        def resolver(host,port,type=None):
            return [
                (socket.AF_INET,socket.SOCK_STREAM,6,"",("158.69.209.125",port)),
                (socket.AF_INET,socket.SOCK_STREAM,6,"",("158.69.210.98",port)),
            ]

        def opener(request,timeout=None):
            raise URLError(ConnectionRefusedError(111,"Connection refused"))

        probe=ArtworkDiagnosticProbe(resolver=resolver,opener=opener)
        with self.assertLogs(
            "otaku_prime.services-artwork_diagnostics",level="INFO"
        ) as logs:
            probe._diagnose(
                "https://assets.fanart.tv/fanart/example.jpg","poster","Example"
            )
        message="\n".join(logs.output)
        self.assertIn("dns=158.69.209.125,158.69.210.98",message)
        self.assertIn("result=connection_refused",message)
        self.assertIn("error=[Errno 111] Connection refused",message)


if __name__ == "__main__":
    unittest.main()
