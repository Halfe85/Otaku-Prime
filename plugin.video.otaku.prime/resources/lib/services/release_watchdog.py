# -*- coding: utf-8 -*-
"""Publish newly released media and ask Kodi for one silent batch scan."""

from __future__ import annotations

import threading
import time


class ReleaseWatchdogService:
    def __init__(self, media_store, stream_library, kodi_db, interval_seconds=60,
                 error_handler=None, schedule_service=None):
        self.media_store = media_store
        self.stream_library = stream_library
        self.kodi_db = kodi_db
        self.interval_seconds = max(5, int(interval_seconds))
        self.error_handler = error_handler or (lambda error: None)
        self.schedule_service = schedule_service
        self._stop = threading.Event()
        self._thread = None

    def run_once(self, now=None):
        now = int(time.time() if now is None else now)
        if self.schedule_service:
            self.schedule_service.refresh_pending(now)
        pending_publications = []
        failed = []
        for episode in self.media_store.list_releasable_episodes(now):
            series = {
                "local_id": episode["related_series_id"],
                "english_name": episode.get("kodi_show_name") or episode.get("series_english_name"),
                "romaji_name": episode.get("series_romaji_name"),
                "year": episode.get("kodi_show_year"),
            }
            episode["season_number"] = (
                episode["kodi_season_number"]
                if episode.get("kodi_season_number") is not None
                else episode["season_number"]
            )
            if episode.get("kodi_episode_number") is not None:
                episode["episode_number"] = episode["kodi_episode_number"]
            try:
                path = self.stream_library.write_episode(series, episode)
                pending_publications.append((episode["local_id"], path))
            except Exception as exc:
                failed.append({"episode_id": episode["local_id"], "error": str(exc)})
        published = []
        if pending_publications:
            try:
                self.kodi_db.scan(self.stream_library.tv_series_root)
            except Exception as exc:
                failed.append({"episode_id": None, "error": str(exc)})
            else:
                for episode_id, path in pending_publications:
                    self.media_store.mark_stream_published("episode", episode_id, path)
                    published.append(path)
        return {"published": published, "failed": failed}

    def start(self, run_immediately=True):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(bool(run_immediately),),
            name="OtakuPrimeReleaseWatchdog", daemon=True
        )
        self._thread.start()

    def _run(self, run_immediately=True):
        if not run_immediately and self._stop.wait(self.interval_seconds):
            return
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self.error_handler(exc)
            self._stop.wait(self.interval_seconds)

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
