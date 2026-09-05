# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
import threading
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
    def request_stop(self): self.events.append("artwork-request-stop")
    def stop(self,timeout=None): self.events.append(("artwork-stop",timeout))


class _Thread:
    def __init__(self, events):
        self.events = events

    def join(self, timeout=None):
        self.events.append(("web-join", timeout))

    def is_alive(self):
        return False


class _Watchdog:
    def __init__(self, events):
        self.events = events

    def stop(self, timeout=None):
        self.events.append(("worker-stop", timeout))
        return True

    def pause(self):
        self.events.append("worker-pause")


class _Physical:
    def __init__(self,events):
        self.events=events
        self._scan_queue=type("Queue",(),{"_thread":None})()
    def request_stop(self): self.events.append("physical-request-stop")
    def stop(self,timeout=None): self.events.append(("physical-stop",timeout)); return True


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

        result=stop_service_components(
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
        self.assertTrue(result["stopped"])

    def test_artwork_downloads_are_halted_before_web_socket_is_released(self):
        events=[]; server=_Server(events); server.artwork_store=_Artwork(events)

        stop_service_components(
            server,_Thread(events),_Watchdog(events),
            web_join_timeout=1,worker_timeout=3)

        self.assertEqual([
            "worker-pause","artwork-request-stop","web-shutdown","web-close"
        ],events[:4])
        self.assertEqual("artwork-stop",events[5][0])

    def test_cleanup_without_web_server_still_stops_physical_and_workers(self):
        events=[]; reports=[]
        result=stop_service_components(
            None,None,_Watchdog(events),physical=_Physical(events),
            artwork_store=_Artwork(events),worker_timeout=3,
            on_event=lambda component,action,facts: reports.append((component,action,facts)))
        self.assertTrue(result["stopped"])
        self.assertIn("physical-request-stop",events)
        self.assertIn("worker-stop",[value[0] if isinstance(value,tuple) else value for value in events])
        self.assertEqual("shutdown-begin",reports[0][1])
        self.assertEqual("shutdown-complete",reports[-1][1])

    def test_untracked_named_prime_thread_is_reported(self):
        stop=threading.Event()
        thread=threading.Thread(
            target=stop.wait,name="OtakuPrimeUntrackedTest",daemon=True)
        thread.start()
        try:
            result=stop_service_components(
                None,None,_Watchdog([]),worker_timeout=0)
            self.assertFalse(result["stopped"])
            self.assertIn("thread:OtakuPrimeUntrackedTest",result["active"])
        finally:
            stop.set(); thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
