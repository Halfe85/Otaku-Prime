# -*- coding: utf-8 -*-
"""Read Kodi's video library through JSON-RPC without touching MyVideos*.db."""

from __future__ import annotations

import json
from typing import Callable, Dict, Iterable, Optional

from resources.lib.database.watchlist_media import WatchlistMediaStore


class KodiLibraryConnector:
    """Small JSON-RPC boundary used by future catalogue synchronization jobs."""

    def __init__(self, execute_json_rpc: Optional[Callable[[str], str]] = None) -> None:
        if execute_json_rpc is None:
            import xbmc

            execute_json_rpc = xbmc.executeJSONRPC
        self._execute = execute_json_rpc
        self._request_id = 0

    def _call(self, method: str, properties: Iterable[str]) -> Dict[str, object]:
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": {"properties": list(properties)},
        }
        response = json.loads(self._execute(json.dumps(request)))
        if "error" in response:
            raise RuntimeError("Kodi JSON-RPC error: {}".format(response["error"]))
        return response.get("result", {})

    def get_tvshows(self) -> Iterable[dict]:
        result = self._call(
            "VideoLibrary.GetTVShows", ("title", "originaltitle", "year", "file", "uniqueid")
        )
        return result.get("tvshows", [])

    def get_episodes(self) -> Iterable[dict]:
        result = self._call(
            "VideoLibrary.GetEpisodes",
            ("title", "originaltitle", "showtitle", "season", "episode", "file", "uniqueid"),
        )
        return result.get("episodes", [])


class KodiLibrarySynchronizer:
    """Resolve Kodi items carrying provider IDs into Prime's local catalogue."""

    ID_ALIASES = {
        "anilist": "anilist_id",
        "anilist_id": "anilist_id",
        "mal": "mal_id",
        "mal_id": "mal_id",
        "myanimelist": "mal_id",
        "kitsu": "kitsu_id",
        "kitsu_id": "kitsu_id",
        "simkl": "simkl_id",
        "simkl_id": "simkl_id",
    }

    def __init__(
        self, library: KodiLibraryConnector, media_store: WatchlistMediaStore
    ) -> None:
        self.library = library
        self.media_store = media_store

    @classmethod
    def _provider_ids(cls, item: dict) -> Dict[str, object]:
        unique_ids = item.get("uniqueid") or {}
        return {
            cls.ID_ALIASES[str(key).lower()]: value
            for key, value in unique_ids.items()
            if str(key).lower() in cls.ID_ALIASES and value not in (None, "")
        }

    def sync(self) -> Dict[str, int]:
        counts = {"series": 0, "skipped": 0}
        for item in self.library.get_tvshows():
            ids = self._provider_ids(item)
            if not ids:
                counts["skipped"] += 1
                continue
            local_id = self.media_store.upsert_tv_series(
                english_name=item.get("title"),
                romaji_name=item.get("originaltitle"),
            )
            self.media_store.upsert_season(
                local_id, 1,
                english_name=item.get("title"),
                romaji_name=item.get("originaltitle"),
                **ids
            )
            self.media_store.link_kodi(
                "series",
                local_id,
                item["tvshowid"],
                kodi_path=item.get("file"),
            )
            counts["series"] += 1

        return counts
