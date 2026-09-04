# -*- coding: utf-8 -*-
"""Provider-priority metadata processor for Prime library mediation."""
from __future__ import annotations

from copy import deepcopy

from resources.lib.logging_config import get_logger
from resources.lib.services.mediator_endpoint_anilist import AniListMediatorEndpoint
from resources.lib.services.mediator_endpoint_kitsu import KitsuMediatorEndpoint
from resources.lib.services.mediator_endpoint_mal import MALMediatorEndpoint, MALMediatorClient
from resources.lib.services.mediator_helper_anilist import AniListMediatorClient
from resources.lib.services.mediator_endpoint_kitsu import KitsuMediatorClient
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
)
from resources.lib.services.mediator_simkl_strict import (
    StrictStructuralSimklMediatorEndpoint,
)
from resources.lib.services.mediator_structure import (
    coverage_state,
    placement_rows,
)
from resources.lib.services.mediator_trace import (
    MediatorTrace,
    placement_facts,
    watchlist_input_facts,
)
from resources.lib.service_lifecycle import ServiceWorkHalted

LOGGER = get_logger(__name__)


class MediatorProviderConflict(MediatorPlacementError):
    """Compatibility name retained for callers from the Alpha11 API surface."""


def _episode_signature(placement):
    season = (placement.get("season") or {}).get("number")
    rows = placement.get("episodes") or []
    return season, [row.get("episode_number") for row in rows]


def _coverage_is_compatible(first, second):
    first_season, first_numbers = first
    second_season, second_numbers = second
    if first_season != second_season or not first_numbers or not second_numbers:
        return False
    shorter, longer = sorted((first_numbers, second_numbers), key=len)
    return shorter == longer[:len(shorter)]


def _merge_dict(primary, secondary):
    result = deepcopy(primary or {})
    for key, value in (secondary or {}).items():
        if result.get(key) in (None, "", []):
            result[key] = deepcopy(value)
    for key in ("genres", "themes"):
        combined = []
        seen = set()
        for value in list((primary or {}).get(key) or []) + list(
            (secondary or {}).get(key) or []
        ):
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
        "anilist": anilist.get("provider_id"), "mal": mal.get("provider_id")
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
    return result


class MediatorProcessor:
    """Resolve Simkl first, then exact native AniList, MAL, and Kitsu paths."""

    def __init__(self, endpoints=None, simkl_client=None, halt_requested=None,
                 network_timeout=30):
        self.halt_requested = halt_requested or (lambda: False)
        supplied = dict(endpoints or {})
        simkl_endpoint = supplied.get("simkl")
        if simkl_endpoint is None:
            simkl_endpoint = StrictStructuralSimklMediatorEndpoint(client=simkl_client)
            if simkl_endpoint.client is None:
                from resources.lib.services.mediator_helper_simkl import SimklMediatorClient
                simkl_endpoint.client = SimklMediatorClient(
                    timeout=network_timeout,
                    halt_requested=self.halt_requested,
                )
        defaults = {
            "simkl": simkl_endpoint,
            "anilist": supplied.get("anilist") or AniListMediatorEndpoint(
                client=AniListMediatorClient(
                    timeout=network_timeout, halt_requested=self.halt_requested)),
            "mal": supplied.get("mal") or MALMediatorEndpoint(
                client=MALMediatorClient(
                    timeout=network_timeout, halt_requested=self.halt_requested)),
            "kitsu": supplied.get("kitsu") or KitsuMediatorEndpoint(
                client=KitsuMediatorClient(
                    timeout=network_timeout, halt_requested=self.halt_requested)),
        }
        self.endpoints = supplied if endpoints is not None else defaults
        if "simkl" not in self.endpoints and endpoints is None:
            self.endpoints["simkl"] = simkl_endpoint

    def _checkpoint(self):
        if self.halt_requested():
            raise ServiceWorkHalted("metadata mediation halted for addon shutdown")

    @staticmethod
    def _available(endpoint, item):
        checker = getattr(endpoint, "available", None)
        return bool(checker(item)) if checker else True

    @staticmethod
    def _require_tvdb_coordinates(item, placement):
        """Reject any TV placement that is not explicitly located by TVDB."""
        owner = (placement or {}).get("structural_owner") or {}
        library_type = str((placement or {}).get("library_type") or "series")
        if library_type != "movie" and owner.get("tvdb_id") in (None, ""):
            raise MediatorPlacementError(
                "Simkl resolved the media item but did not resolve a TVDB structural series owner"
            )

        bad = []
        for row in placement_rows(placement):
            source = row.get("source_episode_number")
            season = row.get("season_number")
            episode = row.get("episode_number")
            try:
                season_number = int(season)
                episode_number = int(episode)
            except (TypeError, ValueError):
                bad.append(source)
                continue
            if season_number < 0 or episode_number <= 0:
                bad.append(source)
        if bad:
            raise MediatorPlacementError(
                "Simkl placement lacks explicit TVDB coordinates for source episodes {}".format(
                    sorted(set(bad), key=lambda value: str(value))
                )
            )

    @staticmethod
    def _normalize_catalogue_owner(placement):
        """Make TVDB structural ownership the bootstrap Prime TV owner key.

        Tracker root IDs are retained only when the strict endpoint proved the
        root through target coordinates plus direct season ownership evidence.
        Otherwise they remain on watchlist/season/episode records and TVDB is
        the sole catalogue owner key.
        """
        result = deepcopy(placement or {})
        if str(result.get("library_type") or "series") == "movie":
            return result
        owner = result.get("structural_owner") or {}
        show = result.setdefault("tv_show", {})
        if owner.get("name"):
            show["name"] = owner.get("name")
        show["tvdb_id"] = str(owner.get("tvdb_id"))
        evidence = result.get("mediation_evidence") or {}
        if not evidence.get("root_identity_verified"):
            show["simkl_id"] = None
            show["anilist_id"] = None
            show["mal_id"] = None
            show["kitsu_id"] = None
        show["source"] = "simkl_tvdb_structural_owner"
        return result

    def _try(self, name, item, attempts, pending):
        self._checkpoint()
        endpoint = self.endpoints.get(name)
        if endpoint is None or not self._available(endpoint, item):
            attempts.append({"provider": name, "skipped": True,
                             "error": "provider ID unavailable"})
            return None
        try:
            result = endpoint.resolve(item)
            self._checkpoint()
            return result
        except ServiceWorkHalted:
            raise
        except MediatorMetadataPending as exc:
            attempts.append({"provider": name, "skipped": False,
                             "pending": True, "error": str(exc)})
            if getattr(exc, "placement", None):
                pending.append((name, exc.placement))
            return None
        except Exception as exc:
            self._checkpoint()
            attempts.append({"provider": name, "skipped": False,
                             "error": str(exc)})
            LOGGER.info(
                "Mediator %s endpoint could not resolve Prime item %s: %s",
                name, item.get("local_id"), exc,
            )
            return None

    @staticmethod
    def _finish(placement, attempts):
        result = deepcopy(placement)
        result["provider_attempts"] = list(attempts)
        return result

    def resolve(self, item):
        trace = MediatorTrace((item or {}).get("local_id"))
        trace.info("START", "ITEM_RECEIVED", watchlist_input_facts(item))
        self._checkpoint()

        if str((item or {}).get("identity_resolution_status") or "").upper() == "CONFLICT_EXACT":
            reason = "watchlist exact provider identity is conflicted; mediation is not allowed to guess"
            trace.error("IDENTITY", "BLOCKED", watchlist_input_facts(item), reason=reason)
            raise MediatorPlacementError(reason)

        attempts = []
        pending = []
        simkl = self._try("simkl", item, attempts, pending)
        if simkl:
            trace.info("SIMKL", "PLACEMENT_DISCOVERED", placement_facts(simkl))
            coverage = coverage_state(item, simkl)
            if coverage["complete"]:
                try:
                    self._require_tvdb_coordinates(item, simkl)
                    normalized = self._normalize_catalogue_owner(simkl)
                except MediatorPlacementError as exc:
                    attempts.append({"provider": "simkl", "skipped": False,
                                     "error": str(exc)})
                else:
                    normalized.pop("relation_path", None)
                    normalized.pop("franchise_relation_path", None)
                    attempts.append({"provider": "simkl", "skipped": False,
                                     "success": True})
                    trace.info("PLAN", "READY_FOR_CATALOGUE", placement_facts(normalized))
                    return self._finish(normalized, attempts)
            else:
                reason = "Simkl covered {} of {} source units ({})".format(
                    coverage["covered"],
                    coverage["expected"] if coverage["expected"] is not None else "unknown",
                    coverage["reason"],
                )
                attempts.append({"provider": "simkl", "skipped": False,
                                 "pending": True, "error": reason})
                pending.append(("simkl", simkl))

        # Native structural resolvers are independent fallbacks. They do not
        # search Simkl using another provider's ID. AniList owns the native
        # coordinate decision while MAL enriches/confirms it when compatible.
        anilist = self._try("anilist", item, attempts, pending)
        mal = self._try("mal", item, attempts, pending)
        if anilist and mal:
            try:
                placement = _merge_placements(anilist, mal)
            except MediatorProviderConflict as exc:
                attempts.append({"provider": "anilist+mal", "skipped": False,
                                 "error": str(exc)})
                anilist["provider_disagreement"] = {
                    "provider": "mal", "error": str(exc)
                }
                placement = anilist
                LOGGER.warning(
                    "MAL alternate coordinates ignored for Prime item %s; "
                    "keeping higher-priority AniList placement: %s",
                    item.get("local_id"), exc,
                )
            attempts.append({"provider": placement.get("provider_path"),
                             "skipped": False, "success": True})
            return self._finish(placement, attempts)
        if anilist or mal:
            placement = anilist or mal
            attempts.append({"provider": placement.get("provider_path"),
                             "skipped": False, "success": True})
            return self._finish(placement, attempts)

        kitsu = self._try("kitsu", item, attempts, pending)
        if kitsu:
            attempts.append({"provider": "kitsu", "skipped": False,
                             "success": True})
            return self._finish(kitsu, attempts)

        usable = [row for row in attempts if not row.get("skipped")]
        if not usable:
            raise MediatorPlacementError("Prime item has no usable provider metadata path")
        reason = "; ".join(
            "{}: {}".format(row["provider"], row.get("error")) for row in usable
        )
        if pending or all(row.get("pending") for row in usable):
            partial = pending[0][1] if pending else None
            if partial is not None:
                partial = self._finish(partial, attempts)
            raise MediatorMetadataPending(
                "Episode metadata has not been published by any available provider: " + reason,
                placement=partial,
            )
        raise MediatorPlacementError(reason)
