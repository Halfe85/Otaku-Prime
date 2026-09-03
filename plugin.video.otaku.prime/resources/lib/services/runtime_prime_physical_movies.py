# -*- coding: utf-8 -*-
"""Final runtime composition for Prime TV and Movies physical libraries."""
from __future__ import annotations

import os

from resources.lib.logging_config import get_logger
from resources.lib.services.age_content_policy import AgeContentPolicyStore
from resources.lib.services.kodi_age_gate import (
    remove_movie_from_kodi,
    remove_prime_directory,
    remove_tvshow_from_kodi,
)
from resources.lib.services.kodi_scan_reliable import ReliableKodiVideoLibraryScanQueue
from resources.lib.services.kodi_scan_verify_prime import verify_prime_movie, verify_prime_series
from resources.lib.services.runtime_prime_movie_physical import RuntimePrimeMoviePhysicalSupport
from resources.lib.services.runtime_prime_physical import RuntimePrimePhysicalService


LOGGER = get_logger(__name__)


class PolicyAwarePrimeMoviePhysicalSupport(RuntimePrimeMoviePhysicalSupport):
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

    def age_policy_state(self):
        return self._age_policy.state() if self._age_policy is not None else None

    def rebuild_structural_catalog_if_required(self):
        """Remove old generated projection, then requeue sources for mediation."""
        checker = getattr(self.catalog_store, "structural_rebuild_required", None)
        resetter = getattr(self.catalog_store, "reset_structural_projection", None)
        if not checker or not resetter or not checker():
            return {"rebuilt": False, "required": False}

        series_rows = list(self.catalog_store.list_series() or [])
        movie_getter = getattr(self.catalog_store, "library_movies", None)
        movie_rows = list(movie_getter() or []) if movie_getter else []
        failures = []

        for series in series_rows:
            series_id = str(series.get("local_id") or "")
            directory = self._series_directory(series_id)
            try:
                if directory:
                    remove_tvshow_from_kodi(series_id, directory)
                    remove_prime_directory(directory, os.path.join(self.root_path, "TV-Series"))
            except Exception as exc:
                failures.append("series {}: {}".format(series_id, exc))
                LOGGER.exception(
                    "Could not remove old Prime TV projection before structural rebuild: %s",
                    series_id,
                )

        for movie in movie_rows:
            movie_id = str(movie.get("local_id") or "")
            directory = self._movies.movie_directory(movie_id)
            try:
                if directory:
                    remove_movie_from_kodi(movie_id, directory)
                    remove_prime_directory(directory, os.path.join(self.root_path, "Movies"))
            except Exception as exc:
                failures.append("movie {}: {}".format(movie_id, exc))
                LOGGER.exception(
                    "Could not remove old Prime movie projection before structural rebuild: %s",
                    movie_id,
                )

        if failures:
            LOGGER.error(
                "Structural mediator rebuild postponed because generated library cleanup failed: %s",
                "; ".join(failures),
            )
            return {
                "rebuilt": False,
                "required": True,
                "cleanup_failed": True,
                "failures": failures,
            }

        result = resetter()
        result["required"] = True
        LOGGER.warning(
            "Prime generated library cleared for structural re-mediation: %s",
            result,
        )
        return result

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
                LOGGER.exception("Kodi age policy could not remove Prime TV show %s", series_id)
            try:
                physical = remove_prime_directory(
                    directory, os.path.join(self.root_path, "TV-Series")
                )
            except Exception as exc:
                physical = {"removed": False, "error": str(exc)}
                LOGGER.exception("Prime age policy could not remove physical TV show %s", series_id)
        else:
            physical = {"removed": False, "reason": "directory_unknown"}
        return {
            "series_id": series_id, "missing": False, "blocked": True,
            "age_policy": decision, "kodi_remove": kodi, "physical_remove": physical,
        }

    def _exclude_movie_from_kodi(self, movie, decision):
        movie_id = str(movie.get("local_id") or "")
        directory = self._movies.movie_directory(movie_id) if hasattr(self, "_movies") else None
        kodi = {"removed": False, "reason": "not_checked"}
        if directory:
            try:
                kodi = remove_movie_from_kodi(movie_id, directory)
            except Exception:
                LOGGER.exception("Kodi age policy could not remove Prime movie %s", movie_id)
            try:
                physical = remove_prime_directory(
                    directory, os.path.join(self.root_path, "Movies")
                )
            except Exception as exc:
                physical = {"removed": False, "error": str(exc)}
                LOGGER.exception("Prime age policy could not remove physical movie %s", movie_id)
        else:
            physical = {"removed": False, "reason": "directory_unknown"}
        return {
            "movie_id": movie_id, "missing": False, "blocked": True,
            "age_policy": decision, "kodi_remove": kodi, "physical_remove": physical,
        }

    def project_series(self, series_id, _log_result=True):
        series, decision = self._series_policy(series_id)
        if series is not None and decision is not None and not decision["kodi_allowed"]:
            return self._exclude_series_from_kodi(series, decision)

        directory = self._series_directory(series_id)
        preprojected = None
        if directory:
            preprojected = self._strm_writer.write_series(
                series_id, directory, now_epoch=int(self._now())
            )

        result = super().project_series(series_id, _log_result=_log_result)
        if preprojected is not None and not result.get("missing"):
            result["strm"] = preprojected
        if decision is not None and not result.get("missing"):
            result["age_policy"] = decision
        return result

    def project_movie(self, movie_id):
        movie = self._movies._movie_row(movie_id)
        if movie is not None and self._age_policy is not None:
            decision = self._age_policy.evaluate(movie)
            if not decision["kodi_allowed"]:
                return self._exclude_movie_from_kodi(movie, decision)
        return super().project_movie(movie_id)

    def _purge_blocked_before_startup_scan(self):
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
        return {"series": series_removed, "movies": movies_removed}

    def project_all(self):
        self._purge_blocked_before_startup_scan()
        result = super().project_all()
        result["age_policy"] = self.age_policy_state()
        return result

    def reconcile_age_policy(self):
        if self._age_policy is None:
            return {"skipped": True, "series": 0, "movies": 0}
        state = self._age_policy.state()
        series_count = movie_count = blocked = failed = 0
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
        return {
            "skipped": False, "series": series_count, "movies": movie_count,
            "blocked": blocked, "failed": failed, "policy": state,
        }

    def apply_mature_preference(self, mature=None):
        return self.reconcile_age_policy()
