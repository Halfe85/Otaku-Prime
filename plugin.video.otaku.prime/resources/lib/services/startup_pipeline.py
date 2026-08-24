# -*- coding: utf-8 -*-
"""Start Prime's dependent background services in a deterministic order."""
import threading


class StartupPipelineService:
    def __init__(self, watchlist_sync, release_watchdog, mediator,
                 result_handler=None, error_handler=None):
        self.watchlist_sync = watchlist_sync
        self.release_watchdog = release_watchdog
        self.mediator = mediator
        self.result_handler = result_handler or (lambda name, result: None)
        self.error_handler = error_handler or (lambda name, error: None)
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="OtakuPrimeStartupPipeline", daemon=True
        )
        self._thread.start()

    def _step(self, name, function):
        if self._stop.is_set():
            return
        try:
            self.result_handler(name, function())
        except Exception as exc:
            self.error_handler(name, exc)

    def _run(self):
        self._step("watchlist", self.watchlist_sync.run_once)
        self._step("release", self.release_watchdog.run_once)
        self._step("kodi-links", self.mediator.start)
        if not self._stop.is_set():
            self.watchlist_sync.start(run_immediately=False)
            self.release_watchdog.start(run_immediately=False)

    def stop(self, timeout=5):
        self._stop.set()
        self.watchlist_sync.stop(timeout=timeout)
        self.release_watchdog.stop(timeout=timeout)
        if self._thread:
            self._thread.join(timeout=timeout)
