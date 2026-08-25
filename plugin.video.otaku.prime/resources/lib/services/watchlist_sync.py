# -*- coding: utf-8 -*-
"""Synchronize connected watchlists into Prime's canonical raw watchlist."""
import threading
from resources.lib.logging_config import get_logger
LOGGER=get_logger(__name__)

# Kodi can reload a service before its previous Python worker has fully
# returned. Keep full watchlist pipelines single-flight across service
# instances in the shared addon interpreter.
_PIPELINE_LOCK = threading.Lock()


class WatchlistSyncService:
    def __init__(self, importers, watchlist_store, interval_seconds=900, error_handler=None,
                 identity_enricher=None):
        self.importers = list(importers)
        self.watchlist_store = watchlist_store
        self.interval_seconds = max(60, int(interval_seconds))
        self.error_handler = error_handler or (lambda error: None)
        self.identity_enricher = identity_enricher
        self._stop = threading.Event()
        self._thread = None
        self._busy_notice = False

    def run_once(self, periodic=False):
        # Immediate callbacks can arrive together (account connected, settings
        # changed, startup pipeline). Do not queue duplicate full pipelines.
        if not _PIPELINE_LOCK.acquire(blocking=False):
            if not self._busy_notice:
                LOGGER.info(
                    "Watchlist synchronization request skipped because a pipeline is already active"
                )
                self._busy_notice = True
            return [{"skipped": True, "reason": "pipeline_already_active"}]
        self._busy_notice = False
        try:
            return self._run_once_locked(periodic=bool(periodic))
        finally:
            _PIPELINE_LOCK.release()

    def _run_once_locked(self, periodic=False):
        results = []
        for importer in self.importers:
            if self._stop.is_set():
                return results + [{"cancelled": True, "reason": "pipeline_stopping"}]
            try:
                LOGGER.info("Running watchlist importer %s",importer.__class__.__name__)
                results.append(importer.sync())
            except Exception as exc:
                LOGGER.exception("Watchlist importer %s failed",importer.__class__.__name__)
                self.error_handler(exc)
                results.append({"error": str(exc)})

        merge=self.watchlist_store.finalize_merge()
        LOGGER.info("Prime watchlist merge complete: items=%s initialized=%s conflicts=%s",
                    merge["items"],merge["initialized"],merge["conflicts"])
        results.append({"prime_watchlist":merge})
        if self.identity_enricher:
            results.append({"provider_id_enrichment":self.identity_enricher.start()})
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
        if run_immediately:
            self.run_once(periodic=False)
        while not self._stop.wait(self.interval_seconds):
            self.run_once(periodic=True)

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        if self.identity_enricher:
            self.identity_enricher.stop(timeout=timeout)
