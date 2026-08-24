# -*- coding: utf-8 -*-
"""Periodically synchronize connected watchlists into Prime SQLite."""
import threading

class WatchlistSyncService:
    def __init__(self,importers,interval_seconds=900,error_handler=None,processors=None):
        self.importers=list(importers); self.processors=list(processors or [])
        self.interval_seconds=max(60,int(interval_seconds))
        self.error_handler=error_handler or (lambda error:None)
        self._stop=threading.Event(); self._thread=None
    def run_once(self):
        results=[]
        for importer in self.importers:
            try: results.append(importer.sync())
            except Exception as exc:
                self.error_handler(exc); results.append({"error":str(exc)})
        for processor in self.processors:
            try: results.append(processor.run_once())
            except Exception as exc:
                self.error_handler(exc); results.append({"error":str(exc)})
        return results
    def start(self,run_immediately=True):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread=threading.Thread(
          target=self._run,args=(bool(run_immediately),),
          name="OtakuPrimeWatchlistSync",daemon=True); self._thread.start()
    def _run(self,run_immediately=True):
        if not run_immediately and self._stop.wait(self.interval_seconds): return
        while not self._stop.is_set():
            self.run_once(); self._stop.wait(self.interval_seconds)
    def stop(self,timeout=5):
        self._stop.set()
        if self._thread: self._thread.join(timeout=timeout)
