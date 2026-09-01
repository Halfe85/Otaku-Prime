# -*- coding: utf-8 -*-
"""Project released Prime catalogue episodes into Kodi's physical library."""
from __future__ import annotations

import os
import re
import time

from resources.lib.logging_config import get_logger
from resources.lib.service_lifecycle import ServiceWorkHalted
from resources.lib.services.watchlist_release import release_epoch


LOGGER = get_logger(__name__)
SPECIAL_ROOT = "special://masterprofile/Library"
INVALID_PATH_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _default_root():
    try:
        import xbmcvfs

        translated = xbmcvfs.translatePath(SPECIAL_ROOT)
        if translated:
            return translated
    except (ImportError, RuntimeError, AttributeError):
        pass
    return os.path.join(os.path.expanduser("~"), ".kodi", "userdata", "Library")


def safe_library_name(value, fallback="Untitled"):
    """Return one portable path component without changing readable titles."""
    text = INVALID_PATH_CHARACTERS.sub(" - ", str(value or "").strip())
    text = " ".join(text.split()).strip(" .")
    return (text or str(fallback)).strip()[:180].rstrip(" .")


class PrimePhysicalService:
    """Create zero-byte STRM placeholders from catalogue IDs handed off by Mediator.

    Prime Physical deliberately receives only a Prime series ID. It reads the
    series, season, episode, title, year, and release data from the catalogue so
    the filesystem layer never depends on provider placement payloads.
    """

    def __init__(self, catalog_store, root_path=None, now=None, halt_requested=None):
        self.catalog_store = catalog_store
        self.root_path = os.path.abspath(str(root_path or _default_root()))
        self._now = now or time.time
        self._halt_requested = halt_requested or (lambda: False)

    def _check_halt(self):
        if self._halt_requested():
            raise ServiceWorkHalted("physical library projection halted for addon shutdown")

    @staticmethod
    def _series_year(series, seasons):
        year = series.get("publish_year")
        if year not in (None, "") and str(year)[:4].isdigit():
            return str(year)[:4]
        dates = sorted(
            str(season.get("release_date"))
            for season in seasons
            if str(season.get("release_date") or "")[:4].isdigit()
        )
        return dates[0][:4] if dates else "Unknown"

    @staticmethod
    def _released(episode, now_epoch):
        epoch = release_epoch((episode or {}).get("release_date"))
        return bool(epoch and epoch <= int(now_epoch))

    def _series_row(self, series_id):
        getter = getattr(self.catalog_store, "get_series", None)
        if getter:
            return getter(str(series_id))
        wanted = str(series_id)
        return next(
            (series for series in self.catalog_store.list_series()
             if str(series.get("local_id")) == wanted),
            None,
        )

    def project_series(self, series_id, _log_result=True):
        """Create missing released episode placeholders for one Prime series ID."""
        self._check_halt()
        series = self._series_row(series_id)
        if not series:
            LOGGER.warning("Prime Physical handoff ignored unknown series ID %s", series_id)
            return {
                "series_id": str(series_id), "created": 0, "existing": 0,
                "future": 0, "unknown_release": 0, "failed": 0,
                "missing": True,
            }

        seasons = self.catalog_store.list_seasons(series["local_id"])
        title = safe_library_name(
            series.get("english_name") or series.get("romaji_name"),
            fallback="Untitled {}".format(series["local_id"]),
        )
        year = self._series_year(series, seasons)
        series_directory = os.path.join(
            self.root_path, "TV-Series", "{} {}".format(title, year)
        )
        now_epoch = int(self._now())
        result = {
            "series_id": str(series["local_id"]), "created": 0, "existing": 0,
            "future": 0, "unknown_release": 0, "failed": 0,
            "missing": False,
        }

        for season in seasons:
            self._check_halt()
            try:
                season_number = int(season.get("season_number"))
            except (TypeError, ValueError):
                LOGGER.warning(
                    "Prime Physical skipped invalid season coordinate for series %s: %r",
                    series["local_id"], season.get("season_number"),
                )
                result["failed"] += 1
                continue
            for episode in self.catalog_store.list_episodes(season["local_id"]):
                self._check_halt()
                release_value = episode.get("release_date")
                release_value_epoch = release_epoch(release_value)
                if not release_value_epoch:
                    result["unknown_release"] += 1
                    continue
                if not self._released(episode, now_epoch):
                    result["future"] += 1
                    continue
                try:
                    episode_number = int(episode.get("episode_number"))
                except (TypeError, ValueError):
                    LOGGER.warning(
                        "Prime Physical skipped invalid episode coordinate for series %s: %r",
                        series["local_id"], episode.get("episode_number"),
                    )
                    result["failed"] += 1
                    continue

                season_directory = os.path.join(
                    series_directory, "Season {:02d}".format(season_number)
                )
                filename = "{} - S{:02d}E{:02d}.strm".format(
                    title, season_number, episode_number
                )
                target = os.path.join(season_directory, filename)
                try:
                    os.makedirs(season_directory, exist_ok=True)
                    # Never truncate a future playable STRM body. Exclusive
                    # creation keeps this placeholder stage forward-compatible.
                    with open(target, "xb"):
                        pass
                    result["created"] += 1
                except FileExistsError:
                    result["existing"] += 1
                except OSError:
                    result["failed"] += 1
                    LOGGER.exception(
                        "Prime Physical could not create STRM placeholder for %s S%02dE%02d",
                        series["local_id"], season_number, episode_number,
                    )

        if _log_result:
            LOGGER.info(
                "Prime Physical projected series %s: created=%s existing=%s future=%s "
                "unknown_release=%s failed=%s root=%s",
                series["local_id"], result["created"], result["existing"],
                result["future"], result["unknown_release"], result["failed"],
                series_directory,
            )
        return result

    def project_all(self):
        """Backfill existing catalogue series after installing this service."""
        total = {"series": 0, "created": 0, "existing": 0, "future": 0,
                 "unknown_release": 0, "failed": 0}
        for series in self.catalog_store.list_series():
            self._check_halt()
            result = self.project_series(series["local_id"], _log_result=False)
            total["series"] += 1
            for field in ("created", "existing", "future", "unknown_release", "failed"):
                total[field] += int(result.get(field) or 0)
        LOGGER.info(
            "Prime Physical catalogue backfill complete: series=%s created=%s existing=%s "
            "future=%s unknown_release=%s failed=%s",
            total["series"], total["created"], total["existing"], total["future"],
            total["unknown_release"], total["failed"],
        )
        return total
