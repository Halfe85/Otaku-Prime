# -*- coding: utf-8 -*-
"""Kodi-runtime behavior layered on top of Prime Physical projection."""
from __future__ import annotations

import json
import os
import threading
import time

from resources.lib.logging_config import get_logger
from resources.lib.services.prime_physical import PrimePhysicalService, safe_library_name


LOGGER = get_logger(__name__)


def _normalized_directory(value):
    path = str(value or "").strip().replace("\\", "/")
    if path and not path.endswith("/"):
        path += "/"
    return path


def _kodi_video_scan(directory):
    """Request one hidden, directory-scoped Kodi video library scan."""
    import xbmc

    request = json.dumps({
        "jsonrpc": "2.0",
        "method": "VideoLibrary.Scan",
        "params": {
            "directory": _normalized_directory(directory),
            "showdialogs": False,
        },
        "id": 1,
    })
    response = json.loads(xbmc.executeJSONRPC(request))
    if response.get("error"):
        raise RuntimeError(
            "Kodi VideoLibrary.Scan failed: {}".format(response["error"])
        )
    return response.get("result")


def _kodi_video_scan_active():
    """Return whether Kodi is currently scanning its video library."""
    try:
        import xbmc

        return bool(xbmc.getCondVisibility("Library.IsScanningVideo"))
    except (ImportError, RuntimeError, AttributeError):
        return False


class KodiVideoLibraryScanQueue:
    """Serialize scoped VideoLibrary.Scan requests without blocking Mediator.

    Kodi only has one video-library scanner. Prime can receive several mediator
    placements in a short burst, so requests are queued and executed one at a
    time. Exact duplicate requests that are still pending are coalesced, while a
    request for a directory that is already being scanned is allowed to queue
    once more because new files may have appeared after that scan started.
    """

    def __init__(self, halt_requested=None, execute_scan=None, scan_active=None,
                 sleep=None):
        self._halt_requested = halt_requested or (lambda: False)
        self._execute_scan = execute_scan or _kodi_video_scan
        self._scan_active = scan_active or _kodi_video_scan_active
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._pending = []
        self._thread = None
        self._active_directory = None

    def request(self, directory, reason="prime_physical"):
        path = _normalized_directory(directory)
        if not path:
            return {"queued": False, "path": path, "reason": "empty_directory"}
        with self._lock:
            pending_paths = [entry[0] for entry in self._pending]
            if path not in pending_paths:
                self._pending.append((path, str(reason or "prime_physical")))
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="OtakuPrimeKodiLibraryScan",
                    daemon=True,
                )
                self._thread.start()
        LOGGER.info("Queued Kodi video library scan: reason=%s path=%s", reason, path)
        return {"queued": True, "path": path, "reason": str(reason or "prime_physical")}

    def _wait_for_current_scan(self):
        while not self._halt_requested():
            try:
                if not self._scan_active():
                    return True
            except Exception:
                LOGGER.exception("Could not inspect Kodi video-library scan state")
                return True
            self._sleep(0.25)
        return False

    def _wait_for_requested_scan(self):
        # Kodi may return from executeJSONRPC just before the GUI condition flips
        # to Library.IsScanningVideo. Give the requested scan a short start
        # window, then wait until it has completed before dispatching another.
        started = False
        for _ in range(10):
            if self._halt_requested():
                return False
            try:
                if self._scan_active():
                    started = True
                    break
            except Exception:
                LOGGER.exception("Could not inspect Kodi video-library scan state")
                return True
            self._sleep(0.05)

        if not started:
            return True

        while not self._halt_requested():
            try:
                if not self._scan_active():
                    return True
            except Exception:
                LOGGER.exception("Could not inspect Kodi video-library scan state")
                return True
            self._sleep(0.25)
        return False

    def _run(self):
        while not self._halt_requested():
            with self._lock:
                if not self._pending:
                    self._active_directory = None
                    return
                directory, reason = self._pending.pop(0)
                self._active_directory = directory
            if not self._wait_for_current_scan():
                return
            try:
                result = self._execute_scan(directory)
                LOGGER.info(
                    "Kodi video library scan requested: reason=%s path=%s result=%s",
                    reason, directory, result,
                )
            except Exception:
                LOGGER.exception(
                    "Kodi video library scan request failed: reason=%s path=%s",
                    reason, directory,
                )
            if not self._wait_for_requested_scan():
                return
        with self._lock:
            self._active_directory = None


class RuntimePrimePhysicalService(PrimePhysicalService):
    """Prime Physical plus automatic Kodi native-library scan requests."""

    def __init__(self, *args, scan_queue=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._scan_queue = scan_queue or KodiVideoLibraryScanQueue(
            halt_requested=self._halt_requested
        )
        self._bulk_projection = False

    def _series_directory(self, series_id):
        series = self._series_row(series_id)
        if not series:
            return None
        seasons = self.catalog_store.list_seasons(series["local_id"])
        title = safe_library_name(
            series.get("english_name") or series.get("romaji_name"),
            fallback="Untitled {}".format(series["local_id"]),
        )
        year = self._series_year(series, seasons)
        return os.path.join(
            self.root_path, "TV-Series", "{} {}".format(title, year)
        )

    def request_kodi_scan(self, directory, reason="prime_physical"):
        """Queue a soft Kodi scan for one physical library directory."""
        path = str(directory or "")
        if not path:
            return {"queued": False, "path": path, "reason": "empty_directory"}
        return self._scan_queue.request(path, reason=reason)

    def project_series(self, series_id, _log_result=True):
        result = super().project_series(series_id, _log_result=_log_result)
        if result.get("missing") or self._bulk_projection:
            return result

        directory = self._series_directory(series_id)
        if directory and os.path.isdir(directory):
            result["scan"] = self.request_kodi_scan(
                directory, reason="mediator_series"
            )
        else:
            result["scan"] = {
                "queued": False,
                "path": _normalized_directory(directory),
                "reason": "series_directory_missing",
            }
        return result

    def project_all(self):
        """Start Kodi's Prime library immediately, then backfill and rescan it."""
        self.ensure_kodi_library_configuration()
        os.makedirs(self.source_url, exist_ok=True)

        # Start the library as soon as Prime's service starts. This scan is
        # intentionally requested before the catalogue backfill finishes.
        startup_scan = self.request_kodi_scan(
            self.source_url, reason="prime_startup"
        )

        self._bulk_projection = True
        try:
            result = super().project_all()
        finally:
            self._bulk_projection = False

        # The startup scan may have walked a directory before its STRM/NFO files
        # were projected. Queue one final root scan to reconcile the completed
        # startup backfill. Mediator calls after this point use per-series scans.
        result["startup_scan"] = startup_scan
        result["final_scan"] = self.request_kodi_scan(
            self.source_url, reason="prime_startup_backfill"
        )
        return result
