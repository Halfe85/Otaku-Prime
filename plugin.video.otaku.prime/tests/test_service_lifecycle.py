# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from resources.lib.service_lifecycle import ServiceInstanceLock, stop_service_components


class _Server:
    def __init__(self, events):
        self.events = events

    def shutdown(self):
        self.events.append("web-shutdown")

    def server_close(self):
        self.events.append("web-close")


class _Thread:
    def __init__(self, events):
        self.events = events

    def join(self, timeout=None):
        self.events.append(("web-join", timeout))


class _Watchdog:
    def __init__(self, events):
        self.events = events

    def stop(self, timeout=None):
        self.events.append(("worker-stop", timeout))


class ServiceLifecycleTests(unittest.TestCase):
    def test_service_instance_lock_rejects_overlap_and_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "service.lock")
            first = ServiceInstanceLock(path)
            second = ServiceInstanceLock(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_web_socket_is_released_before_worker_wait(self):
        events = []

        stop_service_components(
            _Server(events),
            _Thread(events),
            _Watchdog(events),
            web_join_timeout=1,
            worker_timeout=35,
        )

        self.assertEqual(
            events,
            [
                "web-shutdown",
                "web-close",
                ("web-join", 1),
                ("worker-stop", 35),
            ],
        )


if __name__ == "__main__":
    unittest.main()
