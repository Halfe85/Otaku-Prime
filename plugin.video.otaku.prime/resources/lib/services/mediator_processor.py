# -*- coding: utf-8 -*-
"""Provider-independent metadata processor for Prime library mediation."""
from __future__ import annotations

from copy import deepcopy

from resources.lib.logging_config import get_logger
from resources.lib.services.mediator_endpoint_anilist import AniListMediatorEndpoint
from resources.lib.services.mediator_endpoint_kitsu import KitsuMediatorEndpoint
from resources.lib.services.mediator_endpoint_mal import MALMediatorEndpoint
from resources.lib.services.mediator_endpoint_simkl import SimklMediatorEndpoint
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
)

LOGGER=get_logger(__name__)


class MediatorProviderConflict(MediatorPlacementError):
    pass


def _episode_signature(placement):
    season=(placement.get("season") or {}).get("number")
    rows=placement.get("episodes") or []
    return season,[row.get("episode_number") for row in rows]


def _coverage_is_compatible(anilist_signature,mal_signature):
    """Allow exact agreement or one provider covering an initial subset.

    AniList sometimes represents several related OVAs as one watchlist item
    while its native MAL ID points to only the first standalone MAL record.
    That is partial coverage, not contradictory episode placement.
    """
    anilist_season,anilist_numbers=anilist_signature
    mal_season,mal_numbers=mal_signature
    if anilist_season!=mal_season or not anilist_numbers or not mal_numbers:
        return False
    shorter,longer=sorted((anilist_numbers,mal_numbers),key=len)
    return shorter==longer[:len(shorter)]


def _merge_dict(primary,secondary):
    result=deepcopy(primary or {})
    for key,value in (secondary or {}).items():
        if result.get(key) in (None,"",[]): result[key]=deepcopy(value)
    for key in ("genres","themes"):
        combined=[]; seen=set()
        for value in list((primary or {}).get(key) or [])+list((secondary or {}).get(key) or []):
            text=str(value or "").strip(); folded=text.casefold()
            if text and folded not in seen: combined.append(text); seen.add(folded)
        if combined: result[key]=combined
    result["mature"]=bool((primary or {}).get("mature") or (secondary or {}).get("mature"))
    return result


def _merge_placements(anilist,mal):
    anilist_signature=_episode_signature(anilist)
    mal_signature=_episode_signature(mal)
    if not _coverage_is_compatible(anilist_signature,mal_signature):
        raise MediatorProviderConflict(
            "AniList and MAL use different season/episode coordinates: {} != {}".format(
                anilist_signature,mal_signature))
    result=deepcopy(anilist); result["provider_path"]="anilist+mal"
    result["provider_id"]={"anilist":anilist.get("provider_id"),"mal":mal.get("provider_id")}
    result["tv_show"]=_merge_dict(anilist.get("tv_show"),mal.get("tv_show"))
    result["season"]=_merge_dict(anilist.get("season"),mal.get("season"))
    mal_rows={row.get("episode_number"):row for row in (mal.get("episodes") or [])}
    merged=[]
    for row in result.get("episodes") or []:
        merged.append(_merge_dict(row,mal_rows.get(row.get("episode_number"),{})))
    result["episodes"]=merged; result["provider_consensus"]=["anilist","mal"]
    if anilist_signature!=mal_signature:
        result["provider_consensus_scope"]="partial_episode_coverage"
        result["provider_coverage"]={
            "anilist":anilist_signature[1],"mal":mal_signature[1]}
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
    def _available(name,endpoint,item):
        if name=="simkl" and str(item.get("identity_resolution_status") or "")=="CONFLICT_EXACT":
            return False
        checker=getattr(endpoint,"available",None)
        return bool(checker(item)) if checker else True

    def _try(self,name,item,attempts,pending_placements=None):
        endpoint=self.endpoints[name]
        if not self._available(name,endpoint,item):
            reason="Simkl identity is conflicted" if name=="simkl" and str(item.get("identity_resolution_status") or "")=="CONFLICT_EXACT" else "provider ID unavailable"
            attempts.append({"provider":name,"skipped":True,"error":reason})
            return None
        try:
            return endpoint.resolve(item)
        except MediatorMetadataPending as exc:
            attempts.append({"provider":name,"skipped":False,"pending":True,"error":str(exc)})
            if pending_placements is not None and getattr(exc,"placement",None):
                pending_placements.append((name,exc.placement))
            return None
        except Exception as exc:
            attempts.append({"provider":name,"skipped":False,"error":str(exc)})
            LOGGER.info("Mediator %s endpoint could not resolve Prime item %s: %s",name,item.get("local_id"),exc)
            return None

    def resolve(self,item):
        attempts=[]; pending_placements=[]
        simkl=self._try("simkl",item,attempts,pending_placements)
        if simkl:
            simkl["provider_attempts"]=attempts; return simkl

        anilist=self._try("anilist",item,attempts,pending_placements)
        mal=self._try("mal",item,attempts,pending_placements)
        if anilist and mal:
            try:
                combined=_merge_placements(anilist,mal); combined["provider_attempts"]=attempts
                if combined.get("provider_consensus_scope")=="partial_episode_coverage":
                    LOGGER.info(
                        "AniList/MAL partial episode coverage accepted for Prime item %s: %s",
                        item.get("local_id"),combined.get("provider_coverage"))
                return combined
            except MediatorProviderConflict as exc:
                attempts.append({"provider":"anilist+mal","skipped":False,"error":str(exc)})
                LOGGER.info(
                    "MAL alternate coordinates ignored for Prime item %s; "
                    "keeping higher-priority AniList placement: %s",
                    item.get("local_id"),exc)
                # AniList is the higher-priority native structural authority.
                # MAL can enrich or confirm its placement, but must not veto a
                # valid AniList result merely because the catalogues number a
                # special or short-form title differently.
                anilist["provider_attempts"]=attempts
                anilist["provider_disagreement"]={
                    "provider":"mal","error":str(exc)}
                return anilist
        elif anilist:
            anilist["provider_attempts"]=attempts; return anilist
        elif mal:
            mal["provider_attempts"]=attempts; return mal

        kitsu=self._try("kitsu",item,attempts,pending_placements)
        if kitsu:
            kitsu["provider_attempts"]=attempts; return kitsu
        usable=[row for row in attempts if not row.get("skipped")]
        if not usable: raise MediatorPlacementError("Prime item has no usable provider metadata path")
        if pending_placements or all(row.get("pending") for row in usable):
            partial=next((placement for provider,placement in pending_placements
                          if provider=="anilist"),None)
            if partial is None and pending_placements:
                partial=pending_placements[0][1]
            if partial is not None:
                partial["provider_attempts"]=attempts
            raise MediatorMetadataPending(
                "Episode metadata has not been published by any available provider: "+
                "; ".join("{}: {}".format(row["provider"],row["error"]) for row in usable),
                placement=partial)
        raise MediatorPlacementError("; ".join("{}: {}".format(row["provider"],row["error"]) for row in usable))
