# -*- coding: utf-8 -*-
"""Exact-ID Simkl metadata endpoint for the TV-show mediator."""
from __future__ import annotations

import re

from resources.lib.services.mediator_helper_simkl import (
    MediatorPlacementError,
    SPECIAL_MEDIA_TYPES,
    SimklMediatorClient,
    _cast_entries,
    _episodes,
    _find_root,
    _int_or_none,
    _overview,
    _season_number,
)

_LOCATOR=re.compile(r"^S(\d{2,3})E(\d{2,4})$")


class SimklMediatorEndpoint:
    provider="simkl"

    def __init__(self,client=None):
        self.client=client

    @staticmethod
    def available(item):
        return item.get("simkl_id") not in (None,"") or (
            item.get("simkl_reference_id") not in (None,"") and item.get("special_locator") not in (None,""))

    @staticmethod
    def _franchise(client,target,root):
        franchise=client.tv_franchise(target,root_detail=root) or {
            "name":root.get("en_title") or root.get("title"),
            "simkl_id":str((root.get("ids") or {}).get("simkl")),
            "tvdb_id":None,
            "source":"relation_fallback_unmapped",
        }
        root_ids=root.get("ids") or {}
        franchise.update({
            "romaji_name":root.get("title") or target.get("title"),
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
        return franchise

    def _exact(self,item,client):
        simkl_id=str(item["simkl_id"])
        target=client.anime(simkl_id)
        returned=str((target.get("ids") or {}).get("simkl") or "")
        if returned!=simkl_id:
            raise MediatorPlacementError("Simkl returned a different identity for {}".format(simkl_id))
        root,path=_find_root(client,target)
        franchise=self._franchise(client,target,root)
        season_number,number_source=_season_number(target,path)
        target_type=str(target.get("anime_type") or "").lower()
        candidates=_episodes(client.episodes(simkl_id),target_type in SPECIAL_MEDIA_TYPES)
        episodes=[row for row in candidates if row.get("season_number")==season_number]
        if not episodes:
            raise MediatorPlacementError("Simkl returned no episodes for season {}".format(season_number))
        unmapped=[row["source_episode_number"] for row in episodes if row.get("episode_number") is None]
        if unmapped:
            raise MediatorPlacementError("Simkl episodes lack TVDB coordinates: {}".format(unmapped))
        numbers=sorted(row["episode_number"] for row in episodes)
        if len(numbers)>1 and numbers!=list(range(numbers[0],numbers[-1]+1)):
            raise MediatorPlacementError("Simkl franchise episode coordinates contain gaps")
        return {
            "provider_path":"simkl","provider_id":simkl_id,"provider_reference_id":None,
            "tv_show":franchise,
            "season":{"number":season_number,"number_source":number_source,
                      "name":target.get("en_title") or target.get("title"),
                      "media_type":target_type,"first_episode":numbers[0],"last_episode":numbers[-1]},
            "episodes":episodes,
            "relation_path":[str((node.get("ids") or {}).get("simkl")) for node in path],
        }

    def _referenced_special(self,item,client):
        reference=str(item.get("simkl_reference_id") or "")
        match=_LOCATOR.match(str(item.get("special_locator") or "").upper())
        if not reference or not match:
            raise MediatorPlacementError("Simkl special reference is incomplete")
        season_number=int(match.group(1)); episode_number=int(match.group(2))
        target=client.anime(reference)
        root,path=_find_root(client,target)
        franchise=self._franchise(client,target,root)
        candidates=_episodes(client.episodes(reference),True)
        selected=next((row for row in candidates
                       if row.get("season_number")==season_number and row.get("episode_number")==episode_number),None)
        if not selected:
            raise MediatorPlacementError(
                "Simkl reference {} has no {}".format(reference,item.get("special_locator")))
        return {
            "provider_path":"simkl","provider_id":None,"provider_reference_id":reference,
            "tv_show":franchise,
            "season":{"number":season_number,"number_source":"watchlist_special_locator",
                      "name":item.get("english_name") or item.get("romaji_name"),
                      "media_type":str(item.get("media_format") or "SPECIAL").lower(),
                      "first_episode":episode_number,"last_episode":episode_number},
            "episodes":[selected],
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
