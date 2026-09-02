# -*- coding: utf-8 -*-
"""Final runtime composition for Prime TV and Movies physical libraries."""
from __future__ import annotations

import threading
import time

from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.logging_config import get_logger
from resources.lib.services.kodi_scan_reliable import (
    ReliableKodiVideoLibraryScanQueue,
)
from resources.lib.services.kodi_scan_verify_prime import (
    verify_prime_movie,
    verify_prime_series,
)
from resources.lib.services.mature_artwork import MatureAwareArtworkStore
from resources.lib.services.runtime_prime_movie_physical import (
    RuntimePrimeMoviePhysicalSupport,
)
from resources.lib.services.runtime_prime_physical import RuntimePrimePhysicalService


LOGGER = get_logger(__name__)


class RuntimePrimePhysicalMoviesService(RuntimePrimePhysicalService):
    """Use reliable Kodi scans, Prime local-ID STRMs, and mature-art protection."""

    def __init__(self, *args, artwork_store=None, mature_preference_getter=None, **kwargs):
        injected_scan_queue = kwargs.get("scan_queue")
        catalog_store = args[0] if args else kwargs.get("catalog_store")

        self._mature_preference_store = None
        if mature_preference_getter is None and catalog_store is not None:
            db_path = getattr(catalog_store, "db_path", None)
            if db_path:
                try:
                    self._mature_preference_store = WatchlistItemStore(db_path)
                    self._mature_preference_store.initialize()
                    mature_preference_getter = lambda: (
                        self._mature_preference_store.preferences().get("mature", 0)
                    )
                except Exception:
                    LOGGER.exception(
                        "Could not attach Kodi mature-artwork policy to Prime preferences"
                    )
                    self._mature_preference_store = None

        self._mature_preference_getter = mature_preference_getter or (lambda: 0)
        self._original_artwork_store = artwork_store
        self._mature_artwork_store = (
            MatureAwareArtworkStore(
                artwork_store,
                catalog_store,
                preference_getter=self._mature_preference_getter,
            )
            if artwork_store is not None and catalog_store is not None
            else None
        )
        physical_artwork_store = self._mature_artwork_store or artwork_store

        super().__init__(*args, artwork_store=physical_artwork_store, **kwargs)
        # Unit/integration callers may inject their own queue. The real Kodi
        # service replaces the legacy ACK/polling queue with the notification-
        # driven queue before any physical projection can request a scan.
        if injected_scan_queue is None:
            self._scan_queue = ReliableKodiVideoLibraryScanQueue(
                halt_requested=self._halt_requested,
                verify_series=verify_prime_series,
                verify_movie=verify_prime_movie,
                start_timeout=10.0,
            )
        self._movies = RuntimePrimeMoviePhysicalSupport(
            self, artwork_store=physical_artwork_store
        )

        self._last_mature_preference = (
            self._mature_artwork_store.mature_enabled()
            if self._mature_artwork_store is not None
            else None
        )
        self._mature_watch_thread = None
        if self._mature_preference_store is not None:
            self._mature_watch_thread = threading.Thread(
                target=self._watch_mature_preference,
                name="OtakuPrimeKodiMatureArtwork",
                daemon=True,
            )
            self._mature_watch_thread.start()

    def _watch_mature_preference(self):
        """Make the existing Prime Library switch authoritative for native Kodi art."""
        while not self._halt_requested():
            try:
                current = self._mature_artwork_store.mature_enabled()
                if current != self._last_mature_preference:
                    previous = self._last_mature_preference
                    self._last_mature_preference = current
                    LOGGER.info(
                        "Kodi mature artwork switch changed: previous=%s current=%s",
                        previous,
                        current,
                    )
                    self.apply_mature_preference(current)
            except Exception:
                LOGGER.exception("Kodi mature artwork preference watcher failed")
            time.sleep(0.5)

    def project_series(self, series_id, _log_result=True):
        """Write local-ID playback URLs before the base physical projection runs.

        The legacy base projector still has a compatibility path that creates a
        missing STRM as an empty placeholder. The active runtime must never expose
        those placeholders to Kodi. PrimeStrmWriter therefore creates or repairs
        every released episode STRM first, using the episode's opaque local_id as
        the only playback target. The normal runtime projection then writes NFOs,
        requests the Kodi scan, and leaves the already-playable STRM untouched.
        """
        directory = self._series_directory(series_id)
        preprojected = None
        if directory:
            preprojected = self._strm_writer.write_series(
                series_id,
                directory,
                now_epoch=int(self._now()),
            )

        result = super().project_series(series_id, _log_result=_log_result)
        if preprojected is not None and not result.get("missing"):
            # Report the meaningful first pass. The inherited runtime performs a
            # second idempotent write after base projection, which should normally
            # report these files as unchanged.
            result["strm"] = preprojected
        return result

    def apply_mature_preference(self, mature=None):
        """Re-project mature Kodi artwork immediately after the existing UI switch changes."""
        policy = self._mature_artwork_store
        if policy is None:
            LOGGER.warning("Kodi mature artwork update skipped: artwork store unavailable")
            return {"mature": int(bool(mature)), "series": 0, "movies": 0, "skipped": True}

        effective = policy.mature_enabled()
        self._last_mature_preference = effective
        series_ids = policy.mature_series_ids()
        movie_ids = policy.mature_movie_ids()
        LOGGER.info(
            "Kodi mature artwork preference applying: mature=%s series=%s movies=%s",
            effective,
            len(series_ids),
            len(movie_ids),
        )

        projected_series = projected_movies = failed = 0
        for series_id in series_ids:
            if self._halt_requested():
                break
            try:
                result = self.project_series(series_id)
                if not result.get("missing"):
                    projected_series += 1
            except Exception:
                failed += 1
                LOGGER.exception(
                    "Kodi mature artwork re-projection failed for Prime series %s",
                    series_id,
                )

        for movie_id in movie_ids:
            if self._halt_requested():
                break
            try:
                result = self.project_movie(movie_id)
                if not result.get("missing") and not result.get("future"):
                    projected_movies += 1
            except Exception:
                failed += 1
                LOGGER.exception(
                    "Kodi mature artwork re-projection failed for Prime movie %s",
                    movie_id,
                )

        result = {
            "mature": effective,
            "series": projected_series,
            "movies": projected_movies,
            "failed": failed,
            "skipped": False,
        }
        LOGGER.info(
            "Kodi mature artwork preference applied: mature=%s series=%s movies=%s failed=%s",
            result["mature"], result["series"], result["movies"], result["failed"],
        )
        return result
