# -*- coding: utf-8 -*-
"""Generate Kodi .strm library files from Prime media records."""

from __future__ import annotations

import os
import re
from typing import Iterable


INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(value: object, fallback: str) -> str:
    name = INVALID_FILENAME.sub("_", str(value or "")).strip().strip(".")
    return name or fallback


class StreamLibraryService:
    def __init__(self, library_root: str, addon_id: str = "plugin.video.otaku.prime"):
        self.library_root = library_root
        self.addon_id = addon_id

    @property
    def tv_series_root(self) -> str:
        return os.path.join(self.library_root, "tv-series")

    def initialize(self) -> None:
        os.makedirs(self.tv_series_root, exist_ok=True)

    def episode_path(self, series: dict, episode: dict) -> str:
        series_name = safe_name(
            series.get("english_name") or series.get("romaji_name"),
            "Series {}".format(series["local_id"]),
        )
        if series.get("year"):
            series_name = "{} ({})".format(series_name, int(series["year"]))
        season = int(episode["season_number"])
        number = int(episode["episode_number"])
        filename = "{} - S{:02d}E{:02d}.strm".format(
            series_name, season, number
        )
        return os.path.join(
            self.tv_series_root,
            series_name,
            "season {:02d}".format(season),
            filename,
        )

    def write_episode(self, series: dict, episode: dict) -> str:
        path = self.episode_path(series, episode)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stream_url = "plugin://{}/play/episode/{}\n".format(
            self.addon_id, episode.get("local_id", episode.get("local_episode_id"))
        )
        if not os.path.exists(path) or self._read(path) != stream_url:
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(stream_url)
            os.replace(temporary, path)
        return path

    @staticmethod
    def _read(path: str) -> str:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def write_series(self, series: dict, episodes: Iterable[dict]) -> list:
        return [self.write_episode(series, episode) for episode in episodes]
