# -*- coding: utf-8 -*-
"""Final runtime composition for Prime TV and Movies physical libraries."""
from __future__ import annotations

from resources.lib.services.kodi_scan_reliable import (
    ReliableKodiVideoLibraryScanQueue,
)
from resources.lib.services.kodi_scan_verify_prime import (
    verify_prime_movie,
    verify_prime_series,
)
from resources.lib.services.runtime_prime_movie_physical import (
    RuntimePrimeMoviePhysicalSupport,
)
from resources.lib.services.runtime_prime_physical import RuntimePrimePhysicalService


class RuntimePrimePhysicalMoviesService(RuntimePrimePhysicalService):
    """Use reliable Kodi scans and playable episode STRMs from Prime local IDs."""

    def __init__(self, *args, artwork_store=None, **kwargs):
        injected_scan_queue = kwargs.get("scan_queue")
        super().__init__(*args, artwork_store=artwork_store, **kwargs)
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
            self, artwork_store=artwork_store
        )

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
