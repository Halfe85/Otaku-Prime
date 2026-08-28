# -*- coding: utf-8 -*-
"""Exact-ID Kitsu metadata endpoint used as the mediator's final fallback."""
from __future__ import annotations

import json
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

from resources.lib.services.mediator_helper_simkl import MediatorPlacementError

KITSU_API_URL="https://kitsu.io/api/edge"
SPECIAL_FORMATS={"movie","ova","ona","special","music"}
SEASON_FORMATS={"tv","tv_special"}
MAX_PREQUEL_DEPTH=64


class KitsuMediatorClient:
    def __init__(self,timeout=30,opener=None):
        self.timeout=int(timeout); self._open=opener or urlopen
        self._anime_cache={}; self._prequel_cache={}

    @staticmethod
    def _headers():
        return {"Accept":"application/vnd.api+json","User-Agent":"Otaku-Prime/0.1.2 kitsu-mediator"}

    def _json(self,url):
        try:
            with self._open(Request(url,headers=self._headers()),timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise MediatorPlacementError("Kitsu returned HTTP {}".format(exc.code)) from exc
        except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
            raise MediatorPlacementError("Kitsu request failed: {}".format(exc)) from exc

    def anime(self,kitsu_id):
        key=str(kitsu_id)
        if key not in self._anime_cache:
            payload=self._json(KITSU_API_URL+"/anime/"+key)
            data=(payload or {}).get("data") or {}
            if str(data.get("id") or "")!=key:
                raise MediatorPlacementError("Kitsu returned a different or invalid anime identity")
            self._anime_cache[key]=data
        return self._anime_cache[key]

    def prequels(self,kitsu_id):
        key=str(kitsu_id)
        if key in self._prequel_cache: return self._prequel_cache[key]
        params={"filter[source_type]":"Anime","filter[source_id]":key,
                "filter[role]":"prequel","include":"destination","page[limit]":20}
        payload=self._json(KITSU_API_URL+"/media-relationships?"+urlencode(params))
        included={str(row.get("id")):row for row in (payload.get("included") or [])
                  if row.get("type")=="anime" and row.get("id") not in (None,"")}
        result=[]
        for relation in payload.get("data") or []:
            destination=(((relation.get("relationships") or {}).get("destination") or {}).get("data") or {})
            value=included.get(str(destination.get("id") or ""))
            if value: result.append(value)
        self._prequel_cache[key]=result
        return result


def _attrs(media): return (media or {}).get("attributes") or {}
def _format(media): return str(_attrs(media).get("subtype") or "").lower()

def _date_key(media):
    value=str(_attrs(media).get("startDate") or "9999-99-99")
    try: numeric=int(media.get("id") or 0)
    except (TypeError,ValueError): numeric=0
    return value,numeric


def _find_root(client,target):
    path=[target]; current=target; seen={str(target["id"])}
    for _ in range(MAX_PREQUEL_DEPTH):
        candidates=[row for row in client.prequels(current["id"])
                    if str(row.get("id")) not in seen]
        if not candidates: break
        current=sorted(candidates,key=_date_key)[0]
        seen.add(str(current["id"])); path.append(current)
    else:
        raise MediatorPlacementError("Kitsu prequel graph exceeded its safety limit")
    return current,list(reversed(path))


def _season_number(target,path):
    fmt=_format(target)
    if fmt in SPECIAL_FORMATS: return 0,"kitsu_special_format"
    numbered=[row for row in path if _format(row) in SEASON_FORMATS]
    target_id=str(target["id"])
    for index,row in enumerate(numbered,1):
        if str(row["id"])==target_id: return index,"kitsu_prequel_position"
    return max(1,len(numbered)+1),"kitsu_prequel_position"


def _special_offset(target,path):
    target_id=str(target["id"]); offset=0
    for row in path:
        if str(row["id"])==target_id: break
        if _format(row) not in SPECIAL_FORMATS: continue
        try: offset+=max(0,int(_attrs(row).get("episodeCount") or 0))
        except (TypeError,ValueError): pass
    return offset


def _runtime(attrs):
    try:
        value=int(attrs.get("episodeLength") or 0)
        return value if value>0 else None
    except (TypeError,ValueError): return None


class KitsuMediatorEndpoint:
    provider="kitsu"
    def __init__(self,client=None): self.client=client or KitsuMediatorClient()

    @staticmethod
    def available(item): return item.get("kitsu_id") not in (None,"")

    def resolve(self,item,client=None):
        value=item.get("kitsu_id")
        if value in (None,""): raise MediatorPlacementError("watchlist item has no Kitsu ID")
        target=self.client.anime(value); root,path=_find_root(self.client,target)
        target_attrs=_attrs(target); root_attrs=_attrs(root)
        season_number,number_source=_season_number(target,path)
        try: count=int(target_attrs.get("episodeCount") or item.get("episode_count") or 0)
        except (TypeError,ValueError): count=0
        if count<=0: raise MediatorPlacementError("Kitsu has no episode count for this anime")
        offset=_special_offset(target,path) if season_number==0 else 0
        runtime=_runtime(target_attrs)
        episodes=[]
        for source_number in range(1,count+1):
            episodes.append({"source_episode_number":source_number,"episode_number":offset+source_number,
                             "season_number":season_number,"simkl_id":None,"mal_id":None,
                             "title":None,"overview":None,"runtime_minutes":runtime,
                             "release_date":target_attrs.get("startDate") if source_number==1 else None})
        titles=root_attrs.get("titles") or {}; target_titles=target_attrs.get("titles") or {}
        name=titles.get("en") or root_attrs.get("canonicalTitle") or target_titles.get("en") or target_attrs.get("canonicalTitle")
        romaji=titles.get("en_jp") or root_attrs.get("canonicalTitle") or target_titles.get("en_jp") or target_attrs.get("canonicalTitle")
        try: publish_year=int(str(root_attrs.get("startDate") or target_attrs.get("startDate") or "")[:4])
        except (TypeError,ValueError): publish_year=None
        numbers=[row["episode_number"] for row in episodes]
        return {"provider_path":"kitsu","provider_id":str(value),
                "tv_show":{"name":name,"romaji_name":romaji,"simkl_id":None,"tvdb_id":None,
                           "anilist_id":None,"source_format":str(root_attrs.get("subtype") or target_attrs.get("subtype") or "").upper() or None,
                           "source":"kitsu_prequel_graph","publish_year":publish_year,
                           "overview":target_attrs.get("synopsis") or root_attrs.get("synopsis"),
                           "runtime_minutes":runtime or _runtime(root_attrs),
                           "air_status":target_attrs.get("status") or root_attrs.get("status"),"cast":None},
                "season":{"number":season_number,"number_source":number_source,"name":target_attrs.get("canonicalTitle"),
                          "media_type":_format(target),"first_episode":numbers[0],"last_episode":numbers[-1]},
                "episodes":episodes,"relation_path":[str(row["id"]) for row in path]}
