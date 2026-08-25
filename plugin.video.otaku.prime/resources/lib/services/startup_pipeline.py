# -*- coding: utf-8 -*-
"""Start Prime's dependent background services in a deterministic order."""
import threading
from resources.lib.logging_config import get_logger
LOGGER=get_logger(__name__)


class StartupPipelineService:
    def __init__(self, watchlist_sync, mediator,
                 result_handler=None, error_handler=None):
        self.watchlist_sync = watchlist_sync
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
            LOGGER.info("Starting pipeline step: %s",name)
            self.result_handler(name, function())
        except Exception as exc:
            LOGGER.exception("Pipeline step failed: %s",name)
            self.error_handler(name, exc)

    def _run(self):
        self._step("kodi-inventory", self.mediator.inventory)
        self._step("watchlist", self.watchlist_sync.run_once)
        self._step("kodi-reconcile", self.mediator.reconcile)
        if not self._stop.is_set():
            self.watchlist_sync.start(run_immediately=False)

    def stop(self, timeout=5):
        self._stop.set()
        self.watchlist_sync.stop(timeout=timeout)
        if self._thread:
            self._thread.join(timeout=timeout)
