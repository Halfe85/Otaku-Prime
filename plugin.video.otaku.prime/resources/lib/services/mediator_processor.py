# -*- coding: utf-8 -*-
"""Provider-independent metadata processor for Prime library mediation."""
from __future__ import annotations

from copy import deepcopy

from resources.lib.logging_config import get_logger
from resources.lib.services.mediator_endpoint_anilist import AniListMediatorEndpoint
from resources.lib.services.mediator_endpoint_kitsu import KitsuMediatorEndpoint
from resources.lib.services.mediator_endpoint_mal import MALMediatorEndpoint
from resources.lib.services.mediator_helper_anilist import AniListMediatorClient
from resources.lib.services.mediator_endpoint_mal import MALMediatorClient
from resources.lib.services.mediator_endpoint_kitsu import KitsuMediatorClient
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
)
from resources.lib.services.mediator_structure import (
    StructuralSimklMediatorEndpoint,
    apply_structural_hint,
    coverage_state,
    safe_target_series_fallback,
)
from resources.lib.service_lifecycle import ServiceWorkHalted

LOGGER = get_logger(__name__)


class MediatorProviderConflict(MediatorPlacementError):
    pass


def _episode_signature(placement):
    season = (placement.get("season") or {}).get("number")
    rows = placement.get("episodes") or []
    return season, [row.get("episode_number") for row in rows]


def _coverage_is_compatible(anilist_signature, mal_signature):
    """Allow exact agreement or one provider covering an initial subset."""
    anilist_season, anilist_numbers = anilist_signature
    mal_season, mal_numbers = mal_signature
    if anilist_season != mal_season or not anilist_numbers or not mal_numbers:
        return False
    shorter, longer = sorted((anilist_numbers, mal_numbers), key=len)
    return shorter == longer[:len(shorter)]


def _merge_dict(primary, secondary):
    result = deepcopy(primary or {})
    for key, value in (secondary or {}).items():
        if result.get(key) in (None, "", []):
            result[key] = deepcopy(value)
    for key in ("genres", "themes"):
        combined = []
        seen = set()
        for value in list((primary or {}).get(key) or []) + list((secondary or {}).get(key) or []):
            text = str(value or "").strip()
            folded = text.casefold()
            if text and folded not in seen:
                combined.append(text)
                seen.add(folded)
        if combined:
            result[key] = combined
    result["mature"] = bool(
        (primary or {}).get("mature") or (secondary or {}).get("mature")
    )
    return result


def _merge_placements(anilist, mal):
    anilist_signature = _episode_signature(anilist)
    mal_signature = _episode_signature(mal)
    if not _coverage_is_compatible(anilist_signature, mal_signature):
        raise MediatorProviderConflict(
            "AniList and MAL use different season/episode coordinates: {} != {}".format(
                anilist_signature, mal_signature
            )
        )
    result = deepcopy(anilist)
    result["provider_path"] = "anilist+mal"
    result["provider_id"] = {
        "anilist": anilist.get("provider_id"),
        "mal": mal.get("provider_id"),
    }
    result["tv_show"] = _merge_dict(anilist.get("tv_show"), mal.get("tv_show"))
    result["season"] = _merge_dict(anilist.get("season"), mal.get("season"))
    mal_rows = {
        row.get("episode_number"): row for row in (mal.get("episodes") or [])
    }
    result["episodes"] = [
        _merge_dict(row, mal_rows.get(row.get("episode_number"), {}))
        for row in result.get("episodes") or []
    ]
    result["provider_consensus"] = ["anilist", "mal"]
    if anilist_signature != mal_signature:
        result["provider_consensus_scope"] = "partial_episode_coverage"
        result["provider_coverage"] = {
            "anilist": anilist_signature[1],
            "mal": mal_signature[1],
        }
    return result


class MediatorProcessor:
    """Resolve one Watchlist row while separating relations from structure.

    Prime uses provider relation graphs only while resolving an item. A final
    placement must represent one structural series owner and exact Kodi
    coordinates; relation-root identities are never written back over that owner.
    """

    def __init__(self, endpoints=None, simkl_client=None, halt_requested=None,
                 network_timeout=30):
        halt_requested = halt_requested or (lambda: False)
        self.endpoints = endpoints or {
            "simkl": StructuralSimklMediatorEndpoint(client=simkl_client),
            "anilist": AniListMediatorEndpoint(client=AniListMediatorClient(
                timeout=network_timeout, halt_requested=halt_requested)),
            "mal": MALMediatorEndpoint(client=MALMediatorClient(
                timeout=network_timeout, halt_requested=halt_requested)),
            "kitsu": KitsuMediatorEndpoint(client=KitsuMediatorClient(
                timeout=network_timeout, halt_requested=halt_requested)),
        }
        self.halt_requested = halt_requested

    def _checkpoint(self):
        if self.halt_requested():
            raise ServiceWorkHalted("metadata mediation halted for addon shutdown")

    @staticmethod
    def _available(name, endpoint, item):
        if (
            name == "simkl"
            and str(item.get("identity_resolution_status") or "") == "CONFLICT_EXACT"
        ):
            return False
        checker = getattr(endpoint, "available", None)
        return bool(checker(item)) if checker else True

    def _try(self, name, item, attempts, partial_placements=None,
             pending_placements=None):
        self._checkpoint()
        endpoint = self.endpoints[name]
        if not self._available(name, endpoint, item):
            reason = (
                "Simkl identity is conflicted"
                if name == "simkl"
                and str(item.get("identity_resolution_status") or "") == "CONFLICT_EXACT"
                else "provider ID unavailable"
            )
            attempts.append({"provider": name, "skipped": True, "error": reason})
            return None
        try:
            result = endpoint.resolve(item)
            self._checkpoint()
            coverage = coverage_state(item, result)
            if not coverage["complete"]:
                attempts.append({
                    "provider": name,
                    "skipped": False,
                    "pending": True,
                    "partial": True,
                    "error": "{}: covered {} of {} source units".format(
                        coverage["reason"], coverage["covered"],
                        coverage["expected"] if coverage["expected"] is not None else "unknown",
                    ),
                })
                if partial_placements is not None:
                    partial_placements.append((name, result, coverage))
                LOGGER.info(
                    "Mediator %s placement is structurally partial for Prime item %s: "
                    "covered=%s expected=%s",
                    name, item.get("local_id"), coverage["covered"], coverage["expected"],
                )
                return None
            return result
        except ServiceWorkHalted:
            raise
        except MediatorMetadataPending as exc:
            self._checkpoint()
            attempts.append({
                "provider": name,
                "skipped": False,
                "pending": True,
                "error": str(exc),
            })
            if pending_placements is not None and getattr(exc, "placement", None):
                pending_placements.append((name, exc.placement))
            return None
        except Exception as exc:
            self._checkpoint()
            attempts.append({"provider": name, "skipped": False, "error": str(exc)})
            LOGGER.info(
                "Mediator %s endpoint could not resolve Prime item %s: %s",
                name, item.get("local_id"), exc,
            )
            return None

    @staticmethod
    def _structural_hint(partial_placements):
        """Only a TVDB-backed Simkl partial is strong enough to own structure."""
        for provider, placement, coverage in partial_placements or []:
            if provider == "simkl" and (
                ((placement or {}).get("tv_show") or {}).get("tvdb_id") not in (None, "")
            ):
                return placement, coverage
        return None, None

    def _finish(self, item, placement, attempts, partial_placements=None):
        """Finalize structure and discard transient relation traversal state."""
        hint, hint_coverage = self._structural_hint(partial_placements)
        if hint is not None:
            final = apply_structural_hint(item, placement, hint)
            if final is None:
                attempts.append({
                    "provider": "structural_hint",
                    "skipped": False,
                    "pending": True,
                    "error": "partial structural mapping spans multiple seasons",
                })
                LOGGER.warning(
                    "Prime item %s has complete source metadata but only a partial "
                    "multi-season structural map; refusing to synthesize coordinates",
                    item.get("local_id"),
                )
                return None
            final["structural_hint_coverage"] = hint_coverage
        else:
            final = safe_target_series_fallback(item, placement)

        # These paths are useful while a provider is resolving the item, but the
        # final catalogue model stores only the chosen series/season/episodes.
        final.pop("relation_path", None)
        final.pop("franchise_relation_path", None)
        final["provider_attempts"] = attempts
        return final

    def resolve(self, item):
        self._checkpoint()
        attempts = []
        partial_placements = []
        pending_placements = []

        simkl = self._try(
            "simkl", item, attempts,
            partial_placements=partial_placements,
            pending_placements=pending_placements,
        )
        if simkl:
            # A complete Simkl result is already structural: its TVDB cross-map
            # owns the series and its episode rows carry the Kodi coordinates.
            simkl.pop("relation_path", None)
            simkl.pop("franchise_relation_path", None)
            simkl["provider_attempts"] = attempts
            return simkl

        anilist = self._try(
            "anilist", item, attempts,
            partial_placements=partial_placements,
            pending_placements=pending_placements,
        )
        mal = self._try(
            "mal", item, attempts,
            partial_placements=partial_placements,
            pending_placements=pending_placements,
        )

        candidate = None
        if anilist and mal:
            try:
                candidate = _merge_placements(anilist, mal)
                if candidate.get("provider_consensus_scope") == "partial_episode_coverage":
                    LOGGER.info(
                        "AniList/MAL partial episode coverage accepted for Prime item %s: %s",
                        item.get("local_id"), candidate.get("provider_coverage"),
                    )
            except MediatorProviderConflict as exc:
                attempts.append({
                    "provider": "anilist+mal",
                    "skipped": False,
                    "error": str(exc),
                })
                LOGGER.info(
                    "MAL alternate coordinates ignored for Prime item %s; "
                    "keeping higher-priority AniList source placement: %s",
                    item.get("local_id"), exc,
                )
                anilist["provider_disagreement"] = {
                    "provider": "mal", "error": str(exc)
                }
                candidate = anilist
        elif anilist:
            candidate = anilist
        elif mal:
            candidate = mal

        if candidate is not None:
            final = self._finish(
                item, candidate, attempts,
                partial_placements=partial_placements,
            )
            if final is not None:
                return final

        kitsu = self._try(
            "kitsu", item, attempts,
            partial_placements=partial_placements,
            pending_placements=pending_placements,
        )
        if kitsu:
            final = self._finish(
                item, kitsu, attempts,
                partial_placements=partial_placements,
            )
            if final is not None:
                return final

        usable = [row for row in attempts if not row.get("skipped")]
        if not usable:
            raise MediatorPlacementError("Prime item has no usable provider metadata path")

        partial = None
        structural_hint, _coverage = self._structural_hint(partial_placements)
        if structural_hint is not None:
            partial = structural_hint
        if partial is None:
            partial = next(
                (placement for provider, placement in pending_placements
                 if provider == "anilist"),
                None,
            )
        if partial is None and pending_placements:
            partial = pending_placements[0][1]
        if partial is not None:
            partial = deepcopy(partial)
            partial.pop("relation_path", None)
            partial.pop("franchise_relation_path", None)
            partial["provider_attempts"] = attempts
            raise MediatorMetadataPending(
                "Complete source metadata or structural coordinates are not yet available: "
                + "; ".join(
                    "{}: {}".format(row["provider"], row.get("error") or "pending")
                    for row in usable
                ),
                placement=partial,
            )

        if all(row.get("pending") for row in usable):
            raise MediatorMetadataPending(
                "Episode metadata has not been published by any available provider: "
                + "; ".join(
                    "{}: {}".format(row["provider"], row.get("error") or "pending")
                    for row in usable
                )
            )
        raise MediatorPlacementError(
            "; ".join(
                "{}: {}".format(row["provider"], row.get("error") or "failed")
                for row in usable
            )
        )
