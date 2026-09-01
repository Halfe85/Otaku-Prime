# -*- coding: utf-8 -*-
"""Write playable Kodi STRM files for Prime catalogue episodes."""
from __future__ import annotations

import os
import tempfile

from resources.lib.logging_config import get_logger
from resources.lib.services.prime_physical import safe_library_name
from resources.lib.services.watchlist_release import release_epoch


LOGGER = get_logger(__name__)
PLUGIN_BASE = "plugin://plugin.video.otaku.prime/play/library/"


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _write_if_changed(path, payload):
    encoded = payload.encode("utf-8")
    try:
        with open(path, "rb") as handle:
            if handle.read() == encoded:
                return False
    except FileNotFoundError:
        pass

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=directory, prefix=".prime-strm-", suffix=".tmp",
        delete=False,
    )
    temporary = handle.name
    try:
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return True


class PrimeStrmWriter:
    """Replace placeholder STRMs with stable Prime episode playback URLs."""

    def __init__(self, catalog_store):
        self.catalog_store = catalog_store

    def write_series(self, series_id, series_directory, now_epoch):
        getter = getattr(self.catalog_store, "get_series", None)
        if getter:
            series = getter(str(series_id))
        else:
            wanted = str(series_id)
            series = next(
                (row for row in self.catalog_store.list_series()
                 if str(row.get("local_id")) == wanted),
                None,
            )
        if not series:
            return {"written": 0, "unchanged": 0, "missing": True}

        seasons = self.catalog_store.list_seasons(series["local_id"])
        title = safe_library_name(
            series.get("english_name") or series.get("romaji_name"),
            fallback="Untitled {}".format(series["local_id"]),
        )
        written = unchanged = 0

        for season in seasons:
            season_number = _integer(season.get("season_number"))
            if season_number is None or season_number < 0:
                continue
            for episode in self.catalog_store.list_episodes(season["local_id"]):
                episode_number = _integer(episode.get("episode_number"))
                if episode_number is None or episode_number <= 0:
                    continue
                released = release_epoch(episode.get("release_date"))
                if not released or int(released) > int(now_epoch):
                    continue
                episode_id = str(episode.get("local_id") or "").strip()
                if not episode_id:
                    continue

                season_directory = os.path.join(
                    series_directory, "Season {:02d}".format(season_number)
                )
                filename = "{} - S{:02d}E{:02d}.strm".format(
                    title, season_number, episode_number
                )
                target = os.path.join(season_directory, filename)
                payload = PLUGIN_BASE + episode_id + "\n"
                if _write_if_changed(target, payload):
                    written += 1
                else:
                    unchanged += 1

        LOGGER.info(
            "Prime STRM projected series %s: written=%s unchanged=%s",
            series.get("local_id"), written, unchanged,
        )
        return {"written": written, "unchanged": unchanged, "missing": False}
