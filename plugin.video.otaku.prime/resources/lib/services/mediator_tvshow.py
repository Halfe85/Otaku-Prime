# -*- coding: utf-8 -*-
"""Project Watchlist rows into Prime's TV-show catalogue through MediatorProcessor."""
from __future__ import annotations

import threading

from resources.lib.logging_config import get_logger
from resources.lib.services.mediator_trace import MediatorTrace
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    SimklMediatorClient,
)
from resources.lib.services.mediator_processor import MediatorProcessor
from resources.lib.services.mediator_fanarttv import FanartTVClient,FanartTVMediator
from resources.lib.services.artwork_store import (
    ARTWORK_FIELDS,
    WEB_ROOT,
    ArtworkStoreError,
)
from resources.lib.services.remote_identity import clean_remote_text,item_titles
from resources.lib.service_lifecycle import ServiceWorkHalted

LOGGER=get_logger(__name__)
SPECIAL_EPISODE_FORMATS={
    "MOVIE","OVA","OAV","OAD","ONA","SPECIAL","TV_SPECIAL","MUSIC","MUSIC_VIDEO"}


def watchlist_display_title(item):
    """Return the same stable title fallback used by Watchlist Management."""
    for value in item_titles(item or {}):
        title=str(clean_remote_text(value) or "").strip()
        if title:
            return title
    return "Untitled"


def catalogue_episode_title(item,episode,is_special):
    """Preserve provider episode metadata, filling only empty S00 titles."""
    provider_title=str(clean_remote_text((episode or {}).get("title")) or "").strip()
    if provider_title:
        return provider_title
    return watchlist_display_title(item) if is_special else None


class MediatorRunCancelled(RuntimeError):
    """The service instance was retired or its queued watchlist row vanished."""


class TVShowMediatorService:
    """Consume Watchdog-ready rows # -> Z and write resolved catalogue records."""
    def __init__(self,watchlist_store,catalog_store,client=None,processor=None,helpers=None,
                 fanart=None,artwork_store=None,physical=None,network_timeout=30,
                 halt_requested=None):
        self.watchlist_store=watchlist_store; self.catalog_store=catalog_store
        self._external_halt_requested=halt_requested or (lambda:False)
        self.client=client or SimklMediatorClient(
            timeout=network_timeout,
            halt_requested=lambda:(self._stop.is_set() if hasattr(self,"_stop") else False) or
                                   self._external_halt_requested())
        self._stop=threading.Event()
        self.fanart=fanart or FanartTVMediator(
            FanartTVClient(timeout=network_timeout))
        self.artwork_store=artwork_store
        self.physical=physical
        self._stopping=threading.Event()
        self._lock=threading.Lock(); self._thread=None
        # `helpers` remains accepted for older tests/callers; convert it into endpoint-like objects.
        if processor is not None:
            self.processor=processor
        elif helpers is not None:
            self.processor=_LegacyProcessor(helpers,self.client)
        else:
            self.processor=MediatorProcessor(
                simkl_client=self.client,
                halt_requested=lambda:(self._stop.is_set() or self._stopping.is_set() or
                                       self._external_halt_requested()),
                network_timeout=network_timeout)

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

    def _ensure_current(self,item_id):
        if (self._stop.is_set() or self._stopping.is_set() or
                self._external_halt_requested()):
            raise MediatorRunCancelled("mediator service is stopping")
        current=self._item(item_id)
        if not current:
            raise MediatorRunCancelled("watchlist item no longer exists")
        return current

    def _provider_cast(self,provider,provider_id,item_id):
        if provider_id in (None,""): return None
        endpoint=self._provider_endpoint(provider)
        getter=getattr(endpoint,"cast",None)
        if not getter:
            getter=getattr(getattr(endpoint,"client",None),"cast",None)
        if not getter: return None
        try:
            value=getter(str(provider_id)) or None
            self._ensure_current(item_id)
            return value
        except (MediatorRunCancelled,ServiceWorkHalted):
            raise
        except Exception as exc:
            LOGGER.warning(
                "%s staff/character enrichment unavailable for Prime item %s "
                "(%s ID %s): %s",provider.title(),item_id,provider.title(),provider_id,exc)
            return None

    def _provider_endpoint(self,provider):
        endpoint=(getattr(self.processor,"endpoints",{}) or {}).get(provider)
        if endpoint is None:
            endpoint=(getattr(self.processor,"helpers",{}) or {}).get(provider)
        return endpoint

    def _provider_poster(self,provider,provider_id,item_id):
        if provider_id in (None,""): return None
        endpoint=self._provider_endpoint(provider)
        getter=getattr(endpoint,"poster",None)
        if not getter: return None
        try:
            value=str(getter(str(provider_id)) or "").strip()
            self._ensure_current(item_id)
            return value if value.startswith(("https://","http://")) else None
        except (MediatorRunCancelled,ServiceWorkHalted):
            raise
        except Exception as exc:
            LOGGER.warning(
                "%s poster fallback unavailable for Prime item %s (%s ID %s): %s",
                provider.title(),item_id,provider.title(),provider_id,exc)
            return None

    def _fallback_poster(self,item,placement):
        show=(placement or {}).get("tv_show") or {}
        if show.get("poster_url"): return placement
        for provider in ("anilist","mal","kitsu","simkl"):
            provider_id=show.get(provider+"_id") or item.get(provider+"_id")
            poster=self._provider_poster(
                provider,provider_id,item.get("local_id"))
            if not poster: continue
            show["poster_url"]=poster
            show["poster_source"]=provider
            LOGGER.info(
                "Using %s poster fallback for Prime item %s",
                provider.title(),item.get("local_id"))
            break
        return placement

    @staticmethod
    def _artwork_ids(item,show):
        return {provider:(show or {}).get(provider+"_id") or
                (item or {}).get(provider+"_id")
                for provider in ("tvdb","simkl","anilist","mal","kitsu")}

    def _localize_artwork(self,item,placement):
        """Replace remote artwork URLs with Prime-owned persistent web URLs."""
        if self.artwork_store is None: return placement
        show=(placement or {}).get("tv_show") or {}
        media_type="movies" if placement.get("library_type")=="movie" else "tvshows"
        ids=self._artwork_ids(item,show)
        for field,art_type in ARTWORK_FIELDS.items():
            source=str(show.get(field) or "").strip()
            if not source or source.startswith(WEB_ROOT): continue
            if not source.startswith(("https://","http://")): continue
            try:
                stored=self.artwork_store.persist(media_type,ids,art_type,source)
            except ServiceWorkHalted:
                raise
            except (ArtworkStoreError,OSError) as exc:
                # Do not send the browser back to a failing third-party host.
                # Removing a failed poster also allows the provider fallback
                # chain to offer a different source on the next pass.
                show.pop(field,None)
                LOGGER.warning(
                    "Prime could not preserve %s artwork for item %s: %s",
                    art_type,item.get("local_id"),exc)
                continue
            show[field]=stored["web_url"]
            show.setdefault("artwork_local_paths",{})[art_type]=stored["kodi_path"]
        return placement

    def _restore_local_artwork(self,item,placement):
        if self.artwork_store is None: return placement
        show=(placement or {}).get("tv_show") or {}
        media_type="movies" if placement.get("library_type")=="movie" else "tvshows"
        getter=getattr(self.artwork_store,"existing",None)
        if not getter: return placement
        recovered=getter(media_type,self._artwork_ids(item,show))
        for field in ARTWORK_FIELDS:
            if not show.get(field) and recovered.get(field):
                show[field]=recovered[field]
        if recovered.get("kodi_paths"):
            show.setdefault("artwork_local_paths",{}).update(recovered["kodi_paths"])
        return placement

    def _prepare_artwork(self,item,placement):
        if placement.get("library_type")!="movie":
            placement=self.fanart.enrich(placement)
            self._ensure_current(item["local_id"])
        placement=self._localize_artwork(item,placement)
        self._ensure_current(item["local_id"])
        placement=self._restore_local_artwork(item,placement)
        self._ensure_current(item["local_id"])
        placement=self._fallback_poster(item,placement)
        self._ensure_current(item["local_id"])
        return self._localize_artwork(item,placement)

    @staticmethod
    def _merge_terms(existing,incoming):
        result=[]; seen=set()
        for value in list(existing or [])+list(incoming or []):
            text=str(value or "").strip(); key=text.casefold()
            if text and key not in seen:
                result.append(text); seen.add(key)
        return result

    def _provider_classification(self,provider,provider_id,item_id):
        if provider_id in (None,""): return None
        endpoint=self._provider_endpoint(provider)
        getter=getattr(endpoint,"classification",None)
        if not getter: return None
        try:
            value=getter(str(provider_id)) or {}
            self._ensure_current(item_id)
            return value if isinstance(value,dict) else None
        except (MediatorRunCancelled,ServiceWorkHalted):
            raise
        except Exception as exc:
            LOGGER.warning(
                "%s classification enrichment unavailable for Prime item %s "
                "(%s ID %s): %s",
                provider.title(),item_id,provider.title(),provider_id,exc)
            return None

    def _enrich_classification(self,item,placement):
        show=(placement or {}).get("tv_show") or {}
        missing=lambda: (not show.get("genres") or not show.get("themes") or
                         not show.get("age_rating"))
        if not missing(): return placement
        used=[]
        for provider in ("anilist","mal","kitsu","simkl"):
            provider_id=show.get(provider+"_id") or item.get(provider+"_id")
            metadata=self._provider_classification(
                provider,provider_id,item.get("local_id"))
            if not metadata: continue
            before=(tuple(show.get("genres") or []),tuple(show.get("themes") or []),
                    show.get("age_rating"),bool(show.get("mature")))
            show["genres"]=self._merge_terms(show.get("genres"),metadata.get("genres"))
            show["themes"]=self._merge_terms(show.get("themes"),metadata.get("themes"))
            if not show.get("age_rating") and metadata.get("age_rating"):
                show["age_rating"]=metadata["age_rating"]
            show["mature"]=bool(show.get("mature") or metadata.get("mature"))
            after=(tuple(show.get("genres") or []),tuple(show.get("themes") or []),
                   show.get("age_rating"),bool(show.get("mature")))
            if after!=before: used.append(provider)
            if not missing(): break
        if used:
            show["classification_sources"]=list(dict.fromkeys(
                list(show.get("classification_sources") or [])+used))
            LOGGER.info(
                "Enriched classification for Prime item %s through %s",
                item.get("local_id"),", ".join(used))
        return placement

    def _staff_cast(self,ids,item_id):
        """Use every available tracker as a staff source, in stable priority order."""
        for provider in ("anilist","mal","kitsu","simkl"):
            cast=self._provider_cast(provider,(ids or {}).get(provider),item_id)
            if cast: return cast,provider
        return None,None

    def _persist_placement(self,item,placement,placement_state="COMPLETE"):
        self._ensure_current(item["local_id"])
        provider=placement["provider_path"]
        show=placement["tv_show"]
        components=placement.get("seasons") or []
        if components:
            stored=[]; series=None
            for component in components:
                child=dict(placement)
                child.pop("seasons",None)
                child["season"]=component["season"]
                child["episodes"]=component["episodes"]
                series,season=self._persist_placement(
                    item,child,placement_state=placement_state)
                stored.append(season)
            return series,stored
        season_data=placement["season"]
        if placement.get("library_type")=="movie":
            movie=self.catalog_store.add_watchlist_movie(
                item,provider_path=provider,
                placement_source=season_data.get("number_source"),
                english_name=season_data.get("name") or show.get("name"),
                romaji_name=season_data.get("romaji_name") or show.get("romaji_name"),
                release_date=season_data.get("release_date"),
                release_status=season_data.get("release_status"),
                publish_year=show.get("publish_year"),overview=show.get("overview"),
                runtime_minutes=show.get("runtime_minutes"),air_status=show.get("air_status"),
                poster_url=show.get("poster_url"),fanart_url=show.get("fanart_url"),
                clearlogo_url=show.get("clearlogo_url"),
                banner_url=show.get("banner_url"),genres=show.get("genres"),
                themes=show.get("themes"),age_rating=show.get("age_rating"),
                mature=show.get("mature") or item.get("is_adult"))
            movie_cast=show.get("cast") or season_data.get("cast")
            movie_cast_source=show.get("cast_source") or season_data.get("cast_source")
            if not movie_cast:
                movie_cast,movie_cast_source=self._staff_cast({
                    provider:item.get(provider+"_id")
                    for provider in ("anilist","mal","kitsu","simkl")
                },item.get("local_id"))
            self._ensure_current(item["local_id"])
            self.catalog_store.replace_movie_credits(
                movie_cast,movie["local_id"],source_provider=movie_cast_source or provider)
            return movie,None
        series=self.catalog_store.get_or_create_series(
            english_name=show.get("name"),romaji_name=show.get("romaji_name"),
            root_simkl_id=show.get("simkl_id"),tvdb_id=show.get("tvdb_id"),
            root_anilist_id=show.get("anilist_id"),source_provider=provider,
            source_media_format=show.get("source_format"),publish_year=show.get("publish_year"),
            overview=show.get("overview"),runtime_minutes=show.get("runtime_minutes"),
            air_status=show.get("air_status"),poster_url=show.get("poster_url"),
            fanart_url=show.get("fanart_url"),clearlogo_url=show.get("clearlogo_url"),
            banner_url=show.get("banner_url"),
            genres=show.get("genres"),themes=show.get("themes"),
            age_rating=show.get("age_rating"),
            mature=show.get("mature") or item.get("is_adult"))
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
        series_cast=show.get("cast"); series_cast_source=show.get("cast_source")
        if not series_cast:
            series_cast,series_cast_source=self._staff_cast({
                "anilist":show.get("anilist_id") or item.get("anilist_id"),
                "mal":show.get("mal_id") or item.get("mal_id"),
                "kitsu":show.get("kitsu_id") or item.get("kitsu_id"),
                "simkl":show.get("simkl_id") or item.get("simkl_id"),
            },item.get("local_id"))
        self._ensure_current(item["local_id"])
        same_anilist_identity=(show.get("anilist_id") not in (None,"") and
                               str(show.get("anilist_id"))==str(item.get("anilist_id")))
        if season_data.get("cast"):
            season_cast=season_data.get("cast")
        elif same_anilist_identity:
            # One AniList media row is both the franchise root and this season.
            # Reuse success or failure so a slow endpoint is never called twice.
            season_cast=series_cast
        else:
            season_cast,season_cast_source=self._staff_cast({
                provider:item.get(provider+"_id")
                for provider in ("anilist","mal","kitsu","simkl")
            },item.get("local_id"))
        if season_data.get("cast"):
            season_cast_source=season_data.get("cast_source")
        elif same_anilist_identity:
            season_cast_source=series_cast_source
        self._ensure_current(item["local_id"])
        series_credit_count=self.catalog_store.replace_media_credits(
            series_cast,series_id=series["local_id"],
            source_provider=series_cast_source or provider)
        season_credit_count=self.catalog_store.replace_media_credits(
            season_cast,season_id=season["local_id"],
            source_provider=season_cast_source or provider)
        if series_credit_count or season_credit_count:
            LOGGER.info(
                "Stored staff/character metadata for Prime item %s: series=%s season=%s",
                item.get("local_id"),series_credit_count,season_credit_count)
        placement_episodes=placement["episodes"]
        media_format=str(
            season_data.get("media_type") or item.get("media_format") or ""
        ).upper().replace(" ","_").replace("-","_")
        special_episode=(int(season_data.get("number") or 0)==0 or
                         media_format in SPECIAL_EPISODE_FORMATS)
        written_episodes=[]
        for episode in placement_episodes:
            self._ensure_current(item["local_id"])
            provider_ids={
                name:(item.get(name+"_id") if special_episode else None)
                for name in ("anilist","mal","kitsu","simkl")}
            stored_episode=self.catalog_store.add_episode(
                season["local_id"],episode["episode_number"],
                source_episode_number=episode["source_episode_number"],
                anilist_id=provider_ids["anilist"] or episode.get("anilist_id"),
                mal_id=provider_ids["mal"] or episode.get("mal_id"),
                kitsu_id=provider_ids["kitsu"] or episode.get("kitsu_id"),
                simkl_id=provider_ids["simkl"] or episode.get("simkl_id"),
                watch_status=(int(episode["source_episode_number"])<=
                              max(0,int(item.get("progress") or 0))),
                release_date=episode.get("release_date"),
                title=catalogue_episode_title(item,episode,special_episode),
                overview=episode.get("overview"),runtime_minutes=episode.get("runtime_minutes"),
                watchlist_local_id=item["local_id"])
            self.catalog_store.replace_media_credits(
                episode.get("cast"),episode_id=stored_episode["local_id"],
                source_provider=episode.get("cast_source") or provider)
            written_episodes.append({
                "prime_episode_id": stored_episode["local_id"],
                "source_episode": episode["source_episode_number"],
                "requested_episode": episode["episode_number"],
                "stored_episode": stored_episode.get("episode_number"),
                "release_date": stored_episode.get("release_date"),
            })
        MediatorTrace(item.get("local_id")).info("CATALOGUE", "EPISODES_WRITTEN", {
            "prime_series_id": series["local_id"], "prime_season_id": season["local_id"],
            "season_number": season_data.get("number"), "episodes": written_episodes,
        })
        return series,season

    def process_item(self,item):
        placement=self.resolve_item(item)
        self._ensure_current(item["local_id"])
        placement=self._enrich_classification(item,placement)
        self._ensure_current(item["local_id"])
        placement=self._prepare_artwork(item,placement)
        self._ensure_current(item["local_id"])
        provider=placement["provider_path"]
        show=placement["tv_show"]; season_data=placement["season"]
        if placement.get("seasons"):
            removed=self.catalog_store.reset_multiseason_watchlist_projection(
                item["local_id"])
            if removed:
                LOGGER.info(
                    "Removed %s obsolete episodes before rebuilding Prime item %s "
                    "across multiple seasons",removed,item["local_id"])
        stored_series,_=self._persist_placement(
            item,placement,placement_state="COMPLETE")
        if self.physical is not None and placement.get("library_type")!="movie":
            # The handoff contains only Prime's opaque series ID. Prime Physical
            # owns every catalogue lookup and all filesystem decisions.
            MediatorTrace(item.get("local_id")).info("PHYSICAL", "TV_SERIES_PROJECT_BEGIN",
                {"prime_series_id": stored_series["local_id"]})
            self.physical.project_series(stored_series["local_id"])
        if placement.get("library_type")=="movie":
            LOGGER.info("Mediator placed Prime item %s through %s in Movies as %s",
                        item["local_id"],provider,
                        season_data.get("name") or show.get("name"))
        elif placement.get("seasons"):
            numbers=[component["season"]["number"] for component in placement["seasons"]]
            LOGGER.info(
                "Mediator placed Prime item %s through %s across seasons %s-%s",
                item["local_id"],provider,min(numbers),max(numbers))
        else:
            LOGGER.info("Mediator placed Prime item %s through %s as %s S%02dE%02d-E%02d",
                        item["local_id"],provider,show.get("name"),season_data["number"],
                        season_data["first_episode"],season_data["last_episode"])
        marker=getattr(self.watchlist_store,"mark_added_to_library",None)
        self._ensure_current(item["local_id"])
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

    def _record_deferred(self,item,exc):
        self._ensure_current(item["local_id"])
        partial=getattr(exc,"placement",None)
        if partial:
            partial=self._enrich_classification(item,partial)
            partial=self._prepare_artwork(item,partial)
            self._ensure_current(item["local_id"])
            self._persist_placement(item,partial,placement_state="STRUCTURE_ONLY")
            show=partial.get("tv_show") or {}
            season=partial.get("season") or {}
            release=season.get("release_date") or "unannounced"
            status=season.get("release_status") or "UNKNOWN"
            if partial.get("library_type")=="movie":
                LOGGER.info(
                    "Mediator positioned deferred Prime item %s through %s in Movies as %s; "
                    "release=%s status=%s",item["local_id"],partial.get("provider_path"),
                    season.get("name") or show.get("name"),release,status)
            else:
                LOGGER.info(
                    "Mediator positioned deferred Prime item %s through %s as %s "
                    "S%02d; release=%s status=%s",
                    item["local_id"],partial.get("provider_path"),
                    show.get("name"),int(season.get("number") or 0),release,status)
        unchanged=(
            str(item.get("mediator_status") or "").upper()=="DEFERRED"
            and str(item.get("mediator_error") or "")==str(exc))
        if not unchanged:
            LOGGER.info(
                "Mediator deferred Prime item %s until episode metadata is published: %s",
                item["local_id"],exc)
        self._ensure_current(item["local_id"])
        if hasattr(self.watchlist_store,"record_mediator_resolution"):
            self.watchlist_store.record_mediator_resolution(
                item["local_id"],"DEFERRED",error=str(exc))
        clearer=getattr(self.watchlist_store,"clear_mediator_ready",None)
        if clearer: clearer(item["local_id"])

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
                    except (MediatorRunCancelled,ServiceWorkHalted) as exc:
                        progress=True
                        LOGGER.info(
                            "Mediator discarded stale Prime item %s: %s",
                            item["local_id"],exc)
                        break
                    except MediatorMetadataPending as exc:
                        deferred+=1; progress=True
                        try:
                            self._record_deferred(item,exc)
                        except MediatorRunCancelled as cancelled:
                            LOGGER.info(
                                "Mediator discarded stale deferred Prime item %s: %s",
                                item["local_id"],cancelled)
                            break
                        except KeyError:
                            # The row can be removed between the final current-row
                            # check and the SQLite update. Treat that as cancellation.
                            LOGGER.info(
                                "Mediator discarded deferred result because Prime item %s no longer exists",
                                item["local_id"])
                            break
                    except Exception as exc:
                        failed+=1; progress=True
                        LOGGER.exception("Mediator placement failed for Prime item %s",item["local_id"])
                        if hasattr(self.watchlist_store,"record_mediator_resolution"):
                            try:
                                self.watchlist_store.record_mediator_resolution(
                                    item["local_id"],"UNRESOLVED",error=str(exc))
                            except KeyError:
                                LOGGER.info(
                                    "Mediator error result discarded because Prime item %s no longer exists",
                                    item["local_id"])
                        clearer=getattr(self.watchlist_store,"clear_mediator_ready",None)
                        if clearer:
                            try: clearer(item["local_id"])
                            except KeyError: pass
                if not progress: break
            LOGGER.info("TV-show mediator complete: placed=%s existing=%s deferred=%s failed=%s",
                        placed,existing,deferred,failed)
            return {"placed":placed,"existing":existing,"deferred":deferred,"failed":failed}
        finally:
            self._lock.release()

    def start(self):
        if self._stopping.is_set():
            return {"scheduled":False,"busy":False,"stopping":True}
        if self._thread and self._thread.is_alive(): return {"scheduled":False,"busy":True}
        self._stop.clear(); self._thread=threading.Thread(
            target=self.run_once,name="OtakuPrimeTVShowMediator",daemon=True)
        self._thread.start(); return {"scheduled":True,"busy":False}

    def request_stop(self):
        self._stopping.set()
        self._stop.set()

    def stop(self,timeout=35):
        self.request_stop()
        if self._thread: self._thread.join(timeout=timeout)
        return not bool(self._thread and self._thread.is_alive())


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
