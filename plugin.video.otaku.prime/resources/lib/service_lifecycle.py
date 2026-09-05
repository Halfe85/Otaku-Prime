# -*- coding: utf-8 -*-
"""Shutdown ordering for the Otaku Prime background service."""

from __future__ import annotations

import sqlite3
import threading
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
    physical=None,
    artwork_store=None,
    web_join_timeout=1,
    worker_timeout=3,
    on_event=None,
):
    """Halt producers, release the listener, then honor one shutdown deadline.

    Kodi starts the replacement addon service as soon as an update has been
    installed. No component may consume the full timeout independently.
    """
    deadline = time.monotonic() + max(0.0, float(worker_timeout))
    started = time.monotonic()
    errors = []

    def event(component, action, **facts):
        if on_event:
            on_event(component, action, dict(facts, elapsed=round(time.monotonic()-started, 3)))

    event("service", "shutdown-begin", deadline_seconds=float(worker_timeout))
    pause = getattr(watchlist_watchdog, "pause", None)
    if pause:
        try:
            pause(); event("watchlist", "pause-requested")
        except Exception as exc:
            errors.append(("watchlist-pause", exc)); event("watchlist", "pause-failed", error=str(exc))
    artwork_store = artwork_store or getattr(server, "artwork_store", None)
    artwork_probe = getattr(server, "artwork_diagnostic_probe", None) if server else None
    if artwork_probe:
        request_stop=getattr(artwork_probe,"request_stop",None)
        if request_stop:
            try: request_stop(); event("artwork-diagnostic", "stop-requested")
            except Exception as exc: errors.append(("artwork-diagnostic-request",exc)); event("artwork-diagnostic","stop-request-failed",error=str(exc))
    if artwork_store:
        request_stop=getattr(artwork_store,"request_stop",None)
        if request_stop:
            try: request_stop(); event("artwork-store", "stop-requested")
            except Exception as exc: errors.append(("artwork-store-request",exc)); event("artwork-store","stop-request-failed",error=str(exc))
    if physical:
        request_stop=getattr(physical,"request_stop",None)
        if request_stop:
            try: request_stop(); event("prime-physical", "stop-requested")
            except Exception as exc: errors.append(("prime-physical-request",exc)); event("prime-physical","stop-request-failed",error=str(exc))
    if server:
        try:
            server.shutdown(); event("web", "shutdown-requested")
        except Exception as exc:
            errors.append(("web-shutdown",exc)); event("web","shutdown-failed",error=str(exc))
        finally:
            try: server.server_close(); event("web", "socket-closed")
            except Exception as exc: errors.append(("web-close",exc)); event("web","socket-close-failed",error=str(exc))

    if server_thread:
        server_thread.join(timeout=min(
            max(0.0, float(web_join_timeout)),
            max(0.0, deadline - time.monotonic()),
        ))
        event("web", "thread-joined", alive=bool(getattr(server_thread,"is_alive",lambda:False)()))
    if artwork_probe:
        try:
            stopped=artwork_probe.stop(timeout=min(1.0,max(0.0,deadline-time.monotonic())))
            event("artwork-diagnostic","stopped",stopped=stopped)
        except Exception as exc:
            errors.append(("artwork-diagnostic-stop",exc)); event("artwork-diagnostic","stop-failed",error=str(exc))
    if artwork_store:
        try:
            stopped=artwork_store.stop(timeout=min(1.0,max(0.0,deadline-time.monotonic())))
            event("artwork-store","stopped",stopped=stopped)
        except Exception as exc:
            errors.append(("artwork-store-stop",exc)); event("artwork-store","stop-failed",error=str(exc))
    if physical:
        stopper=getattr(physical,"stop",None)
        if stopper:
            try:
                stopped=stopper(timeout=min(1.0,max(0.0,deadline-time.monotonic())))
                event("prime-physical","stopped",stopped=stopped)
            except Exception as exc: errors.append(("prime-physical-stop",exc)); event("prime-physical","stop-failed",error=str(exc))
    try:
        worker_stopped=watchlist_watchdog.stop(
            timeout=max(0.0, deadline - time.monotonic()))
        event("watchlist","stopped",stopped=worker_stopped)
    except Exception as exc:
        worker_stopped=False; errors.append(("watchlist-stop",exc)); event("watchlist","stop-failed",error=str(exc))
    components={
        "web":server_thread,
        "artwork-diagnostic":getattr(artwork_probe,"_thread",None),
        "artwork-store":getattr(artwork_store,"_thread",None),
        "kodi-scan":getattr(getattr(physical,"_scan_queue",None),"_thread",None),
        "timestamp":getattr(
            getattr(getattr(watchlist_watchdog,"mediator",None),"timestamp_mediator",None),
            "_thread",None),
    }
    active=[name for name,thread in components.items()
            if thread and getattr(thread,"is_alive",lambda:False)()]
    if not worker_stopped:
        active.append("watchlist-workers")
    known_threads={id(thread) for thread in components.values() if thread}
    current=threading.current_thread()
    for thread in threading.enumerate():
        if thread is current or id(thread) in known_threads or not thread.is_alive():
            continue
        if str(thread.name).startswith("OtakuPrime"):
            active.append("thread:"+str(thread.name))
    active=list(dict.fromkeys(active))
    event("service", "shutdown-complete", active=active, errors=[name for name,_ in errors])
    return {"stopped":not active,"active":active,
            "errors":[{"component":name,"error":str(exc)} for name,exc in errors],
            "elapsed":round(time.monotonic()-started,3)}
