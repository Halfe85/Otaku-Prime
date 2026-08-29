# -*- coding: utf-8 -*-
"""Project Watchlist rows into Prime's TV-show catalogue through MediatorProcessor."""
from __future__ import annotations

import threading

from resources.lib.logging_config import get_logger
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    SimklMediatorClient,
)
from resources.lib.services.mediator_processor import MediatorProcessor

LOGGER=get_logger(__name__)


class TVShowMediatorService:
    """Consume Watchdog-ready rows # -> Z and write resolved catalogue records."""
    def __init__(self,watchlist_store,catalog_store,client=None,processor=None,helpers=None):
        self.watchlist_store=watchlist_store; self.catalog_store=catalog_store
        self.client=client or SimklMediatorClient(); self._stop=threading.Event()
        self._lock=threading.Lock(); self._thread=None
        # `helpers` remains accepted for older tests/callers; convert it into endpoint-like objects.
        if processor is not None:
            self.processor=processor
        elif helpers is not None:
            self.processor=_LegacyProcessor(helpers,self.client)
        else:
            self.processor=MediatorProcessor(simkl_client=self.client)

    @staticmethod
    def provider_for(item):
        if item.get("simkl_id") not in (None,"") or (
            item.get("simkl_reference_id") not in (None,"") and item.get("special_locator") not in (None,"")):
            return "simkl"
        if item.get("anilist_id") not in (None,""): return "anilist"
        if item.get("mal_id") not in (None,""): return "mal"
        if item.get("kitsu_id") not in (None,""): return "kitsu"
        return None

    def resolve_item(self,item):
        return self.processor.resolve(item)

    def _anilist_cast(self,anilist_id,item_id):
        if anilist_id in (None,""): return None
        endpoint=(getattr(self.processor,"endpoints",{}) or {}).get("anilist")
        if endpoint is None:
            endpoint=(getattr(self.processor,"helpers",{}) or {}).get("anilist")
        client=getattr(endpoint,"client",None)
        getter=getattr(client,"cast",None)
        if not getter: return None
        try:
            return getter(str(anilist_id)) or None
        except Exception as exc:
            LOGGER.warning(
                "AniList staff/character enrichment unavailable for Prime item %s "
                "(AniList ID %s): %s",item_id,anilist_id,exc)
            return None

    def _persist_placement(self,item,placement,placement_state="COMPLETE"):
        provider=placement["provider_path"]
        show=placement["tv_show"]
        series=self.catalog_store.get_or_create_series(
            english_name=show.get("name"),romaji_name=show.get("romaji_name"),
            root_simkl_id=show.get("simkl_id"),tvdb_id=show.get("tvdb_id"),
            root_anilist_id=show.get("anilist_id"),source_provider=provider,
            source_media_format=show.get("source_format"),publish_year=show.get("publish_year"),
            overview=show.get("overview"),runtime_minutes=show.get("runtime_minutes"),
            air_status=show.get("air_status"))
        season_data=placement["season"]
        season=self.catalog_store.add_watchlist_season(
            series["local_id"],item,season_number=season_data["number"],
            provider_path=provider,placement_source=season_data["number_source"],
            first_episode=season_data.get("first_episode"),
            last_episode=season_data.get("last_episode"),
            english_name=season_data.get("name"),
            romaji_name=season_data.get("romaji_name"),
            release_date=season_data.get("release_date"),
            release_status=season_data.get("release_status"),
            placement_state=placement_state)
        series_cast=show.get("cast") or self._anilist_cast(
            show.get("anilist_id"),item.get("local_id"))
        same_anilist_identity=(show.get("anilist_id") not in (None,"") and
                               str(show.get("anilist_id"))==str(item.get("anilist_id")))
        if season_data.get("cast"):
            season_cast=season_data.get("cast")
        elif same_anilist_identity:
            # One AniList media row is both the franchise root and this season.
            # Reuse success or failure so a slow endpoint is never called twice.
            season_cast=series_cast
        else:
            season_cast=self._anilist_cast(item.get("anilist_id"),item.get("local_id"))
        series_credit_count=self.catalog_store.replace_media_credits(
            series_cast,series_id=series["local_id"],
            source_provider=show.get("cast_source") or
                            ("anilist" if series_cast and show.get("anilist_id") else provider))
        season_credit_count=self.catalog_store.replace_media_credits(
            season_cast,season_id=season["local_id"],
            source_provider=season_data.get("cast_source") or
                            ("anilist" if season_cast and item.get("anilist_id") else provider))
        if series_credit_count or season_credit_count:
            LOGGER.info(
                "Stored staff/character metadata for Prime item %s: series=%s season=%s",
                item.get("local_id"),series_credit_count,season_credit_count)
        for episode in placement["episodes"]:
            stored_episode=self.catalog_store.add_episode(
                season["local_id"],episode["episode_number"],
                source_episode_number=episode["source_episode_number"],
                mal_id=episode.get("mal_id"),simkl_id=episode.get("simkl_id"),
                release_date=episode.get("release_date"),title=episode.get("title"),
                overview=episode.get("overview"),runtime_minutes=episode.get("runtime_minutes"))
            self.catalog_store.replace_media_credits(
                episode.get("cast"),episode_id=stored_episode["local_id"],
                source_provider=episode.get("cast_source") or provider)
        return series,season

    def process_item(self,item):
        placement=self.resolve_item(item); provider=placement["provider_path"]
        show=placement["tv_show"]; season_data=placement["season"]
        self._persist_placement(item,placement,placement_state="COMPLETE")
        LOGGER.info("Mediator placed Prime item %s through %s as %s S%02dE%02d-E%02d",
                    item["local_id"],provider,show.get("name"),season_data["number"],
                    season_data["first_episode"],season_data["last_episode"])
        marker=getattr(self.watchlist_store,"mark_added_to_library",None)
        if marker:
            marker(item["local_id"],provider=provider)
        elif hasattr(self.watchlist_store,"record_mediator_resolution"):
            self.watchlist_store.record_mediator_resolution(item["local_id"],"RESOLVED",provider=provider)
        return placement

    def _item(self,local_id):
        getter=getattr(self.watchlist_store,"item",None)
        if getter: return getter(str(local_id))
        return next((row for row in self.watchlist_store.list_all()
                     if str(row["local_id"])==str(local_id)),None)

    def _invalidate_release_cache(self,item):
        simkl_id=(item or {}).get("simkl_id") or (item or {}).get("simkl_reference_id")
        if simkl_id not in (None,""):
            key=str(simkl_id)
            for name in ("_episode_cache","_anime_cache"):
                cache=getattr(self.client,name,None)
                if isinstance(cache,dict): cache.pop(key,None)
        for name in ("_search_cache","_tv_cache"):
            cache=getattr(self.client,name,None)
            if isinstance(cache,dict): cache.clear()

    def refresh_item(self,local_id):
        if not self._lock.acquire(blocking=False): return {"refreshed":False,"busy":True}
        try:
            item=self._item(local_id)
            if not item: raise KeyError("Prime watchlist item not found")
            self._invalidate_release_cache(item)
            placement=self.process_item(item)
            return {"refreshed":True,"busy":False,"placement":placement}
        finally:
            self._lock.release()

    def _ready_rows(self):
        getter=getattr(self.watchlist_store,"list_mediator_ready",None)
        if getter: return getter()
        linked=self.catalog_store.linked_watchlist_ids()
        return [row for row in self.watchlist_store.list_all() if row["local_id"] not in linked]

    def run_once(self):
        if not self._lock.acquire(blocking=False): return {"scheduled":False,"busy":True}
        placed=existing=deferred=failed=0
        try:
            # Drain repeatedly: Watchdog may release another 10% while this worker is active.
            while not self._stop.is_set():
                ready=self._ready_rows()
                if not ready: break
                progress=False
                for item in ready:
                    if self._stop.is_set(): break
                    if int(item.get("added_to_library") or 0):
                        existing+=1
                        clearer=getattr(self.watchlist_store,"clear_mediator_ready",None)
                        if clearer: clearer(item["local_id"])
                        progress=True
                        continue
                    try:
                        self.process_item(item); placed+=1; progress=True
                    except MediatorMetadataPending as exc:
                        deferred+=1; progress=True
                        partial=getattr(exc,"placement",None)
                        if partial:
                            self._persist_placement(
                                item,partial,placement_state="STRUCTURE_ONLY")
                            show=partial.get("tv_show") or {}
                            season=partial.get("season") or {}
                            release=season.get("release_date") or "unannounced"
                            status=season.get("release_status") or "UNKNOWN"
                            LOGGER.info(
                                "Mediator positioned deferred Prime item %s through %s as %s "
                                "S%02d; release=%s status=%s",
                                item["local_id"],partial.get("provider_path"),
                                show.get("name"),int(season.get("number") or 0),
                                release,status)
                        unchanged=(
                            str(item.get("mediator_status") or "").upper()=="DEFERRED"
                            and str(item.get("mediator_error") or "")==str(exc))
                        if not unchanged:
                            LOGGER.info(
                                "Mediator deferred Prime item %s until episode metadata is published: %s",
                                item["local_id"],exc)
                        if hasattr(self.watchlist_store,"record_mediator_resolution"):
                            self.watchlist_store.record_mediator_resolution(
                                item["local_id"],"DEFERRED",error=str(exc))
                        clearer=getattr(self.watchlist_store,"clear_mediator_ready",None)
                        if clearer: clearer(item["local_id"])
                    except Exception as exc:
                        failed+=1; progress=True
                        LOGGER.exception("Mediator placement failed for Prime item %s",item["local_id"])
                        if hasattr(self.watchlist_store,"record_mediator_resolution"):
                            self.watchlist_store.record_mediator_resolution(
                                item["local_id"],"UNRESOLVED",error=str(exc))
                        clearer=getattr(self.watchlist_store,"clear_mediator_ready",None)
                        if clearer: clearer(item["local_id"])
                if not progress: break
            LOGGER.info("TV-show mediator complete: placed=%s existing=%s deferred=%s failed=%s",
                        placed,existing,deferred,failed)
            return {"placed":placed,"existing":existing,"deferred":deferred,"failed":failed}
        finally:
            self._lock.release()

    def start(self):
        if self._thread and self._thread.is_alive(): return {"scheduled":False,"busy":True}
        self._stop.clear(); self._thread=threading.Thread(
            target=self.run_once,name="OtakuPrimeTVShowMediator",daemon=True)
        self._thread.start(); return {"scheduled":True,"busy":False}

    def stop(self,timeout=5):
        self._stop.set()
        if self._thread: self._thread.join(timeout=timeout)


class _LegacyProcessor:
    """Compatibility adapter for the existing mediator unit tests."""
    PRIORITY=("simkl","anilist","mal","kitsu")
    def __init__(self,helpers,client): self.helpers=helpers; self.client=client
    def resolve(self,item):
        attempts=[]
        for provider in self.PRIORITY:
            helper=self.helpers.get(provider)
            provider_id=item.get(provider+"_id")
            if provider=="simkl" and provider_id in (None,""):
                if item.get("simkl_reference_id") not in (None,"") and item.get("special_locator") not in (None,""):
                    provider_id=item.get("simkl_reference_id")
            if helper is None or provider_id in (None,""): continue
            try:
                placement=helper.resolve(item,self.client)
                placement["provider_path"]=provider
                placement["provider_attempts"]=list(attempts)
                return placement
            except Exception as exc:
                attempts.append({"provider":provider,"provider_id":str(provider_id),"error":str(exc)})
        from resources.lib.services.mediator_helper_simkl import MediatorPlacementError
        if not attempts: raise MediatorPlacementError("Prime item has no supported provider ID")
        raise MediatorPlacementError("; ".join(
            "{}: {}".format(row["provider"],row["error"]) for row in attempts))
