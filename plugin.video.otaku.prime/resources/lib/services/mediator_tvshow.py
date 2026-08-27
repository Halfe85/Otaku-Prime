# -*- coding: utf-8 -*-
"""Project canonical watchlist items into Prime TV-show catalogue records."""
from __future__ import annotations

import threading

from resources.lib.logging_config import get_logger
from resources.lib.services.mediator_helper_anilist import AniListMediatorHelper
from resources.lib.services.mediator_helper_kitsu import KitsuMediatorHelper
from resources.lib.services.mediator_helper_mal import MALMediatorHelper
from resources.lib.services.mediator_helper_simkl import (
    MediatorPlacementError,SimklMediatorClient,SimklMediatorHelper,
)
from resources.lib.services.remote_identity import persist_watchlist_id_repair


LOGGER=get_logger(__name__)
PROVIDER_PRIORITY=("simkl","anilist","mal","kitsu")


class TVShowMediatorService:
    """Resolve and persist exact franchise/season/episode placement."""
    def __init__(self,watchlist_store,catalog_store,client=None,helpers=None):
        self.watchlist_store=watchlist_store; self.catalog_store=catalog_store
        self.client=client or SimklMediatorClient(); self._stop=threading.Event()
        self._lock=threading.Lock(); self._thread=None
        self.helpers=helpers or {
            "simkl":SimklMediatorHelper(),"anilist":AniListMediatorHelper(),
            "mal":MALMediatorHelper(),"kitsu":KitsuMediatorHelper()}

    @staticmethod
    def provider_for(item):
        return next((provider for provider in PROVIDER_PRIORITY
                     if item.get(provider+"_id") not in (None,"")),None)

    def resolve_item(self,item):
        """Try every present provider ID in priority order until one resolves."""
        attempts=[]
        for provider in PROVIDER_PRIORITY:
            provider_id=item.get(provider+"_id")
            if provider_id in (None,""): continue
            try:
                placement=self.helpers[provider].resolve(item,self.client)
                placement["provider_path"]=provider
                placement["provider_attempts"]=list(attempts)
                if attempts:
                    LOGGER.info(
                        "Mediator resolved Prime item %s through %s after unavailable paths: %s",
                        item["local_id"],provider,", ".join(
                            value["provider"] for value in attempts))
                return placement
            except Exception as exc:
                attempts.append({"provider":provider,"provider_id":str(provider_id),
                                 "error":str(exc)})
        if not attempts: raise MediatorPlacementError("Prime item has no supported provider ID")
        raise MediatorPlacementError("; ".join(
            "{}: {}".format(value["provider"],value["error"]) for value in attempts))

    def _apply_identity_repair(self,item,placement):
        repair=placement.get("identity_repair")
        if not repair:
            return item
        provider=repair["provider"]
        changed=persist_watchlist_id_repair(
            self.watchlist_store,item["local_id"],provider,
            repair.get("old"),repair.get("new"),repair.get("reason"))
        if not changed:
            return item
        updated=dict(item); updated[provider+"_id"]=str(repair["new"])
        LOGGER.warning(
            "Mediator repaired stale %s ID for Prime item %s: %s -> %s",
            provider,item["local_id"],repair.get("old"),repair.get("new"))
        return updated

    def process_item(self,item):
        placement=self.resolve_item(item); provider=placement["provider_path"]
        item=self._apply_identity_repair(item,placement)
        show=placement["tv_show"]
        series=self.catalog_store.get_or_create_series(
            english_name=show.get("name"),root_simkl_id=show.get("simkl_id"),
            tvdb_id=show.get("tvdb_id"))
        season_data=placement["season"]
        season=self.catalog_store.add_watchlist_season(
            series["local_id"],item,season_number=season_data["number"],
            provider_path=provider,placement_source=season_data["number_source"],
            first_episode=season_data["first_episode"],last_episode=season_data["last_episode"])
        for episode in placement["episodes"]:
            self.catalog_store.add_episode(
                season["local_id"],episode["episode_number"],
                source_episode_number=episode["source_episode_number"],
                mal_id=episode.get("mal_id"),simkl_id=episode.get("simkl_id"),
                release_date=episode.get("release_date"))
        LOGGER.info("Mediator placed Prime item %s through %s as %s S%02dE%02d-E%02d",
                    item["local_id"],provider,show.get("name"),season_data["number"],
                    season_data["first_episode"],season_data["last_episode"])
        if hasattr(self.watchlist_store,"record_mediator_resolution"):
            self.watchlist_store.record_mediator_resolution(item["local_id"],"RESOLVED",provider=provider)
        return placement

    def _item(self,local_id):
        getter=getattr(self.watchlist_store,"item",None)
        if getter:
            return getter(str(local_id))
        return next((row for row in self.watchlist_store.list_all()
                     if str(row["local_id"])==str(local_id)),None)

    def _invalidate_release_cache(self,item):
        """Force provider data to be fetched again for release rollover."""
        simkl_id=item.get("simkl_id") if item else None
        if simkl_id not in (None,""):
            key=str(simkl_id)
            for name in ("_episode_cache","_anime_cache"):
                cache=getattr(self.client,name,None)
                if isinstance(cache,dict):
                    cache.pop(key,None)
        # Search and TV cross-map results can also change between episodes.
        # A release refresh is deliberately narrow (one Prime item), so clearing
        # these small shared caches is preferable to preserving stale mappings.
        for name in ("_search_cache","_tv_cache"):
            cache=getattr(self.client,name,None)
            if isinstance(cache,dict):
                cache.clear()

    def refresh_item(self,local_id):
        """Refresh one already-mediated item when its next release becomes due.

        The watchdog uses this before rolling to the next episode so newly
        published future dates can enter Prime's catalogue without reprocessing
        every series.
        """
        if not self._lock.acquire(blocking=False):
            return {"refreshed":False,"busy":True}
        try:
            item=self._item(local_id)
            if not item:
                raise KeyError("Prime watchlist item not found")
            self._invalidate_release_cache(item)
            placement=self.process_item(item)
            return {"refreshed":True,"busy":False,"placement":placement}
        finally:
            self._lock.release()

    def run_once(self):
        if not self._lock.acquire(blocking=False): return {"scheduled":False,"busy":True}
        placed=existing=failed=0
        try:
            linked=self.catalog_store.linked_watchlist_ids()
            for item in self.watchlist_store.list_all():
                if self._stop.is_set(): break
                if item["local_id"] in linked: existing+=1; continue
                try:
                    self.process_item(item); placed+=1
                except Exception as exc:
                    failed+=1
                    LOGGER.exception("Mediator placement failed for Prime item %s",item["local_id"])
                    if hasattr(self.watchlist_store,"record_mediator_resolution"):
                        self.watchlist_store.record_mediator_resolution(
                            item["local_id"],"UNRESOLVED",error=str(exc))
            LOGGER.info("TV-show mediator complete: placed=%s existing=%s failed=%s",
                        placed,existing,failed)
            return {"placed":placed,"existing":existing,"failed":failed}
        finally: self._lock.release()

    def start(self):
        if self._thread and self._thread.is_alive(): return {"scheduled":False,"busy":True}
        self._stop.clear(); self._thread=threading.Thread(
            target=self.run_once,name="OtakuPrimeTVShowMediator",daemon=True)
        self._thread.start(); return {"scheduled":True,"busy":False}

    def stop(self,timeout=5):
        self._stop.set()
        if self._thread: self._thread.join(timeout=timeout)
