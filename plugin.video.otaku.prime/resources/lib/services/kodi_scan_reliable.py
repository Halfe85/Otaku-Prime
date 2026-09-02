# -*- coding: utf-8 -*-
"""Reliable Kodi directory scans for Prime Physical.

Kodi's JSON-RPC VideoLibrary.Scan returns as soon as the update request has been
accepted.  It does not mean the scanner started or finished.  This queue waits
for Kodi's VideoLibrary.OnScanStarted/OnScanFinished notifications, falls back
to Library.IsScanningVideo, verifies mediator projections against Kodi's actual
library inventory, and retries one missed scan once.
"""
from __future__ import annotations

import json
import os
import threading
import time

from resources.lib.logging_config import get_logger
from resources.lib.services.runtime_prime_physical import (
    KodiVideoLibraryScanQueue,
    _normalized_directory,
    _parent_directory,
)


LOGGER = get_logger(__name__)
SCAN_START_NOTIFICATION = "VideoLibrary.OnScanStarted"
SCAN_FINISH_NOTIFICATION = "VideoLibrary.OnScanFinished"


def _normalized_file(value):
    return str(value or "").strip().replace("\\", "/").rstrip("/")


def _physical_strm_files(directory):
    root = str(directory or "")
    if not root or not os.path.isdir(root):
        return set()
    result = set()
    for current, _directories, filenames in os.walk(root):
        for filename in filenames:
            if filename.lower().endswith(".strm"):
                result.add(_normalized_file(os.path.join(current, filename)))
    return result


def _json_rpc(method, params=None):
    import xbmc

    request = {
        "jsonrpc": "2.0",
        "method": str(method),
        "id": 1,
    }
    if params is not None:
        request["params"] = params
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if response.get("error"):
        raise RuntimeError(
            "Kodi {} failed: {}".format(method, response["error"])
        )
    return response.get("result") or {}


def _kodi_verify_series_directory(directory):
    """Verify every Prime STRM currently on disk exists in Kodi for this show."""
    wanted = _normalized_directory(directory)
    expected = _physical_strm_files(directory)
    shows = _json_rpc(
        "VideoLibrary.GetTVShows",
        {"properties": ["file"]},
    ).get("tvshows", [])
    show = next(
        (
            row for row in shows
            if _normalized_directory(row.get("file")) == wanted
        ),
        None,
    )
    if not show:
        return {
            "complete": False,
            "reason": "tvshow_not_in_library",
            "expected": len(expected),
            "known": 0,
            "missing": sorted(expected),
            "path": wanted,
        }

    episodes = _json_rpc(
        "VideoLibrary.GetEpisodes",
        {
            "tvshowid": int(show["tvshowid"]),
            "properties": ["file"],
        },
    ).get("episodes", [])
    known = {
        _normalized_file(row.get("file"))
        for row in episodes
        if row.get("file")
    }
    missing = sorted(expected - known)
    return {
        "complete": not missing,
        "reason": "complete" if not missing else "episodes_missing",
        "tvshowid": int(show["tvshowid"]),
        "expected": len(expected),
        "known": len(expected & known),
        "missing": missing,
        "path": wanted,
    }


def _kodi_verify_movie_directory(directory):
    """Verify Prime's movie STRM for this folder exists in Kodi's movie library."""
    wanted = _normalized_directory(directory)
    expected = _physical_strm_files(directory)
    movies = _json_rpc(
        "VideoLibrary.GetMovies",
        {"properties": ["file"]},
    ).get("movies", [])
    known = {
        _normalized_file(row.get("file"))
        for row in movies
        if _parent_directory(row.get("file")) == wanted
    }
    missing = sorted(expected - known)
    movie = next(
        (
            row for row in movies
            if _parent_directory(row.get("file")) == wanted
        ),
        None,
    )
    return {
        "complete": bool(movie) and not missing,
        "reason": (
            "complete" if movie and not missing
            else "movie_not_in_library" if not movie
            else "movie_file_missing"
        ),
        "movieid": int(movie["movieid"]) if movie else None,
        "expected": len(expected),
        "known": len(expected & known),
        "missing": missing,
        "path": wanted,
    }


class KodiVideoScanNotificationMonitor:
    """Small xbmc.Monitor bridge for video-library scan notifications."""

    def __init__(self):
        self._started = threading.Event()
        self._finished = threading.Event()
        self.available = False
        self._monitor = None
        try:
            import xbmc

            owner = self

            class _Monitor(xbmc.Monitor):
                def onNotification(self, sender, method, data):
                    notification = str(method or "")
                    if notification == SCAN_START_NOTIFICATION:
                        owner._started.set()
                        LOGGER.info(
                            "Kodi video library scan notification: started sender=%s",
                            sender,
                        )
                    elif notification == SCAN_FINISH_NOTIFICATION:
                        owner._finished.set()
                        LOGGER.info(
                            "Kodi video library scan notification: finished sender=%s",
                            sender,
                        )

            self._monitor = _Monitor()
            self.available = True
        except (ImportError, RuntimeError, AttributeError, TypeError):
            self._monitor = None
            self.available = False

    def reset(self):
        self._started.clear()
        self._finished.clear()

    def started(self):
        return self._started.is_set()

    def finished(self):
        return self._finished.is_set()


class ReliableKodiVideoLibraryScanQueue(KodiVideoLibraryScanQueue):
    """Kodi scan queue that waits for real scanner lifecycle and verifies imports."""

    def __init__(
        self,
        *args,
        notification_monitor=None,
        verify_series=None,
        verify_movie=None,
        start_timeout=5.0,
        finish_timeout=600.0,
        retry_delay=0.5,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self._notification_monitor = (
            notification_monitor
            if notification_monitor is not None
            else KodiVideoScanNotificationMonitor()
        )
        self._verify_series = verify_series or _kodi_verify_series_directory
        self._verify_movie = verify_movie or _kodi_verify_movie_directory
        self._start_timeout = max(0.5, float(start_timeout))
        self._finish_timeout = max(5.0, float(finish_timeout))
        self._retry_delay = max(0.0, float(retry_delay))

    def _reset_scan_notifications(self):
        monitor = self._notification_monitor
        if monitor is not None:
            try:
                monitor.reset()
            except Exception:
                LOGGER.exception("Could not reset Kodi scan notification state")

    def _notification_started(self):
        monitor = self._notification_monitor
        if monitor is None:
            return False
        try:
            return bool(monitor.started())
        except Exception:
            LOGGER.exception("Could not inspect Kodi scan-start notification")
            return False

    def _notification_finished(self):
        monitor = self._notification_monitor
        if monitor is None:
            return False
        try:
            return bool(monitor.finished())
        except Exception:
            LOGGER.exception("Could not inspect Kodi scan-finish notification")
            return False

    def _wait_for_requested_scan(self):
        """Wait for the scan lifecycle after Kodi ACKs VideoLibrary.Scan.

        OnScanStarted/OnScanFinished are authoritative when available. The
        Library.IsScanningVideo condition remains a fallback for Kodi builds or
        tests where notifications are unavailable.
        """
        started = False
        saw_active = False
        start_deadline = time.monotonic() + self._start_timeout
        while not self._halt_requested() and time.monotonic() < start_deadline:
            if self._notification_started():
                started = True
                break
            try:
                if self._scan_active():
                    started = True
                    saw_active = True
                    break
            except Exception:
                LOGGER.exception("Could not inspect Kodi video-library scan state")
                break
            self._sleep(0.05)

        if not started:
            LOGGER.warning(
                "Kodi accepted VideoLibrary.Scan but no scan-start signal arrived "
                "within %.1fs",
                self._start_timeout,
            )
            return False

        finish_deadline = time.monotonic() + self._finish_timeout
        inactive_since = None
        while not self._halt_requested() and time.monotonic() < finish_deadline:
            if self._notification_finished():
                return True
            try:
                active = bool(self._scan_active())
            except Exception:
                LOGGER.exception("Could not inspect Kodi video-library scan state")
                active = False
            if active:
                saw_active = True
                inactive_since = None
            elif saw_active:
                # The scanner condition dropped after being observed active.
                return True
            elif self._notification_started():
                # Some Kodi builds notify before Library.IsScanningVideo becomes
                # visible. Give the matching finish notification a grace window;
                # if the condition remains inactive, treat it as a very fast scan.
                if inactive_since is None:
                    inactive_since = time.monotonic()
                elif time.monotonic() - inactive_since >= 2.0:
                    LOGGER.info(
                        "Kodi scan-start notification completed without an observable "
                        "Library.IsScanningVideo interval"
                    )
                    return True
            self._sleep(0.10)

        if self._halt_requested():
            return False
        LOGGER.warning(
            "Kodi video-library scan did not signal completion within %.1fs",
            self._finish_timeout,
        )
        return False

    def _verify(self, directory, reason):
        if reason == "mediator_series":
            return self._verify_series(directory)
        if reason == "mediator_movie":
            return self._verify_movie(directory)
        return None

    def _scan_once(self, directory, reason, attempt):
        if not self._wait_for_current_scan():
            return {"requested": False, "completed": False, "result": None}
        self._reset_scan_notifications()
        try:
            result = self._execute_scan(directory)
            LOGGER.info(
                "Kodi video library scan accepted: reason=%s path=%s attempt=%s result=%s",
                reason, directory, attempt, result,
            )
        except Exception:
            LOGGER.exception(
                "Kodi video library scan request failed: reason=%s path=%s attempt=%s",
                reason, directory, attempt,
            )
            return {"requested": False, "completed": False, "result": None}
        completed = self._wait_for_requested_scan()
        LOGGER.info(
            "Kodi video library scan lifecycle complete: reason=%s path=%s "
            "attempt=%s completed=%s",
            reason, directory, attempt, completed,
        )
        return {"requested": True, "completed": completed, "result": result}

    def _process_scan(self, directory, reason):
        last_verification = None
        for attempt in (1, 2):
            outcome = self._scan_once(directory, reason, attempt)
            if self._halt_requested():
                return {"completed": False, "verification": last_verification}

            verification = None
            try:
                verification = self._verify(directory, reason)
            except Exception:
                LOGGER.exception(
                    "Kodi library verification failed: reason=%s path=%s attempt=%s",
                    reason, directory, attempt,
                )
            last_verification = verification

            verified = verification is not None and bool(verification.get("complete"))
            if verification is not None:
                LOGGER.info(
                    "Kodi library scan verification: reason=%s path=%s attempt=%s "
                    "complete=%s expected=%s known=%s missing=%s",
                    reason,
                    directory,
                    attempt,
                    verification.get("complete"),
                    verification.get("expected"),
                    verification.get("known"),
                    len(verification.get("missing") or []),
                )

            # Mediator scans have an exact inventory verifier; trust that rather
            # than merely trusting Kodi's ACK/lifecycle signal. Startup root scans
            # have no single-title verifier, so completion is the success signal.
            if verification is not None:
                if verified:
                    return {"completed": True, "verification": verification}
            elif outcome.get("completed"):
                return {"completed": True, "verification": None}

            if attempt == 1 and not self._halt_requested():
                LOGGER.warning(
                    "Kodi scoped scan did not import the requested Prime content; "
                    "retrying once: reason=%s path=%s verification=%s",
                    reason,
                    directory,
                    (verification or {}).get("reason") or "scan_not_observed",
                )
                self._sleep(self._retry_delay)

        LOGGER.error(
            "Kodi scoped scan remained incomplete after retry: reason=%s path=%s "
            "verification=%s missing=%s",
            reason,
            directory,
            (last_verification or {}).get("reason") or "scan_not_observed",
            len((last_verification or {}).get("missing") or []),
        )
        return {"completed": False, "verification": last_verification}

    def _refresh_after_scan(self, directory, reason):
        if reason == "mediator_series" and not self._halt_requested():
            try:
                refresh = self._refresh_series(directory)
                LOGGER.info(
                    "Kodi TV show refresh requested after verified mediator scan: "
                    "path=%s refreshed=%s result=%s",
                    directory, refresh.get("refreshed"), refresh.get("result"),
                )
            except Exception:
                LOGGER.exception(
                    "Kodi TV show refresh failed after mediator scan: path=%s",
                    directory,
                )
        elif reason == "mediator_movie" and not self._halt_requested():
            try:
                refresh = self._refresh_movie(directory)
                LOGGER.info(
                    "Kodi movie refresh requested after verified mediator scan: "
                    "path=%s refreshed=%s result=%s",
                    directory, refresh.get("refreshed"), refresh.get("result"),
                )
            except Exception:
                LOGGER.exception(
                    "Kodi movie refresh failed after mediator scan: path=%s",
                    directory,
                )

    def _run(self):
        while not self._halt_requested():
            with self._lock:
                if not self._pending:
                    self._active_directory = None
                    return
                directory, reason = self._pending.pop(0)
                self._active_directory = directory

            result = self._process_scan(directory, reason)
            if self._halt_requested():
                return
            if result.get("completed"):
                self._refresh_after_scan(directory, reason)

        with self._lock:
            self._active_directory = None
