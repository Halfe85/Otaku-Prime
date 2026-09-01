# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from resources.lib.service_lifecycle import (
    ServiceInstanceLock,
    initialize_service_stores,
    stop_service_components,
)


class _Server:
    def __init__(self, events):
        self.events = events

    def shutdown(self):
        self.events.append("web-shutdown")

    def server_close(self):
        self.events.append("web-close")


class _Artwork:
    def __init__(self,events): self.events=events
    def stop(self,timeout=None): self.events.append(("artwork-stop",timeout))


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

    def pause(self):
        self.events.append("worker-pause")


class _Store:
    def __init__(self, failures=0, message="database is locked"):
        self.failures = failures
        self.message = message
        self.calls = 0

    def initialize(self):
        import sqlite3
        self.calls += 1
        if self.calls <= self.failures:
            raise sqlite3.OperationalError(self.message)


class ServiceLifecycleTests(unittest.TestCase):
    def test_store_initialization_retries_a_replacement_database_lock(self):
        store = _Store(failures=2)
        waits = []
        logs = []

        initialized = initialize_service_stores(
            (store,),
            lambda seconds: waits.append(seconds) or False,
            logs.append,
            retry_seconds=0.25,
        )

        self.assertTrue(initialized)
        self.assertEqual(3, store.calls)
        self.assertEqual([0.25, 0.25], waits)
        self.assertIn("database is busy", logs[0])
        self.assertIn("startup resumed", logs[-1])

    def test_store_initialization_can_be_aborted_while_waiting(self):
        store = _Store(failures=1)

        initialized = initialize_service_stores(
            (store,), lambda seconds: True, lambda message: None
        )

        self.assertFalse(initialized)
        self.assertEqual(1, store.calls)

    def test_store_initialization_does_not_hide_other_sqlite_errors(self):
        store = _Store(failures=1, message="malformed database schema")

        with self.assertRaisesRegex(Exception, "malformed database schema"):
            initialize_service_stores(
                (store,), lambda seconds: False, lambda message: None
            )

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

    def test_workers_are_paused_before_web_socket_is_released(self):
        events = []

        stop_service_components(
            _Server(events),
            _Thread(events),
            _Watchdog(events),
            web_join_timeout=1,
            worker_timeout=3,
        )

        self.assertEqual(
            events[:4],
            [
                "worker-pause",
                "web-shutdown",
                "web-close",
                ("web-join", 1),
            ],
        )
        self.assertEqual("worker-stop",events[-1][0])
        self.assertGreaterEqual(events[-1][1],0)
        self.assertLessEqual(events[-1][1],3)

    def test_artwork_downloads_are_halted_before_web_socket_is_released(self):
        events=[]; server=_Server(events); server.artwork_store=_Artwork(events)

        stop_service_components(
            server,_Thread(events),_Watchdog(events),
            web_join_timeout=1,worker_timeout=3)

        self.assertEqual("worker-pause",events[0])
        self.assertEqual("artwork-stop",events[1][0])
        self.assertEqual("web-shutdown",events[2])


if __name__ == "__main__":
    unittest.main()
