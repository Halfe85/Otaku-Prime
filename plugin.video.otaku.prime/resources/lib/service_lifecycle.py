# -*- coding: utf-8 -*-
"""Shutdown ordering for the Otaku Prime background service."""

from __future__ import annotations

import sqlite3
import time


class ServiceWorkHalted(RuntimeError):
    """Raised when an addon update retires an in-flight unit of work."""


class ServiceInstanceLock:
    """Best-effort Linux lock preventing overlapping Kodi service generations."""

    def __init__(self, path):
        self.path = path
        self._file = None

    def acquire(self):
        handle = open(self.path, "a+b")
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            # Kodi platforms without fcntl retain the previous single-instance
            # behavior supplied by Kodi's service manager.
            self._file = handle
            return True
        except (BlockingIOError, OSError):
            handle.close()
            return False
        self._file = handle
        return True

    def release(self):
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        finally:
            handle.close()


def initialize_service_stores(stores, wait_for_abort, log, retry_seconds=1):
    """Initialize SQLite stores after an older addon generation releases them.

    Kodi can start the replacement service while a daemon worker from the
    previous addon generation is completing its final transaction.  A locked
    database is temporary in that situation and must not terminate the new
    service.  Other SQLite failures remain fatal.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            for store in stores:
                store.initialize()
            if attempt > 1:
                log(
                    "OTAKU PRIME: database became available; startup resumed "
                    "after {} attempts".format(attempt)
                )
            return True
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            if attempt == 1 or attempt % 10 == 0:
                log(
                    "OTAKU PRIME: database is busy during addon replacement; "
                    "waiting for the previous service generation (attempt {})".format(
                        attempt
                    )
                )
            if wait_for_abort(float(retry_seconds)):
                return False


def stop_service_components(
    server,
    server_thread,
    watchlist_watchdog,
    *,
    web_join_timeout=1,
    worker_timeout=3,
):
    """Halt producers, release the listener, then honor one shutdown deadline.

    Kodi starts the replacement addon service as soon as an update has been
    installed. No component may consume the full timeout independently.
    """
    deadline = time.monotonic() + max(0.0, float(worker_timeout))
    pause = getattr(watchlist_watchdog, "pause", None)
    if pause:
        pause()
    artwork_probe = getattr(server, "artwork_diagnostic_probe", None)
    if artwork_probe:
        artwork_probe.stop()
    artwork_store = getattr(server, "artwork_store", None)
    if artwork_store:
        artwork_store.stop(timeout=min(1.0,max(0.0,deadline-time.monotonic())))
    try:
        server.shutdown()
    finally:
        server.server_close()

    server_thread.join(timeout=min(
        max(0.0, float(web_join_timeout)),
        max(0.0, deadline - time.monotonic()),
    ))
    watchlist_watchdog.stop(timeout=max(0.0, deadline - time.monotonic()))
