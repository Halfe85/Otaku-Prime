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
    def __init__(self, importers, interval_seconds=900, error_handler=None,
                 processors=None, gate=None):
        self.importers = list(importers)
        self.processors = list(processors or [])
        self.gate = gate
        self.interval_seconds = max(60, int(interval_seconds))
        self.error_handler = error_handler or (lambda error: None)
        self._stop = threading.Event()
        self._thread = None
        self._busy_notice = False
        for processor in self.processors:
            binder = getattr(processor, "bind_stop_event", None)
            if binder:
                binder(self._stop)

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
            if self._stop.is_set():
                return results + [{"cancelled": True, "reason": "pipeline_stopping"}]
            # Some providers (currently Simkl) explicitly disallow unconditional
            # timer polling. They still run on startup/manual/user-visible syncs.
            if periodic and getattr(importer, "allow_periodic", True) is False:
                results.append({
                    "provider": getattr(importer, "provider", importer.__class__.__name__),
                    "skipped": True,
                    "reason": "provider_disallows_periodic_polling",
                })
                continue
            try:
                LOGGER.info("Running watchlist importer %s",importer.__class__.__name__)
                results.append(importer.sync())
            except Exception as exc:
                LOGGER.exception("Watchlist importer %s failed",importer.__class__.__name__)
                self.error_handler(exc)
                results.append({"error": str(exc)})

        for processor in self.processors:
            if self._stop.is_set():
                return results + [{"cancelled": True, "reason": "pipeline_stopping"}]
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
        if run_immediately:
            self.run_once(periodic=False)
        elif self._stop.wait(self.interval_seconds):
            return
        while not self._stop.is_set():
            self.run_once(periodic=True)
            if self._stop.wait(self.interval_seconds):
                break

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
