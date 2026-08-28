# -*- coding: utf-8 -*-
"""Simkl-backed franchise and episode placement for Prime watchlist items."""
from __future__ import annotations

import json
import re
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

from resources.lib.services.remote_identity import (
    RemoteIdentityError,
    best_title_similarity,
    candidate_is_confident,
    clean_remote_text,
    choose_candidate,
    item_titles,
    payload_titles,
    score_candidate,
)
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
        self._anime_cache={}; self._tv_cache={}; self._episode_cache={}; self._search_cache={}

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

    def search_id(self,provider,value):
        key=("id",str(provider),str(value))
        if key not in self._search_cache:
            payload=self._get("/search/id",{str(provider):str(value)})
            self._search_cache[key]=payload if isinstance(payload,list) else []
        return self._search_cache[key]

    def search_anime(self,query,limit=20):
        key=("anime",str(query).casefold(),int(limit))
        if key not in self._search_cache:
            payload=self._get("/search/anime",{
                "q":str(query),"extended":"full","limit":int(limit)})
            self._search_cache[key]=payload if isinstance(payload,list) else []
        return self._search_cache[key]

    def _candidate_details(self,item,exclude=None):
        exclude={str(value) for value in (exclude or []) if value not in (None,"")}
        candidate_ids=[]
        for provider in ("anilist","mal","kitsu"):
            value=item.get(provider+"_id")
            if value in (None,""): continue
            for row in self.search_id(provider,value):
                if row.get("type")!="anime": continue
                simkl_id=(row.get("ids") or {}).get("simkl")
                if simkl_id not in (None,"") and str(simkl_id) not in candidate_ids:
                    candidate_ids.append(str(simkl_id))
        for title in item_titles(item):
            for row in self.search_anime(title):
                if row.get("type") not in (None,"anime"): continue
                ids=row.get("ids") or {}
                simkl_id=ids.get("simkl") or ids.get("simkl_id")
                if simkl_id not in (None,"") and str(simkl_id) not in candidate_ids:
                    candidate_ids.append(str(simkl_id))
        details=[]
        for simkl_id in candidate_ids[:30]:
            if simkl_id in exclude: continue
            try: details.append(self.anime(simkl_id))
            except MediatorPlacementError: continue
        return details

    def resolve_anime_identity(self,item,stored_simkl_id):
        """Validate a stored Simkl ID and repair it by lookup when necessary."""
        old_id=str(stored_simkl_id)
        current=None; current_score=None; current_error=None
        try:
            current=self.anime(old_id)
            current_score=score_candidate(item,current,ignore_provider="simkl")
        except MediatorPlacementError as exc:
            current_error=str(exc)

        if current is not None and candidate_is_confident(current_score) and (
            current_score["title_similarity"] >= 0.65 or current_score["matched_ids"] >= 2
        ):
            resolved_id=str((current.get("ids") or {}).get("simkl") or old_id)
            repair=None
            if resolved_id!=old_id:
                repair={"provider":"simkl","old":old_id,"new":resolved_id,
                        "reason":"Simkl detail canonicalized the stored ID"}
            return current,repair,current_score

        candidates=[]
        if current is not None:
            candidates.append(current)
        candidates.extend(self._candidate_details(item))
        try:
            selected,score=choose_candidate(item,candidates,ignore_provider="simkl")
        except RemoteIdentityError as exc:
            detail="stored Simkl ID {} did not match Prime".format(old_id)
            if current_error: detail+=" ({})".format(current_error)
            raise MediatorPlacementError("{}; lookup failed: {}".format(detail,exc)) from exc
        new_id=str((selected.get("ids") or {}).get("simkl") or "")
        if not new_id:
            raise MediatorPlacementError("Simkl identity lookup returned a candidate without a Simkl ID")
        repair=None
        if new_id!=old_id:
            repair={"provider":"simkl","old":old_id,"new":new_id,
                    "reason":"Stored Simkl ID failed Prime identity validation"}
        return selected,repair,score

    @staticmethod
    def _franchise_titles(root_detail,anime_detail):
        values=[]
        for payload in (root_detail or {},anime_detail or {}):
            values.extend(payload_titles(payload))
        return list(dict.fromkeys(values))

    @staticmethod
    def _tv_title_ok(expected_titles,detail,row=None):
        actual=payload_titles(detail)
        if row: actual.extend(payload_titles(row))
        return best_title_similarity(expected_titles,actual)>=0.62

    def tv_franchise(self,anime_detail,root_detail=None):
        """Resolve a TV franchise without blindly trusting the anime TVDB ID."""
        anime_ids=anime_detail.get("ids") or {}; tvdb_id=anime_ids.get("tvdb")
        tmdb_id=anime_ids.get("tmdb")
        expected_titles=self._franchise_titles(root_detail,anime_detail)

        if tvdb_id not in (None,""):
            payload=self.search_id("tvdb",tvdb_id)
            for row in payload or []:
                if row.get("type")!="tv": continue
                simkl_id=(row.get("ids") or {}).get("simkl")
                if simkl_id in (None,""): continue
                detail=self.tv(simkl_id)
                if not self._tv_title_ok(expected_titles,detail,row):
                    continue
                ids=detail.get("ids") or {}; row_ids=row.get("ids") or {}
                return {"name":_remote_title(detail) or _remote_title(row),
                        "simkl_id":str(simkl_id),
                        "tvdb_id":str(ids.get("tvdb") or row_ids.get("tvdb") or tvdb_id),
                        "source":"simkl_tvdb_crossmap_validated"}
            anime_rows=[row for row in (payload or []) if row.get("type")=="anime"
                        and str(row.get("anime_type") or "").lower() in ("tv","tv short","tv_short","ona")]
            valid=[]
            for row in anime_rows:
                simkl_id=(row.get("ids") or {}).get("simkl")
                if simkl_id in (None,""): continue
                detail=self.anime(simkl_id)
                if self._tv_title_ok(expected_titles,detail,row):
                    valid.append((row,detail))
            if valid:
                row,detail=sorted(valid,key=lambda value:(
                    int(value[0].get("year") or 9999),
                    int((value[0].get("ids") or {}).get("simkl") or 0)))[0]
                simkl_id=(row.get("ids") or {}).get("simkl")
                ids=detail.get("ids") or {}; row_ids=row.get("ids") or {}
                return {"name":_remote_title(detail) or _remote_title(row),
                        "simkl_id":str(simkl_id),
                        "tvdb_id":str(ids.get("tvdb") or row_ids.get("tvdb") or tvdb_id),
                        "source":"simkl_tvdb_anime_group_validated"}

        queries=[]
        root_ids=(root_detail or {}).get("ids") or {}
        if root_ids.get("tvdbslug"): queries.append(str(root_ids["tvdbslug"]).replace("-"," "))
        if anime_ids.get("tvdbslug"): queries.append(str(anime_ids["tvdbslug"]).replace("-"," "))
        queries.extend(self._franchise_titles(root_detail,anime_detail))
        seen=set(); candidates=[]
        for query in queries:
            if not query or query.casefold() in seen: continue
            seen.add(query.casefold())
            for row in self._get("/search/tv",{"q":query,"limit":50}) or []:
                ids=row.get("ids") or {}
                simkl_id=ids.get("simkl") or ids.get("simkl_id")
                if simkl_id in (None,""): continue
                detail=self.tv(simkl_id)
                detail_ids=detail.get("ids") or {}
                candidate_tvdb=detail_ids.get("tvdb") or ids.get("tvdb")
                candidate_tmdb=detail_ids.get("tmdb") or ids.get("tmdb")
                tmdb_match=tmdb_id not in (None,"") and str(candidate_tmdb or "")==str(tmdb_id)
                if (tvdb_id not in (None,"") and candidate_tvdb not in (None,"")
                        and str(candidate_tvdb)!=str(tvdb_id) and not tmdb_match):
                    continue
                similarity=best_title_similarity(expected_titles,payload_titles(detail)+payload_titles(row))
                if not tmdb_match and similarity<0.82: continue
                candidates.append((1 if tmdb_match else 0,similarity,row,detail))
        if candidates:
            candidates.sort(key=lambda value:(value[0],value[1]),reverse=True)
            best=candidates[0]
            if len(candidates)>1 and best[0]==candidates[1][0] and best[1]-candidates[1][1]<0.05:
                return None
            _,_,row,detail=best; ids=detail.get("ids") or {}; row_ids=row.get("ids") or {}
            simkl_id=ids.get("simkl") or row_ids.get("simkl") or row_ids.get("simkl_id")
            resolved_tvdb=ids.get("tvdb") or row_ids.get("tvdb")
            return {"name":_remote_title(detail) or _remote_title(row),
                    "simkl_id":str(simkl_id),
                    "tvdb_id":str(resolved_tvdb) if resolved_tvdb not in (None,"") else None,
                    "source":"simkl_franchise_lookup_repaired"}
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
            if franchise_tvdb and str((detail.get("ids") or {}).get("tvdb") or "")==franchise_tvdb:
                value=dict(relation); value["_detail"]=detail; candidates.append(value); continue
            if best_title_similarity(payload_titles(current),payload_titles(detail))>=0.30:
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


def _int_or_none(value):
    if value in (None,""): return None
    if isinstance(value,bool): return None
    if isinstance(value,(int,float)): return int(value)
    match=re.search(r"\d+",str(value))
    return int(match.group(0)) if match else None


def _remote_title(payload):
    return clean_remote_text((payload or {}).get("en_title") or (payload or {}).get("title"))


def _overview(payload):
    for key in ("overview","description","synopsis","plot"):
        value=(payload or {}).get(key)
        if value:
            return clean_remote_text(value).strip()
    return None


def _cast_entries(payload):
    """Accept cast-shaped provider data without assuming every provider supplies it."""
    marker=None
    for key in ("cast","actors"):
        if key in (payload or {}):
            marker=(payload or {}).get(key)
            break
    if marker is None:
        return None
    if not isinstance(marker,list):
        return []
    result=[]
    for index,row in enumerate(marker):
        if not isinstance(row,dict): continue
        actor=row.get("person") or row.get("actor") or row.get("name")
        if isinstance(actor,dict): actor=actor.get("name") or actor.get("full_name")
        character=row.get("character") or row.get("role") or row.get("character_name")
        if isinstance(character,dict): character=character.get("name")
        if actor:
            result.append({"person_name":clean_remote_text(actor),
                           "character_name":clean_remote_text(character) if character else None,
                           "sort_order":index})
    return result


def _episodes(rows,watchlist_item_is_special=False):
    """Return only episodes belonging to the requested watchlist item."""
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
                       "title":clean_remote_text(row.get("title") or row.get("name")),
                       "overview":_overview(row),
                       "runtime_minutes":_int_or_none(row.get("runtime") or row.get("runtime_minutes")),
                       "release_date":row.get("date") or row.get("first_aired")})
    return result


class SimklMediatorHelper:
    provider="simkl"
    def resolve_simkl_id(self,item,client):
        value=item.get(self.provider+"_id")
        if value in (None,""):
            raise MediatorPlacementError("watchlist item has no {} ID".format(self.provider))
        return str(value)

    def resolve(self,item,client):
        stored_id=self.resolve_simkl_id(item,client)
        target,identity_repair,identity_score=client.resolve_anime_identity(item,stored_id)
        simkl_id=str((target.get("ids") or {}).get("simkl") or stored_id)
        root,path=_find_root(client,target)
        franchise=client.tv_franchise(target,root_detail=root) or {
            "name":_remote_title(root),
            "simkl_id":str((root.get("ids") or {}).get("simkl")),
            "tvdb_id":None,
            "source":"relation_fallback_unmapped"}
        root_ids=root.get("ids") or {}
        franchise.update({
            "romaji_name":clean_remote_text(root.get("title") or target.get("title")),
            "anilist_id":str(root_ids.get("anilist")) if root_ids.get("anilist") not in (None,"") else None,
            "source_format":str(root.get("anime_type") or target.get("anime_type") or "").upper() or None,
            "publish_year":_int_or_none(root.get("year") or target.get("year")),
            "overview":_overview(root) or _overview(target),
            "runtime_minutes":_int_or_none(target.get("runtime") or root.get("runtime") or
                                            target.get("runtime_minutes") or root.get("runtime_minutes")),
            "air_status":target.get("status") or target.get("release_status") or
                         root.get("status") or root.get("release_status"),
            "cast":_cast_entries(target) if _cast_entries(target) is not None else _cast_entries(root),
        })
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
        return {"provider_path":self.provider,"provider_id":simkl_id,
                "identity_repair":identity_repair,"identity_score":identity_score,
                "tv_show":franchise,
                "season":{"number":season_number,"number_source":number_source,
                          "name":_remote_title(target),
                          "media_type":target_type,
                          "first_episode":numbers[0],"last_episode":numbers[-1]},
                "episodes":episodes,
                "relation_path":[str((node.get("ids") or {}).get("simkl")) for node in path]}
