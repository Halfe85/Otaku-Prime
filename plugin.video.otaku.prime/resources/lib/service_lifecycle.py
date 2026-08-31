# -*- coding: utf-8 -*-
"""Shutdown ordering for the Otaku Prime background service."""

from __future__ import annotations


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


def stop_service_components(
    server,
    server_thread,
    watchlist_watchdog,
    *,
    web_join_timeout=1,
    worker_timeout=35,
):
    """Release the web listener before waiting for background workers.

    Kodi starts the replacement addon service as soon as an update has been
    installed.  The old service must therefore relinquish its listening socket
    first; mediator and watchlist workers may need longer to leave an in-flight
    provider request.
    """
    try:
        server.shutdown()
    finally:
        server.server_close()

    server_thread.join(timeout=web_join_timeout)
    watchlist_watchdog.stop(timeout=worker_timeout)
