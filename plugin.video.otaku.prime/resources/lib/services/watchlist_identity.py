# -*- coding: utf-8 -*-
"""Fill canonical tracker IDs from Simkl's public anime catalog."""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from resources.lib.logging_config import get_logger
from resources.lib.watchlist.simkl import PACKAGED_CLIENT_ID, SIMKL_API_URL

LOGGER=get_logger(__name__)
PROVIDERS=("anilist","mal","kitsu","simkl")


class IdentityMappingConflict(ValueError):
    pass


class _StopRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


class SimklIdentityClient:
    """Resolve any native anime ID to Simkl, then fetch the complete ID set."""
    def __init__(self,client_id=None,timeout=30,opener=None,redirect_opener=None):
        self.client_id=str(client_id or PACKAGED_CLIENT_ID).strip()
        self.timeout=int(timeout)
        self._open=opener or urlopen
        self._redirect_open=redirect_opener or build_opener(_StopRedirect()).open

    def _params(self,extra=None):
        values={"client_id":self.client_id,"app-name":"otaku-prime","app-version":"0.1.2"}
        values.update(extra or {})
        return urlencode(values)

    @staticmethod
    def _headers():
        return {"Accept":"application/json","User-Agent":"Otaku-Prime/0.1.2"}

    def _simkl_id(self,ids):
        if ids.get("simkl"): return str(ids["simkl"])
        source=next(((name,str(ids[name])) for name in ("anilist","mal","kitsu")
                     if ids.get(name)),None)
        if not source: return None
        url=SIMKL_API_URL+"/redirect?"+self._params({"to":"simkl",source[0]:source[1]})
        request=Request(url,headers=self._headers())
        location=None
        try:
            with self._redirect_open(request,timeout=self.timeout) as response:
                location=response.headers.get("Location")
        except HTTPError as exc:
            if exc.code not in (301,302,303,307,308): raise
            location=exc.headers.get("Location")
        if not location: return None
        match=re.search(r"/(?:anime|tv)/(\d+)(?:/|$)",urlparse(location).path)
        return match.group(1) if match else None

    def resolve(self,item):
        known={name:item.get(name+"_id") for name in PROVIDERS}
        simkl_id=self._simkl_id(known)
        if not simkl_id: return {}
        url=SIMKL_API_URL+"/anime/{}?{}".format(simkl_id,self._params())
        try:
            with self._open(Request(url,headers=self._headers()),timeout=self.timeout) as response:
                payload=json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError("Simkl identity request failed with HTTP {}".format(exc.code)) from exc
        except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
            raise RuntimeError("Simkl identity request failed: {}".format(exc)) from exc
        ids=payload.get("ids") or {}
        ids["simkl"]=ids.get("simkl") or simkl_id
        resolved={name:str(ids[name]) for name in PROVIDERS if ids.get(name) not in (None,"")}
        disagreements={name:(str(known[name]),resolved[name]) for name in PROVIDERS
                       if known.get(name) and resolved.get(name) and str(known[name])!=resolved[name]}
        if disagreements:
            details=", ".join("{} {} != {}".format(name,*values)
                              for name,values in sorted(disagreements.items()))
            raise IdentityMappingConflict("Simkl resolved a different anime item: "+details)
        return resolved


class WatchlistIdentityEnrichmentService:
    """Account-independent, resumable catalog identity enrichment worker."""
    def __init__(self,store,client=None,request_delay=0.25):
        self.store=store; self.client=client or SimklIdentityClient()
        self.request_delay=max(0,float(request_delay)); self._stop=threading.Event()
        self._lock=threading.Lock(); self._thread=None

    def run_once(self):
        if not self._lock.acquire(blocking=False): return {"scheduled":False,"busy":True}
        resolved=unresolved=failed=0
        try:
            pending=self.store.list_missing_provider_ids()
            for item in pending:
                if self._stop.is_set(): break
                try:
                    ids=self.client.resolve(item)
                    if ids:
                        self.store.apply_resolved_ids(item["local_id"],ids); resolved+=1
                    else: unresolved+=1
                    if not ids:
                        self.store.record_identity_resolution(item["local_id"],"NOT_FOUND",
                                                              "No Simkl anime mapping")
                except IdentityMappingConflict as exc:
                    unresolved+=1
                    self.store.record_identity_resolution(item["local_id"],"CONFLICT",str(exc))
                    LOGGER.warning("Provider ID mapping conflict for Prime item %s: %s",
                                   item["local_id"],exc)
                except Exception:
                    failed+=1
                    LOGGER.exception("Provider ID enrichment failed for Prime item %s",item["local_id"])
                if self.request_delay and not self._stop.wait(self.request_delay):
                    pass
            # Newly discovered overlaps may have combined multiple provider
            # snapshots into one item; recalculate its master/conflict state.
            self.store.finalize_merge()
            LOGGER.info("Provider ID enrichment complete: resolved=%s unresolved=%s failed=%s",
                        resolved,unresolved,failed)
            return {"resolved":resolved,"unresolved":unresolved,"failed":failed}
        finally: self._lock.release()

    def start(self):
        if self._thread and self._thread.is_alive(): return {"scheduled":False,"busy":True}
        self._stop.clear()
        self._thread=threading.Thread(target=self.run_once,name="OtakuPrimeIdentityEnrichment",daemon=True)
        self._thread.start()
        return {"scheduled":True,"busy":False}

    def stop(self,timeout=5):
        self._stop.set()
        if self._thread: self._thread.join(timeout=timeout)
