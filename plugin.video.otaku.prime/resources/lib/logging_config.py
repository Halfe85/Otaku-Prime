# -*- coding: utf-8 -*-
"""Central INFO/WARNING/ERROR logging for Kodi and the Prime admin UI."""
from __future__ import annotations

import logging
import sys
import threading


LOGGER_NAME = "otaku_prime"
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())


def get_logger(source):
    value=str(source or "prime").replace("resources.lib.", "").replace(".", "-")
    return logging.getLogger("{}.{}".format(LOGGER_NAME,value))


class PrimeLogHandler(logging.Handler):
    def __init__(self, app_log_store=None, kodi_writer=None):
        super().__init__(logging.INFO)
        self.app_log_store=app_log_store
        self.kodi_writer=kodi_writer
        self._pending=[]
        self._store_unavailable_reported=False

    @staticmethod
    def _level(record):
        if record.levelno >= logging.ERROR: return "ERROR"
        if record.levelno >= logging.WARNING: return "WARNING"
        return "INFO"

    def emit(self, record):
        try:
            level=self._level(record)
            source=record.name.split(".",1)[-1][:64]
            message=self.format(record)
            if self.kodi_writer:
                self.kodi_writer(level,source,message)
            if not self.app_log_store:
                return
            entries=self._pending+[(level,source,message)]
            self._pending=[]
            for index,entry in enumerate(entries):
                try:
                    self.app_log_store.write(*entry)
                except Exception:
                    self._pending=entries[index:][-200:]
                    if self.kodi_writer and not self._store_unavailable_reported:
                        self.kodi_writer(
                            "WARNING",
                            "logging",
                            "App log database is unavailable; buffering up to 200 entries",
                        )
                    self._store_unavailable_reported=True
                    return
            if self.kodi_writer and self._store_unavailable_reported:
                self.kodi_writer(
                    "INFO",
                    "logging",
                    "App log database recovered; buffered entries were restored",
                )
            self._store_unavailable_reported=False
        except Exception:
            self.handleError(record)


def configure_logging(app_log_store=None, kodi_writer=None):
    logger=logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate=False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler=PrimeLogHandler(app_log_store,kodi_writer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    def unhandled(exc_type, exc_value, traceback):
        get_logger("unhandled").error("Unhandled exception",exc_info=(exc_type,exc_value,traceback))
    sys.excepthook=unhandled
    if hasattr(threading,"excepthook"):
        def thread_unhandled(args):
            get_logger("thread").error("Unhandled exception in %s",args.thread.name,
              exc_info=(args.exc_type,args.exc_value,args.exc_traceback))
        threading.excepthook=thread_unhandled
    return logger
