# -*- coding: utf-8 -*-
"""Exact-ID Simkl metadata endpoint for the TV-show mediator."""
from __future__ import annotations

import re
from urllib.parse import quote

from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
    SPECIAL_MEDIA_TYPES,
    SimklMediatorClient,
    _cast_entries,
    _episodes,
    _find_root,
    _int_or_none,
    _overview,
    _remote_title,
    _season_number,
)

_LOCATOR=re.compile(r"^S(\d{2,3})E(\d{2,4})(?:-E?(\d{2,4}))?$")


def _terms(*payloads):
    result={"genres":[],"themes":[]}; seen={"genres":set(),"themes":set()}
    for payload in payloads:
        for name in result:
            for row in (payload or {}).get(name) or []:
                value=row.get("name") if isinstance(row,dict) else row
                text=str(value or "").strip(); key=text.casefold()
                if text and key not in seen[name]:
                    result[name].append(text); seen[name].add(key)
    return result


def _age_rating(*payloads):
    for payload in payloads:
        for key in ("certification","age_rating","content_rating"):
            value=(payload or {}).get(key)
            if value not in (None,""): return str(value)
    return None


def _mature(age_rating,*payloads):
    text=str(age_rating or "").upper().replace(" ","")
    return text in ("18+","R18","R18+","NC-17","RX") or any(
        bool((payload or {}).get("is_adult") or (payload or {}).get("adult"))
        for payload in payloads)


class SimklMediatorEndpoint:
    provider="simkl"

    def __init__(self,client=None):
        self.client=client

    @staticmethod
    def available(item):
        return item.get("simkl_id") not in (None,"") or (
            item.get("simkl_reference_id") not in (None,"") and item.get("special_locator") not in (None,""))

    def cast(self,simkl_id):
        if self.client is None or simkl_id in (None,""): return None
        return _cast_entries(self.client.anime(simkl_id))

    def poster(self,simkl_id):
        if self.client is None or simkl_id in (None,""): return None
        value=(self.client.anime(simkl_id) or {}).get("poster")
        if isinstance(value,dict):
            value=(value.get("url") or value.get("large") or value.get("medium"))
        text=str(value or "").strip()
        if text.startswith(("https://","http://")): return text
        if not text: return None
        return "https://simkl.in/posters/{}_m.jpg".format(quote(text,safe=""))

    def classification(self,simkl_id):
        if self.client is None or simkl_id in (None,""): return {}
        payload=self.client.anime(simkl_id) or {}; terms=_terms(payload)
        age_rating=_age_rating(payload)
        return {"genres":terms["genres"],"themes":terms["themes"],
                "age_rating":age_rating,"mature":_mature(age_rating,payload)}

    @staticmethod
    def _franchise(client,target,root):
        franchise=client.tv_franchise(target,root_detail=root) or {
            "name":_remote_title(root),
            "simkl_id":str((root.get("ids") or {}).get("simkl")),
            "tvdb_id":None,
            "source":"relation_fallback_unmapped",
        }
        root_ids=root.get("ids") or {}
        terms=_terms(root,target); age_rating=_age_rating(target,root)
        franchise.update({
            "romaji_name":_remote_title({"title":root.get("title") or target.get("title")}),
            "anilist_id":str(root_ids.get("anilist")) if root_ids.get("anilist") not in (None,"") else None,
            "mal_id":str(root_ids.get("mal")) if root_ids.get("mal") not in (None,"") else None,
            "kitsu_id":str(root_ids.get("kitsu")) if root_ids.get("kitsu") not in (None,"") else None,
            "source_format":str(root.get("anime_type") or target.get("anime_type") or "").upper() or None,
            "publish_year":_int_or_none(root.get("year") or target.get("year")),
            "overview":_overview(root) or _overview(target),
            "runtime_minutes":_int_or_none(target.get("runtime") or root.get("runtime") or
                                            target.get("runtime_minutes") or root.get("runtime_minutes")),
            "air_status":target.get("status") or target.get("release_status") or
                         root.get("status") or root.get("release_status"),
            "cast":_cast_entries(target) if _cast_entries(target) is not None else _cast_entries(root),
            "cast_source":"simkl",
            "genres":terms["genres"],"themes":terms["themes"],
            "age_rating":age_rating,"mature":_mature(age_rating,root,target),
        })
        return franchise

    def _exact(self,item,client):
        simkl_id=str(item["simkl_id"])
        target=client.anime(simkl_id)
        returned=str((target.get("ids") or {}).get("simkl") or "")
        if returned!=simkl_id:
            raise MediatorPlacementError("Simkl returned a different identity for {}".format(simkl_id))
        root,path=_find_root(client,target)
        franchise=self._franchise(client,target,root)
        target_type=str(target.get("anime_type") or "").lower()
        library_type=("movie" if target_type=="movie" and
                      not franchise.get("tvdb_id") else "series")
        candidates=_episodes(client.episodes(simkl_id),target_type in SPECIAL_MEDIA_TYPES)
        mapped=sorted({int(value) for value in target.get("mapped_tvdb_seasons") or []})
        coordinate_seasons=sorted({int(row["season_number"]) for row in candidates
                                   if row.get("season_number") is not None})
        if len(mapped)<=1 and len(coordinate_seasons)>1:
            mapped=coordinate_seasons
        if len(mapped)>1:
            components=[]
            for season_number in mapped:
                episodes=sorted(
                    (row for row in candidates if row.get("season_number")==season_number),
                    key=lambda row:row.get("episode_number") or 0)
                if not episodes:
                    raise MediatorMetadataPending(
                        "Simkl returned no episodes for mapped season {}".format(season_number))
                unmapped=[row["source_episode_number"] for row in episodes
                          if row.get("episode_number") is None]
                if unmapped:
                    raise MediatorPlacementError(
                        "Simkl episodes lack TVDB coordinates: {}".format(unmapped))
                numbers=[row["episode_number"] for row in episodes]
                if len(numbers)>1 and numbers!=list(range(numbers[0],numbers[-1]+1)):
                    raise MediatorPlacementError(
                        "Simkl season {} coordinates contain gaps".format(season_number))
                components.append({
                    "season":{"number":season_number,
                              "number_source":"mapped_tvdb_seasons",
                              "name":"{} season {}".format(
                                  _remote_title(target),season_number),
                              "media_type":target_type,
                              "first_episode":numbers[0],"last_episode":numbers[-1]},
                    "episodes":episodes,
                })
            return {
                "provider_path":"simkl","provider_id":simkl_id,
                "provider_reference_id":None,"library_type":library_type,
                "tv_show":franchise,"season":components[0]["season"],
                "episodes":components[0]["episodes"],"seasons":components,
                "relation_path":[str((node.get("ids") or {}).get("simkl")) for node in path],
            }
        season_number,number_source=_season_number(target,path)
        episodes=[row for row in candidates if row.get("season_number")==season_number]
        if not episodes:
            raise MediatorMetadataPending(
                "Simkl returned no episodes for season {}".format(season_number))
        unmapped=[row["source_episode_number"] for row in episodes if row.get("episode_number") is None]
        if unmapped:
            raise MediatorPlacementError("Simkl episodes lack TVDB coordinates: {}".format(unmapped))
        numbers=sorted(row["episode_number"] for row in episodes)
        if len(numbers)>1 and numbers!=list(range(numbers[0],numbers[-1]+1)):
            raise MediatorPlacementError("Simkl franchise episode coordinates contain gaps")
        return {
            "provider_path":"simkl","provider_id":simkl_id,"provider_reference_id":None,
            "library_type":library_type,
            "tv_show":franchise,
            "season":{"number":season_number,"number_source":number_source,
                      "name":_remote_title(target),
                      "media_type":target_type,"first_episode":numbers[0],"last_episode":numbers[-1]},
            "episodes":episodes,
            "relation_path":[str((node.get("ids") or {}).get("simkl")) for node in path],
        }

    def _referenced_special(self,item,client):
        reference=str(item.get("simkl_reference_id") or "")
        match=_LOCATOR.match(str(item.get("special_locator") or "").upper())
        if not reference or not match:
            raise MediatorPlacementError("Simkl special reference is incomplete")
        season_number=int(match.group(1)); first_episode=int(match.group(2))
        last_episode=int(match.group(3) or first_episode)
        if last_episode<first_episode:
            raise MediatorPlacementError("Simkl special reference range is reversed")
        target=client.anime(reference)
        root,path=_find_root(client,target)
        franchise=self._franchise(client,target,root)
        candidates=_episodes(client.episodes(reference),True)
        selected=[row for row in candidates
                  if row.get("season_number")==season_number and
                  first_episode<=row.get("episode_number",-1)<=last_episode]
        selected.sort(key=lambda row:row["episode_number"])
        expected=list(range(first_episode,last_episode+1))
        if [row["episode_number"] for row in selected]!=expected:
            raise MediatorPlacementError(
                "Simkl reference {} has no {}".format(reference,item.get("special_locator")))
        return {
            "provider_path":"simkl","provider_id":None,"provider_reference_id":reference,
            "library_type":"series",
            "tv_show":franchise,
            "season":{"number":season_number,"number_source":"watchlist_special_locator",
                      "name":item.get("english_name") or item.get("romaji_name"),
                      "media_type":str(item.get("media_format") or "SPECIAL").lower(),
                      "first_episode":first_episode,"last_episode":last_episode},
            "episodes":selected,
            "relation_path":[str((node.get("ids") or {}).get("simkl")) for node in path],
            "special_locator":item.get("special_locator"),
        }

    def resolve(self,item,client=None):
        client=client or self.client or SimklMediatorClient()
        if item.get("simkl_id") not in (None,""):
            return self._exact(item,client)
        if item.get("simkl_reference_id") not in (None,"") and item.get("special_locator") not in (None,""):
            return self._referenced_special(item,client)
        raise MediatorPlacementError("watchlist item has no Simkl identity or special reference")
