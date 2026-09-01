# -*- coding: utf-8 -*-
"""Complete canonical tracker identities before media mediation starts."""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from resources.lib.logging_config import get_logger
from resources.lib.services.remote_identity import best_title_similarity,item_titles
from resources.lib.watchlist.simkl import PACKAGED_CLIENT_ID, SIMKL_API_URL

LOGGER=get_logger(__name__)
PROVIDERS=("anilist","mal","kitsu","simkl")
KITSU_API_URL="https://kitsu.io/api/edge"
SPECIAL_FORMATS={
    "MOVIE","OVA","OAV","OAD","ONA","SPECIAL","TV_SPECIAL","TV_SHORT","MUSIC","MUSIC_VIDEO"}


class IdentityMappingConflict(ValueError):
    pass


class _StopRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None


class KitsuIdentityClient:
    """Resolve hidden and mature Kitsu records through exact provider mappings."""

    EXTERNAL_SITES={"anilist":"anilist/anime","mal":"myanimelist/anime"}

    def __init__(self,timeout=30,opener=None):
        self.timeout=int(timeout); self._open=opener or urlopen

    @staticmethod
    def _headers():
        return {"Accept":"application/vnd.api+json",
                "User-Agent":"Otaku-Prime/0.1.2 identity-watchdog"}

    def _json(self,url):
        endpoint=urlsplit(url)
        safe="{}://{}{}".format(endpoint.scheme,endpoint.netloc,endpoint.path)
        started=time.monotonic()
        LOGGER.info("Kitsu identity API request started: GET %s",safe)
        try:
            with self._open(Request(url,headers=self._headers()),timeout=self.timeout) as response:
                payload=json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            log=LOGGER.warning if exc.code in (401,403,404,429) else LOGGER.error
            log("Kitsu identity API request failed: GET %s returned HTTP %s",safe,exc.code)
            raise RuntimeError("Kitsu identity request failed with HTTP {}".format(exc.code)) from exc
        except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
            LOGGER.error("Kitsu identity API request failed: GET %s: %s",safe,exc)
            raise RuntimeError("Kitsu identity request failed: {}".format(exc)) from exc
        LOGGER.info("Kitsu identity API request complete: GET %s duration=%.2fs",
                    safe,time.monotonic()-started)
        return payload

    def _mapped_ids(self,provider,value):
        site=self.EXTERNAL_SITES[provider]
        url=KITSU_API_URL+"/mappings?"+urlencode({
            "filter[externalSite]":site,
            "filter[externalId]":str(value),
            "include":"item",
            "page[limit]":20,
        })
        payload=self._json(url)
        results=set()
        for row in (payload or {}).get("data") or []:
            attrs=row.get("attributes") or {}
            item=(((row.get("relationships") or {}).get("item") or {}).get("data") or {})
            if (str(attrs.get("externalSite") or "").lower()!=site or
                    str(attrs.get("externalId") or "")!=str(value) or
                    item.get("type")!="anime" or item.get("id") in (None,"")):
                continue
            results.add(str(item["id"]))
        return results

    def resolve(self,item):
        known={provider:item.get(provider+"_id") for provider in ("anilist","mal")}
        mappings=[]
        for provider,value in known.items():
            if value not in (None,""):
                ids=self._mapped_ids(provider,value)
                if len(ids)>1:
                    raise IdentityMappingConflict(
                        "Kitsu returned multiple exact {} mappings for {}".format(provider,value))
                if ids: mappings.append((provider,next(iter(ids))))
        if not mappings: return {}
        unique={value for _,value in mappings}
        if len(unique)>1:
            details=", ".join("{} {}".format(provider,value) for provider,value in mappings)
            raise IdentityMappingConflict("Kitsu exact provider mappings disagree: "+details)
        return {"kitsu":mappings[0][1]}


class ProviderIdentityClient:
    """Combine Simkl cross IDs with Kitsu's exact mapping fallback."""

    def __init__(self,simkl=None,kitsu=None):
        self.simkl=simkl or SimklIdentityClient()
        self.kitsu=kitsu or KitsuIdentityClient()

    def resolve(self,item):
        simkl_error=None
        try:
            resolved=self.simkl.resolve(item) or {}
        except IdentityMappingConflict:
            raise
        except Exception as exc:
            simkl_error=exc; resolved={}
            LOGGER.warning("Simkl identity lookup failed; trying exact Kitsu mappings: %s",exc)
        combined=dict(item)
        for provider,value in resolved.items():
            if provider in PROVIDERS: combined[provider+"_id"]=value
        if combined.get("kitsu_id") in (None,""):
            try:
                resolved.update(self.kitsu.resolve(combined))
            except IdentityMappingConflict:
                raise
            except Exception as exc:
                if not resolved and simkl_error: raise simkl_error
                LOGGER.warning("Exact Kitsu identity lookup unavailable: %s",exc)
        if not resolved and simkl_error: raise simkl_error
        return resolved


class SimklIdentityClient:
    """Watchdog-only identity resolver. Mediator never searches or repairs IDs."""
    def __init__(self,client_id=None,timeout=30,opener=None,redirect_opener=None):
        self.client_id=str(client_id or PACKAGED_CLIENT_ID).strip(); self.timeout=int(timeout)
        self._open=opener or urlopen; self._redirect_open=redirect_opener or build_opener(_StopRedirect()).open

    def _params(self,extra=None):
        values={"client_id":self.client_id,"app-name":"otaku-prime","app-version":"0.1.2"}; values.update(extra or {})
        return urlencode(values)

    @staticmethod
    def _headers(): return {"Accept":"application/json","User-Agent":"Otaku-Prime/0.1.2 watchdog"}

    def _json(self,url):
        try:
            with self._open(Request(url,headers=self._headers()),timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError("Simkl identity request failed with HTTP {}".format(exc.code)) from exc
        except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
            raise RuntimeError("Simkl identity request failed: {}".format(exc)) from exc

    def _redirect_simkl_id(self,provider,value):
        url=SIMKL_API_URL+"/redirect?"+self._params({"to":"simkl",provider:str(value)})
        request=Request(url,headers=self._headers()); location=None
        try:
            with self._redirect_open(request,timeout=self.timeout) as response: location=response.headers.get("Location")
        except HTTPError as exc:
            if exc.code not in (301,302,303,307,308): raise
            location=exc.headers.get("Location")
        if not location: return None
        match=re.search(r"/(?:anime|tv)/(\d+)(?:/|$)",urlparse(location).path)
        return match.group(1) if match else None

    def _simkl_ids(self,ids):
        if ids.get("simkl"): return [str(ids["simkl"])]
        values=[]
        for provider in ("anilist","mal","kitsu"):
            if not ids.get(provider): continue
            simkl_id=self._redirect_simkl_id(provider,ids[provider])
            if simkl_id and simkl_id not in values: values.append(simkl_id)
        return values

    def _detail(self,simkl_id): return self._json(SIMKL_API_URL+"/anime/{}?{}".format(simkl_id,self._params()))
    def _episodes(self,simkl_id):
        payload=self._json(SIMKL_API_URL+"/anime/episodes/{}?{}".format(simkl_id,self._params()))
        return payload if isinstance(payload,list) else []
    def _search(self,provider,value):
        payload=self._json(SIMKL_API_URL+"/search/id?"+self._params({provider:str(value)}))
        return payload if isinstance(payload,list) else []

    @staticmethod
    def _resolved_ids(payload,simkl_id):
        ids=dict((payload or {}).get("ids") or {}); ids["simkl"]=ids.get("simkl") or simkl_id
        return {name:str(ids[name]) for name in PROVIDERS if ids.get(name) not in (None,"")}

    @staticmethod
    def _disagreements(known,resolved):
        return {name:(str(known[name]),resolved[name]) for name in PROVIDERS
                if known.get(name) and resolved.get(name) and str(known[name])!=resolved[name]}

    @staticmethod
    def _special_capable(item): return str(item.get("media_format") or "").upper() in SPECIAL_FORMATS

    @staticmethod
    def _row_coordinate(row):
        tvdb=(row or {}).get("tvdb") or {}
        try: season=int(tvdb.get("season")); episode=int(tvdb.get("episode"))
        except (TypeError,ValueError): return None
        return (season,episode) if season>=0 and episode>0 else None

    def _special_reference(self,item,simkl_reference_id):
        """Map a provider-only special to one Simkl parent S00Eyy coordinate."""
        titles=item_titles(item); release=str(item.get("release_date") or "")[:10]
        candidates=[]; exact_coordinates=[]
        for row in self._episodes(simkl_reference_id):
            coordinate=self._row_coordinate(row)
            if not coordinate or coordinate[0]!=0: continue
            if str(row.get("type") or "").lower() not in ("special","episode",""): continue
            row_ids=row.get("ids") or {}; exact=0
            for provider in ("anilist","mal","kitsu"):
                known=item.get(provider+"_id"); remote=row_ids.get(provider)
                if known not in (None,"") and remote not in (None,"") and str(known)==str(remote): exact+=1
            if exact: exact_coordinates.append(coordinate)
            row_titles=[value for value in (row.get("title"),row.get("name")) if value]
            similarity=best_title_similarity(titles,row_titles) if titles and row_titles else 0.0
            row_date=str(row.get("date") or row.get("first_aired") or "")[:10]
            date_match=bool(release and row_date and release==row_date)
            if exact or similarity>=0.92 or (date_match and similarity>=0.75):
                candidates.append((exact,1 if date_match else 0,similarity,coordinate))
        exact_coordinates=sorted(set(exact_coordinates))
        if exact_coordinates:
            seasons={coordinate[0] for coordinate in exact_coordinates}
            numbers=[coordinate[1] for coordinate in exact_coordinates]
            if len(seasons)!=1 or numbers!=list(range(numbers[0],numbers[-1]+1)):
                return None
            season=exact_coordinates[0][0]
            locator="S{:02d}E{:02d}".format(season,numbers[0])
            if len(numbers)>1: locator+="-E{:02d}".format(numbers[-1])
            return {"_simkl_reference_id":str(simkl_reference_id),"_special_locator":locator}
        if not candidates: return None
        candidates.sort(key=lambda value:(value[0],value[1],value[2]),reverse=True); best=candidates[0]
        if len(candidates)>1 and best[0]==0 and candidates[1][0]==0 and best[1]==candidates[1][1] and best[2]-candidates[1][2]<0.05:
            return None
        season,episode=best[3]
        return {"_simkl_reference_id":str(simkl_reference_id),"_special_locator":"S{:02d}E{:02d}".format(season,episode)}

    def resolve(self,item):
        known={name:item.get(name+"_id") for name in PROVIDERS}; candidates=self._simkl_ids(known)
        if not candidates: return {}
        disagreements={}; parent_candidates=[]
        for simkl_id in candidates:
            resolved=self._resolved_ids(self._detail(simkl_id),simkl_id)
            disagreements=self._disagreements(known,resolved)
            if not disagreements: return resolved
            parent_candidates.append(simkl_id)

        tried=set()
        for provider in ("mal","anilist","kitsu"):
            if not known.get(provider): continue
            for candidate in self._search(provider,known[provider]):
                if candidate.get("type")!="anime": continue
                candidate_id=str(((candidate.get("ids") or {}).get("simkl") or ""))
                if not candidate_id or candidate_id in tried: continue
                tried.add(candidate_id); candidate_ids=self._resolved_ids(self._detail(candidate_id),candidate_id)
                if not self._disagreements(known,candidate_ids): return candidate_ids

        if self._special_capable(item):
            for reference in parent_candidates:
                special=self._special_reference(item,reference)
                if special: return special
            return {}

        if disagreements:
            details=", ".join("{} {} != {}".format(name,*values) for name,values in sorted(disagreements.items()))
            raise IdentityMappingConflict("Simkl resolved a different anime item: "+details)
        return {}


class WatchlistIdentityEnrichmentService:
    """Process Watchlist # -> Z and release mediator work every ten percent."""
    def __init__(self,store,client=None,request_delay=0.25,on_complete=None,on_progress=None):
        self.store=store; self.client=client or ProviderIdentityClient(); self.request_delay=max(0,float(request_delay))
        self._stop=threading.Event(); self.on_complete=on_complete; self.on_progress=on_progress
        self._lock=threading.Lock(); self._thread=None

    @staticmethod
    def _needs_identity(item):
        return any(item.get(name+"_id") in (None,"") for name in ("anilist","mal","kitsu")) or (
            item.get("simkl_id") in (None,"") and item.get("simkl_reference_id") in (None,""))

    @staticmethod
    def _publication_unconfirmed(item):
        try: episode_count=int(item.get("episode_count") or 0)
        except (TypeError,ValueError): episode_count=0
        return episode_count<=0 and item.get("release_date") in (None,"")

    def _notify_progress(self,processed,total,bucket):
        if not self.on_progress or self._stop.is_set(): return
        try: self.on_progress({"processed":processed,"total":total,"percent":min(100,bucket*10)})
        except Exception: LOGGER.exception("Mediator progress callback failed")

    def _record_if_present(self,local_id,status,error=None):
        try:
            self.store.record_identity_resolution(local_id,status,error)
            return True
        except KeyError:
            LOGGER.info(
                "Identity work item %s was merged into another Prime item; "
                "discarding the stale queue entry",local_id)
            return False

    def _release_if_present(self,local_id):
        marker=getattr(self.store,"mark_mediator_ready",None)
        if not marker: return False
        try:
            marker(local_id,True)
            return True
        except KeyError:
            LOGGER.info(
                "Identity work item %s was merged before mediator release",local_id)
            return False

    def run_once(self):
        if not self._lock.acquire(blocking=False): return {"scheduled":False,"busy":True}
        complete=partial=unavailable=failed=0
        try:
            getter=getattr(self.store,"list_watchdog_work",None); pending=getter() if getter else self.store.list_missing_provider_ids()
            total=len(pending); notified_bucket=0
            for index,item in enumerate(pending,1):
                if self._stop.is_set(): break
                current=getattr(self.store,"item",lambda _id:None)(item["local_id"])
                if current is None:
                    LOGGER.info(
                        "Skipping stale identity queue item %s after duplicate merge",
                        item["local_id"])
                    continue
                item=current
                release_to_mediator=True
                try:
                    identity_status=str(item.get("identity_resolution_status") or "")
                    publication_unconfirmed=self._publication_unconfirmed(item)
                    # Published exact contradictions are terminal. An item with no
                    # episode count and no first-release date is not published
                    # enough to make a Simkl mismatch permanent, so retry it.
                    if identity_status=="CONFLICT_EXACT" and not publication_unconfirmed:
                        unavailable+=1
                    elif self._needs_identity(item) or (
                            identity_status in ("CONFLICT_EXACT","PENDING_PUBLICATION") and
                            publication_unconfirmed):
                        resolved=self.client.resolve(item) or {}
                        ids={name:resolved.get(name) for name in PROVIDERS if resolved.get(name) not in (None,"")}
                        if ids: self.store.apply_resolved_ids(item["local_id"],ids)
                        if resolved.get("_simkl_reference_id") and resolved.get("_special_locator"):
                            setter=getattr(self.store,"set_special_reference",None)
                            if setter: setter(item["local_id"],resolved["_simkl_reference_id"],resolved["_special_locator"])
                        current=getattr(self.store,"item",lambda _id:None)(item["local_id"]) or item
                        if self._needs_identity(current):
                            if resolved:
                                partial+=1
                                release_to_mediator=self._record_if_present(
                                    item["local_id"],"PARTIAL")
                            elif publication_unconfirmed:
                                partial+=1
                                release_to_mediator=self._record_if_present(
                                    item["local_id"],"PENDING_PUBLICATION",
                                    "Provider identity and publication metadata are not available yet")
                            else:
                                unavailable+=1
                                release_to_mediator=self._record_if_present(
                                    item["local_id"],"NOT_FOUND","Provider ID not currently available")
                        else:
                            complete+=1
                            release_to_mediator=self._record_if_present(
                                item["local_id"],"RESOLVED")
                    else:
                        complete+=1
                        release_to_mediator=self._record_if_present(
                            item["local_id"],"RESOLVED")
                except IdentityMappingConflict as exc:
                    if self._publication_unconfirmed(item):
                        partial+=1
                        release_to_mediator=self._record_if_present(
                            item["local_id"],"PENDING_PUBLICATION",str(exc))
                        LOGGER.info(
                            "Simkl identity for unpublished Prime item %s is provisional; "
                            "retrying after future watchlist refreshes: %s",item["local_id"],exc)
                    else:
                        unavailable+=1
                        release_to_mediator=self._record_if_present(
                            item["local_id"],"CONFLICT_EXACT",str(exc))
                        LOGGER.warning(
                            "Simkl identity conflict for Prime item %s; mediator will bypass Simkl: %s",
                            item["local_id"],exc)
                except Exception as exc:
                    failed+=1; LOGGER.exception("Provider ID enrichment failed for Prime item %s",item["local_id"])
                    release_to_mediator=self._record_if_present(
                        item["local_id"],"PARTIAL",str(exc))
                finally:
                    if release_to_mediator and not int(item.get("added_to_library") or 0):
                        self._release_if_present(item["local_id"])

                bucket=int(index*10/total) if total else 10
                if bucket>notified_bucket:
                    notified_bucket=bucket; self._notify_progress(index,total,bucket)
                if self.request_delay and self._stop.wait(self.request_delay): break

            self.store.finalize_merge()
            LOGGER.info("Provider ID enrichment complete: complete=%s partial=%s unavailable=%s failed=%s",
                        complete,partial,unavailable,failed)
            result={"complete":complete,"partial":partial,"unavailable":unavailable,"failed":failed}
            if self.on_complete and not self._stop.is_set():
                try: self.on_complete()
                except Exception: LOGGER.exception("Post-enrichment service failed to start")
            return result
        finally: self._lock.release()

    def start(self):
        if self._thread and self._thread.is_alive(): return {"scheduled":False,"busy":True}
        self._stop.clear(); self._thread=threading.Thread(target=self.run_once,name="OtakuPrimeIdentityEnrichment",daemon=True)
        self._thread.start(); return {"scheduled":True,"busy":False}

    def stop(self,timeout=5):
        self._stop.set()
        if self._thread: self._thread.join(timeout=timeout)
