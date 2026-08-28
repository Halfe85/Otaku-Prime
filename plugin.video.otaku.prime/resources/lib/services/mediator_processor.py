# -*- coding: utf-8 -*-
"""Provider-independent metadata processor for Prime library mediation."""
from __future__ import annotations

from copy import deepcopy

from resources.lib.logging_config import get_logger
from resources.lib.services.mediator_endpoint_anilist import AniListMediatorEndpoint
from resources.lib.services.mediator_endpoint_kitsu import KitsuMediatorEndpoint
from resources.lib.services.mediator_endpoint_mal import MALMediatorEndpoint
from resources.lib.services.mediator_endpoint_simkl import SimklMediatorEndpoint
from resources.lib.services.mediator_helper_simkl import MediatorPlacementError

LOGGER=get_logger(__name__)


class MediatorProviderConflict(MediatorPlacementError):
    pass


def _episode_signature(placement):
    season=(placement.get("season") or {}).get("number")
    rows=placement.get("episodes") or []
    numbers=[row.get("episode_number") for row in rows]
    return season,numbers


def _merge_dict(primary,secondary):
    result=deepcopy(primary or {})
    for key,value in (secondary or {}).items():
        if result.get(key) in (None,"",[]): result[key]=deepcopy(value)
    return result


def _merge_placements(anilist,mal):
    if _episode_signature(anilist)!=_episode_signature(mal):
        raise MediatorProviderConflict(
            "AniList and MAL disagree on season/episode placement: {} != {}".format(
                _episode_signature(anilist),_episode_signature(mal)))
    result=deepcopy(anilist)
    result["provider_path"]="anilist+mal"
    result["provider_id"]={"anilist":anilist.get("provider_id"),"mal":mal.get("provider_id")}
    result["tv_show"]=_merge_dict(anilist.get("tv_show"),mal.get("tv_show"))
    result["season"]=_merge_dict(anilist.get("season"),mal.get("season"))
    mal_rows=mal.get("episodes") or []
    merged=[]
    for index,row in enumerate(result.get("episodes") or []):
        other=mal_rows[index] if index<len(mal_rows) else {}
        merged.append(_merge_dict(row,other))
    result["episodes"]=merged
    result["provider_consensus"]=["anilist","mal"]
    return result


class MediatorProcessor:
    """Resolve one Watchlist row without ever changing provider identities."""
    def __init__(self,endpoints=None,simkl_client=None):
        self.endpoints=endpoints or {
            "simkl":SimklMediatorEndpoint(client=simkl_client),
            "anilist":AniListMediatorEndpoint(),
            "mal":MALMediatorEndpoint(),
            "kitsu":KitsuMediatorEndpoint(),
        }

    @staticmethod
    def _available(endpoint,item):
        checker=getattr(endpoint,"available",None)
        return bool(checker(item)) if checker else True

    def _try(self,name,item,attempts):
        endpoint=self.endpoints[name]
        if not self._available(endpoint,item):
            attempts.append({"provider":name,"skipped":True,"error":"provider ID unavailable"})
            return None
        try:
            return endpoint.resolve(item)
        except Exception as exc:
            attempts.append({"provider":name,"skipped":False,"error":str(exc)})
            LOGGER.info("Mediator %s endpoint could not resolve Prime item %s: %s",
                        name,item.get("local_id"),exc)
            return None

    def resolve(self,item):
        attempts=[]

        simkl=self._try("simkl",item,attempts)
        if simkl:
            simkl["provider_attempts"]=attempts
            return simkl

        anilist=self._try("anilist",item,attempts)
        mal=self._try("mal",item,attempts)
        if anilist and mal:
            try:
                combined=_merge_placements(anilist,mal)
                combined["provider_attempts"]=attempts
                return combined
            except MediatorProviderConflict as exc:
                attempts.append({"provider":"anilist+mal","skipped":False,"error":str(exc)})
                LOGGER.warning("AniList/MAL structural disagreement for Prime item %s: %s",
                               item.get("local_id"),exc)
        elif anilist:
            anilist["provider_attempts"]=attempts
            return anilist
        elif mal:
            mal["provider_attempts"]=attempts
            return mal

        kitsu=self._try("kitsu",item,attempts)
        if kitsu:
            kitsu["provider_attempts"]=attempts
            return kitsu

        usable=[row for row in attempts if not row.get("skipped")]
        if not usable:
            raise MediatorPlacementError("Prime item has no supported provider ID")
        raise MediatorPlacementError("; ".join(
            "{}: {}".format(row["provider"],row["error"]) for row in usable))
