# -*- coding: utf-8 -*-
"""Final runtime composition for stable Prime TV and Movies physical libraries."""
from __future__ import annotations

import os

from resources.lib.logging_config import get_logger
from resources.lib.services.age_content_policy import AgeContentPolicyStore
from resources.lib.services.kodi_age_gate import remove_prime_directory
from resources.lib.services.kodi_prime_cleanup import (
    remove_all_prime_video,
    remove_prime_movies,
    remove_prime_tvshows,
)
from resources.lib.services.kodi_scan_reliable import ReliableKodiVideoLibraryScanQueue
from resources.lib.services.kodi_scan_verify_prime import verify_prime_movie, verify_prime_series
from resources.lib.services.physical_library_identity import MEDIA_MOVIE, MEDIA_SERIES
from resources.lib.services.prime_physical import safe_library_name
from resources.lib.services.runtime_prime_movie_physical import RuntimePrimeMoviePhysicalSupport
from resources.lib.services.runtime_prime_physical_identity import IdentityRuntimePrimePhysicalService


LOGGER = get_logger(__name__)


class PolicyAwarePrimeMoviePhysicalSupport(RuntimePrimeMoviePhysicalSupport):
    """Movies projection with age policy and persistent Prime directory identity."""

    def __init__(self, physical, age_policy, artwork_store=None):
        super().__init__(physical, artwork_store=artwork_store)
        self.age_policy = age_policy

    def _directory_info(self, movie_id):
        desired = super().movie_directory(movie_id)
        if not desired:
            return None
        return self.physical.resolve_movie_directory(movie_id, desired)

    def movie_directory(self, movie_id):
        info = self._directory_info(movie_id)
        return info.get("directory") if info else None

    def _cleanup_kodi_identity(self, movie_id, info):
        if not info or not info.get("kodi_cleanup_pending"):
            return {"required": False, "removed": 0}
        stale = list(info.get("migrated_from") or []) + list(
            info.get("duplicates_removed") or []
        )
        try:
            result = remove_prime_movies(movie_id, directories=stale)
            self.physical.physical_identity.mark_cleanup_complete(MEDIA_MOVIE, movie_id)
            result["required"] = True
            return result
        except Exception as exc:
            self.physical._identity_cleanup_failed = True
            LOGGER.exception(
                "Prime could not remove stale Kodi rows before re-projecting movie %s",
                movie_id,
            )
            return {
                "required": True, "removed": 0, "failed": True,
                "error": str(exc),
            }

    def project_movie(self, movie_id, now_epoch):
        movie = self._movie_row(movie_id)
        if movie and self.age_policy is not None:
            decision = self.age_policy.evaluate(movie)
            if not decision["kodi_allowed"]:
                return self.physical._exclude_movie_from_kodi(movie, decision)
        if not movie:
            return super().project_movie(movie_id, now_epoch)

        info = self._directory_info(movie["local_id"])
        directory = info["directory"]
        cleanup = self._cleanup_kodi_identity(movie["local_id"], info)
        os.makedirs(directory, exist_ok=True)
        title = safe_library_name(
            movie.get("english_name") or movie.get("romaji_name") or movie.get("title"),
            fallback="Untitled {}".format(movie["local_id"]),
        )
        stem = "{} {}".format(title, self._movie_year(movie))
        expected_strm = os.path.join(directory, stem + ".strm")
        expected_nfo = os.path.join(directory, stem + ".nfo")
        pruned = self.physical.physical_identity.prune_movie_files(
            movie["local_id"], directory, expected_strm, expected_nfo
        )
        result = super().project_movie(movie["local_id"], now_epoch)
        result["directory_identity"] = info
        result["kodi_identity_cleanup"] = cleanup
        result["pruned"] = pruned
        if self.age_policy is not None and not result.get("missing"):
            result["age_policy"] = self.age_policy.evaluate(movie)
        return result


class RuntimePrimePhysicalMoviesService(IdentityRuntimePrimePhysicalService):
    """Stable physical identity, reliable Kodi scans, and age admission policy."""

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
        )

    def age_policy_state(self):
        return self._age_policy.state() if self._age_policy is not None else None

    def rebuild_structural_catalog_if_required(self):
        """Perform one ID-based physical/Kodi cleanup before catalogue re-mediation."""
        checker = getattr(self.catalog_store, "structural_rebuild_required", None)
        resetter = getattr(self.catalog_store, "reset_structural_projection", None)
        if not checker or not resetter or not checker():
            return {"rebuilt": False, "required": False}

        failures = []
        tv_directories = list(self.physical_identity.discover(MEDIA_SERIES))
        movie_directories = list(self.physical_identity.discover(MEDIA_MOVIE))

        # Remove every Kodi row that advertises a Prime unique ID. This also
        # catches historical paths whose physical directory has already vanished.
        try:
            kodi = remove_all_prime_video()
        except Exception as exc:
            failures.append("Kodi Prime cleanup: {}".format(exc))
            kodi = {"removed": 0, "error": str(exc)}
            LOGGER.exception("Could not remove old Prime rows from Kodi before rebuild")

        for entry in tv_directories:
            try:
                remove_prime_directory(
                    entry["directory"], os.path.join(self.root_path, "TV-Series")
                )
            except Exception as exc:
                failures.append("TV {}: {}".format(entry["directory"], exc))
                LOGGER.exception(
                    "Could not remove Prime-owned TV directory before rebuild: %s",
                    entry["directory"],
                )

        for entry in movie_directories:
            try:
                remove_prime_directory(
                    entry["directory"], os.path.join(self.root_path, "Movies")
                )
            except Exception as exc:
                failures.append("Movie {}: {}".format(entry["directory"], exc))
                LOGGER.exception(
                    "Could not remove Prime-owned movie directory before rebuild: %s",
                    entry["directory"],
                )

        if failures:
            LOGGER.error(
                "Prime identity rebuild postponed because cleanup failed: %s",
                "; ".join(failures),
            )
            return {
                "rebuilt": False, "required": True, "cleanup_failed": True,
                "failures": failures, "kodi": kodi,
                "physical_tv": len(tv_directories),
                "physical_movies": len(movie_directories),
            }

        self.physical_identity.clear()
        result = resetter()
        result.update({
            "required": True,
            "kodi": kodi,
            "physical_tv": len(tv_directories),
            "physical_movies": len(movie_directories),
        })
        LOGGER.warning(
            "Prime generated library removed by NFO/Prime identity for controlled rebuild: %s",
            result,
        )
        return result

    def _series_policy(self, series_id):
        series = self._series_row(series_id)
        if not series or self._age_policy is None:
            return series, None
        return series, self._age_policy.evaluate(series)

    def _known_series_directories(self, series_id):
        rows = self.physical_identity.discover(MEDIA_SERIES, prime_id=series_id)
        mapped = self.physical_identity.mapped(MEDIA_SERIES, series_id)
        result = [row["directory"] for row in rows]
        if mapped and mapped.get("directory") not in result:
            result.append(mapped["directory"])
        return result

    def _known_movie_directories(self, movie_id):
        rows = self.physical_identity.discover(MEDIA_MOVIE, prime_id=movie_id)
        mapped = self.physical_identity.mapped(MEDIA_MOVIE, movie_id)
        result = [row["directory"] for row in rows]
        if mapped and mapped.get("directory") not in result:
            result.append(mapped["directory"])
        return result

    def _exclude_series_from_kodi(self, series, decision):
        series_id = str(series.get("local_id") or "")
        directories = self._known_series_directories(series_id)
        try:
            kodi = remove_prime_tvshows(series_id, directories=directories)
        except Exception as exc:
            kodi = {"removed": 0, "error": str(exc)}
            LOGGER.exception("Kodi age policy could not remove Prime TV show %s", series_id)
        removed = []
        for directory in directories:
            try:
                result = remove_prime_directory(
                    directory, os.path.join(self.root_path, "TV-Series")
                )
                if result.get("removed"):
                    removed.append(directory)
            except Exception:
                LOGGER.exception(
                    "Prime age policy could not remove physical TV show %s at %s",
                    series_id, directory,
                )
        return {
            "series_id": series_id, "missing": False, "blocked": True,
            "age_policy": decision, "kodi_remove": kodi,
            "physical_remove": {"removed": len(removed), "directories": removed},
        }

    def _exclude_movie_from_kodi(self, movie, decision):
        movie_id = str(movie.get("local_id") or "")
        directories = self._known_movie_directories(movie_id)
        try:
            kodi = remove_prime_movies(movie_id, directories=directories)
        except Exception as exc:
            kodi = {"removed": 0, "error": str(exc)}
            LOGGER.exception("Kodi age policy could not remove Prime movie %s", movie_id)
        removed = []
        for directory in directories:
            try:
                result = remove_prime_directory(
                    directory, os.path.join(self.root_path, "Movies")
                )
                if result.get("removed"):
                    removed.append(directory)
            except Exception:
                LOGGER.exception(
                    "Prime age policy could not remove physical movie %s at %s",
                    movie_id, directory,
                )
        return {
            "movie_id": movie_id, "missing": False, "blocked": True,
            "age_policy": decision, "kodi_remove": kodi,
            "physical_remove": {"removed": len(removed), "directories": removed},
        }

    def project_series(self, series_id, _log_result=True):
        series, decision = self._series_policy(series_id)
        if series is not None and decision is not None and not decision["kodi_allowed"]:
            return self._exclude_series_from_kodi(series, decision)
        result = super().project_series(series_id, _log_result=_log_result)
        if decision is not None and not result.get("missing"):
            result["age_policy"] = decision
        return result

    def project_movie(self, movie_id):
        # Avoid RuntimePrimePhysicalService.project_movie because it queues a
        # scan before we can inspect identity-cleanup status.
        result = self._movies.project_movie(movie_id, now_epoch=int(self._now()))
        directory = result.get("directory")
        cleanup = result.get("kodi_identity_cleanup") or {}
        if (
            not self._bulk_projection
            and not result.get("missing")
            and not result.get("future")
            and not result.get("blocked")
            and directory
            and os.path.isdir(directory)
        ):
            if cleanup.get("failed"):
                result["scan"] = {
                    "queued": False, "path": directory,
                    "reason": "prime_identity_cleanup_pending",
                }
            else:
                result["scan"] = self.request_kodi_scan(
                    directory, reason="mediator_movie"
                )
        return result

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
