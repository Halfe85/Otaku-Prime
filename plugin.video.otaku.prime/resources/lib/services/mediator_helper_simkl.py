# -*- coding: utf-8 -*-
"""Simkl-backed franchise and episode placement for Prime watchlist items."""
from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

from resources.lib.watchlist.simkl import PACKAGED_CLIENT_ID,SIMKL_API_URL


MAX_PREQUEL_DEPTH=64
SPECIAL_MEDIA_TYPES=("movie","ova","ona","special","music video")


class MediatorPlacementError(RuntimeError):
    pass


class SimklMediatorClient:
    """Small throttled Simkl client shared by every provider path."""
    def __init__(self,client_id=None,timeout=30,request_delay=0.25,opener=None):
        self.client_id=str(client_id or PACKAGED_CLIENT_ID).strip()
        self.timeout=int(timeout); self.request_delay=max(0,float(request_delay))
        self._open=opener or urlopen; self._last_request=0.0; self._lock=threading.Lock()
        self._anime_cache={}; self._tv_cache={}; self._episode_cache={}

    def _get(self,path,params=None):
        query={"client_id":self.client_id,"app-name":"otaku-prime","app-version":"0.1.2"}
        query.update(params or {})
        request=Request(SIMKL_API_URL+path+"?"+urlencode(query),headers={
            "Accept":"application/json","User-Agent":"Otaku-Prime/0.1.2 mediator"})
        with self._lock:
            remaining=self.request_delay-(time.monotonic()-self._last_request)
            if remaining>0: time.sleep(remaining)
            try:
                with self._open(request,timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                raise MediatorPlacementError(
                    "Simkl {} returned HTTP {}".format(path,exc.code)) from exc
            except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
                raise MediatorPlacementError("Simkl {} failed: {}".format(path,exc)) from exc
            finally: self._last_request=time.monotonic()

    def anime(self,simkl_id):
        key=str(simkl_id)
        if key not in self._anime_cache:
            payload=self._get("/anime/"+key)
            if not isinstance(payload,dict) or not (payload.get("ids") or {}).get("simkl"):
                raise MediatorPlacementError("Simkl returned an invalid anime detail record")
            self._anime_cache[key]=payload
        return self._anime_cache[key]

    def episodes(self,simkl_id):
        key=str(simkl_id)
        if key not in self._episode_cache:
            payload=self._get("/anime/episodes/"+key)
            if payload is None: payload=[]
            if not isinstance(payload,list):
                raise MediatorPlacementError("Simkl returned an invalid anime episode list")
            self._episode_cache[key]=payload
        return self._episode_cache[key]

    def tv(self,simkl_id):
        key=str(simkl_id)
        if key not in self._tv_cache:
            payload=self._get("/tv/"+key)
            if not isinstance(payload,dict):
                raise MediatorPlacementError("Simkl returned an invalid TV detail record")
            self._tv_cache[key]=payload
        return self._tv_cache[key]

    def exact_simkl_id(self,provider,provider_id):
        if provider=="simkl": return str(provider_id)
        payload=self._get("/search/id",{provider:str(provider_id)})
        for match in payload or []:
            if match.get("type")!="anime": continue
            simkl_id=(match.get("ids") or {}).get("simkl")
            if simkl_id in (None,""): continue
            ids=self.anime(simkl_id).get("ids") or {}
            if str(ids.get(provider) or "")==str(provider_id): return str(simkl_id)
        raise MediatorPlacementError(
            "Simkl returned no exact {} identity for {}".format(provider,provider_id))

    def tv_franchise(self,anime_detail):
        anime_ids=anime_detail.get("ids") or {}; tvdb_id=anime_ids.get("tvdb")
        tmdb_id=anime_ids.get("tmdb")
        if tvdb_id in (None,""): return None
        payload=self._get("/search/id",{"tvdb":str(tvdb_id)})
        for row in payload or []:
            if row.get("type")!="tv": continue
            simkl_id=(row.get("ids") or {}).get("simkl")
            if simkl_id not in (None,""):
                detail=self.tv(simkl_id)
                return {"name":detail.get("en_title") or detail.get("title") or row.get("title"),
                        "simkl_id":str(simkl_id),"tvdb_id":str(tvdb_id),
                        "source":"simkl_tvdb_crossmap"}
        anime_rows=[row for row in (payload or []) if row.get("type")=="anime"
                    and str(row.get("anime_type") or "").lower()=="tv"]
        if anime_rows:
            anchor=sorted(anime_rows,key=lambda row:(
                int(row.get("year") or 9999),int((row.get("ids") or {}).get("simkl") or 0)))[0]
            simkl_id=(anchor.get("ids") or {}).get("simkl")
            detail=self.anime(simkl_id)
            return {"name":detail.get("en_title") or detail.get("title") or anchor.get("title"),
                    "simkl_id":str(simkl_id),"tvdb_id":str(tvdb_id),
                    "source":"simkl_tvdb_anime_group"}
        queries=[]
        if anime_ids.get("tvdbslug"): queries.append(str(anime_ids["tvdbslug"]).replace("-"," "))
        queries.extend(value for value in
                       (anime_detail.get("en_title"),anime_detail.get("title")) if value)
        for query in queries:
            for row in self._get("/search/tv",{"q":query,"limit":50}) or []:
                ids=row.get("ids") or {}
                if tmdb_id in (None,"") or str(ids.get("tmdb") or "")!=str(tmdb_id): continue
                simkl_id=ids.get("simkl_id")
                if simkl_id in (None,""): continue
                detail=self.tv(simkl_id)
                return {"name":detail.get("en_title") or detail.get("title") or row.get("title"),
                        "simkl_id":str(simkl_id),"tvdb_id":str(tvdb_id),
                        "source":"simkl_tmdb_tv_match"}
        return None


def _relations(detail):
    relations=detail.get("relations") or []
    if not isinstance(relations,dict): return list(relations) if isinstance(relations,list) else []
    result=[]
    for relation_type,rows in relations.items():
        if isinstance(rows,dict): rows=[rows]
        for row in rows or []:
            value=dict(row); value.setdefault("relation_type",relation_type); result.append(value)
    return result


def _direct(value):
    return value is True or str(value).strip().lower() in ("1","true","yes")


def _find_root(client,target):
    path=[target]; seen={str((target.get("ids") or {})["simkl"])}
    franchise_tvdb=str((target.get("ids") or {}).get("tvdb") or ""); current=target
    for _ in range(MAX_PREQUEL_DEPTH):
        candidates=[]
        for relation in _relations(current):
            relation_type=str(relation.get("relation_type") or "").lower().replace("_"," ")
            candidate_id=(relation.get("ids") or {}).get("simkl")
            if relation_type!="prequel" or candidate_id in (None,"") or str(candidate_id) in seen: continue
            detail=client.anime(candidate_id)
            if franchise_tvdb and str((detail.get("ids") or {}).get("tvdb") or "")!=franchise_tvdb:
                continue
            value=dict(relation); value["_detail"]=detail; candidates.append(value)
        if not candidates: break
        relation=sorted(candidates,key=lambda row:(
            0 if _direct(row.get("is_direct")) else 1,
            0 if str(row.get("anime_type") or "").lower()=="tv" else 1,
            int(row.get("year") or 9999),int((row.get("ids") or {}).get("simkl") or 0)))[0]
        current=relation["_detail"]; seen.add(str((current.get("ids") or {})["simkl"])); path.append(current)
    else: raise MediatorPlacementError("Simkl prequel graph exceeded its safety limit")
    return current,list(reversed(path))


def _season_number(detail,path):
    mapped=[int(value) for value in detail.get("mapped_tvdb_seasons") or []]
    if len(mapped)==1: return mapped[0],"mapped_tvdb_seasons"
    if mapped: raise MediatorPlacementError("Item maps to multiple TVDB seasons: {}".format(mapped))
    if str(detail.get("anime_type") or "").lower() in SPECIAL_MEDIA_TYPES:
        return 0,"special_format"
    main_tv=[node for node in path if str(node.get("anime_type") or "").lower()=="tv"]
    return max(1,len(main_tv)),"direct_prequel_position"


def _episodes(rows,watchlist_item_is_special=False):
    """Return only episodes belonging to the requested watchlist item.

    Simkl can attach bonus specials to an ordinary TV entry. Those rows are
    supplemental franchise data and must not enter Prime unless the canonical
    watchlist item itself is a movie, OVA, ONA, or special.
    """
    result=[]
    for index,row in enumerate(rows,1):
        row_type=str(row.get("type") or "episode").lower()
        if row_type!="episode" and not (watchlist_item_is_special and row_type=="special"):
            continue
        raw_number=row.get("episode") or row.get("number") or index
        try: source_number=int(raw_number)
        except (TypeError,ValueError): source_number=index
        ids=row.get("ids") or {}; tvdb=row.get("tvdb") or {}
        result.append({"source_episode_number":source_number,
                       "episode_number":int(tvdb["episode"]) if tvdb.get("episode") is not None else None,
                       "season_number":int(tvdb["season"]) if tvdb.get("season") is not None else None,
                       "simkl_id":str(ids["simkl_id"]) if ids.get("simkl_id") not in (None,"") else None,
                       "mal_id":str(ids["mal"]) if ids.get("mal") not in (None,"") else None,
                       "release_date":row.get("date") or row.get("first_aired")})
    return result


class SimklMediatorHelper:
    provider="simkl"
    def resolve_simkl_id(self,item,client):
        value=item.get(self.provider+"_id")
        if value in (None,""):
            raise MediatorPlacementError("watchlist item has no {} ID".format(self.provider))
        return client.exact_simkl_id(self.provider,value)

    def resolve(self,item,client):
        simkl_id=self.resolve_simkl_id(item,client); target=client.anime(simkl_id)
        root,path=_find_root(client,target)
        franchise=client.tv_franchise(target) or {
            "name":root.get("en_title") or root.get("title"),
            "simkl_id":str((root.get("ids") or {}).get("simkl")),
            "tvdb_id":str((root.get("ids") or {}).get("tvdb") or "") or None,
            "source":"relation_fallback"}
        season_number,number_source=_season_number(target,path)
        target_type=str(target.get("anime_type") or "").lower()
        episodes=_episodes(client.episodes(simkl_id),target_type in SPECIAL_MEDIA_TYPES)
        if not episodes:
            raise MediatorPlacementError("Simkl returned no episodes for the requested watchlist item")
        unmapped=[row["source_episode_number"] for row in episodes
                  if row["season_number"] is None or row["episode_number"] is None]
        seasons={row["season_number"] for row in episodes if row["season_number"] is not None}
        if unmapped: raise MediatorPlacementError("Episodes lack franchise coordinates: {}".format(unmapped))
        if seasons!={season_number}:
            raise MediatorPlacementError("Episode coordinates {} disagree with season {}".format(
                sorted(seasons),season_number))
        numbers=sorted(row["episode_number"] for row in episodes)
        expected=list(range(numbers[0],numbers[-1]+1))
        if numbers!=expected: raise MediatorPlacementError("Franchise episode coordinates contain gaps")
        return {"provider_path":self.provider,"provider_id":str(item[self.provider+"_id"]),
                "tv_show":franchise,
                "season":{"number":season_number,"number_source":number_source,
                          "name":target.get("en_title") or target.get("title"),
                          "media_type":target_type,
                          "first_episode":numbers[0],"last_episode":numbers[-1]},
                "episodes":episodes,
                "relation_path":[str((node.get("ids") or {}).get("simkl")) for node in path]}
