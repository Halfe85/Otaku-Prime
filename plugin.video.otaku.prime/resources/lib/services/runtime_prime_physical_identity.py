# -*- coding: utf-8 -*-
"""Prime Physical runtime with stable Prime-ID -> directory ownership."""
from __future__ import annotations

import os

from resources.lib.logging_config import get_logger
from resources.lib.services.kodi_prime_cleanup import remove_prime_tvshows
from resources.lib.services.physical_library_identity import (
    MEDIA_MOVIE,
    MEDIA_SERIES,
    PhysicalLibraryIdentityRegistry,
)
from resources.lib.services.prime_physical import safe_library_name
from resources.lib.services.runtime_prime_physical import RuntimePrimePhysicalService
from resources.lib.services.watchlist_release import release_epoch


LOGGER = get_logger(__name__)


class IdentityRuntimePrimePhysicalService(RuntimePrimePhysicalService):
    """Project each Prime ID through one persistent generated directory."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        db_path = getattr(self.catalog_store, "db_path", None)
        if not db_path:
            raise RuntimeError("Prime physical identity requires the catalogue database path")
        self.physical_identity = PhysicalLibraryIdentityRegistry(db_path, self.root_path)
        self.physical_identity.initialize()
        self._identity_cleanup_failed = False

    def _desired_series_directory(self, series_id):
        # Call the pre-registry implementation only to calculate current display
        # metadata. It is never treated as ownership.
        return RuntimePrimePhysicalService._series_directory(self, series_id)

    def _series_directory_info(self, series_id):
        desired = self._desired_series_directory(series_id)
        if not desired:
            return None
        return self.physical_identity.resolve(MEDIA_SERIES, str(series_id), desired)

    def _series_directory(self, series_id):
        info = self._series_directory_info(series_id)
        return info.get("directory") if info else None

    def resolve_movie_directory(self, movie_id, desired_directory):
        return self.physical_identity.resolve(
            MEDIA_MOVIE, str(movie_id), desired_directory
        )

    def _cleanup_series_kodi_identity(self, series_id, info):
        if not info or not info.get("kodi_cleanup_pending"):
            return {"required": False, "removed": 0}
        stale = list(info.get("stale_directories") or [])
        try:
            result = remove_prime_tvshows(series_id, directories=stale)
            self.physical_identity.mark_cleanup_complete(MEDIA_SERIES, series_id)
            result["required"] = True
            result["stale_directories"] = stale
            return result
        except Exception as exc:
            self._identity_cleanup_failed = True
            LOGGER.exception(
                "Prime could not remove stale Kodi rows before re-projecting series %s",
                series_id,
            )
            return {
                "required": True,
                "removed": 0,
                "failed": True,
                "error": str(exc),
                "stale_directories": stale,
            }

    def _series_projection_plan(self, series, seasons, directory, now_epoch):
        title = safe_library_name(
            series.get("english_name") or series.get("romaji_name"),
            fallback="Untitled {}".format(series["local_id"]),
        )
        expected = []
        future = unknown = failed = 0
        for season in seasons:
            try:
                season_number = int(season.get("season_number"))
            except (TypeError, ValueError):
                failed += 1
                continue
            for episode in self.catalog_store.list_episodes(season["local_id"]):
                released = release_epoch(episode.get("release_date"))
                if not released:
                    unknown += 1
                    continue
                if int(released) > int(now_epoch):
                    future += 1
                    continue
                try:
                    episode_number = int(episode.get("episode_number"))
                except (TypeError, ValueError):
                    failed += 1
                    continue
                expected.append(os.path.join(
                    directory,
                    "Season {:02d}".format(season_number),
                    "{} - S{:02d}E{:02d}.strm".format(
                        title, season_number, episode_number
                    ),
                ))
        return {
            "expected": expected,
            "future": future,
            "unknown_release": unknown,
            "failed": failed,
        }

    def project_series(self, series_id, _log_result=True):
        """Project one series without ever deriving ownership from its current title."""
        self._check_halt()
        self.ensure_kodi_library_configuration()
        series = self._series_row(series_id)
        if not series:
            return {
                "series_id": str(series_id), "created": 0, "existing": 0,
                "future": 0, "unknown_release": 0, "failed": 0,
                "missing": True,
            }

        info = self._series_directory_info(series["local_id"])
        directory = info["directory"]
        os.makedirs(directory, exist_ok=True)
        cleanup = self._cleanup_series_kodi_identity(series["local_id"], info)
        seasons = self.catalog_store.list_seasons(series["local_id"])
        now_epoch = int(self._now())
        plan = self._series_projection_plan(series, seasons, directory, now_epoch)
        pruned = self.physical_identity.prune_series_files(
            directory, plan["expected"]
        )
        strm = self._strm_writer.write_series(
            series["local_id"], directory, now_epoch=now_epoch
        )
        nfo = self._nfo_writer.write_series(
            series["local_id"], directory, now_epoch=now_epoch
        )
        result = {
            "series_id": str(series["local_id"]),
            "created": int(strm.get("written") or 0),
            "existing": int(strm.get("unchanged") or 0),
            "future": plan["future"],
            "unknown_release": plan["unknown_release"],
            "failed": plan["failed"],
            "missing": False,
            "directory": directory,
            "directory_identity": info,
            "kodi_identity_cleanup": cleanup,
            "pruned": pruned,
            "strm": strm,
            "nfo": nfo,
        }
        if not self._bulk_projection:
            if cleanup.get("failed"):
                result["scan"] = {
                    "queued": False,
                    "path": directory,
                    "reason": "prime_identity_cleanup_pending",
                }
            else:
                result["scan"] = self.request_kodi_scan(
                    directory, reason="mediator_series"
                )
        if _log_result:
            LOGGER.info(
                "Prime Physical projected stable series %s: directory=%s written=%s "
                "unchanged=%s pruned=%s migrated=%s duplicates=%s stale=%s",
                series["local_id"], directory,
                strm.get("written"), strm.get("unchanged"), len(pruned),
                len(info.get("migrated_from") or []),
                len(info.get("duplicates_removed") or []),
                len(info.get("stale_directories") or []),
            )
        return result

    def project_all(self):
        """Reconcile identities first; only then scan the two Prime source roots."""
        self.ensure_kodi_library_configuration()
        os.makedirs(self.source_url, exist_ok=True)
        os.makedirs(self.movie_source_url, exist_ok=True)
        self._identity_cleanup_failed = False

        total = {
            "series": 0, "created": 0, "existing": 0, "future": 0,
            "unknown_release": 0, "failed": 0,
        }
        self._bulk_projection = True
        try:
            for series in self.catalog_store.list_series():
                self._check_halt()
                result = self.project_series(series["local_id"], _log_result=False)
                total["series"] += 1
                for field in ("created", "existing", "future", "unknown_release", "failed"):
                    total[field] += int(result.get(field) or 0)
            movie_result = self._movies.project_all(now_epoch=int(self._now()))
        finally:
            self._bulk_projection = False

        total["movies"] = movie_result
        if self._identity_cleanup_failed:
            total["final_scan"] = {
                "queued": False,
                "path": self.source_url,
                "reason": "prime_identity_cleanup_pending",
            }
            total["final_movie_scan"] = {
                "queued": False,
                "path": self.movie_source_url,
                "reason": "prime_identity_cleanup_pending",
            }
        else:
            total["final_scan"] = self.request_kodi_scan(
                self.source_url, reason="prime_startup_backfill"
            )
            total["final_movie_scan"] = self.request_kodi_scan(
                self.movie_source_url, reason="prime_startup_movies_backfill"
            )
        return total
