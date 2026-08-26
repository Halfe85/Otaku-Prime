#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read Prime watchlist rows and test Simkl franchise/season placement.

This is intentionally read-only. It prints what the Alpha10 mediator would
create but does not insert TV-series, season, or episode catalogue rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0,ROOT)

from resources.lib.watchlist.simkl import PACKAGED_CLIENT_ID,SIMKL_API_URL


DEFAULT_PRIME_IDS=(
    "3b4d0caac45a4902aa13789f695cce6b",
    "fd5b317e46af47a58b628019bfc18986",
    "c21d438d8b1d4fcdb1a3d3047e4bd98a",
    "c38f76edb5254f5e9b9e8ee2fe605fb1",
    "d0bc6a50a2f24f829317b5054f754883",
)
PROVIDER_PRIORITY=("simkl","anilist","mal","kitsu")
MAX_PREQUEL_DEPTH=64


class SimklProbeClient:
    def __init__(self,client_id=None,timeout=30,opener=None):
        self.client_id=str(client_id or PACKAGED_CLIENT_ID).strip()
        self.timeout=int(timeout); self._open=opener or urlopen

    def _get(self,path,params=None):
        query={"client_id":self.client_id,"app-name":"otaku-prime","app-version":"0.1.2"}
        query.update(params or {})
        request=Request(SIMKL_API_URL+path+"?"+urlencode(query),headers={
            "Accept":"application/json","User-Agent":"Otaku-Prime/0.1.2 mediator-test"})
        try:
            with self._open(request,timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError("Simkl {} returned HTTP {}".format(path,exc.code)) from exc
        except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
            raise RuntimeError("Simkl {} failed: {}".format(path,exc)) from exc

    def anime(self,simkl_id):
        payload=self._get("/anime/{}".format(simkl_id))
        if not isinstance(payload,dict) or not (payload.get("ids") or {}).get("simkl"):
            raise RuntimeError("Simkl returned an invalid anime detail record")
        return payload

    def episodes(self,simkl_id):
        payload=self._get("/anime/episodes/{}".format(simkl_id))
        if payload is None: return []
        if not isinstance(payload,list):
            raise RuntimeError("Simkl returned an invalid anime episode list")
        return payload

    def tv(self,simkl_id):
        payload=self._get("/tv/{}".format(simkl_id))
        if not isinstance(payload,dict): raise RuntimeError("Simkl returned an invalid TV detail record")
        return payload

    def tv_franchise(self,anime_detail):
        anime_ids=anime_detail.get("ids") or {}
        tvdb_id=anime_ids.get("tvdb"); tmdb_id=anime_ids.get("tmdb")
        if tvdb_id in (None,""): return None
        payload=self._get("/search/id",{"tvdb":str(tvdb_id)})
        for row in payload or []:
            if row.get("type")!="tv": continue
            simkl_id=(row.get("ids") or {}).get("simkl")
            if simkl_id not in (None,""):
                detail=self.tv(simkl_id)
                return {
                    "name":detail.get("en_title") or detail.get("title") or row.get("title"),
                    "simkl_id":str(simkl_id),"tvdb_id":str(tvdb_id),"source":"simkl_tvdb_crossmap",
                }
        anime_rows=[row for row in (payload or []) if row.get("type")=="anime"
                    and str(row.get("anime_type") or "").lower()=="tv"]
        if anime_rows:
            anchor=sorted(anime_rows,key=lambda row:(
                int(row.get("year") or 9999),int((row.get("ids") or {}).get("simkl") or 0)))[0]
            simkl_id=(anchor.get("ids") or {}).get("simkl")
            if simkl_id not in (None,""):
                detail=self.anime(simkl_id)
                return {
                    "name":detail.get("en_title") or detail.get("title") or anchor.get("title"),
                    "simkl_id":str(simkl_id),"tvdb_id":str(tvdb_id),
                    "source":"simkl_tvdb_anime_group",
                }
        # `/search/id?tvdb=` can return only the anime catalogue record. Search
        # Simkl's TV catalogue and require the same TMDB TV identity before use.
        queries=[]
        if anime_ids.get("tvdbslug"): queries.append(str(anime_ids["tvdbslug"]).replace("-"," "))
        queries.extend(value for value in (anime_detail.get("en_title"),anime_detail.get("title")) if value)
        for query in queries:
            payload=self._get("/search/tv",{"q":query,"limit":50})
            for row in payload or []:
                ids=row.get("ids") or {}
                if tmdb_id in (None,"") or str(ids.get("tmdb") or "")!=str(tmdb_id): continue
                simkl_id=ids.get("simkl_id")
                if simkl_id in (None,""): continue
                detail=self.tv(simkl_id)
                return {
                    "name":detail.get("en_title") or detail.get("title") or row.get("title"),
                    "simkl_id":str(simkl_id),"tvdb_id":str(tvdb_id),"source":"simkl_tmdb_tv_match",
                }
        return None

    def exact_simkl_id(self,provider,provider_id):
        if provider=="simkl": return str(provider_id)
        payload=self._get("/search/id",{provider:str(provider_id)})
        matches=[row for row in (payload or []) if row.get("type")=="anime"]
        if not matches:
            raise RuntimeError("No Simkl anime matches {} {}".format(provider,provider_id))
        # Verify the full detail record instead of trusting title search.
        for match in matches:
            simkl_id=((match.get("ids") or {}).get("simkl"))
            if simkl_id in (None,""): continue
            detail=self.anime(simkl_id); ids=detail.get("ids") or {}
            if str(ids.get(provider) or "")==str(provider_id): return str(simkl_id)
        raise RuntimeError("Simkl search returned no exact {} identity".format(provider))


class ProviderPath:
    provider=None
    def resolve(self,item,client):
        provider_id=item.get(self.provider+"_id")
        if not provider_id: raise RuntimeError("watchlist item has no {} ID".format(self.provider))
        return client.exact_simkl_id(self.provider,provider_id)


class SimklPath(ProviderPath): provider="simkl"
class AniListPath(ProviderPath): provider="anilist"
class MALPath(ProviderPath): provider="mal"
class KitsuPath(ProviderPath): provider="kitsu"


PATHS={
    "simkl":SimklPath(),
    "anilist":AniListPath(),
    "mal":MALPath(),
    "kitsu":KitsuPath(),
}


def relation_rows(detail):
    relations=detail.get("relations") or []
    if isinstance(relations,dict):
        flattened=[]
        for relation_type,rows in relations.items():
            if isinstance(rows,dict): rows=[rows]
            for row in rows or []:
                value=dict(row); value.setdefault("relation_type",relation_type)
                flattened.append(value)
        return flattened
    return list(relations) if isinstance(relations,list) else []


def prequels(detail):
    rows=[]
    for relation in relation_rows(detail):
        relation_type=str(relation.get("relation_type") or "").strip().lower().replace("_"," ")
        if relation_type!="prequel": continue
        simkl_id=((relation.get("ids") or {}).get("simkl"))
        if simkl_id not in (None,""): rows.append(relation)
    return rows


def choose_prequel(rows):
    if not rows: return None
    # A direct TV predecessor is the strongest main-series edge. If Simkl has
    # several, use release year and stable Simkl ID only as deterministic ties.
    return sorted(rows,key=lambda row:(
        0 if relation_is_direct(row) else 1,
        0 if str(row.get("anime_type") or "").lower()=="tv" else 1,
        int(row.get("year") or 9999),
        int((row.get("ids") or {}).get("simkl") or 0),
    ))[0]


def relation_is_direct(row):
    value=row.get("is_direct")
    return value is True or str(value).strip().lower() in ("1","true","yes")


def find_root(client,target):
    path=[target]; seen={str((target.get("ids") or {})["simkl"])}
    franchise_tvdb=str((target.get("ids") or {}).get("tvdb") or "")
    current=target
    for _ in range(MAX_PREQUEL_DEPTH):
        compatible=[]
        for candidate in prequels(current):
            candidate_id=str((candidate.get("ids") or {})["simkl"])
            if candidate_id in seen: continue
            detail=client.anime(candidate_id)
            candidate_tvdb=str((detail.get("ids") or {}).get("tvdb") or "")
            if franchise_tvdb and candidate_tvdb!=franchise_tvdb: continue
            value=dict(candidate); value["_detail"]=detail; compatible.append(value)
        relation=choose_prequel(compatible)
        if not relation: break
        simkl_id=str((relation.get("ids") or {})["simkl"])
        if simkl_id in seen: raise RuntimeError("cycle detected in Simkl prequel graph")
        current=relation["_detail"]; seen.add(simkl_id); path.append(current)
    else:
        raise RuntimeError("Simkl prequel graph exceeded {} titles".format(MAX_PREQUEL_DEPTH))
    return current,list(reversed(path))


def proposed_season(detail,path):
    mapped=[int(value) for value in detail.get("mapped_tvdb_seasons") or []]
    if len(mapped)==1: return mapped[0],"mapped_tvdb_seasons"
    if mapped: return mapped,"mapped_tvdb_seasons_multiple"
    anime_type=str(detail.get("anime_type") or "").lower()
    if anime_type in ("movie","ova","ona","special","music video"):
        return 0,"special_format"
    main_tv=[node for node in path if str(node.get("anime_type") or "").lower()=="tv"]
    return max(1,len(main_tv)),"direct_prequel_position"


def episode_projection(rows):
    projected=[]
    for index,row in enumerate(rows,1):
        if str(row.get("type") or "episode").lower()!="episode": continue
        number=row.get("episode") or row.get("number") or index
        ids=row.get("ids") or {}
        tvdb=row.get("tvdb") or {}
        projected.append({
            "episode":int(number),
            "simkl_id":str(ids.get("simkl_id")) if ids.get("simkl_id") not in (None,"") else None,
            "mal_id":str(ids.get("mal")) if ids.get("mal") not in (None,"") else None,
            "tvdb_season":tvdb.get("season"),
            "tvdb_episode":tvdb.get("episode"),
            "release_date":row.get("date") or row.get("first_aired"),
        })
    return projected


def special_projection(rows):
    projected=[]
    for row in rows:
        if str(row.get("type") or "").lower()!="special": continue
        ids=row.get("ids") or {}; tvdb=row.get("tvdb") or {}
        projected.append({
            "title":row.get("title"),"episode":row.get("episode"),"season":row.get("season"),
            "simkl_id":str(ids.get("simkl_id")) if ids.get("simkl_id") not in (None,"") else None,
            "tvdb_season":tvdb.get("season"),"tvdb_episode":tvdb.get("episode"),
            "release_date":row.get("date"),
        })
    return projected


def franchise_coordinates(episodes):
    groups={}; unmapped=[]
    for row in episodes:
        season=row.get("tvdb_season"); episode=row.get("tvdb_episode")
        if season is None or episode is None:
            unmapped.append(row["episode"]); continue
        groups.setdefault(int(season),[]).append(int(episode))
    ranges=[]; gaps=[]
    for season,numbers in sorted(groups.items()):
        ordered=sorted(set(numbers)); start=previous=ordered[0]
        for number in ordered[1:]:
            if number!=previous+1:
                ranges.append({"season":season,"first":start,"last":previous})
                gaps.extend(range(previous+1,number)); start=number
            previous=number
        ranges.append({"season":season,"first":start,"last":previous})
    labels=[]
    for value in ranges:
        first="S{:02d}E{:02d}".format(value["season"],value["first"])
        last="S{:02d}E{:02d}".format(value["season"],value["last"])
        labels.append(first if first==last else first+"–"+last)
    return {
        "summary":", ".join(labels) if labels else "unmapped",
        "ranges":ranges,"unmapped_source_episodes":unmapped,"coordinate_gaps":gaps,
        "complete":not unmapped and bool(episodes),
    }


def load_items(db_path,prime_ids):
    uri="file:{}?mode=ro".format(os.path.abspath(db_path))
    db=sqlite3.connect(uri,uri=True); db.row_factory=sqlite3.Row
    try:
        placeholders=",".join("?" for _ in prime_ids)
        rows={row["local_id"]:dict(row) for row in db.execute(
            "SELECT * FROM watchlist_items WHERE local_id IN ({})".format(placeholders),prime_ids)}
    finally: db.close()
    missing=[value for value in prime_ids if value not in rows]
    if missing: raise RuntimeError("Prime IDs not found: "+", ".join(missing))
    return [rows[value] for value in prime_ids]


def resolve_item(item,client):
    provider=next((name for name in PROVIDER_PRIORITY if item.get(name+"_id")),None)
    if not provider: raise RuntimeError("Prime item has no supported provider ID")
    simkl_id=PATHS[provider].resolve(item,client)
    target=client.anime(simkl_id)
    root,path=find_root(client,target)
    franchise=client.tv_franchise(target) or {
        "name":root.get("en_title") or root.get("title"),
        "simkl_id":str((root.get("ids") or {}).get("simkl")),
        "tvdb_id":str((root.get("ids") or {}).get("tvdb") or "") or None,
        "source":"relation_fallback",
    }
    season_number,season_source=proposed_season(target,path)
    raw_episodes=client.episodes(simkl_id)
    episodes=episode_projection(raw_episodes); specials=special_projection(raw_episodes)
    coordinates=franchise_coordinates(episodes)
    return {
        "prime_id":item["local_id"],
        "provider_path":provider,
        "provider_id":item.get(provider+"_id"),
        "tv_show":{
            "name":franchise["name"],
            "root_simkl_id":franchise["simkl_id"],
            "tvdb_id":franchise["tvdb_id"],
            "source":franchise["source"],
        },
        "season":{
            "number":season_number,
            "number_source":season_source,
            "name":target.get("en_title") or target.get("title"),
            "anime_type":target.get("anime_type"),
            "ids":{name:str((target.get("ids") or {}).get(name))
                   for name in PROVIDER_PRIORITY if (target.get("ids") or {}).get(name) not in (None,"")},
            "episode_count":len(episodes),
            "special_count":len(specials),
            "franchise_episode_placement":coordinates,
            "simkl_catalog_ids":target.get("ids") or {},
        },
        "relation_path":[{
            "name":node.get("en_title") or node.get("title"),
            "simkl_id":str((node.get("ids") or {}).get("simkl")),
            "anime_type":node.get("anime_type"),
        } for node in path],
        "episodes":episodes,
        "specials":specials,
    }


def main():
    default_db=os.path.expanduser("~/.kodi/userdata/addon_data/plugin.video.otaku.prime/users.sqlite")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prime_ids",nargs="*",default=list(DEFAULT_PRIME_IDS))
    parser.add_argument("--database",default=default_db)
    parser.add_argument("--output",help="also write the complete JSON result to this file")
    args=parser.parse_args()
    results=[]; client=SimklProbeClient()
    for item in load_items(args.database,args.prime_ids):
        result=resolve_item(item,client); results.append(result)
        print("\n{}".format(result["prime_id"]))
        print("  path:       {} {}".format(result["provider_path"],result["provider_id"]))
        print("  TV show:    {} (Simkl {}, TVDB {}, {})".format(
            result["tv_show"]["name"],result["tv_show"]["root_simkl_id"],
            result["tv_show"]["tvdb_id"],result["tv_show"]["source"]))
        print("  season:     {} ({}, {})".format(result["season"]["number"],result["season"]["number_source"],result["season"]["name"]))
        print("  episodes:   {}".format(result["season"]["episode_count"]))
        print("  placement:  {}{}".format(
            result["season"]["franchise_episode_placement"]["summary"],
            " (complete)" if result["season"]["franchise_episode_placement"]["complete"] else " (incomplete)"))
        print("  specials:   {} (classified separately)".format(result["season"]["special_count"]))
        print("  same-TVDB prequel path: "+" -> ".join(node["name"] for node in result["relation_path"]))
    if args.output:
        with open(args.output,"w",encoding="utf-8") as handle:
            json.dump(results,handle,ensure_ascii=False,indent=2)
            handle.write("\n")
    return 0


if __name__=="__main__": raise SystemExit(main())
