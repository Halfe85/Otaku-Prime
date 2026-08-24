# -*- coding: utf-8 -*-
"""The only service boundary allowed to synchronize with Kodi's video DB."""

from __future__ import annotations

import json
import os
from typing import Callable, Optional

from resources.lib.connectors.kodi_library import (
    KodiLibraryConnector,
    KodiLibrarySynchronizer,
)


class KodiDbMiddleware:
    """Use JSON-RPC to read/change Kodi state; never open MyVideos*.db."""

    def __init__(self, media_store, execute_json_rpc: Optional[Callable[[str], str]] = None):
        self.media_store = media_store
        self.library = KodiLibraryConnector(execute_json_rpc)
        self._execute = self.library._execute
        self._request_id = 1000

    def synchronize_links(self) -> dict:
        return KodiLibrarySynchronizer(self.library, self.media_store).sync()

    def scan(self, directory: str) -> None:
        if not self.is_video_source(directory):
            raise RuntimeError(
                "Kodi video source is not configured: {}".format(directory)
            )
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "VideoLibrary.Scan",
            "params": {"directory": directory, "showdialogs": False},
        }
        response = json.loads(self._execute(json.dumps(payload)))
        if "error" in response:
            raise RuntimeError("Kodi library scan failed: {}".format(response["error"]))

    def video_sources(self) -> list:
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id,
                   "method": "Files.GetSources", "params": {"media": "video"}}
        response = json.loads(self._execute(json.dumps(payload)))
        if "error" in response:
            raise RuntimeError("Kodi source query failed: {}".format(response["error"]))
        return (response.get("result") or {}).get("sources") or []

    def is_video_source(self, directory: str) -> bool:
        target = os.path.normcase(os.path.normpath(directory))
        return any(
            os.path.normcase(os.path.normpath(source.get("file") or "")) == target
            for source in self.video_sources()
        )

    def set_episode_watched(self, kodi_episode_id: int, watched: bool) -> None:
        self._set_details(
            "VideoLibrary.SetEpisodeDetails",
            {"episodeid": int(kodi_episode_id), "playcount": 1 if watched else 0},
        )

    def set_series_watched(self, kodi_tvshow_id: int, watched: bool) -> None:
        self._set_details(
            "VideoLibrary.SetTVShowDetails",
            {"tvshowid": int(kodi_tvshow_id), "playcount": 1 if watched else 0},
        )

    def set_movie_watched(self, kodi_movie_id: int, watched: bool) -> None:
        self._set_details(
            "VideoLibrary.SetMovieDetails",
            {"movieid": int(kodi_movie_id), "playcount": 1 if watched else 0},
        )

    def _set_details(self, method: str, params: dict) -> None:
        self._request_id += 1
        response = json.loads(
            self._execute(
                json.dumps(
                    {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
                )
            )
        )
        if "error" in response:
            raise RuntimeError("Kodi JSON-RPC update failed: {}".format(response["error"]))
