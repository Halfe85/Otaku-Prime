# -*- coding: utf-8 -*-
"""Resolve AniList main-series relations without importing related media."""
from __future__ import annotations
import json
from urllib.request import Request,urlopen

MAIN_RELATIONS=("PREQUEL","SEQUEL")

class AniListRelationClient:
    API_URL="https://graphql.anilist.co"
    def __init__(self,timeout=20,opener=None): self.timeout=timeout; self._open=opener or urlopen
    def fetch(self,anilist_id):
        query="""query($id:Int!){Media(id:$id,type:ANIME){id format
          title{english romaji} startDate{year month day}
          relations{edges{relationType node{id format title{english romaji}
            startDate{year month day}}}}}}"""
        body=json.dumps({"query":query,"variables":{"id":int(anilist_id)}}).encode("utf-8")
        request=Request(self.API_URL,data=body,method="POST",headers={
          "Content-Type":"application/json","Accept":"application/json",
          "User-Agent":"Otaku-Prime/0.1.2"})
        with self._open(request,timeout=self.timeout) as response:
            payload=json.loads(response.read().decode("utf-8"))
        if payload.get("errors") or not payload.get("data",{}).get("Media"):
            raise RuntimeError("AniList relation request failed")
        return payload["data"]["Media"]

class AniListFranchiseResolverService:
    """Resolve and promote only entries present in the staged user snapshot."""
    def __init__(self,media_store,client=None,max_nodes=100):
        self.media_store=media_store; self.client=client or AniListRelationClient()
        self.max_nodes=max(1,int(max_nodes)); self._cache={}

    def run_once(self):
        staged=self.media_store.list_anilist_staging(); active=[]; failed=[]
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

    @staticmethod
    def _date_key(media):
        date=media.get("startDate") or {}
        return (date.get("year") or 9999,date.get("month") or 99,
                date.get("day") or 99,int(media["id"]))

    def _main_neighbors(self,media,relation):
        nodes=[]
        for edge in (media.get("relations") or {}).get("edges") or []:
            node=edge.get("node") or {}
            if edge.get("relationType")==relation and node.get("id"):
                nodes.append(node)
        return sorted(nodes,key=self._date_key)

    def _resolve(self,media_id):
        current=self._media(media_id); seen=set()
        # Follow the oldest prequel at ambiguous forks. Side stories are excluded.
        while str(current["id"]) not in seen and len(seen)<self.max_nodes:
            seen.add(str(current["id"])); previous=self._main_neighbors(current,"PREQUEL")
            if not previous: break
            current=self._media(previous[0]["id"])
        root=current
        chain=[]; current=root; seen=set()
        while str(current["id"]) not in seen and len(seen)<self.max_nodes:
            seen.add(str(current["id"])); chain.append(str(current["id"]))
            following=self._main_neighbors(current,"SEQUEL")
            if not following: break
            current=self._media(following[0]["id"])
        target=str(media_id)
        # If AniList's relation graph is asymmetric, retain a safe standalone season.
        season_number=chain.index(target)+1 if target in chain else 1
        titles=root.get("title") or {}; start=(self._media(media_id).get("startDate") or {})
        return {"root_id":root["id"],"season_number":season_number,
          "franchise_english_name":titles.get("english"),
          "franchise_romaji_name":titles.get("romaji"),"start_year":start.get("year")}
