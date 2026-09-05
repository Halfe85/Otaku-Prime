# -*- coding: utf-8 -*-
"""Kodi-runtime behavior layered on top of Prime Physical projection."""
from __future__ import annotations

import json
import os
import threading
import time

from resources.lib.logging_config import get_logger
from resources.lib.services.prime_movie_physical import PrimeMoviePhysicalSupport
from resources.lib.services.prime_nfo import PrimeNfoWriter
from resources.lib.services.prime_strm import PrimeStrmWriter
from resources.lib.services.prime_physical import PrimePhysicalService, safe_library_name


LOGGER = get_logger(__name__)


def _normalized_directory(value):
    path = str(value or "").strip().replace("\\", "/")
    if path and not path.endswith("/"):
        path += "/"
    return path


def _parent_directory(value):
    path = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not path:
        return ""
    if "/" not in path:
        return ""
    return _normalized_directory(path.rsplit("/", 1)[0])


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


def _kodi_refresh_tvshow(directory):
    """Refresh one already-known Kodi TV show from Prime's local NFO files."""
    import xbmc

    wanted = _normalized_directory(directory)
    lookup = json.dumps({
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetTVShows",
        "params": {"properties": ["file"]},
        "id": 1,
    })
    response = json.loads(xbmc.executeJSONRPC(lookup))
    if response.get("error"):
        raise RuntimeError(
            "Kodi VideoLibrary.GetTVShows failed: {}".format(response["error"])
        )

    tvshow = next(
        (
            row for row in response.get("result", {}).get("tvshows", [])
            if _normalized_directory(row.get("file")) == wanted
        ),
        None,
    )
    if not tvshow:
        return {"refreshed": False, "reason": "tvshow_not_in_library", "path": wanted}

    request = json.dumps({
        "jsonrpc": "2.0",
        "method": "VideoLibrary.RefreshTVShow",
        "params": {
            "tvshowid": int(tvshow["tvshowid"]),
            "ignorenfo": False,
            "refreshepisodes": True,
        },
        "id": 1,
    })
    refreshed = json.loads(xbmc.executeJSONRPC(request))
    if refreshed.get("error"):
        raise RuntimeError(
            "Kodi VideoLibrary.RefreshTVShow failed: {}".format(refreshed["error"])
        )
    return {
        "refreshed": True,
        "tvshowid": int(tvshow["tvshowid"]),
        "path": wanted,
        "result": refreshed.get("result"),
    }


def _kodi_refresh_movie(directory):
    """Refresh one already-known Kodi movie from Prime's adjacent local NFO."""
    import xbmc

    wanted = _normalized_directory(directory)
    lookup = json.dumps({
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetMovies",
        "params": {"properties": ["file"]},
        "id": 1,
    })
    response = json.loads(xbmc.executeJSONRPC(lookup))
    if response.get("error"):
        raise RuntimeError(
            "Kodi VideoLibrary.GetMovies failed: {}".format(response["error"])
        )
    movie = next(
        (
            row for row in response.get("result", {}).get("movies", [])
            if _parent_directory(row.get("file")) == wanted
        ),
        None,
    )
    if not movie:
        return {"refreshed": False, "reason": "movie_not_in_library", "path": wanted}

    request = json.dumps({
        "jsonrpc": "2.0",
        "method": "VideoLibrary.RefreshMovie",
        "params": {
            "movieid": int(movie["movieid"]),
            "ignorenfo": False,
        },
        "id": 1,
    })
    refreshed = json.loads(xbmc.executeJSONRPC(request))
    if refreshed.get("error"):
        raise RuntimeError(
            "Kodi VideoLibrary.RefreshMovie failed: {}".format(refreshed["error"])
        )
    return {
        "refreshed": True,
        "movieid": int(movie["movieid"]),
        "path": wanted,
        "result": refreshed.get("result"),
    }


def _kodi_video_scan_active():
    """Return whether Kodi is currently scanning its video library."""
    try:
        import xbmc

        return bool(xbmc.getCondVisibility("Library.IsScanningVideo"))
    except (ImportError, RuntimeError, AttributeError):
        return False


def _refresh_kodi_vfs_directory_cache(directory):
    """Make Kodi invalidate a directory cached as missing before a scan.

    Prime creates its generated library with Python filesystem calls. Kodi's
    VFS therefore may retain an earlier negative directory lookup. Creating
    and deleting a marker through xbmcvfs makes Kodi observe the directory
    without changing any media content.
    """
    path = str(directory or "").strip()
    if not path or not os.path.isdir(path):
        return False
    try:
        import xbmcvfs
    except (ImportError, RuntimeError):
        return False
    marker = os.path.join(path, ".otaku-prime-vfs-refresh")
    handle = None
    try:
        handle = xbmcvfs.File(marker, "w")
        handle.write("prime-vfs-refresh")
        handle.close()
        handle = None
        if not xbmcvfs.delete(marker):
            try:
                os.remove(marker)
            except FileNotFoundError:
                pass
        return True
    except Exception:
        LOGGER.exception("Kodi VFS directory-cache refresh failed: path=%s", path)
        return False
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


class KodiVideoLibraryScanQueue:
    """Serialize scoped scans and refresh existing Prime local-NFO titles."""

    def __init__(self, halt_requested=None, execute_scan=None, scan_active=None,
                 sleep=None, refresh_series=None, refresh_movie=None):
        external_halt = halt_requested or (lambda: False)
        self._stop = threading.Event()
        self._halt_requested = lambda: self._stop.is_set() or external_halt()
        self._execute_scan = execute_scan or _kodi_video_scan
        self._scan_active = scan_active or _kodi_video_scan_active
        self._refresh_series = refresh_series or _kodi_refresh_tvshow
        self._refresh_movie = refresh_movie or _kodi_refresh_movie
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._pending = []
        self._thread = None
        self._active_directory = None

    def request(self, directory, reason="prime_physical"):
        path = _normalized_directory(directory)
        if self._halt_requested():
            LOGGER.info("Kodi video library scan rejected during shutdown: reason=%s path=%s", reason, path)
            return {"queued": False, "path": path, "reason": "service_stopping"}
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

    def request_stop(self):
        self._stop.set()
        with self._lock:
            discarded = len(self._pending)
            self._pending = []
        LOGGER.info("Kodi video library scan queue stopping: active=%s discarded=%s",
                    self._active_directory, discarded)

    def stop(self, timeout=1):
        self.request_stop()
        thread = self._thread
        if thread:
            thread.join(timeout=max(0.0, float(timeout)))
        stopped = not bool(thread and thread.is_alive())
        LOGGER.info("Kodi video library scan queue stopped=%s", stopped)
        return stopped

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

            if reason == "mediator_series" and not self._halt_requested():
                try:
                    refresh = self._refresh_series(directory)
                    LOGGER.info(
                        "Kodi TV show refresh requested after mediator scan: "
                        "path=%s refreshed=%s result=%s",
                        directory, refresh.get("refreshed"), refresh.get("result"),
                    )
                except Exception:
                    LOGGER.exception(
                        "Kodi TV show refresh failed after mediator scan: path=%s",
                        directory,
                    )
                if not self._wait_for_requested_scan():
                    return
            elif reason == "mediator_movie" and not self._halt_requested():
                try:
                    refresh = self._refresh_movie(directory)
                    LOGGER.info(
                        "Kodi movie refresh requested after mediator scan: "
                        "path=%s refreshed=%s result=%s",
                        directory, refresh.get("refreshed"), refresh.get("result"),
                    )
                except Exception:
                    LOGGER.exception(
                        "Kodi movie refresh failed after mediator scan: path=%s",
                        directory,
                    )
                if not self._wait_for_requested_scan():
                    return
        with self._lock:
            self._active_directory = None


class RuntimePrimePhysicalService(PrimePhysicalService):
    """Prime Physical plus TV-series and movie Kodi native-library projection."""

    def __init__(self, *args, scan_queue=None, artwork_store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._scan_queue = scan_queue or KodiVideoLibraryScanQueue(
            halt_requested=self._halt_requested
        )
        self._strm_writer = PrimeStrmWriter(self.catalog_store)
        self._nfo_writer = PrimeNfoWriter(
            self.catalog_store, artwork_store=artwork_store
        )
        self._movies = PrimeMoviePhysicalSupport(self, artwork_store=artwork_store)
        self._bulk_projection = False

    def request_stop(self):
        requester = getattr(self._scan_queue, "request_stop", None)
        if requester:
            requester()

    def stop(self, timeout=1):
        stopper = getattr(self._scan_queue, "stop", None)
        return bool(stopper(timeout=timeout)) if stopper else True

    @property
    def movie_source_url(self):
        return self._movies.source_url

    def ensure_kodi_library_configuration(self):
        """Configure TV-Series and Movies as independent Local Information sources."""
        result = super().ensure_kodi_library_configuration()
        result["movies"] = self._movies.ensure_configuration()
        return result

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
        refreshed = []
        for candidate in (_parent_directory(path), path):
            if candidate and _refresh_kodi_vfs_directory_cache(candidate):
                refreshed.append(_normalized_directory(candidate))
        if refreshed:
            LOGGER.info(
                "Refreshed Kodi VFS directory cache before scan: path=%s refreshed=%s",
                path, refreshed,
            )
        return self._scan_queue.request(path, reason=reason)

    def project_series(self, series_id, _log_result=True):
        result = super().project_series(series_id, _log_result=_log_result)
        if result.get("missing"):
            return result

        directory = self._series_directory(series_id)
        if directory and os.path.isdir(directory):
            result["strm"] = self._strm_writer.write_series(
                series_id, directory, now_epoch=int(self._now())
            )
            result["nfo"] = self._nfo_writer.write_series(
                series_id, directory, now_epoch=int(self._now())
            )
            if not self._bulk_projection:
                result["scan"] = self.request_kodi_scan(
                    directory, reason="mediator_series"
                )
        else:
            result["strm"] = {
                "written": 0,
                "unchanged": 0,
                "missing": False,
                "reason": "series_directory_missing",
            }
            result["nfo"] = {
                "written": 0,
                "unchanged": 0,
                "episodes": 0,
                "missing": False,
                "artwork": {},
                "reason": "series_directory_missing",
            }
            if not self._bulk_projection:
                result["scan"] = {
                    "queued": False,
                    "path": _normalized_directory(directory),
                    "reason": "series_directory_missing",
                }
        return result

    def project_movie(self, movie_id):
        """Project one standalone Prime movie and softly scan only its folder."""
        result = self._movies.project_movie(movie_id, now_epoch=int(self._now()))
        directory = result.get("directory")
        if (
            not self._bulk_projection
            and not result.get("missing")
            and not result.get("future")
            and directory
            and os.path.isdir(directory)
        ):
            result["scan"] = self.request_kodi_scan(directory, reason="mediator_movie")
        return result

    def project_all(self):
        """Start both Prime libraries immediately, then backfill and reconcile them."""
        self.ensure_kodi_library_configuration()
        os.makedirs(self.source_url, exist_ok=True)
        os.makedirs(self.movie_source_url, exist_ok=True)

        startup_tv_scan = self.request_kodi_scan(
            self.source_url, reason="prime_startup"
        )
        startup_movie_scan = self.request_kodi_scan(
            self.movie_source_url, reason="prime_startup_movies"
        )

        self._bulk_projection = True
        try:
            result = super().project_all()
            movie_result = self._movies.project_all(now_epoch=int(self._now()))
        finally:
            self._bulk_projection = False

        result["movies"] = movie_result
        result["startup_scan"] = startup_tv_scan
        result["startup_movie_scan"] = startup_movie_scan
        result["final_scan"] = self.request_kodi_scan(
            self.source_url, reason="prime_startup_backfill"
        )
        result["final_movie_scan"] = self.request_kodi_scan(
            self.movie_source_url, reason="prime_startup_movies_backfill"
        )
        return result
