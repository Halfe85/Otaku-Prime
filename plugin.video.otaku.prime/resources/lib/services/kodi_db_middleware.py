# -*- coding: utf-8 -*-
"""The only service boundary allowed to synchronize with Kodi's video DB."""

from __future__ import annotations

import json
from typing import Callable, Optional

from resources.lib.connectors.kodi_library import (
    KodiLibraryConnector,
    KodiLibraryInventoryService,
    KodiOwnershipReconciler,
)


class KodiDbMiddleware:
    """Use JSON-RPC to read/change Kodi state; never open MyVideos*.db."""

    def __init__(self, media_store, execute_json_rpc: Optional[Callable[[str], str]] = None,
                 inventory_store=None):
        self.media_store = media_store
        self.library = KodiLibraryConnector(execute_json_rpc)
        self._execute = self.library._execute
        self._request_id = 1000
        self.inventory_service=(KodiLibraryInventoryService(self.library,inventory_store)
                                if inventory_store is not None else None)
        self.reconciler=(KodiOwnershipReconciler(inventory_store)
                         if inventory_store is not None else None)

    def synchronize_links(self) -> dict:
        return self.reconcile()

    def inventory(self) -> dict:
        if self.inventory_service is None:
            return {"available":False,"empty":True,"show_count":0,"episode_count":0}
        return self.inventory_service.run_once()

    def reconcile(self) -> dict:
        if self.reconciler is None:
            return {"local":0,"plugin":0,"missing":0,"ambiguous":0}
        return self.reconciler.run_once()

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
