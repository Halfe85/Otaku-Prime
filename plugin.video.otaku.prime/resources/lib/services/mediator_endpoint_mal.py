# -*- coding: utf-8 -*-
"""Exact-ID MyAnimeList metadata endpoint for cooperative mediation."""
from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
)
from resources.lib.watchlist.mal import MAL_API_URL,MAL_CLIENT_ID

SPECIAL_FORMATS={"movie","ova","ona","special","music"}
SEASON_FORMATS={"tv","tv_special"}
MAX_PREQUEL_DEPTH=64
JIKAN_API_URL="https://api.jikan.moe/v4"


def _runtime_minutes(value):
    try:
        seconds=int(value or 0)
    except (TypeError,ValueError):
        return None
    return max(1,round(seconds/60)) if seconds>0 else None


def _format(value):
    return str(value or "").lower()


def _genres(*media_rows):
    result=[]; seen=set()
    for media in media_rows:
        for row in (media or {}).get("genres") or []:
            value=row.get("name") if isinstance(row,dict) else row
            text=str(value or "").strip(); key=text.casefold()
            if text and key not in seen: result.append(text); seen.add(key)
    return result


def _rating(media):
    value=str((media or {}).get("rating") or "").strip().lower()
    return {"g":"G","pg":"PG","pg_13":"PG-13","r":"R",
            "r+":"R+","rx":"Rx"}.get(
                value,str((media or {}).get("rating") or "").strip() or None)


def _mature(*media_rows):
    return any(str((row or {}).get("nsfw") or "").lower()=="black" or
               str((row or {}).get("rating") or "").lower() in ("r+","rx")
               for row in media_rows)


class MALMediatorClient:
    _staff_lock=threading.Lock()
    _staff_last_request=0.0
    def __init__(self,timeout=30,opener=None):
        self.timeout=int(timeout); self._open=opener or urlopen; self._cache={}; self._cast_cache={}

    def _json(self,url):
        request=Request(url,headers={"Accept":"application/json",
                                     "User-Agent":"Otaku-Prime/0.1.2 mal-staff"})
        try:
            with self._staff_lock:
                remaining=0.4-(time.monotonic()-self.__class__._staff_last_request)
                if remaining>0: time.sleep(remaining)
                try:
                    with self._open(request,timeout=min(self.timeout,15)) as response:
                        return json.loads(response.read().decode("utf-8"))
                finally:
                    self.__class__._staff_last_request=time.monotonic()
        except HTTPError as exc:
            raise MediatorPlacementError("MAL staff data returned HTTP {}".format(exc.code)) from exc
        except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
            raise MediatorPlacementError("MAL staff data failed: {}".format(exc)) from exc

    def cast(self,mal_id):
        key=str(mal_id)
        if key in self._cast_cache: return self._cast_cache[key]
        result=[]; seen=set()
        characters=self._json(JIKAN_API_URL+"/anime/"+key+"/characters")
        for row in (characters or {}).get("data") or []:
            character=row.get("character") or {}; character_name=character.get("name")
            for actor in row.get("voice_actors") or []:
                person=actor.get("person") or {}; person_name=person.get("name")
                marker=(person.get("mal_id"),character.get("mal_id"),actor.get("language"))
                if not person_name or not character_name or marker in seen: continue
                seen.add(marker); result.append({
                    "person":{"provider_id":person.get("mal_id"),"name":person_name,
                              "image_url":(((person.get("images") or {}).get("jpg") or {}).get("image_url"))},
                    "character":{"provider_id":character.get("mal_id"),"name":character_name,
                                 "trivia":character.get("about"),
                                 "image_url":(((character.get("images") or {}).get("jpg") or {}).get("image_url"))},
                    "credit_type":"voice_actor","language":actor.get("language") or "",
                    "source_provider":"mal","sort_order":len(result)})
        staff=self._json(JIKAN_API_URL+"/anime/"+key+"/staff")
        for row in (staff or {}).get("data") or []:
            person=row.get("person") or {}; name=person.get("name")
            for position in row.get("positions") or ["staff"]:
                if not name: continue
                result.append({
                    "person":{"provider_id":person.get("mal_id"),"name":name,
                              "image_url":(((person.get("images") or {}).get("jpg") or {}).get("image_url"))},
                    "character":{},"credit_type":position,"language":"",
                    "source_provider":"mal","sort_order":len(result)})
        self._cast_cache[key]=result
        return result

    def media(self,mal_id):
        key=str(mal_id)
        if key in self._cache: return self._cache[key]
        fields=("alternative_titles,start_date,end_date,synopsis,media_type,status,num_episodes,"
                "average_episode_duration,related_anime,genres,rating,nsfw")
        url=MAL_API_URL+"/anime/{}?".format(key)+urlencode({"fields":fields})
        request=Request(url,headers={"X-MAL-CLIENT-ID":MAL_CLIENT_ID,"Accept":"application/json",
                                     "User-Agent":"Otaku-Prime/0.1.2 mal-mediator"})
        try:
            with self._open(request,timeout=self.timeout) as response:
                payload=json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise MediatorPlacementError("MAL anime {} returned HTTP {}".format(key,exc.code)) from exc
        except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
            raise MediatorPlacementError("MAL anime {} failed: {}".format(key,exc)) from exc
        if not isinstance(payload,dict) or str(payload.get("id") or "")!=key:
            raise MediatorPlacementError("MAL returned a different or invalid anime identity")
        self._cache[key]=payload
        return payload


def _titles(media):
    alt=media.get("alternative_titles") or {}
    return {"english":alt.get("en") or media.get("title"),
            "romaji":media.get("title") or alt.get("en"),"native":alt.get("ja")}


def _prequels(media):
    values=[]
    for relation in media.get("related_anime") or []:
        if str(relation.get("relation_type") or "").lower()!="prequel": continue
        node=relation.get("node") or {}
        if node.get("id") not in (None,""): values.append(str(node["id"]))
    return list(dict.fromkeys(values))


def _find_root(client,target):
    path=[target]; current=target; seen={str(target["id"])}
    for _ in range(MAX_PREQUEL_DEPTH):
        ids=[value for value in _prequels(current) if value not in seen]
        if not ids: break
        candidates=[client.media(value) for value in ids]
        candidates.sort(key=lambda media:(str(media.get("start_date") or "9999-99-99"),int(media.get("id") or 0)))
        current=candidates[0]; seen.add(str(current["id"])); path.append(current)
    else:
        raise MediatorPlacementError("MAL prequel graph exceeded its safety limit")
    return current,list(reversed(path))


def _season_number(target,path):
    fmt=_format(target.get("media_type"))
    if fmt in SPECIAL_FORMATS: return 0,"mal_special_format"
    numbered=[node for node in path if _format(node.get("media_type")) in SEASON_FORMATS]
    target_id=str(target["id"])
    for index,node in enumerate(numbered,1):
        if str(node["id"])==target_id: return index,"mal_prequel_position"
    return max(1,len(numbered)+1),"mal_prequel_position"


class MALMediatorEndpoint:
    provider="mal"
    def __init__(self,client=None): self.client=client or MALMediatorClient()

    @staticmethod
    def available(item): return item.get("mal_id") not in (None,"")

    def cast(self,mal_id): return self.client.cast(mal_id)

    def resolve(self,item,client=None):
        value=item.get("mal_id")
        if value in (None,""): raise MediatorPlacementError("watchlist item has no MAL ID")
        target=self.client.media(value); root,path=_find_root(self.client,target)
        season_number,source=_season_number(target,path)
        try: count=int(target.get("num_episodes") or item.get("episode_count") or 0)
        except (TypeError,ValueError): count=0
        if count<=0: raise MediatorMetadataPending("MAL has no episode count for this anime")
        offset=0
        if season_number==0:
            target_id=str(target["id"])
            for node in path:
                if str(node["id"])==target_id: break
                if _format(node.get("media_type")) in SPECIAL_FORMATS:
                    try: offset+=max(0,int(node.get("num_episodes") or 0))
                    except (TypeError,ValueError): pass
        runtime=_runtime_minutes(target.get("average_episode_duration"))
        episodes=[]
        for source_number in range(1,count+1):
            episodes.append({"source_episode_number":source_number,"episode_number":offset+source_number,
                             "season_number":season_number,"simkl_id":None,"mal_id":None,
                             "title":None,"overview":None,"runtime_minutes":runtime,
                             "release_date":target.get("start_date") if source_number==1 else None})
        root_titles=_titles(root); target_titles=_titles(target); numbers=[row["episode_number"] for row in episodes]
        try: publish_year=int(str(root.get("start_date") or target.get("start_date") or "")[:4])
        except (TypeError,ValueError): publish_year=None
        mature=_mature(root,target)
        return {
            "provider_path":"mal","provider_id":str(value),
            "tv_show":{"name":root_titles["english"] or target_titles["english"],
                       "romaji_name":root_titles["romaji"] or target_titles["romaji"],
                       "simkl_id":None,"tvdb_id":None,"anilist_id":None,
                       "source_format":str(root.get("media_type") or target.get("media_type") or "").upper() or None,
                       "source":"mal_prequel_graph","publish_year":publish_year,
                       "overview":target.get("synopsis") or root.get("synopsis"),
                       "runtime_minutes":runtime or _runtime_minutes(root.get("average_episode_duration")),
                       "air_status":target.get("status") or root.get("status"),"cast":None,
                       "genres":_genres(root,target),"themes":[],
                       "age_rating":_rating(target) or _rating(root) or
                                    ("18+" if mature else None),"mature":mature},
            "season":{"number":season_number,"number_source":source,"name":target_titles["english"],
                      "media_type":_format(target.get("media_type")),"first_episode":numbers[0],"last_episode":numbers[-1]},
            "episodes":episodes,"relation_path":[str(node["id"]) for node in path],
        }
