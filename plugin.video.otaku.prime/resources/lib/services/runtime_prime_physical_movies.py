# -*- coding: utf-8 -*-
"""Final runtime composition for Prime TV and Movies physical libraries."""
from __future__ import annotations

import os
import threading
import time

from resources.lib.logging_config import get_logger
from resources.lib.services.age_content_policy import AgeContentPolicyStore
from resources.lib.services.kodi_age_gate import (
    remove_movie_from_kodi,
    remove_prime_directory,
    remove_tvshow_from_kodi,
)
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


LOGGER = get_logger(__name__)


class PolicyAwarePrimeMoviePhysicalSupport(RuntimePrimeMoviePhysicalSupport):
    """Apply the same administrator age policy before any movie files are written."""

    def __init__(self, physical, age_policy, artwork_store=None):
        super().__init__(physical, artwork_store=artwork_store)
        self.age_policy = age_policy

    def project_movie(self, movie_id, now_epoch):
        movie = self._movie_row(movie_id)
        if movie:
            decision = self.age_policy.evaluate(movie)
            if not decision["kodi_allowed"]:
                return self.physical._exclude_movie_from_kodi(movie, decision)
        result = super().project_movie(movie_id, now_epoch)
        if movie and not result.get("missing"):
            result["age_policy"] = self.age_policy.evaluate(movie)
        return result


class RuntimePrimePhysicalMoviesService(RuntimePrimePhysicalService):
    """Reliable Kodi scans, local-ID STRMs, and administrator age admission policy."""

    def __init__(self, *args, artwork_store=None, **kwargs):
        injected_scan_queue = kwargs.get("scan_queue")
        catalog_store = args[0] if args else kwargs.get("catalog_store")
        db_path = getattr(catalog_store, "db_path", None) if catalog_store is not None else None
        self._age_policy = AgeContentPolicyStore(db_path) if db_path else None
        if self._age_policy is not None:
            self._age_policy.initialize()

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
        self._movies = PolicyAwarePrimeMoviePhysicalSupport(
            self, self._age_policy, artwork_store=artwork_store
        ) if self._age_policy is not None else RuntimePrimeMoviePhysicalSupport(
            self, artwork_store=artwork_store
        )

        self._last_age_policy_signature = self._policy_signature()
        self._age_policy_thread = None
        if self._age_policy is not None:
            self._age_policy_thread = threading.Thread(
                target=self._watch_age_policy,
                name="OtakuPrimeKodiAgePolicy",
                daemon=True,
            )
            self._age_policy_thread.start()

    def _policy_signature(self):
        if self._age_policy is None:
            return None
        state = self._age_policy.state()
        return (
            state.get("birth_date"),
            state.get("age"),
            int(state.get("mature") or 0),
        )

    def _watch_age_policy(self):
        """Apply admin DOB/mature changes to Kodi without requiring a restart."""
        while not self._halt_requested():
            try:
                current = self._policy_signature()
                if current != self._last_age_policy_signature:
                    previous = self._last_age_policy_signature
                    self._last_age_policy_signature = current
                    LOGGER.info(
                        "Kodi age policy changed: previous=%s current=%s",
                        previous, current,
                    )
                    self.reconcile_age_policy()
            except Exception:
                LOGGER.exception("Kodi age policy watcher failed")
            time.sleep(0.5)

    def _series_policy(self, series_id):
        series = self._series_row(series_id)
        if not series or self._age_policy is None:
            return series, None
        return series, self._age_policy.evaluate(series)

    def _exclude_series_from_kodi(self, series, decision):
        series_id = str(series.get("local_id") or "")
        directory = self._series_directory(series_id)
        kodi = {"removed": False, "reason": "not_checked"}
        if directory:
            try:
                kodi = remove_tvshow_from_kodi(series_id, directory)
            except Exception:
                LOGGER.exception(
                    "Kodi age policy could not remove Prime TV show %s", series_id
                )
            try:
                physical = remove_prime_directory(
                    directory, os.path.join(self.root_path, "TV-Series")
                )
            except Exception as exc:
                physical = {"removed": False, "error": str(exc)}
                LOGGER.exception(
                    "Prime age policy could not remove physical TV show %s", series_id
                )
        else:
            physical = {"removed": False, "reason": "directory_unknown"}
        LOGGER.info(
            "Prime TV show excluded from Kodi by age policy: prime=%s rating=%s age=%s reason=%s",
            series_id, decision.get("rating"), decision.get("age"), decision.get("reason"),
        )
        return {
            "series_id": series_id,
            "missing": False,
            "blocked": True,
            "age_policy": decision,
            "kodi_remove": kodi,
            "physical_remove": physical,
        }

    def _exclude_movie_from_kodi(self, movie, decision):
        movie_id = str(movie.get("local_id") or "")
        directory = self._movies.movie_directory(movie_id) if hasattr(self, "_movies") else None
        kodi = {"removed": False, "reason": "not_checked"}
        if directory:
            try:
                kodi = remove_movie_from_kodi(movie_id, directory)
            except Exception:
                LOGGER.exception(
                    "Kodi age policy could not remove Prime movie %s", movie_id
                )
            try:
                physical = remove_prime_directory(
                    directory, os.path.join(self.root_path, "Movies")
                )
            except Exception as exc:
                physical = {"removed": False, "error": str(exc)}
                LOGGER.exception(
                    "Prime age policy could not remove physical movie %s", movie_id
                )
        else:
            physical = {"removed": False, "reason": "directory_unknown"}
        LOGGER.info(
            "Prime movie excluded from Kodi by age policy: prime=%s rating=%s age=%s reason=%s",
            movie_id, decision.get("rating"), decision.get("age"), decision.get("reason"),
        )
        return {
            "movie_id": movie_id,
            "missing": False,
            "blocked": True,
            "age_policy": decision,
            "kodi_remove": kodi,
            "physical_remove": physical,
        }

    def project_series(self, series_id, _log_result=True):
        """Gate the show, then write local-ID playback URLs before Kodi sees it."""
        series, decision = self._series_policy(series_id)
        if series is not None and decision is not None and not decision["kodi_allowed"]:
            return self._exclude_series_from_kodi(series, decision)

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
            result["strm"] = preprojected
        if decision is not None and not result.get("missing"):
            result["age_policy"] = decision
        return result

    def project_movie(self, movie_id):
        """Gate the standalone movie before delegating to the physical projector."""
        movie = self._movies._movie_row(movie_id)
        if movie is not None and self._age_policy is not None:
            decision = self._age_policy.evaluate(movie)
            if not decision["kodi_allowed"]:
                return self._exclude_movie_from_kodi(movie, decision)
        return super().project_movie(movie_id)

    def _purge_blocked_before_startup_scan(self):
        """Remove restricted leftovers before Kodi's startup source scan can see them."""
        if self._age_policy is None:
            return {"series": 0, "movies": 0}
        series_removed = movies_removed = 0
        for series in self.catalog_store.list_series():
            decision = self._age_policy.evaluate(series)
            if not decision["kodi_allowed"]:
                self._exclude_series_from_kodi(series, decision)
                series_removed += 1
        movie_getter = getattr(self.catalog_store, "library_movies", None)
        for movie in list(movie_getter() or []) if movie_getter else []:
            decision = self._age_policy.evaluate(movie)
            if not decision["kodi_allowed"]:
                self._exclude_movie_from_kodi(movie, decision)
                movies_removed += 1
        LOGGER.info(
            "Prime age policy startup purge complete: series=%s movies=%s",
            series_removed, movies_removed,
        )
        return {"series": series_removed, "movies": movies_removed}

    def project_all(self):
        self._purge_blocked_before_startup_scan()
        result = super().project_all()
        result["age_policy"] = self._age_policy.state() if self._age_policy else None
        return result

    def reconcile_age_policy(self):
        """Re-project allowed titles and remove newly restricted ones immediately."""
        if self._age_policy is None:
            return {"skipped": True, "series": 0, "movies": 0}
        state = self._age_policy.state()
        series_count = movie_count = blocked = failed = 0
        LOGGER.info(
            "Reconciling Prime Kodi age policy: age=%s mature=%s configured=%s",
            state.get("age"), state.get("mature"), bool(state.get("birth_date")),
        )
        for series in self.catalog_store.list_series():
            if self._halt_requested():
                break
            try:
                result = self.project_series(series["local_id"])
                series_count += 1
                blocked += int(bool(result.get("blocked")))
            except Exception:
                failed += 1
                LOGGER.exception(
                    "Prime age policy reconciliation failed for TV show %s",
                    series.get("local_id"),
                )
        movie_getter = getattr(self.catalog_store, "library_movies", None)
        for movie in list(movie_getter() or []) if movie_getter else []:
            if self._halt_requested():
                break
            try:
                result = self.project_movie(movie["local_id"])
                movie_count += 1
                blocked += int(bool(result.get("blocked")))
            except Exception:
                failed += 1
                LOGGER.exception(
                    "Prime age policy reconciliation failed for movie %s",
                    movie.get("local_id"),
                )
        summary = {
            "skipped": False,
            "series": series_count,
            "movies": movie_count,
            "blocked": blocked,
            "failed": failed,
            "policy": state,
        }
        LOGGER.info(
            "Prime Kodi age policy reconciled: series=%s movies=%s blocked=%s failed=%s",
            series_count, movie_count, blocked, failed,
        )
        return summary

    # Compatibility with the previous Alpha implementation and any callers that
    # still name the old operation directly.
    def apply_mature_preference(self, mature=None):
        return self.reconcile_age_policy()
