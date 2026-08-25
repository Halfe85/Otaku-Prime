# -*- coding: utf-8 -*-
"""Read Kodi's video library through JSON-RPC without touching MyVideos*.db."""

from __future__ import annotations

import json
from typing import Callable, Dict, Iterable, Optional

from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.logging_config import get_logger
LOGGER=get_logger(__name__)


class KodiLibraryConnector:
    """Small JSON-RPC boundary used by future catalogue synchronization jobs."""

    def __init__(self, execute_json_rpc: Optional[Callable[[str], str]] = None) -> None:
        if execute_json_rpc is None:
            import xbmc

            execute_json_rpc = xbmc.executeJSONRPC
        self._execute = execute_json_rpc
        self._request_id = 0

    def _call(self, method: str, properties: Iterable[str], **params) -> Dict[str, object]:
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": dict(params, properties=list(properties)),
        }
        response = json.loads(self._execute(json.dumps(request)))
        if "error" in response:
            LOGGER.error("Kodi JSON-RPC %s failed: %s",method,response["error"])
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
            ("title", "originaltitle", "showtitle", "season", "episode", "file",
             "uniqueid", "tvshowid", "playcount", "lastplayed"),
        )
        return result.get("episodes", [])

    def inventory(self) -> dict:
        shows=list(self.get_tvshows())
        episodes=list(self.get_episodes())
        return {"available":True,"empty":not shows and not episodes,
                "shows":shows,"episodes":episodes}


class KodiLibraryInventoryService:
    """Read Kodi first, without importing Kodi records into Prime's watchlist."""

    def __init__(self, library, inventory_store):
        self.library=library; self.inventory_store=inventory_store

    def run_once(self):
        try:
            snapshot=self.library.inventory()
            result=self.inventory_store.replace_snapshot(
              snapshot["shows"],snapshot["episodes"])
            LOGGER.info("Kodi inventory complete: %s shows, %s episodes",
              result["show_count"],result["episode_count"])
            if result["empty"]: LOGGER.warning("Kodi video database is available but empty")
            return result
        except Exception as exc:
            self.inventory_store.mark_unavailable(exc)
            LOGGER.exception("Kodi inventory failed")
            raise


class KodiOwnershipReconciler:
    """Match resolved Prime episodes to Kodi; an existing local file always wins."""

    def __init__(self, inventory_store):
        self.store=inventory_store

    @staticmethod
    def _id(item, provider):
        ids=item.get("unique_ids") or {}
        aliases={"tmdb":("tmdb","tmdb_id"),"thetvdb":("tvdb","tvdb_id","thetvdb")}
        for key in aliases.get(provider, (provider,)):
            value=ids.get(key)
            if value not in (None, ""): return str(value)
        return None

    def run_once(self):
        shows=self.store.shows(); episodes=self.store.episodes()
        result={"local":0,"plugin":0,"missing":0,"ambiguous":0}
        for target in self.store.resolution_targets():
            provider=target.get("metadata_provider") or target.get("show_provider")
            show_ids={row["kodi_show_id"] for row in shows
              if self._id(row,provider)==str(target.get("metadata_show_id"))}
            exact=[row for row in episodes
              if self._id(row,provider)==str(target.get("metadata_episode_id"))]
            method="provider_episode_id"
            if not exact and show_ids:
                exact=[row for row in episodes
                  if row.get("kodi_show_id") in show_ids
                  and int(row.get("season_number") or -1)==int(target["kodi_season_number"])
                  and int(row.get("episode_number") or -1)==int(target["kodi_episode_number"])]
                method="provider_show_season_episode"
            local=[row for row in exact if row.get("local_content")]
            if local:
                match=local[0]
                self.store.save_ownership(target,match,"existing_local","external","local",method)
                result["local"]+=1
            elif len(exact)==1:
                match=exact[0]
                self.store.save_ownership(target,match,"existing_plugin","external","prime",method)
                result["plugin"]+=1
            elif len(exact)>1:
                self.store.save_ownership(target,{},"missing","pending","local","ambiguous")
                result["ambiguous"]+=1
            else:
                self.store.save_ownership(target,{},"missing","pending","prime","missing")
                result["missing"]+=1
        LOGGER.info("Kodi reconciliation complete: local=%s plugin=%s missing=%s ambiguous=%s",
          result["local"],result["plugin"],result["missing"],result["ambiguous"])
        if result["ambiguous"]:
            LOGGER.warning("Kodi reconciliation has %s ambiguous episode matches",result["ambiguous"])
        return result


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
