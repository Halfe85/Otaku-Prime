# -*- coding: utf-8 -*-
"""Resolve AniList main-series relations without importing related media."""
from __future__ import annotations
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import time
from resources.lib.services.anilist_rate_limit import ANILIST_RATE_LIMITER

MAIN_RELATIONS=("PREQUEL","SEQUEL")
SERIES_FORMATS=("TV","TV_SHORT")
SPECIAL_FORMATS=("MOVIE","ONA","OVA","SPECIAL","MUSIC")

class AniListRelationClient:
    API_URL="https://graphql.anilist.co"
    def __init__(self,timeout=20,opener=None,batch_size=50):
        self.timeout=timeout; self._rate_limited=opener is None
        self._open=opener or urlopen; self.batch_size=max(1,min(50,int(batch_size)))
    def fetch(self,anilist_id):
        media=self.fetch_many([anilist_id])
        if not media: raise RuntimeError("AniList relation media was not found")
        return media[0]
    def fetch_many(self,anilist_ids):
        results=[]
        values=[int(value) for value in dict.fromkeys(anilist_ids)]
        for offset in range(0,len(values),self.batch_size):
            results.extend(self._fetch_batch(values[offset:offset+self.batch_size]))
        return results
    def _fetch_batch(self,anilist_ids):
        query="""query($ids:[Int]){Page(page:1,perPage:50){media(id_in:$ids,type:ANIME){id format
          title{english romaji} startDate{year month day}
          relations{edges{relationType node{id format title{english romaji}
            startDate{year month day}}}}}}}"""
        body=json.dumps({"query":query,"variables":{"ids":anilist_ids}}).encode("utf-8")
        request=Request(self.API_URL,data=body,method="POST",headers={
          "Content-Type":"application/json","Accept":"application/json",
          "User-Agent":"Otaku-Prime/0.1.2"})
        for attempt in range(2):
            try:
                if self._rate_limited: ANILIST_RATE_LIMITER.wait()
                with self._open(request,timeout=self.timeout) as response:
                    payload=json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code != 429 or attempt: raise
                time.sleep(ANILIST_RATE_LIMITER.retry_delay(exc))
        media=((payload.get("data") or {}).get("Page") or {}).get("media") or []
        if payload.get("errors"):
            raise RuntimeError("AniList relation request failed")
        return media

class AniListFranchiseResolverService:
    """Resolve and promote only entries present in the staged user snapshot."""
    def __init__(self,media_store,client=None,max_nodes=100):
        self.media_store=media_store; self.client=client or AniListRelationClient()
        self.max_nodes=max(1,int(max_nodes)); self._cache={}

    def run_once(self):
        staged=self.media_store.list_anilist_staging(); active=[]; failed=[]
        try: self._load_prequel_graph([entry["anilist_id"] for entry in staged])
        except Exception as exc:
            return {"resolved":0,"failed":[{"anilist_id":None,"error":str(exc)}]}
        for entry in staged:
            try:
                resolution=self._resolve(entry["anilist_id"])
                active.append(self.media_store.promote_anilist_season(entry,resolution))
            except Exception as exc:
                failed.append({"anilist_id":entry["anilist_id"],"error":str(exc)})
        # Membership removal is safe only after every staged entry was resolved.
        if not failed:
            self.media_store.replace_provider_season_memberships("anilist",active)
        return {"resolved":len(active),"failed":failed}

    def _media(self,media_id):
        key=str(media_id)
        if key not in self._cache: self._cache[key]=self.client.fetch(key)
        return self._cache[key]

    def _prefetch(self,media_ids):
        missing=[str(value) for value in dict.fromkeys(media_ids)
                 if str(value) not in self._cache]
        if not missing: return
        if hasattr(self.client,"fetch_many"):
            media=self.client.fetch_many(missing)
        else:
            media=[self.client.fetch(value) for value in missing]
        for item in media:
            if item and item.get("id") is not None: self._cache[str(item["id"])]=item

    def _load_prequel_graph(self,media_ids):
        frontier={str(value) for value in media_ids}; visited=set()
        while frontier and len(visited)<self.max_nodes*max(1,len(media_ids)):
            self._prefetch(frontier); visited.update(frontier)
            following=set()
            for media_id in frontier:
                media=self._cache.get(media_id)
                if not media: continue
                following.update(str(node["id"]) for node in self._main_neighbors(media,"PREQUEL"))
            frontier=following-visited

    @staticmethod
    def _date_key(media):
        date=media.get("startDate") or {}
        return (date.get("year") or 9999,date.get("month") or 99,
                date.get("day") or 99,int(media["id"]))

    def _main_neighbors(self,media,relation):
        nodes=[]
        for edge in (media.get("relations") or {}).get("edges") or []:
            node=edge.get("node") or {}
            if (edge.get("relationType")==relation and node.get("id")
                    and node.get("format") in SERIES_FORMATS):
                nodes.append(node)
        return sorted(nodes,key=self._date_key)

    def _resolve(self,media_id):
        current=self._media(media_id); seen=set()
        media_format=current.get("format")
        relation_type=None
        for edge in (current.get("relations") or {}).get("edges") or []:
            if edge.get("relationType") in ("SPIN_OFF","SIDE_STORY","PARENT"):
                relation_type=edge.get("relationType"); break
        # Follow the oldest prequel at ambiguous forks. Side stories are excluded.
        while str(current["id"]) not in seen and len(seen)<self.max_nodes:
            seen.add(str(current["id"])); previous=self._main_neighbors(current,"PREQUEL")
            if not previous: break
            next_id=str(previous[0]["id"])
            if next_id not in self._cache:
                raise RuntimeError("AniList prequel graph is incomplete")
            current=self._cache[next_id]
        root=current
        season_number=len(seen)
        titles=root.get("title") or {}; start=(self._media(media_id).get("startDate") or {})
        category=self._category(media_format,relation_type)
        return {"root_id":root["id"],"season_number":season_number,
          "franchise_english_name":titles.get("english"),
          "franchise_romaji_name":titles.get("romaji"),"start_year":start.get("year"),
          "media_format":media_format,"relation_type":relation_type,
          "media_category":category}

    @staticmethod
    def _category(media_format,relation_type):
        if relation_type == "SPIN_OFF": return "spin_off"
        return {"MOVIE":"movie","ONA":"ona","OVA":"ova",
                "SPECIAL":"special","MUSIC":"special"}.get(media_format,"tv")
