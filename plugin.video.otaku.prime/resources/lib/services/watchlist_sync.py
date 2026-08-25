# -*- coding: utf-8 -*-
"""Periodically synchronize connected watchlists into Prime SQLite."""
import threading
from resources.lib.logging_config import get_logger
LOGGER=get_logger(__name__)


class WatchlistSyncService:
    def __init__(self, importers, interval_seconds=900, error_handler=None,
                 processors=None, gate=None):
        self.importers = list(importers)
        self.processors = list(processors or [])
        self.gate = gate
        self.interval_seconds = max(60, int(interval_seconds))
        self.error_handler = error_handler or (lambda error: None)
        self._stop = threading.Event()
        self._thread = None

    def run_once(self):
        if self.gate is not None and not self.gate.is_configured():
            status = self.gate.status()
            LOGGER.warning("Watchlist synchronization blocked: metadata provider is required")
            return [{
                "blocked": "metadata_provider_required",
                "configured": False,
                "provider": status.get("provider"),
            }]

        results = []
        for importer in self.importers:
            try:
                LOGGER.info("Running watchlist importer %s",importer.__class__.__name__)
                results.append(importer.sync())
            except Exception as exc:
                LOGGER.exception("Watchlist importer %s failed",importer.__class__.__name__)
                self.error_handler(exc)
                results.append({"error": str(exc)})
        for processor in self.processors:
            try:
                LOGGER.info("Running watchlist processor %s",processor.__class__.__name__)
                results.append(processor.run_once())
            except Exception as exc:
                LOGGER.exception("Watchlist processor %s failed",processor.__class__.__name__)
                self.error_handler(exc)
                results.append({"error": str(exc)})
        return results

    def start(self, run_immediately=True):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(bool(run_immediately),),
            name="OtakuPrimeWatchlistSync",
            daemon=True,
        )
        self._thread.start()

    def _run(self, run_immediately=True):
        if not run_immediately and self._stop.wait(self.interval_seconds):
            return
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.interval_seconds)

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
